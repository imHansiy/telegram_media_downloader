"""Confirmed single-resource deletion, with bounded paths and recoverable local files.

Preview is read-only. Execution consumes only server-held targets, rechecks every
file before changing anything, and reports partial results instead of hiding them.
"""

import copy
import os
import secrets
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

import requests

from module.cloud_drive import CloudDrive
from module.video_cover import cover_path
from .library import clean_path
from .taxonomy import detect_resource_kind

TRASH = ".resource-trash"


@dataclass
class DeletionContext:
    scope: str
    drive: object
    save_path: str
    lookup: Callable
    assert_idle: Callable
    cleanup: Callable


def safe_relative(value):
    if not isinstance(value, str) or value != clean_path(value):
        raise ValueError("需要资源库内的完整相对文件路径")
    if unquote(value) != value:
        raise ValueError("路径包含有歧义的编码，无法安全删除")
    for part in value.split("/"):
        stem = part.split(".", 1)[0].upper()
        if (
            part.endswith((".", " "))
            or part.casefold() == TRASH
            or stem
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *[f"COM{i}" for i in range(1, 10)],
                *[f"LPT{i}" for i in range(1, 10)],
            }
        ):
            raise ValueError("该路径不是可安全删除的资源文件")
    return value


def local_path(root, relative):
    """Reject root, traversal, directories, symlinks and Windows junctions."""
    root = Path(root).absolute()
    target = root.joinpath(*relative.split("/"))
    if root == Path(root.anchor) or target == root:
        raise ValueError("不允许删除根目录")
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise ValueError("本地文件不在配置的下载目录内") from error
    for item in (target, *target.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("文件路径包含符号链接或目录联接，已阻止删除")
        if item == target and not stat.S_ISREG(info.st_mode):
            raise ValueError("只允许删除单个文件，不允许删除目录")
    return target


def local_info(root, relative):
    path = local_path(root, relative)
    try:
        info = path.stat()
        return {
            "exists": True,
            "size": info.st_size,
            "mtime": info.st_mtime_ns,
            "inode": info.st_ino,
            "device": info.st_dev,
        }
    except FileNotFoundError:
        return {"exists": False}


class WebDavFiles:
    def __init__(self, drive):
        self.drive = drive
        self.auth = (
            (drive.webdav_username, drive.webdav_password)
            if drive.webdav_username
            else None
        )

    def url(self, path):
        root = (self.drive.remote_dir or "").strip("/")
        if unquote(root) != root:
            raise ValueError("云盘根目录包含有歧义的编码")
        return CloudDrive.build_webdav_remote_url(
            self.drive, f"{root}/{path}".strip("/")
        )

    def probe(self, path):
        url = self.url(path)
        with requests.request(
            "PROPFIND",
            url,
            auth=self.auth,
            headers={"Depth": "0", "Content-Type": "application/xml"},
            data=b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getcontentlength/><d:getetag/><d:getlastmodified/></d:prop></d:propfind>',
            timeout=(8, 15),
            allow_redirects=False,
            stream=True,
        ) as response:
            if response.status_code == 404:
                return {"exists": False}
            if response.status_code not in (200, 207):
                raise ValueError(f"无法核实云盘文件（HTTP {response.status_code}），未执行删除")
            data = bytearray()
            for chunk in response.iter_content(8192):
                data.extend(chunk)
                if len(data) > 262144:
                    raise ValueError("云盘文件元数据过大，已停止删除")
        try:
            tree = ET.fromstring(data)
        except ET.ParseError as error:
            raise ValueError("云盘文件元数据无效，已停止删除") from error
        expected = unquote(urlsplit(url).path).rstrip("/")
        for item in tree.findall("{DAV:}response"):
            href = item.findtext("{DAV:}href", "")
            if unquote(urlsplit(href).path).rstrip("/") != expected:
                continue
            values = {}
            for propstat in item.findall("{DAV:}propstat"):
                if " 200 " not in propstat.findtext("{DAV:}status", ""):
                    continue
                prop = propstat.find("{DAV:}prop")
                if prop is not None:
                    for child in prop:
                        values[child.tag] = child
            resource_type = values.get("{DAV:}resourcetype")
            if (
                resource_type is None
                or resource_type.find("{DAV:}collection") is not None
            ):
                raise ValueError("只允许删除单个云盘文件，不允许递归删除目录")

            def value(name):
                element = values.get("{DAV:}" + name)
                return (element.text or "").strip() if element is not None else ""

            return {
                "exists": True,
                "size": value("getcontentlength"),
                "etag": value("getetag"),
                "modified": value("getlastmodified"),
            }
        raise ValueError("云盘响应未能确认目标文件，未执行删除")

    def delete(self, path, before):
        headers = {"Depth": "0"}
        if before.get("etag") and not before["etag"].startswith("W/"):
            headers["If-Match"] = before["etag"]
        elif before.get("modified"):
            headers["If-Unmodified-Since"] = before["modified"]
        with requests.delete(
            self.url(path),
            auth=self.auth,
            headers=headers,
            timeout=(8, 15),
            allow_redirects=False,
        ) as response:
            if response.status_code not in (200, 204, 404):
                raise ValueError(f"云盘删除未完成（HTTP {response.status_code}）")
        if self.probe(path)["exists"]:
            raise ValueError("云盘仍返回该文件，未确认删除成功")


class ResourceDeletion:
    def __init__(self, remote_factory=WebDavFiles):
        self.remote_factory = remote_factory
        self.plans = {}
        self.lock = threading.RLock()
        self.guards = [threading.RLock() for _ in range(64)]

    def guard(self, scope, path):
        return self.guards[hash((scope, path)) % len(self.guards)]

    def prepare(self, context, path):
        path = safe_relative(path)
        if context.drive.upload_adapter != "webdav":
            raise ValueError("资源库删除目前仅支持当前 WebDAV 云盘")
        with self.guard(context.scope, path):
            context.assert_idle(path)
            entry = context.lookup(path)
            remote = self.remote_factory(context.drive)
            # Validate local paths BEFORE making any remote request.
            local = local_info(context.save_path, path)
            primary = remote.probe(path)
            if not entry and not primary["exists"]:
                raise ValueError("资源不存在或已被移除，请刷新资源库")
            kind = detect_resource_kind(path, (entry or {}).get("content_type", ""))
            relatives = [path, cover_path(path)] if kind == "video" else [path]
            for relative in relatives:
                context.assert_idle(relative)
            targets = []
            for storage in ("remote", "local"):
                for index, relative in enumerate(relatives):
                    info = (
                        (primary if index == 0 else remote.probe(relative))
                        if storage == "remote"
                        else (
                            local
                            if index == 0
                            else local_info(context.save_path, relative)
                        )
                    )
                    targets.append(
                        {
                            "id": f"{storage}_{index}",
                            "storage": storage,
                            "relative": relative,
                            "path": (
                                "/"
                                + "/".join(
                                    [
                                        (context.drive.remote_dir or "").strip("/"),
                                        relative,
                                    ]
                                ).lstrip("/")
                                if storage == "remote"
                                else str(local_path(context.save_path, relative))
                            ),
                            "label": ("云盘" if storage == "remote" else "本地")
                            + ("资源" if index == 0 else "封面"),
                            "before": info,
                        }
                    )
            token = secrets.token_urlsafe(32)
            plan = {
                "token": token,
                "scope": context.scope,
                "path": path,
                "name": path.rsplit("/", 1)[-1],
                "created": time.monotonic(),
                "operation": uuid.uuid4().hex,
                "targets": targets,
                "results": {},
                "relatives": relatives,
            }
            with self.lock:
                self.plans = {
                    key: value
                    for key, value in self.plans.items()
                    if time.monotonic() - value["created"] < 900
                }
                if len(self.plans) >= 128:
                    raise ValueError("待确认的删除过多，请稍后重试")
                self.plans[token] = plan
            return {
                "token": token,
                "name": plan["name"],
                "path": path,
                "targets": [
                    {
                        key: value
                        for key, value in target.items()
                        if key not in ("before", "relative")
                    }
                    | {"exists": target["before"]["exists"]}
                    for target in targets
                ],
                "recycle_path": str(
                    Path(context.save_path).absolute() / TRASH / plan["operation"]
                ),
            }

    def execute(self, context, token):
        with self.lock:
            plan = self.plans.get(token) if isinstance(token, str) else None
        if (
            not plan
            or plan["scope"] != context.scope
            or time.monotonic() - plan["created"] > 900
        ):
            raise ValueError("删除确认已失效或云盘配置已变化，请重新确认")
        with self.guard(context.scope, plan["path"]):
            if plan.get("complete"):
                return self._result(plan, True, "该资源已删除")
            remote = self.remote_factory(context.drive)
            try:
                for relative in plan["relatives"]:
                    context.assert_idle(relative)
                for target in plan["targets"]:
                    now = self._probe(context, remote, target)
                    self._unchanged(plan, target, now)
                for target in plan["targets"]:
                    context.assert_idle(plan["path"])
                    if target["id"] in plan["results"]:
                        continue
                    now = self._probe(context, remote, target)
                    self._unchanged(plan, target, now)
                    if not now["exists"]:
                        status, destination = "missing", ""
                    elif target["storage"] == "remote":
                        remote.delete(target["relative"], now)
                        status, destination = "deleted", ""
                    else:
                        destination = self._recycle(context, plan, target)
                        status = "recycled"
                    plan["results"][target["id"]] = {
                        "id": target["id"],
                        "label": target["label"],
                        "path": target["path"],
                        "status": status,
                        "destination": destination,
                    }
                context.cleanup(plan["relatives"])
                plan["complete"] = True
                return self._result(plan, True, "资源已删除，收藏和历史已清理；本地副本可从回收目录恢复")
            except (ValueError, OSError, requests.RequestException) as error:
                message = (
                    str(error)
                    if isinstance(error, ValueError)
                    else f"删除未完成（{type(error).__name__}），可重试剩余项目"
                )
                return self._result(plan, False, message)

    @staticmethod
    def _probe(context, remote, target):
        return (
            remote.probe(target["relative"])
            if target["storage"] == "remote"
            else local_info(context.save_path, target["relative"])
        )

    @staticmethod
    def _unchanged(plan, target, now):
        if now["exists"] and (
            target["id"] in plan["results"] or now != target["before"]
        ):
            raise ValueError(f"{target['label']}在确认后发生变化，请重新确认，未继续删除")

    @staticmethod
    def _recycle(context, plan, target):
        source = local_path(context.save_path, target["relative"])
        trash_relative = f"{TRASH}/{plan['operation']}/{target['relative']}"
        destination = local_path(context.save_path, trash_relative)
        if destination.exists():
            raise ValueError("回收位置已被占用，未覆盖现有文件")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = local_path(context.save_path, trash_relative)
        if local_info(context.save_path, target["relative"]) != target["before"]:
            raise ValueError("本地文件发生变化，未移动")
        # Same-filesystem rename preserves bytes, and does not recursively move directories.
        os.rename(source, destination)
        return str(destination)

    @staticmethod
    def _result(plan, success, message):
        return {
            "success": success,
            "message": message,
            "path": plan["path"],
            "results": [
                copy.deepcopy(
                    plan["results"].get(
                        target["id"],
                        {
                            "id": target["id"],
                            "label": target["label"],
                            "path": target["path"],
                            "status": "pending",
                            "destination": "",
                        },
                    )
                )
                for target in plan["targets"]
            ],
        }


resource_deletions = ResourceDeletion()
