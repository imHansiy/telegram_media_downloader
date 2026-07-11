"""Regression tests for active task projection in the Web dashboard."""

import unittest
from unittest import mock

import module.web as web


class WebTaskProjectionTestCase(unittest.TestCase):
    def test_completed_download_with_partial_upload_remains_active(self):
        download_result = {
            -1001: {
                93321: {
                    "down_byte": 100,
                    "total_size": 100,
                    "file_name": "video.mp4",
                    "download_speed": 0,
                    "profile_id": "default",
                }
            }
        }
        upload_result = {
            -1001: {
                93321: {
                    "processed_bytes": 47,
                    "total_bytes": 100,
                    "file_name": "video.mp4",
                    "upload_speed": 10,
                    "profile_id": "default",
                }
            }
        }

        with mock.patch.object(web, "get_download_result", return_value=download_result), mock.patch.object(
            web, "get_upload_result", return_value=upload_result
        ):
            active = web._get_formatted_list(already_down=False)
            history = web._get_formatted_list(already_down=True)

        self.assertEqual(1, len(active))
        self.assertEqual("上传中", active[0]["status"])
        self.assertEqual([], history)

    def test_failed_task_remains_visible_with_failure_reason(self):
        download_result = {
            -1001: {
                93322: {
                    "down_byte": 47,
                    "total_size": 100,
                    "file_name": "failed-video.mp4",
                    "download_speed": 0,
                    "profile_id": "default",
                    "state": "failed",
                    "error": "WebDAV upload failed",
                }
            }
        }

        with mock.patch.object(web, "get_download_result", return_value=download_result), mock.patch.object(
            web, "get_upload_result", return_value={}
        ):
            active = web._get_formatted_list(already_down=False)

        self.assertEqual(1, len(active))
        self.assertEqual("失败", active[0]["status"])
        self.assertEqual("failed", active[0]["state"])
        self.assertEqual("WebDAV upload failed", active[0]["error"])


class WebTaskResetRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.previous_login_disabled = web._flask_app.config.get("LOGIN_DISABLED")
        self.previous_retry_callback = web._retry_upload_callback
        web._flask_app.config["LOGIN_DISABLED"] = True

    def tearDown(self):
        web._flask_app.config["LOGIN_DISABLED"] = self.previous_login_disabled
        web._retry_upload_callback = self.previous_retry_callback

    def test_failed_task_reset_uses_server_derived_remote_path(self):
        task_record = {
            "file_name": "downloads/channel/video.mp4",
            "total_size": 1024,
            "profile_id": "profile-1",
            "state": "failed",
            "error": "WebDAV upload failed",
        }
        projected_task = {
            "chat": "-1001",
            "id": "93341",
            "profile_id": "profile-1",
            "remote_path": "/TelegramBackup/channel/video.mp4",
            "filename": "video.mp4",
        }
        retry_callback = mock.Mock(
            return_value={"status": "queued", "message": "已重置并重新上传"}
        )

        with mock.patch.object(
            web,
            "get_download_result",
            return_value={-1001: {93341: task_record}},
        ), mock.patch.object(
            web,
            "_get_formatted_list",
            return_value=[projected_task],
        ), mock.patch.object(web, "_retry_upload_callback", retry_callback):
            response = web._flask_app.test_client().post(
                "/task_control",
                json={
                    "chat_id": -1001,
                    "message_id": 93341,
                    "profile_id": "profile-1",
                    "action": "reset_upload",
                    "remote_path": "/outside/user-controlled.mp4",
                },
            )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["success"])
        retry_callback.assert_called_once_with(
            -1001,
            93341,
            "profile-1",
            "/TelegramBackup/channel/video.mp4",
            "downloads/channel/video.mp4",
            1024,
        )


if __name__ == "__main__":
    unittest.main()
