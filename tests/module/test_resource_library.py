"""Library behavior across persistence, WebDAV routes and the reanalysis queue."""

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import module.web as web
from module.cloud_drive import CloudDrive, CloudDriveConfig
from module.resource_analysis.jobs import AnalysisJobs
from module.resource_analysis.library import (
    CatalogStore,
    clean_path,
    history_entries,
    legacy_analysis,
    merged_entries,
    query_library,
)


def entry(path, category="education", kind="video", subtype="", status="complete"):
    analysis = legacy_analysis(path)
    analysis.update(
        schema_version=2,
        category=category,
        resource_kind=kind,
        subtype=subtype,
        category_label="课程与教程",
        status=status,
        summary="知识点：Python 循环",
        tags=["Python"],
        analyzed_at="2026-09-05T01:00:00+00:00",
    )
    return {
        "path": path,
        "name": path.rsplit("/", 1)[-1],
        "size": 120,
        "modified": "2026-09-05T01:00:00+00:00",
        "content_type": "video/mp4",
        "analysis": analysis,
    }


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CatalogStore(Path(self.temp.name) / "index.json")

    def test_entries_survive_restart_and_same_name_does_not_collide(self):
        self.store.put(entry("one/video.mp4"))
        self.store.put(entry("two/video.mp4", "music"))
        restarted = CatalogStore(self.store.path).snapshot()
        self.assertEqual(2, len(restarted))
        self.assertEqual(
            "education", restarted["one/video.mp4"]["analysis"]["category"]
        )
        self.assertEqual("music", restarted["two/video.mp4"]["analysis"]["category"])
        self.assertFalse(self.store.path.with_suffix(".tmp").exists())

    def test_filters_combine_type_category_subtype_and_search(self):
        values = [
            entry("one.mp4"),
            entry("two.mp3", "music", "audio"),
            entry("three.mp4", "adult", subtype="animation_3d"),
            entry("four.mp4", "adult", subtype="studio"),
        ]
        entries = {item["path"]: item for item in values}
        result = query_library(
            entries,
            category="adult",
            subtype="animation_3d",
            kind="video",
            query="python",
        )
        self.assertEqual(["three.mp4"], [item["path"] for item in result["files"]])
        self.assertEqual(2, result["counts"]["categories"]["adult"])
        self.assertEqual(
            [], query_library(entries, category="adult", kind="audio")["files"]
        )

    def test_invalid_pages_are_clamped_after_filtering(self):
        entries = {str(i): entry(f"{i}.mp4") for i in range(5)}
        result = query_library(entries, page=500, page_size=2)
        self.assertEqual(3, result["page"])
        self.assertEqual(1, len(result["files"]))

    def test_summary_review_remains_in_category_but_is_findable_for_retry(self):
        item = entry("one.mp4", "adult", subtype="independent")
        item["analysis"]["summary_quality"] = {"status": "review"}
        values = {item["path"]: item}
        result = query_library(values, category="adult", status="review")
        self.assertEqual(1, result["total"])
        self.assertEqual("complete", result["files"][0]["analysis"]["status"])
        self.assertEqual(0, query_library(values, status="complete")["total"])

    def test_pending_content_observations_are_searchable_without_confirming_category(
        self,
    ):
        item = entry("picture.jpg", "other", "image", status="needs_review")
        item["analysis"].update(summary="分类待确认", content_description="可见蓝色几何图形。")
        self.store.put(item)
        result = query_library(CatalogStore(self.store.path).snapshot(), query="几何图形")
        self.assertEqual(1, result["total"])
        self.assertEqual("needs_review", result["files"][0]["analysis"]["status"])
        self.assertEqual("other", result["files"][0]["analysis"]["category"])

    def test_old_summary_is_not_presented_as_new_analysis(self):
        result = legacy_analysis(
            "channel/nsfw/3D动画/video.mp4", {"ai_summary": "旧版不一致简介", "ai_tags": ["旧标签"]}
        )
        self.assertEqual("adult", result["category"])
        self.assertEqual("legacy", result["status"])
        self.assertNotIn("不一致", result["summary"])
        self.assertEqual([], result["tags"])

    def test_history_does_not_include_partial_or_failed_uploads(self):
        history = {
            1: {
                1: {"file_name": "one.mp4", "state": "completed", "total_size": 100},
                2: {
                    "file_name": "two.mp4",
                    "state": "upload_failed",
                    "total_size": 100,
                    "down_byte": 100,
                },
                3: {"file_name": "three.mp4", "total_size": 100, "down_byte": 50},
            }
        }
        self.assertEqual(
            ["one.mp4"],
            list(history_entries(history, "", CloudDrive.get_relative_upload_path)),
        )

    def test_reanalysis_result_overrides_history_only_when_newer(self):
        old = entry("one.mp4")
        old["analysis"]["analyzed_at"] = "2026-09-01T00:00:00+00:00"
        history = {
            1: {
                1: {
                    "file_name": "one.mp4",
                    "state": "completed",
                    "resource_analysis": old["analysis"],
                }
            }
        }
        self.store.put(entry("one.mp4", "music"))
        result = merged_entries(
            history, "", CloudDrive.get_relative_upload_path, self.store
        )
        self.assertEqual("music", result["one.mp4"]["analysis"]["category"])


class LibraryRouteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CatalogStore(Path(self.temp.name) / "index.json")
        self.store.put(entry("source/lesson.mp4"))
        self.drive = CloudDriveConfig(
            remote_dir="/Library",
            webdav_url="https://dav.example.test/dav",
            webdav_username="u",
            webdav_password="secret",
        )
        app = SimpleNamespace(
            save_path="downloads",
            cloud_drive_config=self.drive,
            video_classifier_config={"enable": True, "api_key": "test-only"},
        )
        self.previous_login_disabled = web._flask_app.config.get("LOGIN_DISABLED")
        web._flask_app.config["LOGIN_DISABLED"] = True
        self.addCleanup(
            lambda: web._flask_app.config.update(
                LOGIN_DISABLED=self.previous_login_disabled
            )
        )
        for patcher in (
            mock.patch.object(web, "_app_instance", app),
            mock.patch.object(web, "catalog_store", return_value=self.store),
            mock.patch.object(web, "get_download_result", return_value={}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = web._flask_app.test_client()

    def test_taxonomy_and_filtered_library_contract(self):
        taxonomy = self.client.get("/api/library/taxonomy").get_json()
        self.assertGreaterEqual(len(taxonomy["adult_subtypes"]), 10)
        result = self.client.get(
            "/api/library?category=education&kind=video"
        ).get_json()
        self.assertEqual(1, result["total"])
        self.assertEqual("Library", result["root"])

    def test_details_match_full_relative_path_not_basename(self):
        result = self.client.get("/api/webdav/summary?path=other/lesson.mp4").get_json()
        self.assertEqual("unanalysed", result["analysis"]["status"])
        result = self.client.get(
            "/api/webdav/summary?path=source/lesson.mp4"
        ).get_json()
        self.assertEqual("complete", result["analysis"]["status"])

    def test_saved_activity_label_is_review_in_list_and_details_without_rewriting_storage(self):
        item = entry("source/generic.mp4", "adult")
        item["analysis"].update(
            summary="参与者进行成人亲密互动。",
            summary_quality={"version": 5, "status": "ready", "issues": [],
                             "audit": {"version": 2, "status": "passed"}},
            narrative={"version": 2, "mode": "activity", "events": [
                {"id": "event_1", "action": "参与者进行成人亲密互动", "evidence_refs": ["frame_1"]}]},
        )
        self.store.put(item)
        before = self.store.snapshot()
        listed = self.client.get("/api/library?category=adult&status=review").get_json()
        self.assertEqual(1, listed["total"])
        detail = self.client.get("/api/webdav/summary?path=source/generic.mp4").get_json()
        for analysis in (listed["files"][0]["analysis"], detail["analysis"]):
            self.assertEqual("complete", analysis["status"], "classification is retained")
            self.assertEqual("review", analysis["summary_quality"]["status"])
            self.assertIn("category_only_summary", analysis["summary_quality"]["issues"])
        complete = self.client.get("/api/library?category=adult&status=complete").get_json()
        self.assertEqual(0, complete["total"])
        self.assertEqual(before, self.store.snapshot(), "read-time quality projection must not rewrite the catalog")

    def test_analyze_rejects_invalid_and_traversal_paths_before_job(self):
        with mock.patch.object(web.analysis_jobs, "submit") as submit:
            for path in (
                "",
                "../secret.txt",
                "%2e%2e/secret.txt",
                "folder/../file.txt",
                "http://evil.test/file",
            ):
                with self.subTest(path=path):
                    response = self.client.post(
                        "/api/library/analyze", json={"path": path}
                    )
                    self.assertEqual(400, response.status_code)
            submit.assert_not_called()

    def test_analyze_derives_url_and_credentials_on_server(self):
        with mock.patch.object(
            web.analysis_jobs, "submit", return_value={"status": "queued"}
        ) as submit:
            response = self.client.post(
                "/api/library/analyze",
                json={"path": "source/lesson.mp4", "url": "https://evil.test"},
            )
            self.assertEqual(202, response.status_code)
            self.assertEqual(
                "https://dav.example.test/dav/Library/source/lesson.mp4",
                submit.call_args.args[2],
            )
            self.assertEqual(("u", "secret"), submit.call_args.args[3])

    def test_reanalysis_updates_matching_task_but_never_a_replacement_task(self):
        record = {
            "file_name": "source/lesson.mp4",
            "state": "completed",
            "profile_id": "test",
        }
        history = {123: {307: record}}
        with mock.patch.object(
            web, "get_download_result", return_value=history
        ), mock.patch.object(
            web, "_resource_task_records", return_value=iter([(123, 307, record)])
        ), mock.patch.object(
            web.analysis_jobs, "submit", return_value={"status": "queued"}
        ) as submit, mock.patch.object(
            web, "set_resource_analysis", create=True
        ) as write_result, mock.patch.object(
            web, "set_ai_status", create=True
        ) as write_status:
            response = self.client.post(
                "/api/library/analyze", json={"path": "source/lesson.mp4"}
            )
            self.assertEqual(202, response.status_code)
            on_result = submit.call_args.kwargs["on_result"]
            on_progress = submit.call_args.kwargs["on_progress"]
            on_progress("分类识别：正在重试 1/1")
            write_status.assert_called_once()
            analysis = entry("source/lesson.mp4")["analysis"]
            on_result(analysis)
            write_result.assert_called_once_with(123, 307, analysis, "test")
            history[123][307] = dict(record)
            on_result(analysis)
            self.assertEqual(1, write_result.call_count)

    def test_browse_ignores_outside_paths_and_handles_repeated_folder_names(self):
        xml = b"""<d:multistatus xmlns:d="DAV:">
        <d:response><d:href>/dav/Library/source/</d:href></d:response>
        <d:response><d:href>/dav/Library/source/lesson.mp4</d:href><d:propstat><d:prop><d:getcontentlength>120</d:getcontentlength><d:getcontenttype>video/mp4</d:getcontenttype></d:prop></d:propstat></d:response>
        <d:response><d:href>/dav/elsewhere/wrong.mp4</d:href></d:response>
        <d:response><d:href>/dav/Library/source/Library/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
        </d:multistatus>"""
        with mock.patch.object(
            web.requests,
            "request",
            return_value=SimpleNamespace(status_code=207, content=xml),
        ):
            result = self.client.get("/api/webdav/browse?path=source").get_json()
        self.assertEqual(["lesson.mp4"], [item["name"] for item in result["files"]])
        self.assertEqual("source/Library", result["folders"][0]["path"])
        self.assertEqual("education", result["files"][0]["analysis"]["category"])


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.jobs = AnalysisJobs()
        self.addCleanup(self.jobs.executor.shutdown, wait=True)

    def test_duplicate_jobs_and_capacity_are_bounded(self):
        config = {"enable": True, "api_key": "test-only"}
        with mock.patch.object(self.jobs.executor, "submit") as submit:
            for key in ("one", "one", "two"):
                self.jobs.submit(
                    key,
                    entry(key + ".mp4"),
                    "https://example.test",
                    None,
                    config,
                    mock.Mock(),
                )
            self.assertEqual(2, submit.call_count)
            with self.assertRaises(ValueError):
                self.jobs.submit(
                    "three",
                    entry("three.mp4"),
                    "https://example.test",
                    None,
                    config,
                    mock.Mock(),
                )

    def test_disabled_resource_type_cannot_start_remote_download(self):
        with mock.patch.object(self.jobs.executor, "submit") as submit:
            with self.assertRaises(ValueError):
                self.jobs.submit(
                    "one",
                    entry("one.mp4"),
                    "https://example.test",
                    None,
                    {
                        "enable": True,
                        "api_key": "test-only",
                        "resource_types": ["document"],
                    },
                    mock.Mock(),
                )
            submit.assert_not_called()

    def test_summary_only_requires_confirmed_video_before_any_download(self):
        config = {"enable": True, "api_key": "fixture"}
        for resource in (entry("image.jpg", kind="image"), entry("video.mp4", status="needs_review")):
            with mock.patch.object(self.jobs.executor, "submit") as submit:
                with self.assertRaises(ValueError):
                    self.jobs.submit("one", resource, "https://fixture.invalid", None, config, mock.Mock(), mode="summary")
                submit.assert_not_called()

    def test_summary_mode_is_preserved_at_worker_boundary(self):
        config = {"enable": True, "api_key": "fixture"}
        with mock.patch.object(self.jobs.executor, "submit") as submit:
            self.jobs.submit("one", entry("video.mp4"), "https://fixture.invalid", None, config, mock.Mock(), mode="summary")
        self.assertEqual("summary", submit.call_args.kwargs["mode"])

    def test_worker_uses_full_file_name_for_extension_and_persists_result(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.headers = {"Content-Length": "4", "Content-Type": "video/mp4"}
        response.iter_content.return_value = [b"data"]
        self.jobs.jobs["one"] = {"status": "queued"}
        result = entry("one.mp4")["analysis"]
        result["summary_quality"] = {"status": "ready"}
        store = mock.Mock()
        with mock.patch(
            "module.resource_analysis.jobs.requests.get", return_value=response
        ), mock.patch(
            "module.resource_analysis.jobs.analyze_resource",
            new_callable=mock.AsyncMock,
            return_value=result,
        ) as analyze:
            self.jobs._run(
                "one",
                entry("one.mp4"),
                "https://example.test/one.mp4",
                None,
                {"api_key": "test-only", "reasoning_effort": "medium"},
                store,
            )
        self.assertEqual("done", self.jobs.status("one")["status"])
        self.assertEqual("one.mp4", analyze.call_args.kwargs["resource_name"])
        self.assertEqual("medium", analyze.call_args.kwargs["reasoning_effort"])
        store.put.assert_called_once()
        self.assertFalse(Path(analyze.call_args.args[0]).exists())

    def test_oversized_stream_stops_before_model_and_preserves_previous_result(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.headers = {}
        response.iter_content.return_value = [b"x" * (2 * 1024 * 1024)]
        self.jobs.jobs["one"] = {"status": "queued"}
        store = mock.Mock()
        with mock.patch(
            "module.resource_analysis.jobs.requests.get", return_value=response
        ):
            self.jobs._run(
                "one",
                entry("one.mp4"),
                "https://example.test",
                None,
                {"max_file_size_mb": 1},
                store,
            )
        self.assertEqual("failed", self.jobs.status("one")["status"])
        store.put.assert_not_called()

    def test_large_valid_remote_file_gets_size_aware_bounded_read_deadline(self):
        for elapsed, expected in ((350, "done"), (600, "done"), (1900, "failed")):
            with self.subTest(elapsed=elapsed):
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.status_code = 200
                response.headers = {"Content-Length": str(200 * 1024 * 1024), "Content-Type": "video/mp4"}
                response.iter_content.return_value = [b"x" * (256 * 1024)] * 800
                self.jobs.jobs["one"] = {"status": "queued"}
                result = entry("one.mp4")["analysis"]
                result["summary_quality"] = {"status": "ready"}
                store = mock.Mock()
                with mock.patch("module.resource_analysis.jobs.requests.get", return_value=response), mock.patch(
                    "module.resource_analysis.download.time"
                ) as clock, mock.patch(
                    "module.resource_analysis.jobs.analyze_resource", new_callable=mock.AsyncMock, return_value=result
                ) as analyze:
                    clock.monotonic.side_effect = [0] + [elapsed] * 800
                    self.jobs._run("one", entry("one.mp4"), "https://fixture.invalid", None, {}, store)
                self.assertEqual(expected, self.jobs.status("one")["status"])
                if expected == "failed":
                    analyze.assert_not_called()
                    store.put.assert_not_called()

    def test_unqualified_video_retry_keeps_existing_summary_and_reports_attempt_failure(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.headers = {"Content-Type": "video/mp4"}
        response.iter_content.return_value = [b"data"]
        for status in ("complete", "needs_review", "failed"):
            with self.subTest(status=status):
                self.jobs.jobs["one"] = {"status": "queued"}
                original = entry("one.mp4")
                original["analysis"]["status"] = "complete"
                previous = copy.deepcopy(original)
                candidate = {**original["analysis"], "status": status,
                             "summary_quality": {"status": "review", "issues": ["duplicate_sentences"]}}
                store, publish, progress = mock.Mock(), mock.Mock(), mock.Mock()
                with mock.patch("module.resource_analysis.jobs.requests.get", return_value=response), mock.patch(
                    "module.resource_analysis.jobs.analyze_resource", new_callable=mock.AsyncMock, return_value=candidate
                ):
                    self.jobs._run("one", original, "https://fixture.invalid", None, {}, store,
                                   on_result=publish, on_progress=progress)
                store.put.assert_not_called()
                publish.assert_not_called()
                self.assertEqual(previous["analysis"], original["analysis"])
                self.assertEqual("failed", self.jobs.status("one")["status"])
                self.assertIn("保留原", self.jobs.status("one")["message"])
                self.assertIn("保留原", progress.call_args.args[0])

    def test_rejected_synopsis_exposes_current_redacted_diagnostics_not_old_sampling(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.headers = {"Content-Type": "video/mp4"}
        response.iter_content.return_value = [b"data"]
        self.jobs.jobs["one"] = {"status": "queued"}
        original = entry("one.mp4")
        original["analysis"]["sampling"] = {"timestamps": [5]}
        candidate = {**original["analysis"], "summary_quality": {"status": "review", "issues": ["model_server"]},
                     "sampling": {"timestamps": [0, 30, 60]},
                     "diagnostics": {"stage": "narrative", "narrative_shape": {"event_count": 0},
                                     "narrative_input": {"frame_count": 3, "transcript_characters": 0},
                                     "segments": {"windows": 4, "candidate_events": 0, "results": [
                                         {"window": 1, "status": "rejected", "issues": ["narrative_event_shape"],
                                          "shape": {"event_count": 1}}]},
                                     "ending_observation": {"status": "accepted", "presence": [
                                         {"frame_ref": "frame_3", "timestamp": 60, "presence": "not_visible"}]},
                                     "ending_event_projection": {"event_ids": ["event_2"], "reason": "absence_only", "source": "ending_observation"},
                                     "summary_revision": {"strategy": "activity_projection", "status": "projected", "repairs": 0},
                                     "attempts": [{"stage": "narrative", "attempt": 2, "status": "failed",
                                                   "reason": "model_server", "http_status": 503,
                                                   "raw_response": "PRIVATE_REPLY_MUST_NOT_ESCAPE"}]}}
        with mock.patch("module.resource_analysis.jobs.requests.get", return_value=response), mock.patch(
            "module.resource_analysis.jobs.analyze_resource", new_callable=mock.AsyncMock, return_value=candidate
        ):
            self.jobs._run("one", original, "https://fixture.invalid", None, {}, mock.Mock())
        attempt = self.jobs.status("one")["last_attempt"]
        self.assertEqual("narrative", attempt["stage"])
        self.assertEqual("rejected", attempt["segments"]["results"][0]["status"])
        self.assertEqual("model_server", attempt["reason"])
        self.assertEqual([0, 30, 60], attempt["sampling"]["timestamps"])
        self.assertEqual(503, attempt["attempts"][0]["http_status"])
        self.assertEqual("not_visible", attempt["ending_observation"]["presence"][0]["presence"])
        self.assertEqual(["event_2"], attempt["ending_event_projection"]["event_ids"])
        self.assertEqual("activity_projection", attempt["summary_revision"]["strategy"])
        self.assertNotIn("PRIVATE_REPLY", str(attempt))
        self.assertEqual([5], original["analysis"]["sampling"]["timestamps"])

    def test_worker_publishes_detailed_failure_to_task_and_job(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.headers = {"Content-Length": "4", "Content-Type": "image/jpeg"}
        response.iter_content.return_value = [b"data"]
        self.jobs.jobs["one"] = {"status": "queued"}
        result = {
            **entry("one.jpg")["analysis"],
            "status": "failed",
            "reason": "model_timeout",
            "summary": "分类识别未完成：模型请求超过 120 秒（已重试 1 次）。",
        }
        store, publish, progress = mock.Mock(), mock.Mock(), mock.Mock()

        async def analyze(*args, **kwargs):
            kwargs["progress_callback"]("分类识别：正在重试 1/1")
            return result

        with mock.patch(
            "module.resource_analysis.jobs.requests.get", return_value=response
        ), mock.patch(
            "module.resource_analysis.jobs.analyze_resource", side_effect=analyze
        ):
            self.jobs._run(
                "one",
                entry("one.jpg", kind="image", status="unanalysed"),
                "https://fixture.invalid",
                None,
                {},
                store,
                on_result=publish,
                on_progress=progress,
            )
        publish.assert_called_once_with(result)
        self.assertIn(mock.call("分类识别：正在重试 1/1"), progress.call_args_list)
        self.assertEqual("failed", self.jobs.status("one")["status"])
        self.assertEqual(result["summary"], self.jobs.status("one")["message"])
        store.put.assert_called_once()

    def test_confirmed_image_is_preserved_when_new_candidate_fails(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.headers = {"Content-Type": "image/jpeg"}
        response.iter_content.return_value = [b"fixture"]
        self.jobs.jobs["one"] = {"status": "queued"}
        store, publish = mock.Mock(), mock.Mock()
        original = entry("one.jpg", kind="image")
        with mock.patch("module.resource_analysis.jobs.requests.get", return_value=response), mock.patch(
                "module.resource_analysis.jobs.analyze_resource", new_callable=mock.AsyncMock,
                return_value={"status": "failed", "reason": "model_timeout"}):
            self.jobs._run("one", original, "https://fixture.invalid", None, {}, store, on_result=publish)
        store.put.assert_not_called()
        publish.assert_not_called()
        self.assertEqual("complete", original["analysis"]["status"])
        self.assertEqual("failed", self.jobs.status("one")["status"])


if __name__ == "__main__":
    unittest.main()
