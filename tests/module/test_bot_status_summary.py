"""Regression tests for the Telegram text task panel."""

import unittest

from module.app import TaskNode
from module.pyrogram_extension import build_bot_task_summary


class BotTaskSummaryTestCase(unittest.TestCase):
    def test_summary_uses_real_file_progress_for_streaming_downloads(self):
        node = TaskNode(
            chat_id=-1002237269038,
            task_id=3,
            profile_id="account-siy-han",
        )
        node.created_at = 1000
        node.total_task = 1
        node.total_download_task = 1
        node.success_download_task = 1
        node.total_download_byte = 0

        summary = build_bot_task_summary(
            node,
            current_download_speed=2 * 1024 * 1024,
            current_upload_speed=3 * 1024 * 1024,
            task_records=[
                {
                    "down_byte": 142 * 1024 * 1024,
                    "total_size": 142 * 1024 * 1024,
                }
            ],
            now=1065,
        )

        self.assertIn("✅ 全部完成", summary)
        self.assertIn("[██████████] 100%  (1/1)", summary)
        self.assertIn("💾 数据  142.0MB / 142.0MB", summary)
        self.assertIn("⚡ 速度  ↓ 2.0MB/s  ↑ 3.0MB/s", summary)
        self.assertIn("🕒 耗时  1分05秒", summary)
        self.assertIn("👤 账号  account-siy-han", summary)

    def test_summary_reports_partial_completion_and_remaining_count(self):
        node = TaskNode(chat_id=-1001, task_id=8)
        node.created_at = 1000
        node.total_task = 4
        node.total_download_task = 3
        node.success_download_task = 2
        node.failed_download_task = 1

        summary = build_bot_task_summary(node, now=1005)

        self.assertIn("🔄 传输中", summary)
        self.assertIn("[███████░░░] 75%  (3/4)", summary)
        self.assertIn("├─ ❌ 失败  1", summary)
        self.assertIn("└─ ⏳ 剩余  1", summary)


if __name__ == "__main__":
    unittest.main()
