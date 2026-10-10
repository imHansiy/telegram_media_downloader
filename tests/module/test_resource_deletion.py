"""Deletion tests operate ONLY on temporary files and an in-memory WebDAV stand-in."""

import copy
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

import requests
import module.web as web
from module.cloud_drive import CloudDriveConfig
from module.resource_analysis.activity import ActivityStore, with_activity
from module.resource_analysis.deletion import (
    DeletionContext,
    ResourceDeletion,
    WebDavFiles,
    local_path,
)
from module.resource_analysis.library import CatalogStore, merged_entries
from tests.module.test_resource_library import entry


class FakeRemote:
    def __init__(self):
        self.files = {}
        self.deleted = []
        self.fail = ""
        self.probes = []

    def probe(self, path):
        self.probes.append(path)
        return copy.deepcopy(self.files.get(path, {"exists": False}))

    def delete(self, path, before):
        if path == self.fail:
            raise ValueError("模拟封面删除失败")
        if self.files.get(path) != before:
            raise ValueError("文件版本已变化")
        self.deleted.append(path)
        self.files.pop(path, None)


class DeletionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = "频道/clip #1.mp4"
        self.cover = self.path + ".cover.jpg"
        for relative in (self.path, self.cover, "另一目录/clip #1.mp4"):
            file = self.root / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(relative.encode())
        self.drive = CloudDriveConfig(
            upload_adapter="webdav",
            remote_dir="Library",
            webdav_url="https://fixture.invalid/dav",
        )
        self.remote = FakeRemote()
        for path in (self.path, self.cover):
            self.remote.files[path] = {
                "exists": True,
                "etag": '"original"',
                "size": 100,
                "modified": "yesterday",
            }
        self.cleanup = mock.Mock()
        self.idle = mock.Mock()
        self.context = DeletionContext(
            "fixture",
            self.drive,
            str(self.root),
            lambda path: entry(path),
            self.idle,
            self.cleanup,
        )
        self.manager = ResourceDeletion(lambda drive: self.remote)

    def prepare(self):
        return self.manager.prepare(self.context, self.path)

    def test_preview_is_read_only_and_names_all_four_exact_targets(self):
        result = self.prepare()
        self.assertEqual(4, len(result["targets"]))
        self.assertEqual(
            {
                "/Library/" + self.path,
                "/Library/" + self.cover,
                str(self.root / self.path),
                str(self.root / self.cover),
            },
            {item["path"] for item in result["targets"]},
        )
        self.assertEqual([], self.remote.deleted)
        self.assertTrue((self.root / self.path).is_file())
        self.assertFalse((self.root / ".resource-trash").exists())
        self.cleanup.assert_not_called()

    def test_deletes_remote_recycles_local_and_preserves_same_name_in_other_directory(
        self,
    ):
        plan = self.prepare()
        plan["targets"][0]["path"] = "untrusted-client-path"
        result = self.manager.execute(self.context, plan["token"])
        self.assertTrue(result["success"], result)
        self.assertEqual([self.path, self.cover], self.remote.deleted)
        for path in (self.path, self.cover):
            self.assertFalse((self.root / path).exists())
            self.assertEqual(
                path.encode(), (Path(plan["recycle_path"]) / path).read_bytes()
            )
        self.assertTrue((self.root / "另一目录/clip #1.mp4").exists())
        self.assertTrue((self.root / "频道").is_dir())
        self.cleanup.assert_called_once_with([self.path, self.cover])
        self.assertEqual(
            result,
            self.manager.execute(self.context, plan["token"])
            | {"message": result["message"]},
        )
        self.assertEqual(2, len(self.remote.deleted), "double submit is idempotent")

    def test_missing_cover_is_successful_noop(self):
        (self.root / self.cover).unlink()
        self.remote.files.pop(self.cover)
        result = self.manager.execute(self.context, self.prepare()["token"])
        self.assertTrue(result["success"])
        self.assertEqual(
            2, sum(item["status"] == "missing" for item in result["results"])
        )

    def test_nonvideo_resource_has_no_speculative_cover_target(self):
        path = "document.pdf"
        self.remote.files[path] = self.remote.files[self.path].copy()
        result = self.manager.prepare(self.context, path)
        self.assertEqual(2, len(result["targets"]))

    def test_traversal_encoded_paths_root_and_windows_aliases_are_rejected(self):
        for path in (
            "",
            "/",
            "/clip.mp4",
            "../clip.mp4",
            "folder/../../clip.mp4",
            "C:/private/file",
            "folder/clip.mp4:secret",
            "%2e%2e/clip.mp4",
            "folder%2fclip.mp4",
            "folder\\clip.mp4",
            ".resource-trash/file.mp4",
            "folder/clip.mp4.",
            "folder/NUL.mp4",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.manager.prepare(self.context, path)
        self.assertEqual([], self.remote.probes)

    def test_local_directory_and_symlinks_are_rejected_before_remote_access(self):
        with self.assertRaises(ValueError):
            self.manager.prepare(self.context, "频道")
        original = Path.lstat

        def lstat(path):
            if path == self.root / self.path:
                return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
            return original(path)

        with mock.patch.object(Path, "lstat", lstat), self.assertRaises(ValueError):
            local_path(str(self.root), self.path)
        self.assertEqual([], self.remote.deleted)

    def test_busy_resource_is_rejected_before_preview(self):
        self.idle.side_effect = ValueError("正在下载")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual([], self.remote.probes)

    def test_becoming_busy_after_confirmation_prevents_every_deletion(self):
        plan = self.prepare()
        self.idle.side_effect = ValueError("正在分析")
        self.assertFalse(self.manager.execute(self.context, plan["token"])["success"])
        self.assertEqual([], self.remote.deleted)
        self.assertTrue((self.root / self.path).exists())

    def test_changed_local_or_remote_version_prevents_all_deletions(self):
        for storage in ("local", "remote"):
            with self.subTest(storage=storage):
                plan = self.prepare()
                if storage == "local":
                    (self.root / self.path).write_bytes(b"a new version")
                else:
                    self.remote.files[self.path]["etag"] = '"new version"'
                self.assertFalse(
                    self.manager.execute(self.context, plan["token"])["success"]
                )
                self.assertEqual([], self.remote.deleted)
                self.cleanup.assert_not_called()

    def test_partial_failure_is_reported_and_retry_only_handles_remaining_targets(self):
        plan = self.prepare()
        self.remote.fail = self.cover
        result = self.manager.execute(self.context, plan["token"])
        self.assertFalse(result["success"])
        self.assertEqual(
            ["deleted", "pending", "pending", "pending"],
            [item["status"] for item in result["results"]],
        )
        self.assertTrue((self.root / self.path).exists())
        self.cleanup.assert_not_called()
        self.remote.fail = ""
        self.assertTrue(self.manager.execute(self.context, plan["token"])["success"])
        self.assertEqual([self.path, self.cover], self.remote.deleted)

    def test_file_recreated_during_partial_failure_is_not_deleted_again(self):
        plan = self.prepare()
        original = self.remote.files[self.path].copy()
        self.remote.fail = self.cover
        self.manager.execute(self.context, plan["token"])
        self.remote.files[self.path] = original
        self.remote.fail = ""
        self.assertFalse(self.manager.execute(self.context, plan["token"])["success"])
        self.assertEqual([self.path], self.remote.deleted)

    def test_local_move_and_metadata_failures_do_not_claim_success(self):
        plan = self.prepare()
        with mock.patch(
            "module.resource_analysis.deletion.os.rename", side_effect=PermissionError
        ):
            result = self.manager.execute(self.context, plan["token"])
        self.assertFalse(result["success"])
        self.assertTrue((self.root / self.path).exists())
        self.cleanup.side_effect = OSError("disk full")
        self.assertFalse(self.manager.execute(self.context, plan["token"])["success"])
        self.assertTrue((Path(plan["recycle_path"]) / self.path).exists())
        self.cleanup.side_effect = None
        self.assertTrue(self.manager.execute(self.context, plan["token"])["success"])

    def test_wrong_scope_unknown_token_and_expired_confirmation_do_nothing(self):
        plan = self.prepare()
        for context, token in (
            (replace(self.context, scope="another cloud"), plan["token"]),
            (self.context, "tampered"),
        ):
            with self.assertRaises(ValueError):
                self.manager.execute(context, token)
        self.manager.plans[plan["token"]]["created"] -= 901
        with self.assertRaises(ValueError):
            self.manager.execute(self.context, plan["token"])
        self.assertEqual([], self.remote.deleted)


class DeletionRouteTest(unittest.TestCase):
    def setUp(self):
        DeletionTest.setUp(self)
        self.catalog = CatalogStore(self.root / "catalog.json")
        self.catalog.put(entry(self.path, "adult"))
        self.activity = ActivityStore(self.root / "activity.json")
        for action in ("favorite", "open", "progress"):
            self.activity.update(
                action,
                path=self.path,
                entry=entry(self.path, "adult"),
                value=True,
                position=3,
                duration=10,
            )
        self.history = {
            12: {
                34: {
                    "state": "completed",
                    "file_name": str(self.root / self.path),
                    "profile_id": "test",
                    "total_size": 10,
                    "down_byte": 10,
                }
            }
        }
        self.old_disabled = web._flask_app.config.get("LOGIN_DISABLED")
        web._flask_app.config["LOGIN_DISABLED"] = True
        self.addCleanup(
            lambda: web._flask_app.config.update(LOGIN_DISABLED=self.old_disabled)
        )
        for patcher in (
            mock.patch.object(web, "resource_deletions", self.manager),
            mock.patch.object(
                web,
                "_app_instance",
                SimpleNamespace(
                    cloud_drive_config=self.drive, save_path=str(self.root)
                ),
            ),
            mock.patch.object(web, "catalog_store", return_value=self.catalog),
            mock.patch.object(web, "activity_store", return_value=self.activity),
            mock.patch.object(web, "get_download_result", return_value=self.history),
            mock.patch.object(web, "get_upload_result", return_value={}),
            mock.patch.object(web, "set_task_state"),
            mock.patch.object(web, "cancel_bot_tasks_for_message"),
            mock.patch.object(
                web,
                "remove_download_task",
                side_effect=lambda chat, msg, profile: self.history.get(chat, {}).pop(
                    msg, None
                ),
            ),
            mock.patch.object(web.analysis_jobs, "status", return_value={}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = web._flask_app.test_client()

    def api_preview(self, **changes):
        return self.client.post(
            "/api/library/delete/preview",
            json={"path": self.path, "scope_id": self.activity.path.stem, **changes},
        )

    def test_route_completion_cleans_records_and_stale_activity_cannot_resurrect_resource(
        self,
    ):
        plan = self.api_preview().get_json()
        response = self.client.post(
            "/api/library/delete",
            json={
                "token": plan["token"],
                "confirmed": True,
                "path": "client/path/ignored",
            },
        )
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertNotIn(self.path, self.catalog.snapshot())
        self.assertIn(self.path, CatalogStore(self.catalog.path).deleted_paths())
        self.assertNotIn(
            self.path, ActivityStore(self.activity.path).snapshot()["entries"]
        )
        self.assertEqual({}, self.history[12])
        for action in ("open", "favorite", "progress"):
            self.assertEqual(
                409,
                self.client.post(
                    "/api/library/activity",
                    json={"action": action, "path": self.path, "value": True},
                ).status_code,
            )
        self.assertEqual(
            409,
            self.client.post(
                "/api/library/analyze", json={"path": self.path}
            ).status_code,
        )
        for view in ("all", "favorites", "history"):
            self.assertEqual(
                0, self.client.get("/api/library?view=" + view).get_json()["total"]
            )

    def test_route_auth_origin_json_scope_and_explicit_confirmation(self):
        self.assertEqual(
            400,
            self.client.post(
                "/api/library/delete", data={"confirmed": True}
            ).status_code,
        )
        self.assertEqual(
            403,
            self.client.post(
                "/api/library/delete",
                json={},
                headers={"Origin": "https://evil.invalid"},
            ).status_code,
        )
        self.assertEqual(
            403,
            self.client.post(
                "/api/library/delete", json={}, headers={"Sec-Fetch-Site": "cross-site"}
            ).status_code,
        )
        self.assertEqual(409, self.api_preview(scope_id="old cloud").status_code)
        self.assertEqual(400, self.api_preview(path="../private").status_code)
        plan = self.api_preview().get_json()
        self.assertEqual(
            400,
            self.client.post(
                "/api/library/delete", json={"token": plan["token"]}
            ).status_code,
        )
        web._flask_app.config["LOGIN_DISABLED"] = False
        self.assertEqual(302, self.api_preview().status_code)
        self.assertEqual([], self.remote.deleted)

    def test_route_rejects_running_download_upload_and_analysis(self):
        record = self.history[12][34]
        record["state"] = "downloading"
        self.assertEqual(400, self.api_preview().status_code)
        record["state"] = "completed"
        with mock.patch.object(
            web,
            "get_upload_result",
            return_value={12: {34: {**record, "state": "uploading"}}},
        ):
            self.assertEqual(400, self.api_preview().status_code)
        with mock.patch.object(
            web.analysis_jobs, "status", return_value={"status": "running"}
        ):
            self.assertEqual(400, self.api_preview().status_code)
        self.assertEqual([], self.remote.deleted)

    def test_local_tombstone_beats_stale_database_and_download_history(self):
        stale = self.catalog.snapshot()
        self.catalog.mark_deleted([self.path])
        backend = SimpleNamespace(conn=True, load_setting=lambda key: stale)
        restarted = CatalogStore(self.catalog.path, backend, "fixture")
        self.assertEqual({}, restarted.snapshot())
        values = merged_entries(
            self.history,
            str(self.root),
            web.CloudDrive.get_relative_upload_path,
            restarted,
        )
        self.assertEqual(
            {},
            with_activity(
                values, self.activity.snapshot(), "favorites", restarted.deleted_paths()
            ),
        )


class FakeResponse:
    def __init__(self, status=207, body=b""):
        self.status_code, self.body = status, body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, size):
        yield self.body


class WebDavDeletionTest(unittest.TestCase):
    def setUp(self):
        self.drive = CloudDriveConfig(
            upload_adapter="webdav",
            remote_dir="Library",
            webdav_url="https://fixture.invalid/dav",
        )
        self.remote = WebDavFiles(self.drive)
        self.path = "目录/clip #1.mp4"

    def metadata(self, collection=""):
        href = "/dav/Library/" + quote(self.path, safe="/")
        return f'<d:multistatus xmlns:d="DAV:"><d:response><d:href>{href}</d:href><d:propstat><d:prop><d:resourcetype>{collection}</d:resourcetype><d:getetag>"v1"</d:getetag><d:getcontentlength>123</d:getcontentlength></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>'.encode()

    def test_probe_and_conditional_delete_are_single_file_no_redirects(self):
        with mock.patch(
            "module.resource_analysis.deletion.requests.request",
            return_value=FakeResponse(body=self.metadata()),
        ) as request:
            before = self.remote.probe(self.path)
        self.assertTrue(before["exists"])
        self.assertEqual("0", request.call_args.kwargs["headers"]["Depth"])
        self.assertFalse(request.call_args.kwargs["allow_redirects"])
        with mock.patch(
            "module.resource_analysis.deletion.requests.delete",
            return_value=FakeResponse(204),
        ) as delete, mock.patch(
            "module.resource_analysis.deletion.requests.request",
            return_value=FakeResponse(404),
        ):
            self.remote.delete(self.path, before)
        self.assertEqual('"v1"', delete.call_args.kwargs["headers"]["If-Match"])
        self.assertEqual("0", delete.call_args.kwargs["headers"]["Depth"])
        self.assertFalse(delete.call_args.kwargs["allow_redirects"])
        self.assertTrue(delete.call_args.args[0].endswith(quote(self.path, safe="/")))

    def test_directories_malformed_metadata_redirects_and_auth_failures_are_rejected(
        self,
    ):
        for response in (
            FakeResponse(body=self.metadata("<d:collection/>")),
            FakeResponse(body=b"bad XML"),
            FakeResponse(302),
            FakeResponse(401),
        ):
            with self.subTest(status=response.status_code), mock.patch(
                "module.resource_analysis.deletion.requests.request",
                return_value=response,
            ), self.assertRaises(ValueError):
                self.remote.probe(self.path)

    def test_delete_rejection_or_still_present_never_reports_success(self):
        for code in (302, 403, 412, 500):
            with mock.patch(
                "module.resource_analysis.deletion.requests.delete",
                return_value=FakeResponse(code),
            ), self.assertRaises(ValueError):
                self.remote.delete(self.path, {"etag": '"v1"'})
        with mock.patch(
            "module.resource_analysis.deletion.requests.delete",
            return_value=FakeResponse(204),
        ), mock.patch(
            "module.resource_analysis.deletion.requests.request",
            return_value=FakeResponse(body=self.metadata()),
        ), self.assertRaises(
            ValueError
        ):
            self.remote.delete(self.path, {"etag": '"v1"'})
