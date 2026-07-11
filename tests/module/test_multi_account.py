"""Regression tests for the multi-account control plane."""

import copy
import unittest
from types import SimpleNamespace
from unittest import mock

import media_downloader
import pyrogram
import module.bot as bot_module
from module.bot import DownloadBot, direct_download, public_text_hint, send_help_str
from module.telegram_card import render_telegram_card
import module.profiles as profiles
import module.web as web


class MemoryProfileDB:
    """Small settings adapter used to exercise the profile repository seam."""

    def __init__(self, store):
        self.conn = object()
        self.settings = {profiles.PROFILE_STORE_KEY: copy.deepcopy(store)}

    def load_setting(self, key):
        return copy.deepcopy(self.settings.get(key))

    def save_setting(self, key, value):
        self.settings[key] = copy.deepcopy(value)


class ProfileRepositoryTestCase(unittest.TestCase):
    def test_normalized_store_has_no_global_active_profile(self):
        database = MemoryProfileDB(
            {
                "active_profile_id": "account-b",
                "profiles": [
                    {"id": "account-a", "name": "A", "session": "session-a"},
                    {
                        "id": "account-b",
                        "name": "B",
                        "session": "session-b",
                        "config": {"api_id": "100", "api_hash": "legacy"},
                        "bot_setting": {"download_filter": "video"},
                    },
                ],
            }
        )

        with mock.patch.object(profiles, "db", database):
            store = profiles.load_store()

        self.assertNotIn("active_profile_id", store)
        self.assertEqual(
            ["account-a", "account-b"], [item["id"] for item in store["profiles"]]
        )
        self.assertTrue(all("config" not in item for item in store["profiles"]))
        self.assertTrue(all("bot_setting" not in item for item in store["profiles"]))
        self.assertNotIn(
            "active_profile_id", database.settings[profiles.PROFILE_STORE_KEY]
        )
        self.assertEqual(
            {"api_id": "100", "api_hash": "legacy"}, database.settings["config"]
        )
        self.assertEqual({"download_filter": "video"}, database.settings["bot_setting"])

    def test_former_active_profile_can_be_deleted(self):
        database = MemoryProfileDB(
            {
                "active_profile_id": "account-b",
                "profiles": [
                    {"id": "account-a", "name": "A"},
                    {"id": "account-b", "name": "B"},
                ],
            }
        )

        with mock.patch.object(profiles, "db", database):
            store = profiles.delete_profile("account-b")

        self.assertEqual(["account-a"], [item["id"] for item in store["profiles"]])


class AccountStatusTestCase(unittest.TestCase):
    def test_running_profiles_make_the_system_logged_in(self):
        account_profiles = [
            {
                "id": "account-a",
                "name": "A",
                "session": "session-a",
                "account": {"id": "1"},
                "config": {},
                "runtime_enabled": True,
            },
            {
                "id": "account-b",
                "name": "B",
                "session": "session-b",
                "account": {"id": "2"},
                "config": {},
                "runtime_enabled": True,
            },
        ]
        runtime_status = {
            "account-a": {
                "running": True,
                "status": "running",
                "account": {"id": "1", "firstName": "A"},
            },
            "account-b": {
                "running": True,
                "status": "running",
                "account": {"id": "2", "firstName": "B"},
            },
        }

        with mock.patch.object(web.db, "conn", object()), mock.patch.object(
            web, "get_profiles", return_value=account_profiles
        ), mock.patch.object(
            web, "_runtime_status_callback", return_value=runtime_status
        ), mock.patch.object(
            web, "_load_current_config", return_value={}
        ), mock.patch.object(
            web, "_client", None
        ), mock.patch.object(
            web, "get_download_bot_diagnostics", return_value={}
        ):
            status = web._get_telegram_account_status()

        self.assertTrue(status["logged_in"])
        self.assertTrue(status["session_exists"])
        self.assertEqual(2, sum(1 for item in status["accounts"] if item["isRunning"]))
        self.assertNotIn("active_profile_id", status)

    def test_connecting_saved_session_does_not_activate_a_global_profile(self):
        target = {
            "id": "account-b",
            "name": "B",
            "session": "session-b",
            "config": {},
        }
        activate_profile = mock.Mock(
            side_effect=AssertionError(
                "starting an account must not activate it globally"
            )
        )
        start_runtime = mock.Mock(return_value={"status": "started"})
        previous_login_disabled = web._flask_app.config.get("LOGIN_DISABLED")
        web._flask_app.config["LOGIN_DISABLED"] = True
        try:
            with mock.patch.object(web.db, "conn", object()), mock.patch.object(
                web, "get_profile", create=True, return_value=target
            ), mock.patch.object(
                web, "activate_profile", activate_profile, create=True
            ), mock.patch.object(
                web, "_start_runtime_callback", start_runtime
            ), mock.patch.object(
                web, "_get_telegram_account_status", return_value={"accounts": []}
            ):
                response = web._flask_app.test_client().post(
                    "/api/account/connect_saved_session",
                    json={"profile_id": "account-b"},
                )
        finally:
            web._flask_app.config["LOGIN_DISABLED"] = previous_login_disabled

        self.assertEqual(200, response.status_code)
        activate_profile.assert_not_called()
        self.assertEqual("account-b", start_runtime.call_args.args[1]["id"])

    def test_bot_access_endpoint_updates_shared_config_without_profile_selection(self):
        save_config = mock.Mock(return_value={"status": "success", "message": "saved"})
        previous_login_disabled = web._flask_app.config.get("LOGIN_DISABLED")
        web._flask_app.config["LOGIN_DISABLED"] = True
        try:
            with mock.patch.object(web.db, "conn", object()), mock.patch.object(
                web,
                "_load_current_config",
                return_value={"media_types": ["photo"]},
            ), mock.patch.object(
                web, "_save_current_config", save_config
            ), mock.patch.object(
                web, "_get_telegram_account_status", return_value={"accounts": []}
            ):
                response = web._flask_app.test_client().post(
                    "/api/bot/access",
                    json={"mode": "public", "allowedUsers": []},
                )
        finally:
            web._flask_app.config["LOGIN_DISABLED"] = previous_login_disabled

        self.assertEqual(200, response.status_code)
        saved_config = save_config.call_args.args[0]
        self.assertEqual("public", saved_config["bot_download_access_mode"])
        self.assertTrue(saved_config["bot_allow_public_download"])


class RuntimeConfigurationTestCase(unittest.TestCase):
    def test_every_account_runtime_uses_the_shared_downloader_config(self):
        previous_config = media_downloader.app.config
        media_downloader.app.config = {
            "api_id": "100",
            "api_hash": "shared-api-hash",
            "media_types": ["photo"],
        }
        try:
            runtime_app = media_downloader._build_runtime_app(
                {
                    "id": "account-b",
                    "config": {
                        "api_id": "999",
                        "api_hash": "legacy-profile-api-hash",
                    },
                    "app_data": {},
                }
            )
        finally:
            media_downloader.app.config = previous_config

        self.assertEqual("100", runtime_app.api_id)
        self.assertEqual("shared-api-hash", runtime_app.api_hash)
        self.assertEqual(["photo"], runtime_app.media_types)
        self.assertEqual("account-b", runtime_app.profile_id)
        runtime_app.executor.shutdown(wait=False)


class BotAccessTestCase(unittest.TestCase):
    def setUp(self):
        self.bot = DownloadBot()
        self.bot.app = SimpleNamespace(
            bot_download_access_mode="self",
            allowed_user_ids=[],
            profile_id="owner-profile",
        )
        self.bot.admin_user_ids = ["100"]
        self.bot.download_runtime_resolver = lambda user_id: {
            "matched_submitter": str(user_id) == "200",
            "profile_id": "second-profile",
        }

    def test_self_mode_allows_every_logged_account_to_submit(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=200, username="second_account"),
            chat=SimpleNamespace(type=pyrogram.enums.ChatType.PRIVATE),
        )

        self.assertTrue(self.bot.can_submit_download(message))

    def test_self_mode_rejects_unknown_private_user(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=300, username="unknown"),
            chat=SimpleNamespace(type=pyrogram.enums.ChatType.PRIVATE),
        )

        self.assertFalse(self.bot.can_submit_download(message))

    def test_bot_api_self_mode_uses_the_same_logged_account_rule(self):
        message = {
            "from": {"id": 200, "username": "second_account"},
            "chat": {"type": "private"},
        }

        self.assertTrue(self.bot.can_submit_bot_api_message(message))


class BotTaskCardTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_public_text_hint_does_not_claim_telegram_links(self):
        client = SimpleNamespace(send_message=mock.AsyncMock())
        message = SimpleNamespace(
            id=30,
            text="https://t.me/channel/123",
            chat=SimpleNamespace(id=200),
        )

        with mock.patch.object(
            bot_module._bot, "mark_private_message_processed"
        ) as mark_processed:
            await public_text_hint(client, message)

        mark_processed.assert_not_called()
        client.send_message.assert_not_awaited()

    async def test_public_text_hint_ignores_bot_outgoing_status(self):
        client = SimpleNamespace(send_message=mock.AsyncMock())
        message = SimpleNamespace(
            id=31,
            text="task id: 3 Downloading: 0.0b",
            outgoing=True,
            from_user=SimpleNamespace(id=8706259813),
            chat=SimpleNamespace(id=200),
        )

        with mock.patch.object(
            bot_module._bot, "mark_private_message_processed"
        ) as mark_processed:
            await public_text_hint(client, message)

        mark_processed.assert_not_called()
        client.send_message.assert_not_awaited()

    def test_renderer_returns_a_real_png(self):
        card = render_telegram_card("任务状态", ["状态：下载中", "进度：50%"])

        self.assertEqual(b"\x89PNG\r\n\x1a\n", card.read(8))

    async def test_help_is_sent_as_one_photo_instead_of_text(self):
        client = SimpleNamespace(
            send_photo=mock.AsyncMock(return_value=SimpleNamespace(id=901)),
            send_message=mock.AsyncMock(),
        )

        await send_help_str(client, 200)

        client.send_photo.assert_awaited_once()
        client.send_message.assert_not_awaited()

    async def test_media_group_uses_one_task_card(self):
        bot = DownloadBot()
        bot.send_message = mock.AsyncMock(return_value=SimpleNamespace(id=900))
        bot.add_download_task = mock.AsyncMock()
        source_message = SimpleNamespace(id=10, from_user=SimpleNamespace(id=200))
        media_messages = [SimpleNamespace(id=11), SimpleNamespace(id=12)]

        node = await direct_download(
            bot,
            -1001,
            source_message,
            media_messages,
            profile_id="second-profile",
        )

        bot.send_message.assert_awaited_once()
        self.assertEqual(2, bot.add_download_task.await_count)
        self.assertEqual(2, node.limit)
        self.assertEqual("second-profile", node.profile_id)


if __name__ == "__main__":
    unittest.main()
