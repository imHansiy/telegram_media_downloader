"""Path-keyed catalog, legacy presentation, filtering and atomic local persistence."""

import copy
import hashlib
import json
import os
import threading
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path

from .taxonomy import ADULT_BY_ID, CATEGORY_BY_ID, detect_resource_kind
from .summary import category_only_summary


def clean_path(path):
    value = str(path or "").replace("\\", "/").strip("/")
    parts = value.split("/")
    if not value or any(
        part in ("", ".", "..") or ":" in part or "\x00" in part for part in parts
    ):
        raise ValueError("无效的资源相对路径")
    return value


def legacy_analysis(path, record=None, mime_type=""):
    record = record or {}
    analysis = record.get("resource_analysis")
    if isinstance(analysis, dict) and analysis.get("schema_version") == 2:
        return copy.deepcopy(analysis)
    kind = detect_resource_kind(path, mime_type)
    adult = "nsfw" in path.lower().split("/")
    legacy = bool(record.get("ai_summary") or record.get("ai_tags") or adult)
    return {
        "schema_version": 1 if legacy else 0,
        "resource_kind": kind,
        "category": "adult" if adult else "other",
        "category_label": "成人内容" if adult else "待确认",
        "subtype": "",
        "subtype_label": "",
        "status": "legacy" if legacy else "unanalysed",
        "confidence": None,
        "facets": {},
        "directions": [],
        "tags": [],
        "evidence_sources": [],
        "limitations": ["目录名称只是历史归档线索，尚未通过新分析流程确认。"] if adult else [],
        "summary": "旧版简介尚未按资源类型验证，请重新分析。" if legacy else "尚未分析此资源。",
    }


class CatalogStore:
    def __init__(self, path, backend=None, setting_key=None):
        self.path = Path(path)
        self.backend, self.setting_key = backend, setting_key
        self.lock = threading.RLock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.entries = {}
        if backend and backend.conn:
            saved = backend.load_setting(setting_key)
            if isinstance(saved, dict):
                # A durable local deletion must survive a stale/unavailable DB write.
                deleted = {key: value for key, value in self.entries.items() if value.get("deleted")}
                self.entries.update(saved)
                self.entries.update(deleted)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy({key: value for key, value in self.entries.items() if not value.get("deleted")})

    def deleted_paths(self):
        with self.lock:
            return {key for key, value in self.entries.items() if value.get("deleted")}

    def mark_deleted(self, paths):
        with self.lock:
            entries = copy.deepcopy(self.entries)
            for path in paths:
                key = clean_path(path)
                entries[key] = {"path": key, "deleted": True}
            self._persist(entries)
            self.entries = entries

    def put(self, entry):
        key = clean_path(entry["path"])
        with self.lock:
            self.entries[key] = copy.deepcopy(entry)
            self._persist()

    def remove(self, path):
        with self.lock:
            self.entries.pop(clean_path(path), None)
            self._persist()

    def _persist(self, entries=None):
        values = self.entries if entries is None else entries
        if self.backend and self.backend.conn:
            self.backend.save_setting(self.setting_key, values)
        # Atomic file fallback also preserves reanalysis when PostgreSQL is unavailable.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        try:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(values, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        finally:
            if temp.exists():
                temp.unlink()


@lru_cache(maxsize=8)
def _store_for_scope(scope):
    from module.db import db

    digest = hashlib.sha256(scope.encode()).hexdigest()[:24]
    return CatalogStore(
        Path(".cache") / "resource-library" / f"{digest}.json",
        db,
        f"resource_library_{digest}",
    )


def catalog_store(drive_config):
    scope = "|".join(
        str(getattr(drive_config, key, "") or "")
        for key in ("webdav_url", "remote_dir", "webdav_username")
    )
    return _store_for_scope(scope)


def history_entries(history, save_path, relative_path):
    entries = {}
    # Snapshot the containers: a downloader can update them while a web request reads.
    for messages in list(history.values()):
        for record in list((messages or {}).values()):
            if not isinstance(record, dict) or not record.get("file_name"):
                continue
            state = record.get("state")
            if state and state != "completed":
                continue
            if not state and (
                record.get("total_size", 0) <= 0
                or record.get("down_byte", 0) < record.get("total_size", 0)
            ):
                continue
            try:
                path = clean_path(relative_path(save_path, record["file_name"]))
            except ValueError:
                continue
            timestamp = record.get("end_time") or 0
            try:
                modified = datetime.fromtimestamp(
                    float(timestamp), timezone.utc
                ).isoformat()
            except (ValueError, TypeError, OSError):
                modified = ""
            entries[path] = {
                "name": path.rsplit("/", 1)[-1],
                "path": path,
                "size": record.get("total_size", 0),
                "modified": modified,
                "content_type": "",
                "analysis": legacy_analysis(path, record),
                "origin": "download_history",
                "source_context": copy.deepcopy(record.get("source_context", {})),
            }
    return entries


def merged_entries(history, save_path, relative_path, store):
    entries = history_entries(history, save_path, relative_path)
    for path, saved in store.snapshot().items():
        current = entries.get(path)
        saved_at = saved.get("analysis", {}).get("analyzed_at", "")
        current_at = (current or {}).get("analysis", {}).get("analyzed_at", "")
        if not current or saved_at >= current_at:
            entries[path] = saved
            if current and current.get("source_context") and not saved.get("source_context"):
                entries[path]["source_context"] = copy.deepcopy(current["source_context"])
    deleted = store.deleted_paths()
    visible = {path: entry for path, entry in entries.items() if path not in deleted}
    for entry in visible.values():
        analysis = entry["analysis"]
        if (analysis.get("status") == "complete" and analysis.get("resource_kind") == "video"
                and analysis.get("category") == "adult" and category_only_summary(analysis.get("summary"))):
            # Entries are detached snapshots. Re-evaluate old acceptance for all
            # readers without migrating or overwriting the stored analysis.
            quality = analysis.setdefault("summary_quality", {})
            quality["status"] = "review"
            issues = quality.setdefault("issues", [])
            if "category_only_summary" not in issues:
                issues.append("category_only_summary")
    return visible


def query_library(
    entries,
    *,
    category="",
    subtype="",
    kind="",
    status="",
    query="",
    page=1,
    page_size=60,
    sort="newest",
    tags=(),
    tag_query="",
    scope="all",
    view="all",
    history_kind="opened",
):
    values = list(entries.values())
    counts = {
        "categories": dict(Counter(entry["analysis"]["category"] for entry in values)),
        "kinds": dict(Counter(entry["analysis"]["resource_kind"] for entry in values)),
        "subtypes": dict(
            Counter(
                entry["analysis"].get("subtype")
                for entry in values
                if entry["analysis"].get("category") == "adult"
            )
        ),
        "statuses": dict(Counter(entry["analysis"]["status"] for entry in values)),
    }
    search = query.strip().casefold()
    selected_tags = {tag.strip().casefold() for tag in tags if tag.strip()}
    matches = []
    tag_counts = Counter()
    for entry in values:
        analysis = entry["analysis"]
        if scope in ("adult", "movie") and analysis["category"] != scope:
            continue
        if scope == "other" and analysis["category"] in ("adult", "movie"):
            continue
        activity = entry.get("activity", {})
        if view == "favorites" and not activity.get("favorite"):
            continue
        if view == "history" and not activity.get(f"{history_kind}_at"):
            continue
        if category and analysis["category"] != category:
            continue
        if subtype and (
            analysis["category"] != "adult" or analysis.get("subtype") != subtype
        ):
            continue
        if kind and analysis["resource_kind"] != kind:
            continue
        if status and (
            (analysis["status"] != status or (status == "complete" and analysis.get("summary_quality", {}).get("status") == "review"))
            if status != "review"
            else (analysis["status"] == "complete"
                  and analysis.get("summary_quality", {}).get("status") != "review")
        ):
            continue
        text = " ".join(
            [
                entry["name"],
                entry["path"],
                analysis.get("summary", ""),
                analysis.get("content_description", ""),
                *analysis.get("tags", []),
            ]
        ).casefold()
        if search and search not in text:
            continue
        entry_tags = {str(tag).strip() for tag in analysis.get("tags", []) if str(tag).strip()}
        tag_counts.update(entry_tags)
        if not selected_tags.issubset({tag.casefold() for tag in entry_tags}):
            continue
        matches.append(entry)
    if view == "history":
        matches.sort(key=lambda entry: (entry.get("activity", {}).get(f"{history_kind}_at", ""), entry["path"]), reverse=True)
    elif view == "favorites" and sort == "newest":
        matches.sort(key=lambda entry: (entry.get("activity", {}).get("favorited_at", ""), entry["path"]), reverse=True)
    elif sort == "name":
        matches.sort(key=lambda entry: (entry["name"].casefold(), entry["path"]))
    elif sort == "size":
        matches.sort(
            key=lambda entry: (entry.get("size", 0), entry["path"]), reverse=True
        )
    else:
        matches.sort(
            key=lambda entry: (
                _modified_timestamp(entry.get("modified", "")),
                entry["path"],
            ),
            reverse=True,
        )
    page_size = max(1, min(200, page_size))
    pages = max(1, (len(matches) + page_size - 1) // page_size)
    page = max(1, min(page, pages))
    return {
        "files": matches[(page - 1) * page_size : page * page_size],
        "total": len(matches),
        "indexed_total": len(values),
        "page": page,
        "pages": pages,
        "counts": counts,
        "tag_facets": [{"name": name, "count": count} for name, count in
                       sorted(tag_counts.items(), key=lambda item: (-item[1], item[0].casefold()))
                       if tag_query.strip().casefold() in name.casefold()][:200],
    }


def _modified_timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        try:
            return parsedate_to_datetime(value).timestamp()
        except (ValueError, TypeError, AttributeError):
            return 0
