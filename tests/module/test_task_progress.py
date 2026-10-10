"""Same real projections as Web/SSE and Telegram, with synthetic records only."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import module.bot as bot
import module.pyrogram_extension as telegram
import module.web as web
from module.app import DownloadStatus, TaskNode


class TaskProgressTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.record = {
            "file_name": "current.mp4",
            "down_byte": 100,
            "total_size": 100,
            "download_speed": 2097152,
            "state": "downloading",
            "ai_status": "分析其他成人内容",
            "profile_id": "fixture",
            "task_id": 1,
            "created_at": 1001,
        }
        self.node = TaskNode(
            chat_id=-1001,
            task_id=1,
            profile_id="fixture",
            bot=True,
            from_user_id=1,
            reply_message_id=1,
        )
        self.node.created_at = 1000
        self.node.total_task = 1
        self.node.download_status[312] = DownloadStatus.Downloading

    async def project(self, *, record=None, upload=None, extra=None, control="running"):
        record = self.record if record is None else record
        history = {-1001: {312: record, **(extra or {})}}
        uploads = {-1001: {312: upload}} if upload is not None else {}
        client = mock.Mock(edit_message_text=mock.AsyncMock())
        with mock.patch.object(
            web, "get_download_result", return_value=history
        ), mock.patch.object(
            web, "get_upload_result", return_value=uploads
        ), mock.patch.object(
            web, "get_task_state", return_value=control
        ), mock.patch.object(
            telegram, "get_download_result", return_value=history
        ), mock.patch(
            "module.upload_stat.get_upload_result", return_value=uploads
        ), mock.patch(
            "module.download_stat.get_task_state", return_value=control
        ), mock.patch.object(
            telegram, "get_total_download_speed", return_value=999999, create=True
        ):
            active = web._get_formatted_list(False)
            finished = web._get_formatted_list(True)
            await telegram._report_bot_status(client, self.node, immediate_reply=True)
        return active, finished, client.edit_message_text.await_args.args[2]

    async def test_analysis_has_real_download_progress_no_phantom_upload_or_speed(self):
        active, finished, text = await self.project()
        self.assertEqual([], finished)
        self.assertEqual("100.0", active[0]["download_progress"])
        self.assertEqual("0.0", active[0]["upload_progress"])
        self.assertEqual("analyzing", active[0]["phase"])
        self.assertEqual("0B/s", active[0]["download_speed"])
        self.assertIn("AI分析中", text)
        self.assertIn("📥 下载  [██████████] 100%", text)
        self.assertIn("📤 上传  [░░░░░░░░░░] 0% · 待上传", text)
        self.assertNotIn("总体进度", text)
        self.assertNotIn("✅ current.mp4", text)
        self.assertIn("⏳ current.mp4", text)

    async def test_partial_download_does_not_simulate_upload(self):
        active, _, text = await self.project(
            record={**self.record, "down_byte": 50, "ai_status": ""}
        )
        self.assertEqual("50.0", active[0]["download_progress"])
        self.assertEqual("0.0", active[0]["upload_progress"])
        self.assertIn("📥 下载  [█████░░░░░] 50%", text)

    async def test_finished_analysis_waits_for_upload_without_becoming_complete(self):
        active, finished, text = await self.project(
            record={**self.record, "ai_status": "分析完成:成人内容"}
        )
        self.assertEqual([], finished)
        self.assertEqual("waiting_upload", active[0]["phase"])
        self.assertEqual("0.0", active[0]["upload_progress"])
        self.assertIn("等待上传", text)

    async def test_upload_from_another_profile_cannot_change_status_or_error(self):
        upload = {
            "processed_bytes": 100,
            "total_bytes": 100,
            "profile_id": "other-profile",
            "state": "failed",
            "error": "OTHER_PROFILE_PRIVATE_ERROR",
        }
        active, finished, text = await self.project(upload=upload)
        self.assertEqual([], finished)
        self.assertEqual("analyzing", active[0]["phase"])
        self.assertEqual("", active[0]["error"])
        self.assertNotIn("OTHER_PROFILE_PRIVATE_ERROR", text)

    async def test_new_partial_upload_overrides_local_completion_marker(self):
        upload = {"processed_bytes": 40, "total_bytes": 100, "profile_id": "fixture"}
        active, finished, _ = await self.project(
            record={**self.record, "state": "completed"}, upload=upload
        )
        self.assertEqual([], finished)
        self.assertEqual("uploading", active[0]["phase"])
        self.assertEqual("40.0", active[0]["upload_progress"])

    async def test_upload_overrides_stale_ai_label_and_speeds_match(self):
        upload = {
            "processed_bytes": 40,
            "total_bytes": 100,
            "upload_speed": 1024,
            "profile_id": "fixture",
        }
        active, finished, text = await self.project(upload=upload)
        self.assertEqual([], finished)
        self.assertEqual("uploading", active[0]["phase"])
        self.assertEqual("上传中", active[0]["status"])
        self.assertEqual("40.0", active[0]["upload_progress"])
        self.assertEqual("0B/s", active[0]["download_speed"])
        self.assertIn("📤 上传  [████░░░░░░] 40%", text)
        self.assertIn("↑ 1.0KB/s", text)

    async def test_all_upload_bytes_wait_for_completion_acknowledgement(self):
        upload = {
            "processed_bytes": 100,
            "total_bytes": 100,
            "upload_speed": 1024,
            "profile_id": "fixture",
        }
        active, finished, text = await self.project(upload=upload)
        self.assertEqual([], finished)
        self.assertEqual("finalizing", active[0]["phase"])
        self.assertEqual("100.0", active[0]["upload_progress"])
        self.assertIn("确认上传", text)
        self.assertNotIn("✅ current.mp4", text)

    async def test_completed_with_lingering_upload_is_numeric_and_terminal(self):
        self.node.total_download_task = self.node.success_download_task = 1
        upload = {
            "processed_bytes": 100,
            "total_bytes": 100,
            "upload_speed": 1024,
            "profile_id": "fixture",
        }
        active, finished, text = await self.project(
            record={**self.record, "state": "completed"}, upload=upload
        )
        self.assertEqual([], active)
        self.assertEqual("100.0", finished[0]["download_progress"])
        self.assertEqual("100.0", finished[0]["upload_progress"])
        self.assertEqual("completed", finished[0]["phase"])
        self.assertEqual("0B/s", finished[0]["upload_speed"])
        self.assertIn("✅ 全部完成", text)
        self.assertIn("✅ current.mp4", text)

    async def test_reused_task_number_never_includes_old_file_or_its_bytes(self):
        old = {
            **self.record,
            "file_name": "old-private.jpg",
            "state": "completed",
            "created_at": 800,
        }
        _, _, text = await self.project(extra={307: old})
        self.assertNotIn("old-private", text)
        self.assertIn("100B / 100B", text)
        self.assertNotIn("200B", text)

    async def test_pause_and_failure_are_not_reported_as_active_analysis(self):
        for control, state, phase in (
            ("paused", "downloading", "paused"),
            ("running", "upload_failed", "upload_failed"),
        ):
            with self.subTest(phase=phase):
                active, finished, text = await self.project(
                    record={**self.record, "state": state}, control=control
                )
                self.assertEqual([], finished)
                self.assertEqual(phase, active[0]["phase"])
                self.assertEqual("0B/s", active[0]["download_speed"])
                self.assertNotIn("状态  🧠 AI分析中", text)


class TaskIdentityTest(unittest.TestCase):
    def test_legacy_empty_membership_is_bounded_by_creation_time_and_profile(self):
        from module.task_progress import task_record_matches

        node = TaskNode(chat_id=-1001, task_id=1, profile_id="fixture")
        node.created_at = 1000
        record = {"task_id": 1, "profile_id": "fixture", "created_at": 1001}
        self.assertTrue(task_record_matches(node, 312, record))
        self.assertFalse(task_record_matches(node, 307, {**record, "created_at": 800}))
        self.assertFalse(
            task_record_matches(node, 312, {**record, "profile_id": "different"})
        )
        node.download_status["312"] = DownloadStatus.Downloading
        self.assertTrue(task_record_matches(node, 312, record))
        self.assertFalse(task_record_matches(node, 307, record))

    def test_counter_advances_past_persisted_history_and_live_nodes(self):
        instance = bot.DownloadBot()
        instance.task_node[9] = TaskNode(chat_id=-1001, task_id=9)
        with mock.patch.object(
            bot, "get_download_result", return_value={-1001: {307: {"task_id": 12}}}
        ):
            with ThreadPoolExecutor(max_workers=4) as workers:
                identifiers = list(
                    workers.map(lambda _: instance.gen_task_id(), range(8))
                )
        self.assertEqual(list(range(13, 21)), sorted(identifiers))

    def test_old_record_cannot_match_new_task_for_cancellation(self):
        node = TaskNode(chat_id=-1001, task_id=1, profile_id="fixture")
        node.created_at = 1000
        node.download_status[312] = DownloadStatus.Downloading
        old = {"task_id": 1, "profile_id": "fixture", "created_at": 800}
        with mock.patch.object(
            bot, "get_download_result", return_value={-1001: {307: old}}
        ):
            self.assertFalse(
                bot.DownloadBot._node_matches_message(node, -1001, 307, "fixture")
            )


if __name__ == "__main__":
    unittest.main()
