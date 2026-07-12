"""Regression tests for delayed WebDAV upload auto-retry bookkeeping."""

import unittest

import module.download_stat as download_stat


class UploadAutoRetryCounterTestCase(unittest.TestCase):
    def setUp(self):
        self.chat_id = -1002237269038
        self.message_id = 93362
        self.profile_id = "default"
        download_stat._download_result.setdefault(self.chat_id, {})[self.message_id] = {
            "state": "upload_failed",
            "file_name": "video.mp4",
            "total_size": 100,
            "down_byte": 100,
            "profile_id": self.profile_id,
            "error": "WebDAV upload failed",
        }

    def tearDown(self):
        chat_tasks = download_stat._download_result.get(self.chat_id, {})
        chat_tasks.pop(self.message_id, None)
        if not chat_tasks and self.chat_id in download_stat._download_result:
            del download_stat._download_result[self.chat_id]

    def test_bump_and_clear_auto_retry_count(self):
        """Given: 上传失败任务
        When: 连续登记自动重试再成功清理
        Then: 计数递增，清理后归零
        """
        self.assertEqual(
            0,
            download_stat.get_upload_auto_retry_count(
                self.chat_id, self.message_id, self.profile_id
            ),
        )

        first = download_stat.bump_upload_auto_retry_count(
            self.chat_id, self.message_id, self.profile_id
        )
        second = download_stat.bump_upload_auto_retry_count(
            self.chat_id, self.message_id, self.profile_id
        )
        self.assertEqual(1, first)
        self.assertEqual(2, second)
        self.assertEqual(
            2,
            download_stat.get_upload_auto_retry_count(
                self.chat_id, self.message_id, self.profile_id
            ),
        )

        download_stat.clear_upload_auto_retry_count(
            self.chat_id, self.message_id, self.profile_id
        )
        self.assertEqual(
            0,
            download_stat.get_upload_auto_retry_count(
                self.chat_id, self.message_id, self.profile_id
            ),
        )

    def test_delay_table_matches_policy(self):
        """自动重传退避：30s / 2min / 5min，最多 3 次。"""
        from media_downloader import (
            UPLOAD_AUTO_RETRY_DELAYS_SEC,
            UPLOAD_AUTO_RETRY_MAX,
        )

        self.assertEqual((30, 120, 300), UPLOAD_AUTO_RETRY_DELAYS_SEC)
        self.assertEqual(3, UPLOAD_AUTO_RETRY_MAX)
        self.assertEqual(len(UPLOAD_AUTO_RETRY_DELAYS_SEC), UPLOAD_AUTO_RETRY_MAX)


if __name__ == "__main__":
    unittest.main()
