"""Bot for media downloader"""

import asyncio
import json
import os
import platform
import threading
from datetime import datetime
from types import SimpleNamespace
from typing import Callable, List, Union

import pyrogram
import requests
from loguru import logger
from pyrogram import types
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from ruamel import yaml

import utils
from module.app import (
    Application,
    ChatDownloadConfig,
    ForwardStatus,
    QueryHandler,
    QueryHandlerStr,
    TaskNode,
    TaskType,
    UploadStatus,
)
from module.db import db
from module.filter import Filter
from module.get_chat_history_v2 import get_chat_history_v2
from module.language import Language, _t
from module.profiles import save_active_profile
from module.pyrogram_extension import (
    check_user_permission,
    get_utf16_length,
    parse_link,
    proc_cache_forward,
    report_bot_forward_status,
    report_bot_status,
    retry,
    set_meta_data,
    upload_telegram_chat_message,
)
from utils.format import replace_date_time, validate_title
from utils.meta_data import MetaData

# pylint: disable = C0301, R0902


class DownloadBot:
    """Download bot"""

    def __init__(self):
        self.bot = None
        self.client = None
        self.add_download_task: Callable = None
        self.download_chat_task: Callable = None
        self.app = None
        self.listen_forward_chat: dict = {}
        self.config: dict = {}
        self._yaml = yaml.YAML()
        self.config_path = os.path.join(os.path.abspath("."), "bot.yaml")
        self.download_command: dict = {}
        self.filter = Filter()
        self.bot_info = None
        self.task_node: dict = {}
        self.is_running = True
        self.allowed_user_ids: List[Union[int, str]] = []
        self.monitor_task = None

        meta = MetaData(datetime(2022, 8, 5, 14, 35, 12), 0, "", 0, 0, 0, "", 0)
        self.filter.set_meta_data(meta)

        self.download_filter: List[str] = []
        self.task_id: int = 0
        self.reply_task = None
        self.admin_user_ids: List[Union[int, str]] = []
        self.bot_api_poll_thread = None
        self.bot_api_poll_stop = threading.Event()
        self.bot_api_poll_offset = None
        self.bot_api_poll_error = None
        self.processed_private_messages = []
        self.processed_private_message_set = set()
        self.processed_private_message_lock = threading.Lock()

    def gen_task_id(self) -> int:
        """Gen task id"""
        self.task_id += 1
        return self.task_id

    def add_task_node(self, node: TaskNode):
        """Add task node"""
        self.task_node[node.task_id] = node
        self.save_tasks()

    def save_tasks(self):
        """Save current tasks to DB"""
        if db.conn:
            try:
                tasks_data = [node.to_dict() for node in self.task_node.values()]
                db.save_setting("active_tasks", tasks_data)
            except Exception as e:
                logger.error(f"Failed to save tasks to DB: {e}")

    def remove_task_node(self, task_id: int):
        """Remove task node"""
        if task_id in self.task_node:
            self.task_node.pop(task_id)
            self.save_tasks()

    def stop_task(self, task_id: str):
        """Stop task"""
        if task_id == "all":
            for value in self.task_node.values():
                value.stop_transmission()
        else:
            try:
                task = self.task_node.get(int(task_id))
                if task:
                    task.stop_transmission()
            except Exception:
                return

    async def update_reply_message(self):
        """Update reply message"""
        while self.is_running:
            self.ensure_bot_api_polling()
            for key, value in self.task_node.copy().items():
                if value.is_running:
                    await report_bot_status(self, value)

            for key, value in self.task_node.copy().items():
                if value.is_running and value.is_finish():
                    self.remove_task_node(key)
            await asyncio.sleep(3)

    def assign_config(self, _config: dict):
        """assign config from str.

        Parameters
        ----------
        _config: dict
            application config dict

        Returns
        -------
        bool
        """

        self.download_filter = _config.get("download_filter", self.download_filter)

        return True

    def user_in_allowed_config(self, user: pyrogram.types.User) -> bool:
        """Check configured allowed users without requiring a bot restart."""

        user_id = str(user.id)
        username = (user.username or "").lower().lstrip("@")

        for allowed_user_id in self.allowed_user_ids:
            if user_id == str(allowed_user_id):
                return True

        for allowed_user_id in getattr(self.app, "allowed_user_ids", []) or []:
            value = str(allowed_user_id).strip()
            if not value:
                continue
            if user_id == value:
                return True
            if username and username == value.lower().lstrip("@"):
                return True

        return False

    def can_submit_download(self, message: pyrogram.types.Message) -> bool:
        """Check whether a message sender can submit bot download jobs."""

        if not message.from_user:
            return False

        if str(message.from_user.id) in {str(item) for item in self.admin_user_ids}:
            return True

        if not message.chat or message.chat.type != pyrogram.enums.ChatType.PRIVATE:
            return False

        access_mode = getattr(self.app, "bot_download_access_mode", "self")
        if access_mode == "public":
            return True
        if access_mode == "allowed":
            return self.user_in_allowed_config(message.from_user)

        return False

    def download_submitter_filter(self):
        """Allow permitted users to submit direct media and link downloads."""

        def check(_, __, message: pyrogram.types.Message):
            return self.can_submit_download(message)

        return pyrogram.filters.create(check, "DownloadSubmitterFilter")

    def ensure_bot_api_polling(self):
        """Keep the Bot API polling fallback alive while the bot is running."""

        if not self.is_running or not self.app or not self.app.bot_token:
            return

        if self.bot_api_poll_thread and self.bot_api_poll_thread.is_alive():
            return

        if self.bot_api_poll_error:
            logger.warning(
                "Bot API polling fallback stopped; restarting: {}",
                self.bot_api_poll_error,
            )

        self.bot_api_poll_stop.clear()
        self.bot_api_poll_thread = threading.Thread(
            target=self.poll_bot_api_updates,
            name="bot-api-polling",
            daemon=True,
        )
        self.bot_api_poll_thread.start()

    def mark_private_message_processed(self, chat_id, message_id) -> bool:
        """Return False when another bot receive path already handled a message."""

        if chat_id is None or message_id is None:
            return True

        key = (str(chat_id), int(message_id))
        with self.processed_private_message_lock:
            if key in self.processed_private_message_set:
                return False

            self.processed_private_messages.append(key)
            self.processed_private_message_set.add(key)
            while len(self.processed_private_messages) > 1000:
                old_key = self.processed_private_messages.pop(0)
                self.processed_private_message_set.discard(old_key)

        return True

    def bot_api_user_in_allowed_config(self, user: dict) -> bool:
        """Check Bot API user dictionaries against allowed user config."""

        user_id = str(user.get("id") or "")
        username = str(user.get("username") or "").lower().lstrip("@")

        for allowed_user_id in self.allowed_user_ids:
            if user_id == str(allowed_user_id):
                return True

        for allowed_user_id in getattr(self.app, "allowed_user_ids", []) or []:
            value = str(allowed_user_id).strip()
            if not value:
                continue
            if user_id == value:
                return True
            if username and username == value.lower().lstrip("@"):
                return True

        return False

    def can_submit_bot_api_message(self, message: dict) -> bool:
        """Check whether a Bot API update sender can submit download jobs."""

        user = message.get("from") or {}
        chat = message.get("chat") or {}
        if not user.get("id"):
            return False

        if str(user.get("id")) in {str(item) for item in self.admin_user_ids}:
            return True

        if chat.get("type") != "private":
            return False

        access_mode = getattr(self.app, "bot_download_access_mode", "self")
        if access_mode == "public":
            return True
        if access_mode == "allowed":
            return self.bot_api_user_in_allowed_config(user)

        return False

    async def bot_api_request(
        self,
        method: str,
        payload: dict = None,
        *,
        request_method: str = "post",
        timeout: int = 20,
    ):
        """Call Telegram Bot API without exposing the token in logs."""

        return await asyncio.to_thread(
            self.bot_api_request_sync,
            method,
            payload,
            request_method=request_method,
            timeout=timeout,
        )

    def bot_api_request_sync(
        self,
        method: str,
        payload: dict = None,
        *,
        request_method: str = "post",
        timeout: int = 20,
    ):
        """Synchronous Bot API call used by polling threads and async wrappers."""

        if not self.app or not self.app.bot_token:
            raise RuntimeError("Bot token is not configured.")

        url = f"https://api.telegram.org/bot{self.app.bot_token}/{method}"
        if request_method == "get":
            response = requests.get(url, params=payload or {}, timeout=timeout)
        else:
            response = requests.post(url, json=payload or {}, timeout=timeout)
        data = response.json()

        if not data.get("ok"):
            description = data.get("description") or "unknown error"
            raise RuntimeError(f"Telegram Bot API {method} failed: {description}")
        return data.get("result")

    @staticmethod
    def _bot_api_parse_mode(parse_mode):
        if parse_mode == pyrogram.enums.ParseMode.HTML:
            return "HTML"
        if parse_mode == pyrogram.enums.ParseMode.MARKDOWN:
            return "Markdown"
        markdown_v2 = getattr(pyrogram.enums.ParseMode, "MARKDOWN_V2", None)
        if markdown_v2 is not None and parse_mode == markdown_v2:
            return "MarkdownV2"
        if isinstance(parse_mode, str):
            return parse_mode
        return None

    async def send_message(
        self,
        chat_id,
        text: str,
        *,
        reply_to_message_id=None,
        parse_mode=None,
        **_,
    ):
        """Send a message through Bot API and return a Pyrogram-like object."""

        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
            payload["allow_sending_without_reply"] = True
        api_parse_mode = self._bot_api_parse_mode(parse_mode)
        if api_parse_mode:
            payload["parse_mode"] = api_parse_mode

        result = await self.bot_api_request("sendMessage", payload)
        return SimpleNamespace(id=result.get("message_id"))

    def send_message_sync(
        self,
        chat_id,
        text: str,
        *,
        reply_to_message_id=None,
        parse_mode=None,
    ):
        """Synchronous Bot API send used by the polling thread."""

        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
            payload["allow_sending_without_reply"] = True
        api_parse_mode = self._bot_api_parse_mode(parse_mode)
        if api_parse_mode:
            payload["parse_mode"] = api_parse_mode

        result = self.bot_api_request_sync("sendMessage", payload)
        return SimpleNamespace(id=result.get("message_id"))

    async def edit_message_text(
        self, chat_id, message_id, text: str, parse_mode=None, **_
    ):
        """Edit a Bot API message, matching the Pyrogram method used by tasks."""

        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        api_parse_mode = self._bot_api_parse_mode(parse_mode)
        if api_parse_mode:
            payload["parse_mode"] = api_parse_mode

        result = await self.bot_api_request("editMessageText", payload)
        return SimpleNamespace(id=result.get("message_id"))

    @staticmethod
    def _bot_api_adapter_message(message: dict, text: str = None):
        user = message.get("from") or {}
        chat = message.get("chat") or {}
        return SimpleNamespace(
            id=message.get("message_id"),
            text=text if text is not None else message.get("text"),
            caption=message.get("caption"),
            media=None,
            from_user=SimpleNamespace(
                id=user.get("id"),
                username=user.get("username"),
                first_name=user.get("first_name"),
                last_name=user.get("last_name"),
            ),
            chat=SimpleNamespace(id=chat.get("id"), type=chat.get("type")),
            bot_api_processed=True,
        )

    def handle_bot_api_message(self, message: dict):
        """Route private Bot API messages when Pyrogram updates are not delivered."""

        if not message:
            return

        chat = message.get("chat") or {}
        user = message.get("from") or {}
        text = message.get("text") or message.get("caption") or ""
        chat_id = chat.get("id")
        message_id = message.get("message_id")

        if chat.get("type") != "private" or not chat_id:
            return

        if not self.mark_private_message_processed(chat_id, message_id):
            logger.info(
                "Bot API update skipped because message was already handled: chat={} message_id={}",
                chat_id,
                message_id,
            )
            return

        logger.info(
            "Bot API update received: user={} username={} message_id={} text={}",
            user.get("id"),
            user.get("username"),
            message_id,
            text[:80],
        )

        if not self.can_submit_bot_api_message(message):
            if text.startswith("/start") or text.startswith("/help"):
                self.send_message_sync(
                    chat_id,
                    "当前 Bot 未向你的账号开放。",
                    reply_to_message_id=message_id,
                )
            logger.info(
                "Bot API update ignored by access policy: user={} mode={}",
                user.get("id"),
                getattr(self.app, "bot_download_access_mode", "self"),
            )
            return

        if text.startswith("/start") or text.startswith("/help"):
            self.send_message_sync(
                chat_id,
                "Bot 已开放给你使用。\n\n"
                "请在私聊中发送以下内容之一：\n"
                "1. Telegram 消息链接，例如 https://t.me/channel/123\n"
                "2. 直接发送或转发包含媒体的消息\n\n"
                "管理员命令不会对普通用户开放。",
                reply_to_message_id=message_id,
            )
            return

        if text.startswith("/"):
            return

        if text.startswith("https://t.me"):
            adapter_message = self._bot_api_adapter_message(message, text.split()[0])
            future = asyncio.run_coroutine_threadsafe(
                download_from_link(self, adapter_message),
                self.app.loop,
            )
            future.result(timeout=60)
            return

        if any(
            key in message
            for key in ("photo", "video", "document", "audio", "voice", "video_note")
        ):
            self.send_message_sync(
                chat_id,
                "已收到媒体消息。当前线上兜底通道先支持 Telegram 消息链接下载，请发送对应消息链接。",
                reply_to_message_id=message_id,
            )
            return

        self.send_message_sync(
            chat_id,
            "请发送 Telegram 消息链接，或直接发送/转发包含媒体的消息。",
            reply_to_message_id=message_id,
        )

    def poll_bot_api_updates(self):
        """Poll Bot API updates as a fallback for hosted bot message delivery."""

        logger.info("Bot API polling fallback started.")
        self.bot_api_poll_error = None
        while self.is_running and not self.bot_api_poll_stop.is_set():
            try:
                params = {
                    "timeout": 25,
                    "allowed_updates": json.dumps(["message"]),
                }
                if self.bot_api_poll_offset is not None:
                    params["offset"] = self.bot_api_poll_offset

                data = self.bot_api_request_sync(
                    "getUpdates",
                    params,
                    request_method="get",
                    timeout=35,
                )

                for update in data or []:
                    update_id = update.get("update_id")
                    if update_id is not None:
                        self.bot_api_poll_offset = update_id + 1
                    try:
                        self.handle_bot_api_message(update.get("message"))
                    except Exception as e:
                        logger.exception(
                            "Failed to handle Bot API update {}: {}",
                            update_id,
                            e.__class__.__name__,
                        )
            except Exception as e:
                self.bot_api_poll_error = e.__class__.__name__
                logger.warning("Bot API polling error: {}", e.__class__.__name__)
                self.bot_api_poll_stop.wait(5)

    def update_config(self):
        """Update config to database."""
        self.config["download_filter"] = self.download_filter
        if db.conn:
            db.save_setting("bot_setting", self.config)
            save_active_profile(bot_setting=self.config, sync_legacy=False)
        else:
            # Fallback to file if DB is not available (though user said it is PG now)
            try:
                with open(self.config_path, "w", encoding="utf-8") as yaml_file:
                    self._yaml.dump(self.config, yaml_file)
            except Exception as e:
                logger.error(f"Failed to save config to file: {e}")

    async def start(
        self,
        app: Application,
        client: pyrogram.Client,
        add_download_task: Callable,
        download_chat_task: Callable,
    ):
        """Start bot"""
        self.is_running = True
        self.allowed_user_ids = []
        self.admin_user_ids = []
        self.monitor_task = None
        self.bot = pyrogram.Client(
            app.application_name + "_bot",
            api_hash=app.api_hash,
            api_id=app.api_id,
            bot_token=app.bot_token,
            workdir=app.session_file_path,
            proxy=app.proxy,
            in_memory=True,
        )

        # Command list
        commands = [
            types.BotCommand("help", _t("帮助")),
            types.BotCommand("get_info", _t("从消息链接获取群组和用户信息")),
            types.BotCommand(
                "download",
                _t("下载视频，使用方法：直接输入 /download 查看"),
            ),
            types.BotCommand(
                "forward",
                _t("转发视频，使用方法：直接输入 /forward 查看"),
            ),
            types.BotCommand(
                "listen_forward",
                _t("监听转发，使用方法：直接输入 /listen_forward 查看"),
            ),
            types.BotCommand(
                "add_filter",
                _t("添加下载过滤器，使用方法：直接输入 /add_filter 查看"),
            ),
            types.BotCommand("set_language", _t("设置语言")),
            types.BotCommand("status", _t("获取运行设备系统信息")),
            types.BotCommand("stop", _t("停止机器人下载或转发")),
        ]

        self.app = app
        self.client = client
        self.add_download_task = add_download_task
        self.download_chat_task = download_chat_task

        # load config
        if db.conn:
            config = db.load_setting("bot_setting")
            if config:
                self.config = config
                self.assign_config(self.config)

        # Fallback to file if config not loaded from DB
        if not self.config and os.path.exists(self.config_path):
            with open(self.config_path, encoding="utf-8") as f:
                config = self._yaml.load(f.read())
                if config:
                    self.config = config
                    self.assign_config(self.config)

        await self.bot.start()

        # Restore tasks from DB
        if db.conn:
            try:
                saved_tasks = db.load_setting("active_tasks")
                if saved_tasks and isinstance(saved_tasks, list):
                    logger.info(f"Restoring {len(saved_tasks)} active tasks from DB...")
                    for task_data in saved_tasks:
                        try:
                            # Recreate TaskNode
                            node = TaskNode.from_dict(task_data, bot=self.bot)

                            # Re-add to local dict (without saving again to avoid recursion/redundancy)
                            self.task_node[node.task_id] = node

                            # Important: Update max task_id to avoid collision
                            if node.task_id >= self.task_id:
                                self.task_id = node.task_id + 1

                            # Re-trigger download logic
                            # We need to recreate ChatDownloadConfig and start the task
                            chat_download_config = ChatDownloadConfig()
                            chat_download_config.last_read_message_id = (
                                node.start_offset_id
                            )
                            chat_download_config.download_filter = node.download_filter

                            # Start background task
                            self.app.loop.create_task(
                                self.download_chat_task(
                                    self.client, chat_download_config, node
                                )
                            )
                            node.is_running = True
                            logger.info(
                                f"Restored task {node.task_id} for chat {node.chat_id}"
                            )
                        except Exception as e:
                            logger.error(f"Failed to restore task {task_data}: {e}")
            except Exception as e:
                logger.error(f"Failed to load active tasks: {e}")

        self.bot_info = await self.bot.get_me()
        logger.info(f"Bot started: @{self.bot_info.username} (ID: {self.bot_info.id})")

        for allowed_user_id in self.app.allowed_user_ids:
            try:
                chat = await self.client.get_chat(allowed_user_id)
                self.allowed_user_ids.append(chat.id)
                logger.info(f"Added allowed user: {chat.id}")
            except Exception as e:
                logger.warning(f"set allowed_user_ids error: {e}")

        admin = await self.client.get_me()
        self.admin_user_ids.append(admin.id)
        logger.info(f"Admin user added: {admin.first_name} (ID: {admin.id})")
        logger.info(f"Configured allowed user IDs: {self.allowed_user_ids}")
        logger.info(f"Bot download access mode: {self.app.bot_download_access_mode}")

        await self.bot.set_bot_commands(commands)

        admin_filter = pyrogram.filters.user(self.admin_user_ids)
        download_submitter_filter = self.download_submitter_filter()
        non_admin_submitter_filter = download_submitter_filter & ~admin_filter

        self.bot.add_handler(
            MessageHandler(
                download_from_bot,
                filters=pyrogram.filters.command(["download"])
                & admin_filter,
            )
        )
        self.bot.add_handler(
            MessageHandler(
                forward_messages,
                filters=pyrogram.filters.command(["forward"])
                & admin_filter,
            )
        )
        self.bot.add_handler(
            MessageHandler(
                download_forward_media,
                filters=pyrogram.filters.media & download_submitter_filter,
            )
        )
        self.bot.add_handler(
            MessageHandler(
                download_from_link,
                filters=pyrogram.filters.regex(r"^https://t.me.*")
                & download_submitter_filter,
            )
        )
        self.bot.add_handler(
            MessageHandler(
                set_listen_forward_msg,
                filters=pyrogram.filters.command(["listen_forward"])
                & admin_filter,
            )
        )
        self.bot.add_handler(
            MessageHandler(
                help_command,
                filters=pyrogram.filters.command(["help"])
                & admin_filter,
            )
        )
        self.bot.add_handler(
            MessageHandler(
                get_info,
                filters=pyrogram.filters.command(["get_info"])
                & admin_filter,
            )
        )
        self.bot.add_handler(
            MessageHandler(
                help_command,
                filters=pyrogram.filters.command(["start"])
                & admin_filter,
            )
        )
        self.bot.add_handler(
            MessageHandler(
                public_help_command,
                filters=pyrogram.filters.command(["start", "help"])
                & non_admin_submitter_filter,
            )
        )
        self.bot.add_handler(
            MessageHandler(
                public_text_hint,
                filters=pyrogram.filters.private
                & pyrogram.filters.text
                & ~pyrogram.filters.regex(r"^/")
                & ~pyrogram.filters.regex(r"^https://t.me.*")
                & non_admin_submitter_filter,
            )
        )
        self.bot.add_handler(
            MessageHandler(
                set_language,
                filters=pyrogram.filters.command(["set_language"])
                & admin_filter,
            )
        )
        self.bot.add_handler(
            MessageHandler(
                add_filter,
                filters=pyrogram.filters.command(["add_filter"])
                & admin_filter,
            )
        )

        self.bot.add_handler(
            MessageHandler(
                stop,
                filters=pyrogram.filters.command(["stop"])
                & admin_filter,
            )
        )

        self.bot.add_handler(
            MessageHandler(
                system_status,
                filters=pyrogram.filters.command(["status"])
                & admin_filter,
            )
        )

        self.bot.add_handler(
            CallbackQueryHandler(on_query_handler, filters=admin_filter)
        )

        try:
            await send_help_str(self.bot, admin.id)
        except Exception:
            pass

        self.reply_task = _bot.app.loop.create_task(_bot.update_reply_message())
        self.bot_api_poll_offset = None
        self.ensure_bot_api_polling()

        self.bot.add_handler(
            MessageHandler(
                forward_to_comments,
                filters=pyrogram.filters.command(["forward_to_comments"])
                & admin_filter,
            )
        )


_bot = DownloadBot()


async def start_download_bot(
    app: Application,
    client: pyrogram.Client,
    add_download_task: Callable,
    download_chat_task: Callable,
):
    """Start download bot"""
    await _bot.start(app, client, add_download_task, download_chat_task)


async def stop_download_bot():
    """Stop download bot"""
    _bot.update_config()
    _bot.is_running = False
    if _bot.reply_task:
        _bot.reply_task.cancel()
        _bot.reply_task = None
    _bot.bot_api_poll_stop.set()
    if _bot.bot_api_poll_thread and _bot.bot_api_poll_thread.is_alive():
        _bot.bot_api_poll_thread.join(timeout=2)
    _bot.bot_api_poll_thread = None
    _bot.stop_task("all")
    if _bot.bot:
        try:
            await _bot.bot.stop()
        except ConnectionError as e:
            logger.warning(f"Bot client already stopped: {e}")
        _bot.bot = None
    if _bot.monitor_task:
        _bot.monitor_task.cancel()
        _bot.monitor_task = None
    _bot.client = None


def get_download_bot_diagnostics(ensure_polling: bool = False):
    """Return Bot receiver diagnostics for Web/runtime status."""

    if ensure_polling:
        _bot.ensure_bot_api_polling()

    return {
        "receiverVersion": "bot-api-thread-sync-v2",
        "isRunning": bool(_bot.is_running),
        "botConfigured": bool(_bot.app and _bot.app.bot_token),
        "pollThreadAlive": bool(
            _bot.bot_api_poll_thread and _bot.bot_api_poll_thread.is_alive()
        ),
        "pollOffset": _bot.bot_api_poll_offset,
        "pollError": _bot.bot_api_poll_error,
        "processedCount": len(_bot.processed_private_messages),
    }


async def send_help_str(client: pyrogram.Client, chat_id):
    """
    Sends a help string to the specified chat ID using the provided client.

    Parameters:
        client (pyrogram.Client): The Pyrogram client used to send the message.
        chat_id: The ID of the chat to which the message will be sent.

    Returns:
        str: The help string that was sent.

    Note:
        The help string includes information about the Telegram Media Downloader bot,
        its version, and the available commands.
    """

    update_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Github",
                    url="https://github.com/tangyoha/telegram_media_downloader/releases",
                ),
                InlineKeyboardButton(
                    "Join us", url="https://t.me/TeegramMediaDownload"
                ),
            ]
        ]
    )
    latest_release_str = ""
    # try:
    #     latest_release = get_latest_release(_bot.app.proxy)

    #     latest_release_str = (
    #         f"{_t('New Version')}: [{latest_release['name']}]({latest_release['html_url']})\an"
    #         if latest_release
    #         else ""
    #     )
    # except Exception:
    #     latest_release_str = ""

    msg = (
        f"`\n🤖 {_t('Telegram 媒体下载器')}\n"
        f"🌐 {_t('版本')}: {utils.__version__}`\n"
        f"{latest_release_str}\n"
        f"{_t('可用命令:')}\n"
        f"/help - {_t('显示可用命令')}\n"
        f"/get_info - {_t('从消息链接获取群组和用户信息')}\n"
        f"/download - {_t('下载消息')}\n"
        f"/forward - {_t('转发消息')}\n"
        f"/listen_forward - {_t('监听转发消息')}\n"
        f"/forward_to_comments - {_t('将特定媒体转发到评论区')}\n"
        f"/set_language - {_t('设置语言')}\n"
        f"/status - {_t('获取运行设备系统信息')}\n"
        f"/stop - {_t('停止机器人下载或转发')}\n\n"
        f"{_t('**注意**: 1 表示整个聊天的开始')},"
        f"{_t('0 表示整个聊天的结束')}\n"
        f"`[` `]` {_t('表示可选，非必须')}\n"
    )

    await client.send_message(chat_id, msg, reply_markup=update_keyboard)


async def help_command(client: pyrogram.Client, message: pyrogram.types.Message):
    """
    Sends a message with the available commands and their usage.

    Parameters:
        client (pyrogram.Client): The client instance.
        message (pyrogram.types.Message): The message object.

    Returns:
        None
    """

    await send_help_str(client, message.chat.id)


async def public_help_command(client: pyrogram.Client, message: pyrogram.types.Message):
    """Send usage help for non-admin users allowed to submit downloads."""

    if not _bot.mark_private_message_processed(message.chat.id, message.id):
        return

    await client.send_message(
        message.chat.id,
        "Bot 已开放给你使用。\n\n"
        "请在私聊中发送以下内容之一：\n"
        "1. Telegram 消息链接，例如 https://t.me/channel/123\n"
        "2. 直接发送或转发包含媒体的消息\n\n"
        "管理员命令不会对普通用户开放。",
        reply_to_message_id=message.id,
    )


async def public_text_hint(client: pyrogram.Client, message: pyrogram.types.Message):
    """Tell public users what kind of messages can trigger downloads."""

    if message.text and message.text.startswith("/"):
        return
    if not _bot.mark_private_message_processed(message.chat.id, message.id):
        return

    await client.send_message(
        message.chat.id,
        "请发送 Telegram 消息链接，或直接发送/转发包含媒体的消息。",
        reply_to_message_id=message.id,
    )


async def set_language(client: pyrogram.Client, message: pyrogram.types.Message):
    """
    Set the language of the bot.

    Parameters:
        client (pyrogram.Client): The pyrogram client.
        message (pyrogram.types.Message): The message containing the command.

    Returns:
        None
    """

    if len(message.text.split()) != 2:
        await client.send_message(
            message.from_user.id,
            _t("Invalid command format. Please use /set_language en/ru/zh/ua"),
        )
        return

    language = message.text.split()[1]

    try:
        language = Language[language.upper()]
        _bot.app.set_language(language)
        await client.send_message(
            message.from_user.id, f"{_t('Language set to')} {language.name}"
        )
    except KeyError:
        await client.send_message(
            message.from_user.id,
            _t("Invalid command format. Please use /set_language en/ru/zh/ua"),
        )


async def get_info(client: pyrogram.Client, message: pyrogram.types.Message):
    """
    Async function that retrieves information from a group message link.
    """

    msg = _t("Invalid command format. Please use /get_info group_message_link")

    args = message.text.split()
    if len(args) != 2:
        await client.send_message(
            message.from_user.id,
            msg,
        )
        return

    chat_id, message_id, _ = await parse_link(_bot.client, args[1])

    entity = None
    if chat_id:
        entity = await _bot.client.get_chat(chat_id)

    if entity:
        if message_id:
            _message = await retry(_bot.client.get_messages, args=(chat_id, message_id))
            if _message:
                meta_data = MetaData()
                set_meta_data(meta_data, _message)
                msg = (
                    f"`\n"
                    f"{_t('Group/Channel')}\n"
                    f"├─ {_t('id')}: {entity.id}\n"
                    f"├─ {_t('first name')}: {entity.first_name}\n"
                    f"├─ {_t('last name')}: {entity.last_name}\n"
                    f"└─ {_t('name')}: {entity.username}\n"
                    f"{_t('Message')}\n"
                )

                for key, value in meta_data.data().items():
                    if key == "send_name":
                        msg += f"└─ {key}: {value or None}\n"
                    else:
                        msg += f"├─ {key}: {value or None}\n"

                msg += "`"
    await client.send_message(
        message.from_user.id,
        msg,
    )


async def add_filter(client: pyrogram.Client, message: pyrogram.types.Message):
    """
    Set the download filter of the bot.

    Parameters:
        client (pyrogram.Client): The pyrogram client.
        message (pyrogram.types.Message): The message containing the command.

    Returns:
        None
    """

    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        await client.send_message(
            message.from_user.id,
            _t("Invalid command format. Please use /add_filter your filter"),
        )
        return

    filter_str = replace_date_time(args[1])
    res, err = _bot.filter.check_filter(filter_str)
    if res:
        # _bot.app.down = args[1]
        _bot.download_filter.append(args[1])
        _bot.update_config()
        await client.send_message(
            message.from_user.id, f"{_t('Add download filter')} : {args[1]}"
        )
    else:
        await client.send_message(
            message.from_user.id, f"{err}\n{_t('Check error, please add again!')}"
        )
    return


async def system_status(client: pyrogram.Client, message: pyrogram.types.Message):
    """
    Get system status information.

    Parameters:
        client (pyrogram.Client): The pyrogram client.
        message (pyrogram.types.Message): The message containing the command.

    Returns:
        None
    """
    try:
        import psutil
    except ImportError:
        await client.send_message(
            message.from_user.id,
            _t("psutil 模块未安装，请运行: pip install psutil"),
        )
        return

    try:
        # System basic info
        uname = platform.uname()
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time

        # CPU info
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count()
        cpu_freq = psutil.cpu_freq()
        cpu_freq_str = f"{cpu_freq.current:.0f}MHz" if cpu_freq else "N/A"

        # Memory info
        mem = psutil.virtual_memory()
        mem_used_gb = mem.used / (1024**3)
        mem_total_gb = mem.total / (1024**3)

        # Disk info
        disk = psutil.disk_usage("/")
        disk_used_gb = disk.used / (1024**3)
        disk_total_gb = disk.total / (1024**3)

        # Network info
        net_io = psutil.net_io_counters()
        bytes_sent_gb = net_io.bytes_sent / (1024**3)
        bytes_recv_gb = net_io.bytes_recv / (1024**3)

        # Active tasks info
        active_tasks = len(_bot.task_node)
        running_tasks = sum(1 for node in _bot.task_node.values() if node.is_running)

        # Python info
        python_version = platform.python_version()

        # Format uptime
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"

        msg = (
            f"`\n"
            f"🖥️ {_t('系统状态')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 {_t('系统')}: {uname.system} {uname.release}\n"
            f"🏷️ {_t('主机名')}: {uname.node}\n"
            f"🐍 Python: {python_version}\n"
            f"📦 TDL: {utils.__version__}\n"
            f"⏱️ {_t('运行时间')}: {uptime_str}\n"
            f"\n"
            f"💻 CPU\n"
            f"├─ {_t('使用率')}: {cpu_percent}%\n"
            f"├─ {_t('核心数')}: {cpu_count}\n"
            f"└─ {_t('频率')}: {cpu_freq_str}\n"
            f"\n"
            f"🧠 {_t('内存')}\n"
            f"├─ {_t('使用')}: {mem_used_gb:.1f} GB / {mem_total_gb:.1f} GB\n"
            f"└─ {_t('占用')}: {mem.percent}%\n"
            f"\n"
            f"💾 {_t('磁盘')}\n"
            f"├─ {_t('使用')}: {disk_used_gb:.1f} GB / {disk_total_gb:.1f} GB\n"
            f"└─ {_t('占用')}: {disk.percent}%\n"
            f"\n"
            f"🌐 {_t('网络 (累计)')}\n"
            f"├─ ↑ {_t('发送')}: {bytes_sent_gb:.2f} GB\n"
            f"└─ ↓ {_t('接收')}: {bytes_recv_gb:.2f} GB\n"
            f"\n"
            f"📋 {_t('任务')}\n"
            f"├─ {_t('活动任务')}: {active_tasks}\n"
            f"└─ {_t('运行中')}: {running_tasks}\n"
            f"`"
        )

        await client.send_message(message.from_user.id, msg)

    except Exception as e:
        logger.error(f"Failed to get system status: {e}")
        await client.send_message(
            message.from_user.id,
            f"{_t('获取系统状态失败')}: {str(e)}",
        )


async def add_filter_advertisement_filter(
    client: pyrogram.Client, message: pyrogram.types.Message
):
    """Add an advertisement filter."""

    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        await client.send_message(
            message.from_user.id,
            _t("Invalid command format. Please use /add_ad filter"),
        )
        return

    filter_str = args[1]

    _bot.app.filter_advertisement_list.append(filter_str)
    await client.send_message(message.from_user.id, f"{_t('Add filter')} : {args[1]}")
    _bot.app.update_config(True)


async def remove_filter_advertisement_filter(
    client: pyrogram.Client, message: pyrogram.types.Message
):
    """
    Add or remove advertisement filter
    """

    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        await client.send_message(
            message.from_user.id,
            _t("Invalid command format. Please use /remove_ad filter"),
        )
        return

    filter_str = args[1]
    if filter_str in _bot.app.filter_advertisement_list:
        _bot.app.filter_advertisement_list.remove(filter_str)
        await client.send_message(
            message.from_user.id, f"{_t('Remove filter')} : {args[1]}"
        )

        _bot.app.update_config(True)
    else:
        await client.send_message(
            message.from_user.id, f"{_t('Filter')} : {args[1]} {_t('not exist')}"
        )


async def set_add_advertisement(
    client: pyrogram.Client, message: pyrogram.types.Message
):
    """
    Add or remove advertisement filter
    """

    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await client.send_message(
            message.from_user.id,
            _t("Invalid command format. Please use /set_ad mesage_link advertisement"),
        )
        return

    mesage_link = args[1]
    advertisement_str = None if len(args) < 3 else args[2]

    try:
        chat_id, _, _ = await parse_link(_bot.client, mesage_link)
        _bot.app.group_add_advertisement[chat_id] = advertisement_str
        _bot.app.update_config(True)
        await client.send_message(
            message.from_user.id, f"{_t('Set advertisement')} : {advertisement_str}"
        )
    except Exception as e:
        await client.send_message(
            message.from_user.id, f"{_t('Parse link error')}: {e}"
        )
        return


class MessageProcessor:
    """Helper class for processing message captions and entities."""

    def __init__(self, raw_message, filter_str):
        self.raw_message = raw_message
        self.raw_caption = raw_message.caption
        self.filter_str = filter_str
        self.raw_filter_str = pyrogram.parser.utils.add_surrogates(filter_str)
        self.raw_caption_str = pyrogram.parser.utils.add_surrogates(raw_message.caption)
        self.idx = self.raw_caption_str.find(self.raw_filter_str)
        self.start_offset = self.idx
        self.end_offset = self.idx + get_utf16_length(filter_str)
        self.filtered_entities = []

    # pylint: disable = R0916
    def process_entities(self):
        """Process and filter message entities."""
        for entity in self.raw_message.caption_entities:
            cur_start_offset = entity.offset
            cur_end_offset = entity.offset + entity.length

            # Check if entity should be included
            if (
                (
                    cur_start_offset >= self.start_offset
                    and cur_end_offset <= self.end_offset
                )
                or (
                    cur_start_offset < self.start_offset
                    and cur_end_offset > self.start_offset
                )
                or (
                    cur_start_offset < self.end_offset
                    and cur_end_offset > self.end_offset
                )
            ):
                self.filtered_entities.append(entity)

        self.filtered_entities.sort(key=lambda x: x.offset)

    def get_total_span(self):
        """Calculate the total span for text extraction."""
        if self.filtered_entities:
            first_entity = self.filtered_entities[0]
            last_entity = self.filtered_entities[-1]
            return (
                min(self.start_offset, first_entity.offset),
                max(self.end_offset, last_entity.offset + last_entity.length),
            )
        return (self.start_offset, self.end_offset)

    def extract_text(self, total_span):
        """Extract and process text with adjusted entity offsets."""
        text = self.raw_caption[total_span[0] : total_span[1]]
        for entity in self.filtered_entities:
            entity.offset -= total_span[0]
        return pyrogram.parser.Parser.unparse(text, self.filtered_entities, True)


async def proc_replace_advertisement(mesage_link: str, filter_str: str):
    """
    Process and replace advertisement content in a message.

    This function takes a message link and a filter string, retrieves the message,
    and processes its caption by handling entities and filtering advertisement content.
    It preserves the formatting and entities while replacing the specified filter text.

    Args:
        mesage_link (str): The link to the Telegram message
        filter_str (str): The string to filter/replace in the message caption

    Returns:
        str: The processed caption with preserved formatting and entities

    Raises:
        Exception: If there are issues parsing the message link or accessing the message
    """
    chat_id, message_id, _ = await parse_link(_bot.client, mesage_link)
    raw_message = await retry(_bot.client.get_messages, args=(chat_id, message_id))

    processor = MessageProcessor(raw_message, filter_str)
    processor.process_entities()
    total_span = processor.get_total_span()
    return processor.extract_text(total_span)


async def add_replace_advertisement_filter(
    client: pyrogram.Client, message: pyrogram.types.Message
):
    """
    Set the download filter of the bot.

    Parameters:
        client (pyrogram.Client): The pyrogram client.
        message (pyrogram.types.Message): The message containing the command.

    Returns:
        None
    """

    args = message.text.split(maxsplit=2)
    if len(args) != 3:
        await client.send_message(
            message.from_user.id,
            _t("Invalid command format. Please use /add_replace_ad your filter"),
        )
        return

    mesage_link = args[1]
    filter_str = args[2]

    try:
        filter_str = await proc_replace_advertisement(mesage_link, filter_str)
        _bot.app.replace_advertisement_list.append(filter_str)
        _bot.app.update_config(True)
        await client.send_message(
            message.from_user.id, f"{_t('Add filter')} : {filter_str}"
        )
    except Exception as e:
        await client.send_message(
            message.from_user.id, f"{_t('Add filter')} : {filter_str}\n{e}"
        )
        return


async def remove_replace_advertisement_filter(
    client: pyrogram.Client, message: pyrogram.types.Message
):
    """
    Set the download filter of the bot.

    Parameters:
        client (pyrogram.Client): The pyrogram client.
        message (pyrogram.types.Message): The message containing the command.

    Returns:
        None
    """

    args = message.text.split(maxsplit=2)
    if len(args) != 3:
        await client.send_message(
            message.from_user.id,
            _t(
                "Invalid command format. Please use /remove_replace_ad mesage_link advertisement_filter"
            ),
        )
        return

    mesage_link = args[1]
    filter_str = args[2]

    try:
        filter_str = await proc_replace_advertisement(mesage_link, filter_str)

        if filter_str in _bot.app.replace_advertisement_list:
            _bot.app.replace_advertisement_list.remove(filter_str)
            await client.send_message(
                message.from_user.id, f"{_t('Remove filter')} : {filter_str}"
            )
        else:
            _bot.app.replace_advertisement_list.append(filter_str)
            await client.send_message()
        _bot.app.update_config(True)
    except Exception as e:
        await client.send_message(
            message.from_user.id, f"{_t('Add filter')} : {filter_str}\n{e}"
        )
        return


async def direct_download(
    download_bot: DownloadBot,
    chat_id: Union[str, int],
    message: pyrogram.types.Message,
    download_message: pyrogram.types.Message,
    client: pyrogram.Client = None,
):
    """Direct Download"""

    replay_message = "Direct download..."
    last_reply_message = await download_bot.send_message(
        message.from_user.id, replay_message, reply_to_message_id=message.id
    )

    node = TaskNode(
        chat_id=chat_id,
        from_user_id=message.from_user.id,
        reply_message_id=last_reply_message.id,
        replay_message=replay_message,
        limit=1,
        bot=download_bot,
        task_id=_bot.gen_task_id(),
    )

    node.client = client

    _bot.add_task_node(node)

    await _bot.add_download_task(
        download_message,
        node,
    )

    node.is_running = True


async def download_forward_media(
    client: pyrogram.Client, message: pyrogram.types.Message
):
    """
    Downloads the media from a forwarded message.

    Parameters:
        client (pyrogram.Client): The client instance.
        message (pyrogram.types.Message): The message object.

    Returns:
        None
    """

    if not _bot.mark_private_message_processed(message.chat.id, message.id):
        return

    if message.media and getattr(message, message.media.value):
        await direct_download(_bot, message.from_user.id, message, message, client)
        return

    await client.send_message(
        message.from_user.id,
        f"1. {_t('Direct download, directly forward the message to your robot')}\n\n",
        parse_mode=pyrogram.enums.ParseMode.HTML,
    )


async def download_from_link(client: pyrogram.Client, message: pyrogram.types.Message):
    """
    Downloads a single message or media group from a Telegram link.

    Parameters:
        client (pyrogram.Client): The pyrogram client.
        message (pyrogram.types.Message): The message containing the Telegram link.

    Returns:
        None
    """
    logger.info(
        f"[download_from_link] Received message from user {message.from_user.id}: {message.text}"
    )

    if not getattr(message, "bot_api_processed", False):
        if not _bot.mark_private_message_processed(message.chat.id, message.id):
            return

    if not message.text or not message.text.startswith("https://t.me"):
        logger.warning(f"[download_from_link] Invalid link format: {message.text}")
        return

    msg = (
        f"1. {_t('Directly download a single message')}\n"
        "<i>https://t.me/12000000/1</i>\n\n"
    )

    text = message.text.split()
    if len(text) != 1:
        await client.send_message(
            message.from_user.id, msg, parse_mode=pyrogram.enums.ParseMode.HTML
        )

    try:
        chat_id, message_id, _ = await parse_link(_bot.client, text[0])
        entity = None
        if chat_id:
            entity = await _bot.client.get_chat(chat_id)
    except Exception as e:
        logger.warning(f"[download_from_link] Failed to read link {text[0]}: {e}")
        await client.send_message(
            message.from_user.id,
            f"{_t('download')} {_t('error')}: {e}",
            reply_to_message_id=message.id,
        )
        return

    if entity:
        if message_id:
            download_message = await retry(
                _bot.client.get_messages, args=(chat_id, message_id)
            )
            if download_message:
                # Check if this message belongs to a media group
                if download_message.media_group_id:
                    logger.info(
                        f"[download_from_link] Detected media group: {download_message.media_group_id}"
                    )
                    # Get all messages in the media group
                    try:
                        media_group_messages = await _bot.client.get_media_group(
                            chat_id, message_id
                        )
                        logger.info(
                            f"[download_from_link] Found {len(media_group_messages)} messages in media group"
                        )

                        # Download each message in the group
                        for idx, group_msg in enumerate(media_group_messages):
                            logger.info(
                                f"[download_from_link] Downloading {idx+1}/{len(media_group_messages)}: msg_id={group_msg.id}"
                            )
                            await direct_download(_bot, entity.id, message, group_msg)
                    except Exception as e:
                        logger.warning(
                            f"[download_from_link] Failed to get media group: {e}, downloading single message"
                        )
                        await direct_download(
                            _bot, entity.id, message, download_message
                        )
                else:
                    # Single message, download directly
                    await direct_download(_bot, entity.id, message, download_message)
            else:
                await client.send_message(
                    message.from_user.id,
                    f"{_t('From')} {entity.title} {_t('download')} {message_id} {_t('error')}!",
                    reply_to_message_id=message.id,
                )
        return

    await client.send_message(
        message.from_user.id, msg, parse_mode=pyrogram.enums.ParseMode.HTML
    )


# pylint: disable = R0912, R0915,R0914


async def download_from_bot(client: pyrogram.Client, message: pyrogram.types.Message):
    """Download from bot"""

    msg = (
        f"{_t('Parameter error, please enter according to the reference format')}:\n\n"
        f"1. {_t('Download all messages of common group')}\n"
        "<i>/download https://t.me/fkdhlg 1 0</i>\n\n"
        f"{_t('The private group (channel) link is a random group message link')}\n\n"
        f"2. {_t('The download starts from the N message to the end of the M message')}. "
        f"{_t('When M is 0, it means the last message. The filter is optional')}\n"
        f"<i>/download https://t.me/12000000 N M [filter]</i>\n\n"
    )

    args = message.text.split(maxsplit=4)
    if not message.text or len(args) < 4:
        await client.send_message(
            message.from_user.id, msg, parse_mode=pyrogram.enums.ParseMode.HTML
        )
        return

    url = args[1]
    try:
        start_offset_id = int(args[2])
        end_offset_id = int(args[3])
    except Exception:
        await client.send_message(
            message.from_user.id, msg, parse_mode=pyrogram.enums.ParseMode.HTML
        )
        return

    limit = 0
    if end_offset_id:
        if end_offset_id < start_offset_id:
            raise ValueError(
                f"end_offset_id < start_offset_id, {end_offset_id} < {start_offset_id}"
            )

        limit = end_offset_id - start_offset_id + 1

    download_filter = args[4] if len(args) > 4 else None

    if download_filter:
        download_filter = replace_date_time(download_filter)
        res, err = _bot.filter.check_filter(download_filter)
        if not res:
            await client.send_message(
                message.from_user.id, err, reply_to_message_id=message.id
            )
            return
    try:
        chat_id, _, _ = await parse_link(_bot.client, url)
        if chat_id:
            entity = await _bot.client.get_chat(chat_id)
        if entity:
            chat_title = entity.title
            reply_message = f"from {chat_title} "
            chat_download_config = ChatDownloadConfig()
            chat_download_config.last_read_message_id = start_offset_id
            chat_download_config.download_filter = download_filter
            reply_message += (
                f"download message id = {start_offset_id} - {end_offset_id} !"
            )
            last_reply_message = await client.send_message(
                message.from_user.id, reply_message, reply_to_message_id=message.id
            )
            node = TaskNode(
                chat_id=entity.id,
                from_user_id=message.from_user.id,
                reply_message_id=last_reply_message.id,
                replay_message=reply_message,
                limit=limit,
                start_offset_id=start_offset_id,
                end_offset_id=end_offset_id,
                bot=_bot.bot,
                task_id=_bot.gen_task_id(),
            )
            _bot.add_task_node(node)
            _bot.app.loop.create_task(
                _bot.download_chat_task(_bot.client, chat_download_config, node)
            )
    except Exception as e:
        await client.send_message(
            message.from_user.id,
            f"{_t('chat input error, please enter the channel or group link')}\n\n"
            f"{_t('Error type')}: {e.__class__}"
            f"{_t('Exception message')}: {e}",
        )
        return


async def get_forward_task_node(
    client: pyrogram.Client,
    message: pyrogram.types.Message,
    task_type: TaskType,
    src_chat_link: str,
    dst_chat_link: str,
    offset_id: int = 0,
    end_offset_id: int = 0,
    download_filter: str = None,
    reply_comment: bool = False,
):
    """Get task node"""
    limit: int = 0

    if end_offset_id:
        if end_offset_id < offset_id:
            await client.send_message(
                message.from_user.id,
                f" end_offset_id({end_offset_id}) < start_offset_id({offset_id}),"
                f" end_offset_id{_t('must be greater than')} offset_id",
            )
            return None

        limit = end_offset_id - offset_id + 1

    src_chat_id, _, _ = await parse_link(_bot.client, src_chat_link)
    dst_chat_id, target_msg_id, topic_id = await parse_link(_bot.client, dst_chat_link)

    if not src_chat_id or not dst_chat_id:
        logger.info(f"{src_chat_id} {dst_chat_id}")
        await client.send_message(
            message.from_user.id,
            _t("Invalid chat link") + f"{src_chat_id} {dst_chat_id}",
            reply_to_message_id=message.id,
        )
        return None

    try:
        src_chat = await _bot.client.get_chat(src_chat_id)
        dst_chat = await _bot.client.get_chat(dst_chat_id)
    except Exception as e:
        await client.send_message(
            message.from_user.id,
            f"{_t('Invalid chat link')} {e}",
            reply_to_message_id=message.id,
        )
        logger.exception(f"get chat error: {e}")
        return None

    me = await client.get_me()
    if dst_chat.id == me.id:
        # TODO: when bot receive message judge if download
        await client.send_message(
            message.from_user.id,
            _t("Cannot be forwarded to this bot, will cause an infinite loop"),
            reply_to_message_id=message.id,
        )
        return None

    if download_filter:
        download_filter = replace_date_time(download_filter)
        res, err = _bot.filter.check_filter(download_filter)
        if not res:
            await client.send_message(
                message.from_user.id, err, reply_to_message_id=message.id
            )

    last_reply_message = await client.send_message(
        message.from_user.id,
        "Forwarding message, please wait...",
        reply_to_message_id=message.id,
    )

    node = TaskNode(
        chat_id=src_chat.id,
        from_user_id=message.from_user.id,
        upload_telegram_chat_id=dst_chat_id,
        reply_message_id=last_reply_message.id,
        replay_message=last_reply_message.text,
        has_protected_content=src_chat.has_protected_content,
        download_filter=download_filter,
        limit=limit,
        start_offset_id=offset_id,
        end_offset_id=end_offset_id,
        bot=_bot.bot,
        task_id=_bot.gen_task_id(),
        task_type=task_type,
        topic_id=topic_id,
    )

    if target_msg_id and reply_comment:
        node.reply_to_message = await _bot.client.get_discussion_message(
            dst_chat_id, target_msg_id
        )

    _bot.add_task_node(node)

    node.upload_user = _bot.client
    if not dst_chat.type is pyrogram.enums.ChatType.BOT:
        has_permission = await check_user_permission(_bot.client, me.id, dst_chat.id)
        if has_permission:
            node.upload_user = _bot.bot

    if node.upload_user is _bot.client:
        await client.edit_message_text(
            message.from_user.id,
            last_reply_message.id,
            "Note that the robot may not be in the target group,"
            " use the user account to forward",
        )

    return node


# pylint: disable = R0914
async def forward_message_impl(
    client: pyrogram.Client, message: pyrogram.types.Message, reply_comment: bool
):
    """
    Forward message
    """

    async def report_error(client: pyrogram.Client, message: pyrogram.types.Message):
        """Report error"""

        await client.send_message(
            message.from_user.id,
            f"{_t('Invalid command format')}."
            f"{_t('Please use')} "
            "/forward https://t.me/c/src_chat https://t.me/c/dst_chat "
            f"1 400 `[`{_t('Filter')}`]`\n",
        )

    args = message.text.split(maxsplit=5)
    if len(args) < 5:
        await report_error(client, message)
        return

    src_chat_link = args[1]
    dst_chat_link = args[2]

    try:
        offset_id = int(args[3])
        end_offset_id = int(args[4])
    except Exception:
        await report_error(client, message)
        return

    download_filter = args[5] if len(args) > 5 else None

    node = await get_forward_task_node(
        client,
        message,
        TaskType.Forward,
        src_chat_link,
        dst_chat_link,
        offset_id,
        end_offset_id,
        download_filter,
        reply_comment,
    )

    if not node:
        return

    if not node.has_protected_content:
        try:
            async for item in get_chat_history_v2(  # type: ignore
                _bot.client,
                node.chat_id,
                limit=node.limit,
                max_id=node.end_offset_id,
                offset_id=offset_id,
                reverse=True,
            ):
                await forward_normal_content(client, node, item)
                if node.is_stop_transmission:
                    await client.edit_message_text(
                        message.from_user.id,
                        node.reply_message_id,
                        f"{_t('Stop Forward')}",
                    )
                    break
        except Exception as e:
            await client.edit_message_text(
                message.from_user.id,
                node.reply_message_id,
                f"{_t('Error forwarding message')} {e}",
            )
        finally:
            await report_bot_status(client, node, immediate_reply=True)
            node.stop_transmission()
    else:
        await forward_msg(node, offset_id)


async def forward_messages(client: pyrogram.Client, message: pyrogram.types.Message):
    """
    Forwards messages from one chat to another.

    Parameters:
        client (pyrogram.Client): The pyrogram client.
        message (pyrogram.types.Message): The message containing the command.

    Returns:
        None
    """
    return await forward_message_impl(client, message, False)


async def forward_normal_content(
    client: pyrogram.Client, node: TaskNode, message: pyrogram.types.Message
):
    """Forward normal content"""
    forward_ret = ForwardStatus.FailedForward
    caption = message.caption
    if caption:
        caption = validate_title(caption)
        _bot.app.set_caption_name(node.chat_id, message.media_group_id, caption)
    else:
        caption = _bot.app.get_caption_name(node.chat_id, message.media_group_id)

    if caption and _bot.app.is_match_advertisement(caption):
        forward_ret = ForwardStatus.SkipForward
        if message.media_group_id:
            # TODO
            node.upload_status[message.id] = UploadStatus.SkipUpload
        return

    if node.download_filter:
        meta_data = MetaData()
        set_meta_data(meta_data, message, caption)
        _bot.filter.set_meta_data(meta_data)
        if not _bot.filter.exec(node.download_filter):
            forward_ret = ForwardStatus.SkipForward
            if message.media_group_id:
                node.upload_status[message.id] = UploadStatus.SkipUpload
                await proc_cache_forward(_bot.client, node, message, False, _bot.app)
            await report_bot_forward_status(client, node, forward_ret)
            return

    await upload_telegram_chat_message(
        _bot.client, node.upload_user, _bot.app, node, message
    )


async def forward_msg(node: TaskNode, message_id: int):
    """Forward normal message"""

    chat_download_config = ChatDownloadConfig()
    chat_download_config.last_read_message_id = message_id
    chat_download_config.download_filter = node.download_filter  # type: ignore

    await _bot.download_chat_task(_bot.client, chat_download_config, node)


async def check_new_messages(
    client: pyrogram.Client, chat_id: int, node: TaskNode, last_message_id: int = 0
):
    """
    Checks for new messages in the chat and forwards them.

    Parameters:
        client (pyrogram.Client): The pyrogram client
        chat_id (int): The chat ID to monitor
        node (TaskNode): The task node containing forwarding configuration
        last_message_id (int): The ID of the last processed message
    """
    try:
        # Only get the most recent message if last_message_id is 0
        if last_message_id == 0:
            async for message in get_chat_history_v2(  # type: ignore
                client, chat_id, limit=1  # Get only the latest message
            ):
                last_message_id = message.id
                return last_message_id

        # Otherwise check for new messages after last_message_id
        async for message in get_chat_history_v2(  # type: ignore
            client, chat_id, limit=100, offset_id=last_message_id, reverse=True
        ):
            if message.id > last_message_id:
                if not node.has_protected_content:
                    await forward_normal_content(client, node, message)
                    await report_bot_status(client, node, immediate_reply=True)
                else:
                    await _bot.add_download_task(message, node)
                last_message_id = message.id
    except Exception as e:
        logger.exception(f"Error checking new messages in chat {chat_id}: {e}")

    return last_message_id


async def start_message_monitor():
    """
    Starts monitoring all chats that need to be forwarded.
    Runs every 60 seconds to check for new messages.
    """
    last_message_ids = {}  # 存储每个聊天的最后处理的消息ID

    while _bot.is_running:
        try:
            for chat_id, node in _bot.listen_forward_chat.items():
                if not node.is_running:
                    continue

                last_id = last_message_ids.get(chat_id, 0)
                new_last_id = await check_new_messages(
                    _bot.client, chat_id, node, last_id
                )
                last_message_ids[chat_id] = new_last_id

        except Exception as e:
            logger.exception(f"Error in message monitor: {e}")

        await asyncio.sleep(60)  # 每60秒检查一次


async def set_listen_forward_msg(
    client: pyrogram.Client, message: pyrogram.types.Message
):
    """
    Set the chat to listen for forwarded messages.
    """
    args = message.text.split(maxsplit=3)

    if len(args) < 3:
        await client.send_message(
            message.from_user.id,
            f"{_t('Invalid command format')}. {_t('Please use')} /listen_forward "
            f"https://t.me/c/src_chat https://t.me/c/dst_chat [{_t('Filter')}]\n",
        )
        return

    src_chat_link = args[1]
    dst_chat_link = args[2]
    download_filter = args[3] if len(args) > 3 else None

    node = await get_forward_task_node(
        client,
        message,
        TaskType.ListenForward,
        src_chat_link,
        dst_chat_link,
        download_filter=download_filter,
    )

    if not node:
        return

    if node.chat_id in _bot.listen_forward_chat:
        _bot.remove_task_node(_bot.listen_forward_chat[node.chat_id].task_id)

    node.is_running = True
    _bot.listen_forward_chat[node.chat_id] = node

    if not hasattr(_bot, "monitor_task") or _bot.monitor_task is None:
        _bot.monitor_task = _bot.app.loop.create_task(start_message_monitor())


async def stop(client: pyrogram.Client, message: pyrogram.types.Message):
    """Stops listening for forwarded messages."""

    await client.send_message(
        message.chat.id,
        _t("Please select:"),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        _t("Stop Download"), callback_data="stop_download"
                    ),
                    InlineKeyboardButton(
                        _t("Stop Forward"), callback_data="stop_forward"
                    ),
                ],
                [  # Second row
                    InlineKeyboardButton(
                        _t("Stop Listen Forward"), callback_data="stop_listen_forward"
                    )
                ],
            ]
        ),
    )


async def stop_task(
    client: pyrogram.Client,
    query: pyrogram.types.CallbackQuery,
    queryHandler: str,
    task_type: TaskType,
):
    """Stop task"""
    if query.data == queryHandler:
        buttons: List[InlineKeyboardButton] = []
        temp_buttons: List[InlineKeyboardButton] = []
        for key, value in _bot.task_node.copy().items():
            if not value.is_finish() and value.task_type is task_type:
                if len(temp_buttons) == 3:
                    buttons.append(temp_buttons)
                    temp_buttons = []
                temp_buttons.append(
                    InlineKeyboardButton(
                        f"{key}", callback_data=f"{queryHandler} task {key}"
                    )
                )
        if temp_buttons:
            buttons.append(temp_buttons)

        if buttons:
            buttons.insert(
                0,
                [
                    InlineKeyboardButton(
                        _t("all"), callback_data=f"{queryHandler} task all"
                    )
                ],
            )
            await client.edit_message_text(
                query.message.from_user.id,
                query.message.id,
                f"{_t('Stop')} {_t(task_type.name)}...",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        else:
            await client.edit_message_text(
                query.message.from_user.id,
                query.message.id,
                f"{_t('No Task')}",
            )
    else:
        task_id = query.data.split(" ")[2]
        await client.edit_message_text(
            query.message.from_user.id,
            query.message.id,
            f"{_t('Stop')} {_t(task_type.name)}...",
        )
        _bot.stop_task(task_id)


async def on_query_handler(
    client: pyrogram.Client, query: pyrogram.types.CallbackQuery
):
    """
    Asynchronous function that handles query callbacks.

    Parameters:
        client (pyrogram.Client): The Pyrogram client object.
        query (pyrogram.types.CallbackQuery): The callback query object.

    Returns:
        None
    """

    for it in QueryHandler:
        queryHandler = QueryHandlerStr.get_str(it.value)
        if queryHandler in query.data:
            await stop_task(client, query, queryHandler, TaskType(it.value))


async def forward_to_comments(client: pyrogram.Client, message: pyrogram.types.Message):
    """
    Forwards specified media to a designated comment section.

    Usage: /forward_to_comments <source_chat_link> <destination_chat_link> <msg_start_id> <msg_end_id>

    Parameters:
        client (pyrogram.Client): The pyrogram client.
        message (pyrogram.types.Message): The message containing the command.
    """
    return await forward_message_impl(client, message, True)
