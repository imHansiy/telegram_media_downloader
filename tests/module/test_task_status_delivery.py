"""Real status-update path with synthetic Telegram responses; no external I/O."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

import module.bot as bot_module
import module.pyrogram_extension as telegram
from module.app import DownloadStatus, TaskNode


class TaskStatusDeliveryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.node = TaskNode(
            -1001,
            from_user_id=1,
            reply_message_id=2,
            task_id=1,
            profile_id="fixture",
            bot=True,
        )
        self.node.is_running = True
        self.node.total_task = 1
        self.node.download_status[312] = DownloadStatus.Downloading
        self.record = {
            "file_name": "fixture.mp4",
            "down_byte": 100,
            "total_size": 100,
            "state": "downloading",
            "ai_status": "分析中",
            "profile_id": "fixture",
            "task_id": 1,
        }
        self.clock = [100.0]
        patches = (
            mock.patch.object(
                telegram,
                "get_download_result",
                return_value={-1001: {312: self.record}},
            ),
            mock.patch("module.upload_stat.get_upload_result", return_value={}),
            mock.patch("module.download_stat.get_task_state", return_value="running"),
            mock.patch.object(
                telegram, "_status_now", side_effect=lambda: self.clock[0], create=True
            ),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def complete(self):
        self.record["state"] = "completed"
        self.node.total_download_task = self.node.success_download_task = 1

    async def test_failed_edit_does_not_acknowledge_or_cache_unsent_text(self):
        self.node.last_edit_msg = "previously delivered"
        client = SimpleNamespace(
            edit_message_text=mock.AsyncMock(side_effect=RuntimeError("temporary"))
        )
        with self.assertRaises(RuntimeError):
            await telegram._report_bot_status(client, self.node, immediate_reply=True)
        self.assertEqual("previously delivered", self.node.last_edit_msg)
        self.assertFalse(getattr(self.node, "final_status_delivered", False))

    async def test_rate_limit_waits_then_delivers_latest_terminal_state(self):
        error = RuntimeError("synthetic rate limit")
        error.retry_after, error.error_code = 22, 429
        client = SimpleNamespace(
            edit_message_text=mock.AsyncMock(side_effect=[error, None])
        )
        await telegram.report_bot_status(client, self.node, immediate_reply=True)
        self.complete()
        other = TaskNode(-1001, from_user_id=1, reply_message_id=3, bot=True)
        await telegram.report_bot_status(client, other, immediate_reply=True)
        await telegram.report_bot_status(client, self.node, immediate_reply=True)
        self.assertEqual(
            1, client.edit_message_text.await_count, "cooldown also covers other cards"
        )
        self.assertFalse(getattr(self.node, "final_status_delivered", False))
        self.clock[0] = 123
        await telegram.report_bot_status(client, self.node, immediate_reply=True)
        self.assertEqual(2, client.edit_message_text.await_count)
        self.assertIn("全部完成", client.edit_message_text.await_args.args[2])
        self.assertTrue(self.node.final_status_delivered)

    async def test_monitor_retains_completed_node_until_final_edit_succeeds(self):
        self.complete()
        downloader = bot_module.DownloadBot()
        downloader.task_node[1] = self.node
        downloader.ensure_bot_api_polling = mock.Mock()
        downloader.save_tasks = mock.Mock()
        downloader.edit_message_text = mock.AsyncMock(
            side_effect=[RuntimeError("temporary"), None]
        )
        observed = []

        async def tick(_):
            observed.append(1 in downloader.task_node)
            self.clock[0] += 5
            if len(observed) == 2:
                downloader.is_running = False

        with mock.patch.object(bot_module.asyncio, "sleep", side_effect=tick):
            await downloader.update_reply_message()
        self.assertEqual([True, False], observed)
        self.assertEqual(2, downloader.edit_message_text.await_count)

    async def test_pyrogram_flood_wait_uses_the_same_cooldown(self):
        client = SimpleNamespace(
            edit_message_text=mock.AsyncMock(
                side_effect=[telegram.pyrogram.errors.FloodWait(9), None]
            )
        )
        await telegram.report_bot_status(client, self.node, immediate_reply=True)
        await telegram.report_bot_status(client, self.node, immediate_reply=True)
        self.assertEqual(1, client.edit_message_text.await_count)
        self.clock[0] = 110
        await telegram.report_bot_status(client, self.node, immediate_reply=True)
        self.assertEqual(2, client.edit_message_text.await_count)

    async def test_forbidden_final_card_is_not_retried_or_falsely_acknowledged(self):
        self.complete()
        error = RuntimeError("synthetic permission failure")
        error.error_code = 403
        client = SimpleNamespace(edit_message_text=mock.AsyncMock(side_effect=error))
        await telegram.report_bot_status(client, self.node, immediate_reply=True)
        self.clock[0] += 100
        await telegram.report_bot_status(client, self.node, immediate_reply=True)
        self.assertEqual(1, client.edit_message_text.await_count)
        self.assertEqual("", self.node.last_edit_msg)
        self.assertFalse(self.node.final_status_delivered)
        self.assertTrue(self.node.status_delivery_unavailable)

    async def test_pyrogram_not_modified_confirms_final_delivery(self):
        self.complete()
        client = SimpleNamespace(
            edit_message_text=mock.AsyncMock(
                side_effect=telegram.pyrogram.errors.MessageNotModified()
            )
        )
        await telegram.report_bot_status(client, self.node, immediate_reply=True)
        self.assertTrue(self.node.final_status_delivered)
        self.assertIn("全部完成", self.node.last_edit_msg)

    async def test_concurrent_edits_cannot_overwrite_final_state_with_old_snapshot(
        self,
    ):
        entered, release = asyncio.Event(), asyncio.Event()

        async def edit(*_, **__):
            entered.set()
            await release.wait()

        client = SimpleNamespace(edit_message_text=mock.AsyncMock(side_effect=edit))
        first = asyncio.create_task(
            telegram.report_bot_status(client, self.node, immediate_reply=True)
        )
        try:
            await entered.wait()
            self.complete()
            await asyncio.wait_for(
                telegram.report_bot_status(client, self.node, immediate_reply=True),
                timeout=1,
            )
        finally:
            release.set()
            await first
        self.assertEqual(1, client.edit_message_text.await_count)
        self.assertFalse(
            getattr(self.node, "final_status_delivered", False),
            "old in-flight payload was not a completion acknowledgement",
        )
        await telegram.report_bot_status(client, self.node, immediate_reply=True)
        self.assertTrue(self.node.final_status_delivered)
        self.assertIn("全部完成", self.node.last_edit_msg)

    def test_bot_api_preserves_structured_retry_delay_without_token(self):
        downloader = bot_module.DownloadBot()
        downloader.app = SimpleNamespace(bot_token="SYNTHETIC_TOKEN")
        response = mock.Mock()
        response.json.return_value = {
            "ok": False,
            "error_code": 429,
            "description": "Too Many Requests",
            "parameters": {"retry_after": 22},
        }
        with mock.patch.object(bot_module.requests, "post", return_value=response):
            with self.assertRaises(RuntimeError) as raised:
                downloader.bot_api_request_sync("editMessageText", {"message_id": 2})
        self.assertEqual(22, getattr(raised.exception, "retry_after", None))
        self.assertEqual(429, getattr(raised.exception, "error_code", None))
        self.assertNotIn("SYNTHETIC_TOKEN", str(raised.exception))

    async def test_unchanged_remote_message_is_a_successful_acknowledgement(self):
        downloader = bot_module.DownloadBot()
        downloader.app = SimpleNamespace(bot_token="SYNTHETIC_TOKEN")
        response = mock.Mock()
        response.json.return_value = {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: message is not modified",
        }
        with mock.patch.object(bot_module.requests, "post", return_value=response):
            result = await downloader.edit_message_text(1, 2, "fixture")
        self.assertEqual(2, result.id)


if __name__ == "__main__":
    unittest.main()
