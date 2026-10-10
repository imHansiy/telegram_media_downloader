"""Personal library persistence and HTTP contract; never uses real cloud files."""
import copy
import math
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import module.web as web
from module.cloud_drive import CloudDriveConfig
from module.resource_analysis.activity import ActivityStore, activity_store, with_activity
from module.resource_analysis.library import CatalogStore, query_library
from tests.module.test_resource_library import entry


class ActivityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ActivityStore(Path(self.temp.name) / "activity.json")
        self.adult = entry("one/same.mp4", "adult", subtype="studio")
        self.movie = entry("two/same.mp4", "movie")

    def test_favorites_and_both_histories_survive_restart(self):
        for item in (self.adult, self.movie):
            self.store.update("favorite", path=item["path"], entry=item, value=True)
        self.store.update("open", path=self.adult["path"], entry=self.adult)
        self.store.update("progress", path=self.adult["path"], entry=self.adult, position=31, duration=40)
        state = ActivityStore(self.store.path).snapshot()
        self.assertEqual(2, len(state["entries"]))
        self.assertEqual(31, state["entries"][self.adult["path"]]["position"])
        self.assertIn("opened_at", state["entries"][self.adult["path"]])
        self.assertNotIn("watched_at", state["entries"][self.movie["path"]])
        self.assertFalse(self.store.path.with_suffix(".tmp").exists())

    def test_pause_blocks_both_histories_but_not_favorites(self):
        self.store.update("pause", value=True)
        self.store.update("open", path=self.adult["path"], entry=self.adult)
        self.store.update("progress", path=self.adult["path"], position=10, duration=20)
        self.store.update("favorite", path=self.adult["path"], entry=self.adult, value=True)
        record = self.store.snapshot()["entries"][self.adult["path"]]
        self.assertTrue(record["favorite"])
        self.assertNotIn("opened_at", record)
        self.assertNotIn("position", record)
        self.assertTrue(ActivityStore(self.store.path).snapshot()["paused"])

    def test_clear_scopes_and_history_kinds_do_not_remove_favorites_or_media(self):
        media = Path(self.temp.name) / "media.mp4"
        media.write_bytes(b"test-media")
        for item in (self.adult, self.movie):
            for action in ("favorite", "open", "progress"):
                self.store.update(action, path=item["path"], entry=item, value=True, position=19, duration=20)
        self.store.update("clear", scope="adult", history_kind="watched")
        records = self.store.snapshot()["entries"]
        self.assertNotIn("watched_at", records[self.adult["path"]])
        self.assertIn("opened_at", records[self.adult["path"]])
        self.assertIn("watched_at", records[self.movie["path"]])
        self.assertTrue(records[self.adult["path"]]["favorite"])
        self.store.update("remove", path=self.adult["path"], scope="adult", history_kind="opened")
        self.store.update("favorite", path=self.adult["path"], value=False)
        self.assertNotIn(self.adult["path"], self.store.snapshot()["entries"])
        self.assertEqual(b"test-media", media.read_bytes())

    def test_failed_disk_write_does_not_publish_false_success(self):
        self.store.update("favorite", path=self.adult["path"], entry=self.adult, value=True)
        before = self.store.snapshot()
        with mock.patch("module.resource_analysis.activity.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.update("favorite", path=self.adult["path"], value=False)
        self.assertEqual(before, self.store.snapshot())
        self.assertEqual(before, ActivityStore(self.store.path).snapshot())

    def test_parallel_writes_preserve_every_favorite(self):
        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(lambda i: self.store.update("favorite", path=f"video/{i}.mp4", value=True), range(30)))
        self.assertEqual(30, len(ActivityStore(self.store.path).snapshot()["entries"]))

    def test_invalid_progress_and_states_are_rejected(self):
        for position, duration in ((math.nan, 50), (4, math.inf), (-1, 20), (10, 0), (True, 4), (3, 800000), ("2", 4)):
            with self.subTest(position=position, duration=duration), self.assertRaises(ValueError):
                self.store.update("progress", path="one.mp4", position=position, duration=duration)
        with self.assertRaises(ValueError):
            self.store.update("pause", value="false")
        self.assertFalse(self.store.path.exists())

    def test_viewing_completion_and_progress_are_clamped(self):
        self.store.update("progress", path=self.adult["path"], entry=self.adult, position=150, duration=100)
        record = self.store.snapshot()["entries"][self.adult["path"]]
        self.assertEqual(100, record["position"])
        self.assertTrue(record["completed"])

    def test_personal_snapshots_preserve_directory_favorites_without_polluting_all_view(self):
        self.store.update("favorite", path=self.adult["path"], entry=self.adult, value=True)
        self.assertEqual({}, with_activity({}, self.store.snapshot()))
        self.assertIn(self.adult["path"], with_activity({}, self.store.snapshot(), "favorites"))

    def test_tags_intersect_before_pagination_and_facets_are_scope_specific(self):
        entries = {}
        for index in range(8):
            item = entry(f"{index}.mp4", "adult")
            item["analysis"]["tags"] = ["室内", "固定机位"] if index % 2 else ["室内", "多机位"]
            entries[item["path"]] = item
        self.movie["analysis"]["tags"] = ["悬疑"]
        entries[self.movie["path"]] = self.movie
        result = query_library(entries, scope="adult", tags=["室内", "固定机位"], page_size=2, page=2)
        self.assertEqual(4, result["total"])
        self.assertEqual(2, len(result["files"]))
        self.assertNotIn("悬疑", [tag["name"] for tag in result["tag_facets"]])
        self.assertEqual(["固定机位"], [tag["name"] for tag in query_library(entries, tag_query="固定")["tag_facets"]])

    def test_drive_and_login_scopes_are_distinct(self):
        drive = SimpleNamespace(webdav_url="https://fixture.invalid", remote_dir="/A", webdav_username="one")
        first = activity_store(drive, "root")
        self.assertNotEqual(first.path, activity_store(drive, "another-login").path)
        drive.remote_dir = "/B"
        self.assertNotEqual(first.path, activity_store(drive, "root").path)


class ActivityRoutesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ActivityStore(Path(self.temp.name) / "activity.json")
        self.catalog = CatalogStore(Path(self.temp.name) / "catalog.json")
        self.resource = entry("source/item.mp4", "adult")
        self.catalog.put(self.resource)
        self.drive = CloudDriveConfig(remote_dir="/Library", webdav_url="https://dav.example.test/dav", webdav_username="u", webdav_password="fixture-only")
        self.old_disabled = web._flask_app.config.get("LOGIN_DISABLED")
        web._flask_app.config["LOGIN_DISABLED"] = True
        self.addCleanup(lambda: web._flask_app.config.update(LOGIN_DISABLED=self.old_disabled))
        for patcher in (mock.patch.object(web, "_app_instance", SimpleNamespace(cloud_drive_config=self.drive, save_path="downloads")),
                        mock.patch.object(web, "catalog_store", return_value=self.catalog),
                        mock.patch.object(web, "activity_store", return_value=self.store),
                        mock.patch.object(web, "get_download_result", return_value={})):
            patcher.start(); self.addCleanup(patcher.stop)
        self.client = web._flask_app.test_client()

    def post(self, action, **extra):
        return self.client.post("/api/library/activity", json={"action": action, "path": self.resource["path"], **extra})

    def test_favorite_history_query_and_no_store_contract(self):
        self.assertEqual(200, self.post("favorite", value=True).status_code)
        self.assertEqual(200, self.post("open").status_code)
        self.assertEqual(200, self.post("progress", position=12, duration=48).status_code)
        for view in ("all", "favorites", "history"):
            result = self.client.get(f"/api/library?scope=adult&view={view}&history_kind=watched")
            self.assertIn("no-store", result.headers["Cache-Control"])
            data = result.get_json()
            self.assertEqual(1, data["total"])
            self.assertTrue(data["files"][0]["activity"]["favorite"])
            self.assertNotIn("entry", data["files"][0]["activity"])
        self.assertEqual(0, self.client.get("/api/library?scope=movie&view=favorites").get_json()["total"])
        self.assertEqual(200, self.post("clear", scope="adult", history_kind="watched").status_code)
        self.assertEqual(0, self.client.get("/api/library?scope=adult&view=history&history_kind=watched").get_json()["total"])
        self.assertEqual(1, self.client.get("/api/library?scope=adult&view=favorites").get_json()["total"])

    def test_auth_json_origin_and_traversal_guards(self):
        with mock.patch.object(web.requests, "head") as head:
            for path in ("../private", "%2e%2e/private", "folder/../private", "https://evil.test/file"):
                self.assertEqual(400, self.post("favorite", path=path, value=True).status_code)
            head.assert_not_called()
        self.assertEqual(400, self.client.post("/api/library/activity", data={"action": "clear"}).status_code)
        self.assertEqual(403, self.client.post("/api/library/activity", json={"action": "clear"}, headers={"Origin": "https://evil.test"}).status_code)
        web._flask_app.config["LOGIN_DISABLED"] = False
        self.assertEqual(302, self.post("favorite", value=True).status_code)

    def test_durable_failure_is_reported_without_toggling_favorite(self):
        with mock.patch("module.resource_analysis.activity.os.replace", side_effect=OSError("disk full")):
            response = self.post("favorite", value=True)
        self.assertEqual(503, response.status_code)
        self.assertFalse(response.get_json()["success"])
        self.assertEqual({}, self.store.snapshot()["entries"])

    def test_reading_library_or_summary_never_creates_history(self):
        self.client.get("/api/library")
        self.client.get("/api/webdav/summary?path=source/item.mp4")
        self.assertFalse(self.store.path.exists())


if __name__ == "__main__":
    unittest.main()
