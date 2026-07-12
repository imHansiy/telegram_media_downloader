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

    def test_upload_failed_task_shows_local_complete_and_upload_failed_status(self):
        download_result = {
            -1001: {
                93341: {
                    "down_byte": 100,
                    "total_size": 100,
                    "file_name": "local-complete.mp4",
                    "download_speed": 0,
                    "profile_id": "default",
                    "state": "upload_failed",
                    "error": "WebDAV upload failed; local completed file retained",
                }
            }
        }

        with mock.patch.object(web, "get_download_result", return_value=download_result), mock.patch.object(
            web, "get_upload_result", return_value={}
        ):
            active = web._get_formatted_list(already_down=False)
            history = web._get_formatted_list(already_down=True)

        self.assertEqual(1, len(active))
        self.assertEqual([], history)
        self.assertEqual("上传失败", active[0]["status"])
        self.assertEqual("upload_failed", active[0]["state"])
        self.assertEqual("100.0", active[0]["download_progress"])


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
            "state": "upload_failed",
            "error": "WebDAV upload failed; local completed file retained",
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

    def test_delete_action_removes_task_history(self):
        """Given a persisted task, delete must remove it instead of only hiding its worker state."""
        with mock.patch.object(
            web,
            "remove_download_task",
            return_value=True,
        ) as remove_mock, mock.patch.object(web, "set_task_state") as state_mock:
            response = web._flask_app.test_client().post(
                "/task_control",
                json={
                    "chat_id": -1001,
                    "message_id": 93341,
                    "profile_id": "profile-1",
                    "action": "delete",
                },
            )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["success"])
        remove_mock.assert_called_once_with(-1001, 93341, "profile-1")
        state_mock.assert_called_once_with(-1001, 93341, "deleted", "profile-1")

    def test_delete_remote_uses_server_derived_path_then_removes_task(self):
        projected_task = {
            "chat": "-1001",
            "id": "93341",
            "profile_id": "profile-1",
            "remote_path": "/TelegramBackup/channel/video.mp4",
        }
        with mock.patch.object(
            web,
            "_get_formatted_list",
            return_value=[projected_task],
        ), mock.patch.object(
            web,
            "_delete_webdav_resource",
            return_value=(True, "deleted"),
        ) as delete_remote_mock, mock.patch.object(
            web,
            "remove_download_task",
            return_value=True,
        ) as remove_mock, mock.patch.object(web, "set_task_state") as state_mock:
            response = web._flask_app.test_client().post(
                "/task_control",
                json={
                    "chat_id": -1001,
                    "message_id": 93341,
                    "profile_id": "profile-1",
                    "action": "delete_remote",
                    "remote_path": "/outside/user-controlled.mp4",
                },
            )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["success"])
        delete_remote_mock.assert_called_once_with(
            "/TelegramBackup/channel/video.mp4"
        )
        remove_mock.assert_called_once_with(-1001, 93341, "profile-1")
        state_mock.assert_called_once_with(-1001, 93341, "deleted", "profile-1")

    def test_delete_remote_failure_keeps_task_record(self):
        projected_task = {
            "chat": "-1001",
            "id": "93341",
            "profile_id": "profile-1",
            "remote_path": "/TelegramBackup/channel/video.mp4",
        }
        with mock.patch.object(
            web,
            "_get_formatted_list",
            return_value=[projected_task],
        ), mock.patch.object(
            web,
            "_delete_webdav_resource",
            return_value=(False, "WebDAV 删除失败（HTTP 423）"),
        ), mock.patch.object(web, "remove_download_task") as remove_mock:
            response = web._flask_app.test_client().post(
                "/task_control",
                json={
                    "chat_id": -1001,
                    "message_id": 93341,
                    "profile_id": "profile-1",
                    "action": "delete_remote",
                },
            )

        self.assertEqual(409, response.status_code)
        self.assertFalse(response.get_json()["success"])
        remove_mock.assert_not_called()

    def test_delete_action_cancels_matching_bot_task_card(self):
        """网页删除活跃任务时，必须同步停止 Bot 任务节点并更新状态卡片。"""
        with mock.patch.object(
            web,
            "remove_download_task",
            return_value=True,
        ), mock.patch.object(web, "set_task_state"), mock.patch.object(
            web,
            "cancel_bot_tasks_for_message",
            return_value=1,
        ) as cancel_bot_mock:
            response = web._flask_app.test_client().post(
                "/task_control",
                json={
                    "chat_id": -1001,
                    "message_id": 93341,
                    "profile_id": "profile-1",
                    "action": "delete",
                },
            )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["success"])
        cancel_bot_mock.assert_called_once_with(-1001, 93341, "profile-1")

    def test_delete_remote_cancels_matching_bot_task_card(self):
        """删除远程资源成功后，同样要清理 Bot 活跃任务卡片。"""
        projected_task = {
            "chat": "-1001",
            "id": "93341",
            "profile_id": "profile-1",
            "remote_path": "/TelegramBackup/channel/video.mp4",
        }
        with mock.patch.object(
            web,
            "_get_formatted_list",
            return_value=[projected_task],
        ), mock.patch.object(
            web,
            "_delete_webdav_resource",
            return_value=(True, "deleted"),
        ), mock.patch.object(
            web,
            "remove_download_task",
            return_value=True,
        ), mock.patch.object(web, "set_task_state"), mock.patch.object(
            web,
            "cancel_bot_tasks_for_message",
            return_value=1,
        ) as cancel_bot_mock:
            response = web._flask_app.test_client().post(
                "/task_control",
                json={
                    "chat_id": -1001,
                    "message_id": 93341,
                    "profile_id": "profile-1",
                    "action": "delete_remote",
                },
            )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["success"])
        cancel_bot_mock.assert_called_once_with(-1001, 93341, "profile-1")


class CancelBotTaskCardTestCase(unittest.TestCase):
    def test_cancel_bot_tasks_for_message_stops_node_and_finalizes_card(self):
        """Given: Bot 仍持有活跃 TaskNode
        When: 网页按 chat/message 删除该任务
        Then: 节点停止传输、卡片改为已删除，并从活跃列表移除
        """
        from module.app import TaskNode
        from module.bot import DownloadBot, cancel_bot_tasks_for_message
        import module.bot as bot_module

        bot = DownloadBot()
        bot.is_running = True
        node = TaskNode(
            chat_id=-1001,
            from_user_id=8906676091,
            reply_message_id=501,
            task_id=7,
            profile_id="profile-1",
            bot=bot,
        )
        node.is_running = True
        node.download_status[93341] = object()
        bot.task_node[7] = node

        edit_mock = mock.AsyncMock(return_value=mock.Mock(id=501))
        bot.edit_message_text = edit_mock

        previous_bot = bot_module._bot
        bot_module._bot = bot
        try:
            cancelled = cancel_bot_tasks_for_message(-1001, 93341, "profile-1")
        finally:
            bot_module._bot = previous_bot

        self.assertEqual(1, cancelled)
        self.assertTrue(node.is_stop_transmission)
        self.assertFalse(node.is_running)
        self.assertNotIn(7, bot.task_node)
        edit_mock.assert_awaited()
        edited_text = edit_mock.await_args.args[2]
        self.assertIn("已删除", edited_text)


if __name__ == "__main__":
    unittest.main()
