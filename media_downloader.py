"""Downloads media from telegram."""

import asyncio
import copy
import inspect
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union

import pyrogram
from loguru import logger
from pyrogram.types import Audio, Document, Photo, Video, VideoNote, Voice
from rich.logging import RichHandler

from module.app import Application, ChatDownloadConfig, DownloadStatus, TaskNode
from module.bot import start_download_bot, stop_download_bot
from module.cloud_drive import CloudDrive
from module.db import db
from module.download_stat import (
    add_pending_download,
    get_pending_downloads,
    remove_pending_download,
    update_download_status,
    verify_and_save_download,
)
from module.get_chat_history_v2 import get_chat_history_v2
from module.language import _t
from module.profiles import (
    get_active_profile,
    get_profiles,
    save_active_profile,
    sync_active_profile_to_legacy,
    update_profile,
)
from module.pyrogram_extension import (
    HookClient,
    fetch_message,
    get_extension,
    record_download_status,
    report_bot_download_status,
    set_max_concurrent_transmissions,
    set_meta_data,
    update_cloud_upload_stat,
    update_upload_stat,
    upload_telegram_chat,
)
from module.web import init_web
from utils.format import truncate_filename, validate_title
from utils.log import LogFilter
from utils.meta import print_meta
from utils.meta_data import MetaData

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler()],
)

CONFIG_NAME = "config.yaml"
DATA_FILE_NAME = "data.yaml"
APPLICATION_NAME = "media_downloader"
app = Application(CONFIG_NAME, DATA_FILE_NAME, APPLICATION_NAME)

queue: asyncio.Queue = asyncio.Queue()
RETRY_TIME_OUT = 3

logging.getLogger("pyrogram.session.session").addFilter(LogFilter())
logging.getLogger("pyrogram.client").addFilter(LogFilter())

logging.getLogger("pyrogram").setLevel(logging.WARNING)


def _check_download_finish(media_size: int, download_path: str, ui_file_name: str):
    """Check download task if finish

    Parameters
    ----------
    media_size: int
        The size of the downloaded resource
    download_path: str
        Resource download hold path
    ui_file_name: str
        Really show file name

    """
    download_size = os.path.getsize(download_path)
    if media_size == download_size:
        logger.success(f"{_t('Successfully downloaded')} - {ui_file_name}")
    else:
        logger.warning(
            f"{_t('Media downloaded with wrong size')}: "
            f"{download_size}, {_t('actual')}: "
            f"{media_size}, {_t('file name')}: {ui_file_name}"
        )
        os.remove(download_path)
        raise pyrogram.errors.exceptions.bad_request_400.BadRequest()


def _move_to_download_path(temp_download_path: str, download_path: str):
    """Move file to download path

    Parameters
    ----------
    temp_download_path: str
        Temporary download path

    download_path: str
        Download path

    """

    directory, _ = os.path.split(download_path)
    os.makedirs(directory, exist_ok=True)
    shutil.move(temp_download_path, download_path)


def _check_timeout(retry: int, _: int):
    """Check if message download timeout, then add message id into failed_ids

    Parameters
    ----------
    retry: int
        Retry download message times

    message_id: int
        Try to download message 's id

    """
    if retry == 2:
        return True
    return False


def _can_download(_type: str, file_formats: dict, file_format: Optional[str]) -> bool:
    """
    Check if the given file format can be downloaded.

    Parameters
    ----------
    _type: str
        Type of media object.
    file_formats: dict
        Dictionary containing the list of file_formats
        to be downloaded for `audio`, `document` & `video`
        media types
    file_format: str
        Format of the current file to be downloaded.

    Returns
    -------
    bool
        True if the file format can be downloaded else False.
    """
    if _type in ["audio", "document", "video"]:
        allowed_formats: list = file_formats[_type]
        if not file_format in allowed_formats and allowed_formats[0] != "all":
            return False
    return True


def _is_exist(file_path: str) -> bool:
    """
    Check if a file exists and it is not a directory.

    Parameters
    ----------
    file_path: str
        Absolute path of the file to be checked.

    Returns
    -------
    bool
        True if the file exists else False.
    """
    return not os.path.isdir(file_path) and os.path.exists(file_path)


# pylint: disable = R0912


async def _get_media_meta(
    chat_id: Union[int, str],
    message: pyrogram.types.Message,
    media_obj: Union[Audio, Document, Photo, Video, VideoNote, Voice],
    _type: str,
    runtime_app: Application = app,
) -> Tuple[str, str, Optional[str]]:
    """Extract file name and file id from media object.

    Parameters
    ----------
    media_obj: Union[Audio, Document, Photo, Video, VideoNote, Voice]
        Media object to be extracted.
    _type: str
        Type of media object.

    Returns
    -------
    Tuple[str, str, Optional[str]]
        file_name, file_format
    """
    if _type in ["audio", "document", "video"]:
        # pylint: disable = C0301
        file_format: Optional[str] = media_obj.mime_type.split("/")[-1]  # type: ignore
    else:
        file_format = None

    file_name = None
    temp_file_name = None
    dirname = validate_title(f"{chat_id}")
    if message.chat and message.chat.title:
        dirname = validate_title(f"{message.chat.title}")

    if message.date:
        datetime_dir_name = message.date.strftime(runtime_app.date_format)
    else:
        datetime_dir_name = "0"

    if _type in ["voice", "video_note"]:
        # pylint: disable = C0209
        file_format = media_obj.mime_type.split("/")[-1]  # type: ignore
        file_save_path = runtime_app.get_file_save_path(
            _type, dirname, datetime_dir_name
        )
        file_name = "{} - {}_{}.{}".format(
            message.id,
            _type,
            media_obj.date.isoformat(),  # type: ignore
            file_format,
        )
        file_name = validate_title(file_name)
        temp_file_name = os.path.join(runtime_app.temp_save_path, dirname, file_name)

        file_name = os.path.join(file_save_path, file_name)
    else:
        file_name = getattr(media_obj, "file_name", None)
        caption = getattr(message, "caption", None)

        file_name_suffix = ".unknown"
        if not file_name:
            file_name_suffix = get_extension(
                media_obj.file_id, getattr(media_obj, "mime_type", "")
            )
        else:
            # file_name = file_name.split(".")[0]
            _, file_name_without_suffix = os.path.split(os.path.normpath(file_name))
            file_name, file_name_suffix = os.path.splitext(file_name_without_suffix)
            if not file_name_suffix:
                file_name_suffix = get_extension(
                    media_obj.file_id, getattr(media_obj, "mime_type", "")
                )

        if caption:
            caption = validate_title(caption)
            runtime_app.set_caption_name(chat_id, message.media_group_id, caption)
            runtime_app.set_caption_entities(
                chat_id, message.media_group_id, message.caption_entities
            )
        else:
            caption = runtime_app.get_caption_name(chat_id, message.media_group_id)

        if not file_name and message.photo:
            file_name = f"{message.photo.file_unique_id}"

        gen_file_name = (
            runtime_app.get_file_name(message.id, file_name, caption)
            + file_name_suffix
        )

        file_save_path = runtime_app.get_file_save_path(
            _type, dirname, datetime_dir_name
        )

        temp_file_name = os.path.join(runtime_app.temp_save_path, dirname, gen_file_name)

        file_name = os.path.join(file_save_path, gen_file_name)
    return truncate_filename(file_name), truncate_filename(temp_file_name), file_format


async def add_download_task(
    message: pyrogram.types.Message,
    node: TaskNode,
    runtime_queue: asyncio.Queue = queue,
):
    """Add Download task"""
    if message.empty:
        return False
    node.download_status[message.id] = DownloadStatus.Downloading
    await runtime_queue.put((message, node))
    node.total_task += 1
    return True


async def save_msg_to_file(
    app, chat_id: Union[int, str], message: pyrogram.types.Message
):
    """Write message text into file"""
    dirname = validate_title(
        message.chat.title if message.chat and message.chat.title else str(chat_id)
    )
    datetime_dir_name = message.date.strftime(app.date_format) if message.date else "0"

    file_save_path = app.get_file_save_path("msg", dirname, datetime_dir_name)
    file_name = os.path.join(
        app.temp_save_path,
        file_save_path,
        f"{app.get_file_name(message.id, None, None)}.txt",
    )

    os.makedirs(os.path.dirname(file_name), exist_ok=True)

    if _is_exist(file_name):
        return DownloadStatus.SkipDownload, None

    with open(file_name, "w", encoding="utf-8") as f:
        f.write(message.text or "")

    return DownloadStatus.SuccessDownload, file_name


async def download_task(
    client: pyrogram.Client,
    message: pyrogram.types.Message,
    node: TaskNode,
    runtime_app: Application = app,
):
    """Download and Forward media"""

    download_status, file_name = await download_media(
        client, message, runtime_app.media_types, runtime_app.file_formats, node, runtime_app
    )

    if runtime_app.enable_download_txt and message.text and not message.media:
        download_status, file_name = await save_msg_to_file(
            runtime_app, node.chat_id, message
        )

    if not node.bot:
        runtime_app.set_download_id(node, message.id, download_status)

    node.download_status[message.id] = download_status

    # Skip file size check for WebDAV streaming (file doesn't exist locally)
    if (
        runtime_app.cloud_drive_config.upload_adapter == "webdav"
        and download_status == DownloadStatus.SuccessDownload
    ):
        file_size = 0  # File was streamed directly, not saved locally
    else:
        file_size = (
            os.path.getsize(file_name) if file_name and os.path.exists(file_name) else 0
        )

    await upload_telegram_chat(
        client,
        node.upload_user if node.upload_user else client,
        runtime_app,
        node,
        message,
        download_status,
        file_name,
    )

    # rclone upload
    if (
        not node.upload_telegram_chat_id
        and download_status is DownloadStatus.SuccessDownload
    ):
        ui_file_name = file_name
        if runtime_app.hide_file_name:
            ui_file_name = f"****{os.path.splitext(file_name)[-1]}"
        if await runtime_app.upload_file(
            file_name, update_cloud_upload_stat, (node, message.id, ui_file_name)
        ):
            node.upload_success_count += 1

    await report_bot_download_status(
        node.bot,
        node,
        download_status,
        file_size,
    )


# pylint: disable = R0915,R0914


@record_download_status
async def download_media(
    client: pyrogram.client.Client,
    message: pyrogram.types.Message,
    media_types: List[str],
    file_formats: dict,
    node: TaskNode,
    runtime_app: Application = app,
):
    """
    Download media from Telegram.

    Each of the files to download are retried 3 times with a
    delay of 5 seconds each.

    Parameters
    ----------
    client: pyrogram.client.Client
        Client to interact with Telegram APIs.
    message: pyrogram.types.Message
        Message object retrieved from telegram.
    media_types: list
        List of strings of media types to be downloaded.
        Ex : `["audio", "photo"]`
        Supported formats:
            * audio
            * document
            * photo
            * video
            * voice
    file_formats: dict
        Dictionary containing the list of file_formats
        to be downloaded for `audio`, `document` & `video`
        media types.

    Returns
    -------
    int
        Current message id.
    """

    # pylint: disable = R0912

    file_name: str = ""
    ui_file_name: str = ""
    task_start_time: float = time.time()
    media_size = 0
    _media = None
    message = await fetch_message(client, message)
    try:
        for _type in media_types:
            _media = getattr(message, _type, None)
            if _media is None:
                continue
            file_name, temp_file_name, file_format = await _get_media_meta(
                node.chat_id, message, _media, _type, runtime_app
            )
            media_size = getattr(_media, "file_size", 0)

            ui_file_name = file_name
            if runtime_app.hide_file_name:
                ui_file_name = f"****{os.path.splitext(file_name)[-1]}"

            if _can_download(_type, file_formats, file_format):
                if _is_exist(file_name):
                    file_size = os.path.getsize(file_name)
                    if file_size or file_size == media_size:
                        logger.info(
                            f"id={message.id} {ui_file_name} "
                            f"{_t('already download,download skipped')}.\n"
                        )

                        # Return Success so that upload logic can proceed if needed
                        return DownloadStatus.SuccessDownload, file_name
            else:
                return DownloadStatus.SkipDownload, None

            break
    except Exception as e:
        logger.error(
            f"Message[{message.id}]: "
            f"{_t('could not be downloaded due to following exception')}:\n[{e}].",
            exc_info=True,
        )
        return DownloadStatus.FailedDownload, None
    if _media is None:
        return DownloadStatus.SkipDownload, None

    message_id = message.id

    # Register as pending download for resume on restart
    add_pending_download(node.chat_id, message_id, ui_file_name, node.profile_id)

    for retry in range(3):

        try:
            # Check if using WebDAV for streaming
            if runtime_app.cloud_drive_config.upload_adapter == "webdav":
                logger.info(f"Starting streaming upload to WebDAV: {ui_file_name}")

                # Use pyrogram's stream_media which returns an async generator
                stream_generator = client.stream_media(message, limit=0, offset=0)

                success = await CloudDrive.webdav_upload_stream(
                    runtime_app.cloud_drive_config,
                    runtime_app.save_path,
                    file_name,  # Relative path handled inside
                    stream_generator,
                    media_size,
                    progress_callback=update_upload_stat,
                    progress_args=(
                        message_id,
                        ui_file_name,
                        task_start_time,
                        node,
                        client,
                        True,
                    ),
                )

                if success:
                    # Mock successful download path to satisfy later logic, though file doesn't exist locally
                    # We might need to adjust logic later if it checks for file existence
                    # For now, we trick it by returning a dummy path if successful
                    temp_download_path = "STREAMED_TO_WEBDAV"
                    # Mark as success with proper file info
                    verify_and_save_download(
                        node.chat_id,
                        message.id,
                        ui_file_name,
                        media_size,
                        node.task_id,
                        node.profile_id,
                    )
                    # CRITICAL: Remove from pending downloads to prevent re-download on restart
                    remove_pending_download(node.chat_id, message.id, node.profile_id)
                    return DownloadStatus.SuccessDownload, file_name
                else:
                    raise Exception("WebDAV stream upload failed")
            else:
                # Standard Download
                temp_download_path = await client.download_media(
                    message,
                    file_name=temp_file_name,
                    progress=update_download_status,
                    progress_args=(
                        message_id,
                        ui_file_name,
                        task_start_time,
                        node,
                        client,
                    ),
                )

            # Success handling for standard download (outside try block but inside for loop)
            if temp_download_path:
                if temp_download_path != "STREAMED_TO_WEBDAV":
                    if isinstance(temp_download_path, str):
                        _check_download_finish(
                            media_size, temp_download_path, ui_file_name
                        )

                        # Verify and persist completion to DB
                        verify_and_save_download(
                            node.chat_id,
                            message.id,
                            ui_file_name,
                            media_size,
                            node.task_id,
                            node.profile_id,
                        )

                        await asyncio.sleep(0.5)
                        _move_to_download_path(temp_download_path, file_name)
                else:
                    # Logic for streamed content - already handled above
                    pass

                # Remove from pending downloads (completed successfully)
                remove_pending_download(node.chat_id, message.id, node.profile_id)
                return DownloadStatus.SuccessDownload, file_name

        except pyrogram.errors.exceptions.bad_request_400.BadRequest:
            logger.warning(
                f"Message[{message.id}]: {_t('file reference expired, refetching')}..."
            )
            await asyncio.sleep(RETRY_TIME_OUT)
            message = await fetch_message(client, message)
            if _check_timeout(retry, message.id):
                # pylint: disable = C0301
                logger.error(
                    f"Message[{message.id}]: "
                    f"{_t('file reference expired for 3 retries, download skipped.')}"
                )
        except pyrogram.errors.exceptions.flood_420.FloodWait as wait_err:
            await asyncio.sleep(wait_err.value)
            logger.warning("Message[{}]: FlowWait {}", message.id, wait_err.value)
            _check_timeout(retry, message.id)
        except TypeError:
            # pylint: disable = C0301
            logger.warning(
                f"{_t('Timeout Error occurred when downloading Message')}[{message.id}], "
                f"{_t('retrying after')} {RETRY_TIME_OUT} {_t('seconds')}"
            )
            await asyncio.sleep(RETRY_TIME_OUT)
            if _check_timeout(retry, message.id):
                logger.error(
                    f"Message[{message.id}]: {_t('Timing out after 3 reties, download skipped.')}"
                )
        except Exception as e:
            # pylint: disable = C0301
            logger.error(
                f"Message[{message.id}]: "
                f"{_t('could not be downloaded due to following exception')}:\n[{e}].",
                exc_info=True,
            )
            break

    return DownloadStatus.FailedDownload, None


def _load_config():
    """Load config"""
    app.load_config()


def _check_config() -> bool:
    """Check config"""
    print_meta(logger)
    try:
        _load_config()
        logger.add(
            os.path.join(app.log_file_path, "tdl.log"),
            rotation="10 MB",
            retention="10 days",
            level=app.log_level,
        )
    except Exception as e:
        logger.exception(f"load config error: {e}")
        return False

    return True


async def worker(
    client: pyrogram.client.Client,
    runtime_app: Application = app,
    runtime_queue: asyncio.Queue = queue,
):
    """Work for download task"""
    while runtime_app.is_running:
        try:
            item = await runtime_queue.get()
            message = item[0]
            node: TaskNode = item[1]

            if node.is_stop_transmission:
                continue

            if node.client:
                await download_task(node.client, message, node, runtime_app)
            else:
                await download_task(client, message, node, runtime_app)
        except Exception as e:
            logger.exception(f"{e}")


async def download_chat_task(
    client: pyrogram.Client,
    chat_download_config: ChatDownloadConfig,
    node: TaskNode,
    runtime_app: Application = app,
    runtime_queue: asyncio.Queue = queue,
):
    """Download all task"""
    messages_iter = get_chat_history_v2(
        client,
        node.chat_id,
        limit=node.limit,
        max_id=node.end_offset_id,
        offset_id=chat_download_config.last_read_message_id,
        reverse=True,
    )

    chat_download_config.node = node

    if chat_download_config.ids_to_retry:
        logger.info(f"{_t('Downloading files failed during last run')}...")
        skipped_messages: list = await client.get_messages(  # type: ignore
            chat_id=node.chat_id, message_ids=chat_download_config.ids_to_retry
        )

        for message in skipped_messages:
            await add_download_task(message, node, runtime_queue)

    async for message in messages_iter:  # type: ignore
        meta_data = MetaData()

        caption = message.caption
        if caption:
            caption = validate_title(caption)
            runtime_app.set_caption_name(node.chat_id, message.media_group_id, caption)
            runtime_app.set_caption_entities(
                node.chat_id, message.media_group_id, message.caption_entities
            )
        else:
            caption = runtime_app.get_caption_name(node.chat_id, message.media_group_id)
        set_meta_data(meta_data, message, caption)

        if runtime_app.need_skip_message(chat_download_config, message.id):
            continue

        if runtime_app.exec_filter(chat_download_config, meta_data):
            await add_download_task(message, node, runtime_queue)
        else:
            node.download_status[message.id] = DownloadStatus.SkipDownload
            if message.media_group_id:
                await upload_telegram_chat(
                    client,
                    node.upload_user,
                    runtime_app,
                    node,
                    message,
                    DownloadStatus.SkipDownload,
                )

    chat_download_config.need_check = True
    chat_download_config.total_task = node.total_task
    node.is_running = True


async def download_all_chat(
    client: pyrogram.Client,
    runtime_app: Application = app,
    runtime_queue: asyncio.Queue = queue,
    profile_id: str = None,
):
    """Download All chat"""

    # Pre-load dialogs to cache Access Hashes for peers
    # This fixes PEER_ID_INVALID errors for newly added chat_ids
    logger.info("Refreshing dialogs/peers cache to fix PEER_ID_INVALID...")
    try:
        dialog_count = 0
        # Fetch recent dialogs (limit 200 to be fast but cover active chats)
        async for dialog in client.get_dialogs(limit=200):
            dialog_count += 1
        logger.success(f"Successfully cached {dialog_count} dialogs.")
    except Exception as e:
        logger.warning(f"Failed to refresh dialogs cache: {e}")

    # Resume pending downloads from previous session
    pending = get_pending_downloads(profile_id)
    if pending:
        logger.info(
            f"Resuming {len(pending)} pending downloads from previous session..."
        )
        for item in pending:
            chat_id = item.get("chat_id")
            message_id = item.get("message_id")
            file_name = item.get("file_name", "unknown")
            if chat_id and message_id:
                try:
                    logger.info(
                        f"Resuming download: chat={chat_id}, msg={message_id}, file={file_name}"
                    )
                    # Fetch the message
                    message = await client.get_messages(chat_id, message_id)
                    if message and not message.empty:
                        # Create a task node for this download
                        node = TaskNode(chat_id=chat_id, profile_id=profile_id)
                        await runtime_queue.put((message, node))
                        logger.success(f"Queued for resume: msg={message_id}")
                    else:
                        logger.warning(
                            f"Message {message_id} no longer exists, removing from pending"
                        )
                        remove_pending_download(chat_id, message_id, profile_id)
                except Exception as e:
                    logger.error(f"Failed to resume download {message_id}: {e}")
                    # Remove failed items to avoid infinite retry
                    remove_pending_download(chat_id, message_id, profile_id)

    for key, value in runtime_app.chat_download_config.items():
        value.node = TaskNode(chat_id=key, profile_id=profile_id)
        try:
            await download_chat_task(
                client, value, value.node, runtime_app, runtime_queue
            )
        except Exception as e:
            logger.warning(f"Download {key} error: {e}")
        finally:
            value.need_check = True


async def run_until_all_task_finish():
    """Normal download"""
    tick = 0
    idle_logged = False
    while True:
        finish: bool = True
        for _, value in app.chat_download_config.items():
            if not value.need_check or value.total_task != value.finish_task:
                finish = False

        if app.restart_program:
            break

        if not app.bot_token and finish and not idle_logged:
            logger.info("All download tasks are finished. Keeping Web UI alive.")
            idle_logged = True
        elif not finish:
            idle_logged = False

        await asyncio.sleep(1)

        # Periodic auto-save every 10 seconds
        tick += 1
        if tick % 10 == 0:
            app.update_config()


def _exec_loop():
    """Exec loop"""

    app.loop.run_until_complete(run_until_all_task_finish())


async def start_server(client: pyrogram.Client):
    """
    Start the server using the provided client.
    """
    await client.start()


async def stop_server(client: pyrogram.Client):
    """
    Stop the server using the provided client.
    """
    await client.stop()


@dataclass
class ProfileRuntime:
    """Runtime state for one Telegram account profile."""

    profile_id: str
    profile_name: str
    app: Application
    client: pyrogram.Client
    queue: asyncio.Queue
    tasks: list = field(default_factory=list)
    running: bool = False
    status: str = "starting"
    message: str = ""
    bot_started: bool = False
    started_at: float = field(default_factory=time.time)


def _clean_telegram_api_value(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _effective_bot_token(config: dict = None, runtime_app: Application = None) -> str:
    """Return the bot token that Application.assign_config will actually use."""
    env_bot_token = _clean_telegram_api_value(os.getenv("BOT_TOKEN", ""))
    if env_bot_token:
        return env_bot_token
    if runtime_app is not None:
        return _clean_telegram_api_value(getattr(runtime_app, "bot_token", ""))
    return _clean_telegram_api_value((config or {}).get("bot_token", ""))


def _build_runtime_app(profile: dict) -> Application:
    """Build an isolated Application object for a profile."""
    runtime_app = Application(CONFIG_NAME, DATA_FILE_NAME, APPLICATION_NAME)
    created_loop = runtime_app.loop
    if created_loop is not app.loop and not created_loop.is_closed():
        created_loop.close()
    runtime_app.loop = app.loop
    asyncio.set_event_loop(app.loop)
    runtime_app.profile_id = profile.get("id")
    runtime_app.config = copy.deepcopy(profile.get("config") or {})
    runtime_app.app_data = copy.deepcopy(profile.get("app_data") or {})
    runtime_app.assign_config(runtime_app.config)
    runtime_app.assign_app_data(runtime_app.app_data)
    return runtime_app


def _create_client_for_runtime(profile: dict, runtime_app: Application):
    """Create a Telegram client for a profile's saved session."""
    session_string = profile.get("session")
    api_id = _clean_telegram_api_value(runtime_app.api_id)
    api_hash = _clean_telegram_api_value(runtime_app.api_hash)
    if not session_string:
        raise RuntimeError("Profile has no saved Telegram session.")
    if not api_id or not api_hash:
        raise RuntimeError("Profile api_id or api_hash is missing.")

    runtime_client = HookClient(
        f"media_downloader_{profile.get('id')}",
        api_id=int(api_id),
        api_hash=api_hash,
        proxy=runtime_app.proxy,
        workdir=runtime_app.session_file_path,
        start_timeout=runtime_app.start_timeout,
        session_string=session_string,
        in_memory=False,
        no_updates=True,
    )
    runtime_client.loop = app.loop
    return runtime_client


def main():
    """Main function of the downloader."""
    runtimes: dict[str, ProfileRuntime] = {}
    bot_owner_profile_id = None

    if db.conn:
        try:
            active_profile = sync_active_profile_to_legacy()
            active_config = active_profile.get("config") or {}
            active_app_data = active_profile.get("app_data") or {}
            app.profile_id = active_profile.get("id")
            if active_config:
                app.chat_download_config = {}
                app._chat_id = ""
                app.config = active_config
                app.assign_config(active_config)
            if active_app_data:
                app.app_data = active_app_data
                app.assign_app_data(active_app_data)
            logger.info(
                f"Active profile loaded: {active_profile.get('name')} "
                f"({active_profile.get('id')})"
            )
        except Exception as e:
            logger.warning(f"Failed to sync active profile on startup: {e}")

    def restart_callback():
        logger.warning("Restarting application via Web UI request...")
        app.is_running = False
        try:
            app.loop.stop()
        except Exception:
            pass

    async def ensure_client_runtime_ready(runtime_client: pyrogram.Client):
        """Bring a Web-authenticated client to the same ready state as client.start()."""
        if not runtime_client.is_connected:
            await start_server(runtime_client)
            return

        if not getattr(runtime_client, "me", None):
            await runtime_client.invoke(pyrogram.raw.functions.updates.GetState())
            runtime_client.me = await runtime_client.get_me()

        if not getattr(runtime_client, "is_initialized", False):
            await runtime_client.initialize()

    def get_profile_by_id(profile_id: str) -> dict:
        for item in get_profiles():
            if item.get("id") == profile_id:
                return item
        raise KeyError(f"Profile {profile_id} not found")

    async def runtime_maintenance(state: ProfileRuntime):
        tick = 0
        while state.app.is_running:
            await asyncio.sleep(1)
            tick += 1
            if tick % 10 == 0:
                state.app.update_config()

    def _runtime_download_payload(state: ProfileRuntime, matched_submitter: bool):
        async def enqueue_download_task(message, node):
            node.profile_id = state.profile_id
            return await add_download_task(message, node, state.queue)

        return {
            "client": state.client,
            "add_download_task": enqueue_download_task,
            "profile_id": state.profile_id,
            "profile_name": state.profile_name,
            "matched_submitter": matched_submitter,
        }

    def resolve_bot_download_runtime(submitter_user_id: str):
        """Prefer the Telegram account session that matches the bot submitter."""

        submitter_user_id = str(submitter_user_id or "")
        for state in runtimes.values():
            me = getattr(state.client, "me", None)
            if (
                state.running
                and me
                and submitter_user_id
                and str(getattr(me, "id", "")) == submitter_user_id
            ):
                return _runtime_download_payload(state, matched_submitter=True)

        owner_state = runtimes.get(bot_owner_profile_id) if bot_owner_profile_id else None
        if owner_state and owner_state.running:
            return _runtime_download_payload(owner_state, matched_submitter=False)
        return None

    async def activate_runtime(runtime_client: pyrogram.Client = None, profile=None):
        """Start one profile runtime without stopping other running profiles."""
        nonlocal bot_owner_profile_id
        profile = profile or get_active_profile()
        profile_id = profile.get("id")
        profile_name = profile.get("name") or profile_id
        current = runtimes.get(profile_id)
        if current and current.running:
            return {
                "status": "already_running",
                "message": f"{profile_name} 已在运行。",
                "profile_id": profile_id,
            }

        runtime_app = _build_runtime_app(profile)
        runtime_queue = asyncio.Queue()
        if runtime_client is None:
            runtime_client = _create_client_for_runtime(profile, runtime_app)

        state = ProfileRuntime(
            profile_id=profile_id,
            profile_name=profile_name,
            app=runtime_app,
            client=runtime_client,
            queue=runtime_queue,
        )
        runtimes[profile_id] = state

        async def runtime_add_download_task(message, node):
            node.profile_id = profile_id
            return await add_download_task(message, node, runtime_queue)

        async def runtime_download_chat_task(client_arg, chat_config, node):
            node.profile_id = profile_id
            return await download_chat_task(
                client_arg, chat_config, node, runtime_app, runtime_queue
            )

        try:
            await ensure_client_runtime_ready(runtime_client)
            set_max_concurrent_transmissions(
                runtime_client, runtime_app.max_concurrent_transmissions
            )
            try:
                session_string = runtime_client.export_session_string()
                if inspect.isawaitable(session_string):
                    session_string = await session_string
                update_profile(
                    profile_id,
                    session=session_string,
                    runtime_enabled=True,
                )
                logger.info(f"Telegram session saved for profile {profile_id}.")
            except Exception as e:
                logger.warning(f"Failed to export/save session string: {e}")

            runtime_app.is_running = True
            state.tasks.append(
                app.loop.create_task(
                    download_all_chat(
                        runtime_client, runtime_app, runtime_queue, profile_id
                    )
                )
            )
            for _ in range(runtime_app.max_download_task):
                state.tasks.append(
                    app.loop.create_task(
                        worker(runtime_client, runtime_app, runtime_queue)
                    )
                )
            state.tasks.append(app.loop.create_task(runtime_maintenance(state)))

            if runtime_app.bot_token:
                if bot_owner_profile_id and bot_owner_profile_id != profile_id:
                    owner_state = runtimes.get(bot_owner_profile_id)
                    owner_bot_token = _effective_bot_token(
                        runtime_app=owner_state.app
                    ) if owner_state else ""
                    runtime_bot_token = _effective_bot_token(runtime_app=runtime_app)
                    if (
                        owner_state
                        and owner_bot_token
                        and owner_bot_token == runtime_bot_token
                    ):
                        apply_bot_access_config(owner_state.app, runtime_app.config)
                        logger.info(
                            "Bot access config synced from profile {} to bot owner {}.",
                            profile_id,
                            bot_owner_profile_id,
                        )
                    state.message = (
                        "后台下载任务已启动；Bot 已由其它账号运行，当前账号跳过 Bot。"
                    )
                else:
                    try:
                        await start_download_bot(
                            runtime_app,
                            runtime_client,
                            runtime_add_download_task,
                            runtime_download_chat_task,
                            resolve_bot_download_runtime,
                        )
                        state.bot_started = True
                        bot_owner_profile_id = profile_id
                    except Exception as e:
                        logger.exception(
                            "Failed to start Telegram bot for profile {}: {}",
                            profile_id,
                            e,
                        )
                        state.message = (
                            "后台下载任务已启动；Bot 启动失败，请检查 Telegram API 配置。"
                        )
                        try:
                            await stop_download_bot()
                        except Exception as stop_error:
                            logger.warning(
                                "Failed to clean up bot after startup error: "
                                f"{stop_error}"
                            )

            state.running = True
            state.status = "running"
            if not state.message:
                state.message = (
                    "后台下载任务和 Bot 已启动。"
                    if state.bot_started
                    else "后台下载任务已启动。"
                )
            logger.success(f"Profile runtime started: {profile_name} ({profile_id})")
            return {
                "status": "started",
                "message": state.message,
                "profile_id": profile_id,
                "bot_started": state.bot_started,
            }
        except Exception as e:
            state.running = False
            state.status = "error"
            state.message = str(e)
            logger.exception(f"Failed to start profile runtime {profile_id}: {e}")
            await deactivate_runtime(profile_id, stop_client=True, mark_disabled=True)
            return {
                "status": "error",
                "message": str(e),
                "profile_id": profile_id,
            }

    async def deactivate_runtime(
        profile_id: str = None,
        stop_client: bool = True,
        mark_disabled: bool = True,
    ):
        """Stop one profile runtime without stopping Flask or other profiles."""
        nonlocal bot_owner_profile_id
        if profile_id == "all":
            results = []
            for running_profile_id in list(runtimes.keys()):
                results.append(
                    await deactivate_runtime(
                        running_profile_id, stop_client, mark_disabled
                    )
                )
            return {
                "status": "stopped",
                "message": "所有账号运行态已停止。",
                "results": results,
            }

        if not profile_id:
            profile_id = get_active_profile().get("id") if db.conn else None

        state = runtimes.get(profile_id)
        if not state:
            if mark_disabled and db.conn and profile_id:
                update_profile(profile_id, runtime_enabled=False)
            return {
                "status": "not_running",
                "message": "后台任务未运行。",
                "profile_id": profile_id,
            }

        state.running = False
        state.status = "stopping"
        state.app.is_running = False

        if state.bot_started:
            try:
                await stop_download_bot()
            except Exception as e:
                logger.warning(f"Failed to stop bot cleanly: {e}")
            bot_owner_profile_id = None

        for task in list(state.tasks):
            task.cancel()
        if state.tasks:
            try:
                await asyncio.gather(*state.tasks, return_exceptions=True)
            except Exception as e:
                logger.warning(f"Failed to cancel runtime tasks cleanly: {e}")
        state.tasks.clear()

        while not state.queue.empty():
            try:
                state.queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        if stop_client and state.client:
            try:
                if getattr(state.client, "is_connected", False) or getattr(
                    state.client, "is_initialized", False
                ):
                    await stop_server(state.client)
            except ConnectionError as e:
                logger.warning(f"Telegram client already stopped: {e}")
            except Exception as e:
                logger.warning(f"Failed to stop Telegram client cleanly: {e}")

        state.status = "stopped"
        state.message = "账号运行态已停止。"
        runtimes.pop(profile_id, None)
        if mark_disabled and db.conn and profile_id:
            update_profile(profile_id, runtime_enabled=False)

        return {
            "status": "stopped",
            "message": state.message,
            "profile_id": profile_id,
        }

    def runtime_status_callback():
        status = {}
        for profile_id, state in runtimes.items():
            account = None
            me = getattr(state.client, "me", None)
            if me:
                first_name = getattr(me, "first_name", None) or ""
                last_name = getattr(me, "last_name", None) or ""
                full_name = (
                    f"{first_name} {last_name}".strip()
                    or getattr(me, "username", None)
                    or str(me.id)
                )
                account = {
                    "id": str(me.id),
                    "phoneNumber": getattr(me, "phone_number", None) or "",
                    "username": f"@{me.username}" if getattr(me, "username", None) else "",
                    "firstName": full_name,
                }
            status[profile_id] = {
                "running": state.running,
                "status": state.status,
                "message": state.message,
                "bot_started": state.bot_started,
                "started_at": state.started_at,
                "account": account,
            }
        return status

    def start_runtime_callback(runtime_client: pyrogram.Client = None, profile=None):
        if isinstance(profile, str):
            profile = get_profile_by_id(profile)
        future = asyncio.run_coroutine_threadsafe(
            activate_runtime(runtime_client, profile), app.loop
        )
        return future.result(timeout=120)

    def stop_runtime_callback(profile_id: str = None):
        future = asyncio.run_coroutine_threadsafe(
            deactivate_runtime(profile_id, stop_client=True), app.loop
        )
        return future.result(timeout=120)

    def apply_bot_access_config(target_app: Application, config: dict):
        allowed_user_ids = (config or {}).get("allowed_user_ids", [])
        if not isinstance(allowed_user_ids, list):
            allowed_user_ids = []

        access_mode = (config or {}).get("bot_download_access_mode")
        if access_mode not in ("self", "allowed", "public"):
            if (config or {}).get("bot_allow_public_download"):
                access_mode = "public"
            elif allowed_user_ids:
                access_mode = "allowed"
            else:
                access_mode = "self"

        target_app.allowed_user_ids = copy.deepcopy(allowed_user_ids)
        target_app.bot_download_access_mode = access_mode
        target_app.bot_allow_public_download = access_mode == "public"

    async def update_runtime_config(profile_id: str, config: dict):
        state = runtimes.get(profile_id)
        source_bot_token = _effective_bot_token(config)
        if not state:
            owner_state = runtimes.get(bot_owner_profile_id) if bot_owner_profile_id else None
            if (
                owner_state
                and source_bot_token
                and source_bot_token == _effective_bot_token(runtime_app=owner_state.app)
            ):
                apply_bot_access_config(owner_state.app, config)
                logger.info(
                    "Bot access config synced from profile {} to bot owner {}.",
                    profile_id,
                    bot_owner_profile_id,
                )
                return {
                    "status": "applied",
                    "message": "Bot 权限已同步到当前 Bot 运行账户。",
                    "profile_id": profile_id,
                    "bot_owner_profile_id": bot_owner_profile_id,
                }

            return {
                "status": "not_running",
                "message": "账号运行态未启动，配置已保存待下次启动生效。",
                "profile_id": profile_id,
            }

        state.app.config = copy.deepcopy(config or {})
        state.app.assign_config(state.app.config)
        owner_state = runtimes.get(bot_owner_profile_id) if bot_owner_profile_id else None
        if (
            owner_state
            and source_bot_token
            and source_bot_token == _effective_bot_token(runtime_app=owner_state.app)
        ):
            apply_bot_access_config(owner_state.app, config)
            logger.info(
                "Bot access config synced from profile {} to bot owner {}.",
                profile_id,
                bot_owner_profile_id,
            )
        return {
            "status": "applied",
            "message": "账号运行态配置已热更新。",
            "profile_id": profile_id,
            "bot_owner_profile_id": bot_owner_profile_id,
        }

    def update_runtime_config_callback(profile_id: str, config: dict):
        future = asyncio.run_coroutine_threadsafe(
            update_runtime_config(profile_id, config), app.loop
        )
        return future.result(timeout=30)

    try:
        app.pre_run()
        init_web(
            app,
            None,
            restart_callback,
            start_runtime_callback,
            stop_runtime_callback,
            runtime_status_callback,
            update_runtime_config_callback,
        )

        if db.conn:
            profiles = get_profiles()
            autostart_profiles = [
                profile
                for profile in profiles
                if profile.get("runtime_enabled") and profile.get("session")
            ]
            for profile in autostart_profiles:
                try:
                    result = app.loop.run_until_complete(activate_runtime(None, profile))
                    if result.get("status") == "error":
                        logger.warning(
                            f"Profile {profile.get('id')} failed to start: "
                            f"{result.get('message')}"
                        )
                except pyrogram.errors.exceptions.unauthorized_401.AuthKeyUnregistered:
                    update_profile(
                        profile.get("id"),
                        session=None,
                        runtime_enabled=False,
                    )
                    logger.warning(
                        f"Invalid session cleared for profile {profile.get('id')}."
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to auto-start profile {profile.get('id')}: {e}"
                    )

        logger.info(f"Web UI running at http://{app.web_host}:{app.web_port}")
        app.loop.run_forever()

    except KeyboardInterrupt:
        logger.info(_t("KeyboardInterrupt"))
    except pyrogram.errors.exceptions.flood_420.FloodWait as e:
        logger.warning(
            f"Telegram FloodWait detected. Waiting for {e.value} seconds before retry..."
        )
        import time

        time.sleep(e.value + 5)
        # We exit after sleep, allowing Docker to restart normally but with the required delay
    except Exception as e:
        logger.exception("{}", e)
    finally:
        app.is_running = False
        try:
            if not app.loop.is_closed():
                app.loop.run_until_complete(
                    deactivate_runtime("all", stop_client=True, mark_disabled=False)
                )
        except Exception as e:
            logger.warning(f"Failed to stop profile runtimes cleanly: {e}")
        logger.info(_t("Stopped!"))
        logger.info(f"{_t('update config')}......")
        app.update_config()
        logger.success(
            f"{_t('Updated last read message_id to config file')},"
            f"{_t('total download')} {app.total_download_task}, "
            f"{_t('total upload file')} "
            f"{app.cloud_drive_config.total_upload_success_file_count}"
        )


if __name__ == "__main__":
    if _check_config():
        main()
