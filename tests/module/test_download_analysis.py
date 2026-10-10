"""Download integration keeps classification separate from moving or uploading files."""

import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import media_downloader as downloader
import module.download_stat as stat


class DownloadAnalysisTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = self.root / "channel" / "2026-09-05" / "lesson.txt"
        self.original.parent.mkdir(parents=True)
        self.original.write_text("课程内容", encoding="utf-8")
        self.node = SimpleNamespace(chat_id=123, profile_id="one")
        self.message = SimpleNamespace(
            id=456, media_group_id=None, caption="", chat=None
        )
        self.app = SimpleNamespace(
            save_path=str(self.root),
            video_classifier_config={"enable": True, "api_key": "test-only"},
            get_caption_name=lambda *args: "",
        )
        self.result = {
            "schema_version": 2,
            "category": "education",
            "category_label": "课程与教程",
            "resource_kind": "document",
            "status": "complete",
            "confidence": 0.9,
            "summary": "学习主题：编程",
            "tags": ["编程"],
        }
        self.record = {
            "profile_id": "one",
            "file_name": str(self.original),
            "down_byte": 12,
            "total_size": 12,
        }
        for patcher in (
            mock.patch.object(stat, "_download_result", {123: {456: self.record}}),
            mock.patch.object(stat.db, "conn", None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def apply(self):
        return await downloader._apply_resource_category_path(
            str(self.original),
            str(self.original),
            self.message,
            SimpleNamespace(mime_type="text/plain"),
            self.app,
            self.node,
            media_type="document",
        )

    async def test_local_complete_file_remains_readable_for_upload(self):
        with mock.patch.object(
            downloader,
            "analyze_resource",
            new_callable=mock.AsyncMock,
            return_value=self.result,
        ) as analyze:
            destination = await self.apply()
        self.assertTrue(self.original.exists())
        self.assertFalse(Path(destination).exists())
        self.assertTrue(
            destination.endswith(os.path.join("channel", "2026-09-05", "lesson.txt"))
        )
        self.assertEqual("document", analyze.call_args.kwargs["media_type"])
        self.assertEqual("学习主题：编程", self.record["ai_summary"])

    async def test_retry_reuses_analysis_but_changed_options_invalidate_it(self):
        with mock.patch.object(
            downloader,
            "analyze_resource",
            new_callable=mock.AsyncMock,
            return_value=self.result,
        ) as analyze:
            await self.apply()
            await self.apply()
            self.assertEqual(1, analyze.await_count)
            self.app.video_classifier_config["min_confidence"] = 0.95
            await self.apply()
            self.assertEqual(2, analyze.await_count)

    async def test_failed_reanalysis_clears_old_summary_and_does_not_plan_move(self):
        self.record.update(ai_summary="old unrelated introduction", ai_tags=["old"])
        result = {
            **self.result,
            "status": "needs_review",
            "category": "other",
            "summary": "证据不足",
            "tags": [],
        }
        with mock.patch.object(
            downloader,
            "analyze_resource",
            new_callable=mock.AsyncMock,
            return_value=result,
        ):
            self.assertIsNone(await self.apply())
        self.assertEqual("证据不足", self.record["ai_summary"])
        self.assertEqual([], self.record["ai_tags"])

    async def test_reasoning_policy_changes_invalidate_analysis_cache(self):
        self.app.video_classifier_config["reasoning_effort"] = "low"
        with mock.patch.object(
            downloader,
            "analyze_resource",
            new_callable=mock.AsyncMock,
            return_value=self.result,
        ) as analyze:
            await self.apply()
            self.assertEqual("low", analyze.call_args.kwargs.get("reasoning_effort"))
            await self.apply()
            self.assertEqual(1, analyze.await_count)
            self.app.video_classifier_config["reasoning_effort"] = "high"
            await self.apply()
            self.assertEqual(2, analyze.await_count)
            self.assertEqual("high", analyze.call_args.kwargs.get("reasoning_effort"))

    async def test_source_is_saved_when_analysis_is_disabled(self):
        self.message.caption = "原始说明\n#示例"
        self.app.video_classifier_config["enable"] = False
        await self.apply()
        self.assertEqual(self.message.caption, self.record["source_context"]["caption"])

    async def test_small_download_without_progress_row_still_keeps_source(self):
        stat._download_result[123].clear()
        self.app.video_classifier_config["enable"] = False
        self.message.caption = "完整原文"
        await self.apply()
        stat.verify_and_save_download(123, 456, str(self.original), self.original.stat().st_size, profile_id="one")
        record = stat._download_result[123][456]
        self.assertEqual("完整原文", record["source_context"]["caption"])
        self.assertEqual("completed", record["state"])

    async def test_failed_candidate_preserves_confirmed_analysis(self):
        self.record.update(resource_analysis=copy.deepcopy(self.result), ai_summary=self.result["summary"])
        with mock.patch.object(downloader, "analyze_resource", new_callable=mock.AsyncMock,
                               return_value={"status": "failed", "reason": "fixture_failure"}):
            self.assertIsNone(await self.apply())
        self.assertEqual(self.result, self.record["resource_analysis"])
        self.assertEqual(self.result["summary"], self.record["ai_summary"])
        self.assertEqual("review", self.record["analysis_last_attempt"]["status"])

    async def test_caption_and_same_size_file_changes_invalidate_cache(self):
        with mock.patch.object(downloader, "analyze_resource", new_callable=mock.AsyncMock, return_value=self.result) as analyze:
            self.message.caption = "第一版来源说明"
            await self.apply()
            self.message.caption = "第二版来源说明"
            await self.apply()
            self.assertEqual(2, analyze.await_count)
            self.original.write_bytes(b"x" * self.original.stat().st_size)
            await self.apply()
            self.assertEqual(3, analyze.await_count)
            self.assertFalse(analyze.call_args.kwargs["share_source_text"])
            self.assertEqual("第二版来源说明", analyze.call_args.kwargs["source_context"]["caption"])

    async def test_existing_target_is_never_overwritten_even_if_same_size(self):
        with mock.patch.object(
            downloader,
            "analyze_resource",
            new_callable=mock.AsyncMock,
            return_value=self.result,
        ):
            destination = Path(await self.apply())
            destination.write_bytes(self.original.read_bytes())
            self.assertIsNone(await self.apply())
            self.assertTrue(self.original.exists())

    async def test_organize_toggle_keeps_metadata_without_moving(self):
        self.app.video_classifier_config["organize_folders"] = False
        with mock.patch.object(
            downloader,
            "analyze_resource",
            new_callable=mock.AsyncMock,
            return_value=self.result,
        ):
            self.assertIsNone(await self.apply())
        self.assertEqual(self.result, self.record["resource_analysis"])

    async def test_disabled_format_does_not_call_model(self):
        self.app.video_classifier_config["resource_types"] = ["video"]
        with mock.patch.object(
            downloader, "analyze_resource", new_callable=mock.AsyncMock
        ) as analyze:
            self.assertIsNone(await self.apply())
            analyze.assert_not_called()

    def test_completion_persists_final_classified_path(self):
        destination = str(self.root / "知识与资讯" / "课程与教程" / "lesson.txt")
        stat.verify_and_save_download(123, 456, destination, 12, profile_id="one")
        self.assertEqual(destination, self.record["file_name"])
        self.assertEqual("completed", self.record["state"])

    def test_task_description_keeps_observations_separate_from_unconfirmed_classification(
        self,
    ):
        result = {
            **self.result,
            "status": "needs_review",
            "category": "other",
            "summary": "分类待确认",
            "content_description": "可见蓝色几何图形。",
            "review_note": "用途不明确",
            "tags": [],
        }
        stat.set_resource_analysis(123, 456, result, "one")
        self.assertIn("内容观察（分类未确认）：可见蓝色几何图形。", self.record["ai_summary"])
        self.assertIn("分类待确认", self.record["ai_summary"])
        self.assertIn("模型说明：用途不明确", self.record["ai_summary"])
        self.assertEqual(result, self.record["resource_analysis"])
