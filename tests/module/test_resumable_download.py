"""Regression tests for byte-level Telegram download resume."""

import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from media_downloader import (
    TELEGRAM_CHUNK_SIZE,
    download_media_resumable,
    local_file_stream_factory,
)
import module.download_stat as download_stat
from module.app import TaskNode


class FakeStreamClient:
    def __init__(self, chunks):
        self.chunks = chunks
        self.offsets = []

    async def stream_media(self, message, limit=0, offset=0):
        self.offsets.append(offset)
        for chunk in self.chunks:
            yield chunk


class ResumableDownloadTestCase(unittest.IsolatedAsyncioTestCase):
    def test_add_pending_download_clears_deleted_task_state(self):
        """Given: 同一消息曾被网页删除
        When: 再次入队为 pending
        Then: deleted 控制状态被清掉，允许重新下载/上传
        """
        chat_id = -1002237269038
        message_id = 93341
        profile_id = "default"
        download_stat.set_task_state(chat_id, message_id, "deleted", profile_id)
        self.assertEqual(
            "deleted",
            download_stat.get_task_state(chat_id, message_id, profile_id),
        )

        download_stat.add_pending_download(
            chat_id, message_id, "video.mp4", profile_id
        )

        self.assertEqual(
            "running",
            download_stat.get_task_state(chat_id, message_id, profile_id),
        )
        download_stat.remove_pending_download(chat_id, message_id, profile_id)

    async def test_legacy_progress_record_is_upgraded_during_resume(self):
        chat_id = -1001
        message_id = 21
        download_stat._download_result[chat_id] = {
            message_id: {
                "down_byte": 1024,
                "total_size": 4096,
                "file_name": "legacy.mp4",
                "end_time": time.time() - 2,
                "download_speed": 0,
            }
        }

        await download_stat.update_download_status(
            2048,
            4096,
            message_id,
            "legacy.mp4",
            time.time() - 3,
            TaskNode(chat_id=chat_id, task_id=5),
            SimpleNamespace(stop_transmission=mock.Mock()),
        )

        upgraded = download_stat._download_result[chat_id][message_id]
        self.assertEqual(2048, upgraded["down_byte"])
        self.assertIn("each_second_total_download", upgraded)
        self.assertEqual(5, upgraded["task_id"])

    async def test_local_upload_stream_can_be_reopened_for_each_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "upload.bin")
            with open(target, "wb") as completed_file:
                completed_file.write(b"repeatable")
            factory = local_file_stream_factory(target, chunk_size=3)

            first = b"".join([chunk async for chunk in factory()])
            second = b"".join([chunk async for chunk in factory()])

            self.assertEqual(b"repeatable", first)
            self.assertEqual(first, second)

    async def test_completed_temp_file_is_reused_for_upload_retry(self):
        client = FakeStreamClient([b"unexpected"])

        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "ready.mp4")
            with open(target, "wb") as completed_file:
                completed_file.write(b"ready")

            result = await download_media_resumable(
                client,
                SimpleNamespace(id=9),
                target,
                5,
            )

            self.assertEqual(target, result)
            self.assertEqual([], client.offsets)

    async def test_existing_aligned_part_resumes_from_next_telegram_chunk(self):
        first = b"a" * TELEGRAM_CHUNK_SIZE
        second = b"b" * 32
        client = FakeStreamClient([second])
        progress = mock.AsyncMock()

        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "video.mp4")
            with open(f"{target}.part", "wb") as part_file:
                part_file.write(first)

            result = await download_media_resumable(
                client,
                SimpleNamespace(id=10),
                target,
                len(first) + len(second),
                progress,
                (10, "video.mp4", 0, SimpleNamespace(), client),
            )

            self.assertEqual(target, result)
            self.assertEqual([1], client.offsets)
            with open(target, "rb") as completed_file:
                self.assertEqual(first + second, completed_file.read())
            self.assertFalse(os.path.exists(f"{target}.part"))
            progress.assert_awaited_with(
                len(first) + len(second), len(first) + len(second), 10,
                "video.mp4", 0, mock.ANY, client,
            )

    async def test_partial_chunk_is_truncated_before_resuming(self):
        client = FakeStreamClient([b"z" * 10])

        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "clip.mp4")
            with open(f"{target}.part", "wb") as part_file:
                part_file.write(b"broken")

            await download_media_resumable(
                client,
                SimpleNamespace(id=11),
                target,
                10,
                mock.AsyncMock(),
                (11, "clip.mp4", 0, SimpleNamespace(), client),
            )

            self.assertEqual([0], client.offsets)
            with open(target, "rb") as completed_file:
                self.assertEqual(b"z" * 10, completed_file.read())


if __name__ == "__main__":
    unittest.main()
