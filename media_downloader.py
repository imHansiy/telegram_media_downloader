"""Downloads media from telegram."""

import asyncio
import copy
import inspect
import logging
import os
import shutil
import socket
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
    bump_upload_auto_retry_count,
    clear_upload_auto_retry_count,
    get_download_result,
    get_pending_downloads,
    get_task_state,
    get_upload_auto_retry_count,
    mark_download_failed,
    mark_upload_failed,
    prepare_download_retry,
    remove_pending_download,
    update_download_status,
    verify_and_save_download,
)
from module.get_chat_history_v2 import get_chat_history_v2
from module.language import _t
from module.profiles import (
    get_profile,
    get_profiles,
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
from module.upload_stat import register_upload_task, remove_upload_status
from utils.format import truncate_filename, validate_title
from utils.log import LogFilter
from utils.meta import print_meta
from utils.meta_data import MetaData
from utils.updates import check_for_updates

_instance_lock_socket = None


def acquire_instance_lock(port: int = 43791) -> bool:
    """Keep exactly one downloader process responsible for Telegram replies."""

    global _instance_lock_socket
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock_socket.bind(("127.0.0.1", port))
        lock_socket.listen(1)
    except OSError:
        lock_socket.close()
        return False
    _instance_lock_socket = lock_socket
    return True

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
TELEGRAM_CHUNK_SIZE = 1024 * 1024
# 单次任务内 WebDAV 已有 6 次连接重试；这里再做任务级延迟重传，避免瞬时断网后要人手点重置。
UPLOAD_AUTO_RETRY_DELAYS_SEC = (30, 120, 300)
UPLOAD_AUTO_RETRY_MAX = len(UPLOAD_AUTO_RETRY_DELAYS_SEC)

logging.getLogger("pyrogram.session.session").addFilter(LogFilter())
logging.getLogger("pyrogram.client").addFilter(LogFilter())

logging.getLogger("pyrogram").setLevel(logging.WARNING)


async def download_media_resumable(
    client,
    message,
    target_path: str,
    total_size: int,
    progress_callback=None,
    progress_args: tuple = (),
):
    """Download Telegram media into a persistent .part file and resume by chunk."""

    if total_size <= 0 or not callable(getattr(client, "stream_media", None)):
        return await client.download_media(
            message,
            file_name=target_path,
            progress=progress_callback,
            progress_args=progress_args,
        )

    if os.path.exists(target_path) and os.path.getsize(target_path) == total_size:
        return target_path

    part_path = f"{target_path}.part"
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    existing_size = os.path.getsize(part_path) if os.path.exists(part_path) else 0

    # Telegram offset is counted in 1 MiB chunks. Discard an incomplete tail so the
    # next request always begins at an exact chunk boundary and never duplicates bytes.
    aligned_size = min(existing_size, total_size)
    if aligned_size < total_size:
        aligned_size = (aligned_size // TELEGRAM_CHUNK_SIZE) * TELEGRAM_CHUNK_SIZE
    if existing_size != aligned_size:
        with open(part_path, "r+b") as part_file:
            part_file.truncate(aligned_size)

    downloaded = aligned_size
    if downloaded < total_size:
        offset_chunks = downloaded // TELEGRAM_CHUNK_SIZE
        with open(part_path, "ab") as part_file:
            async for chunk in client.stream_media(
                message, limit=0, offset=offset_chunks
            ):
                remaining = total_size - downloaded
                if remaining <= 0:
                    break
                chunk = chunk[:remaining]
                part_file.write(chunk)
                part_file.flush()
                downloaded += len(chunk)
                if progress_callback:
                    result = progress_callback(
                        downloaded, total_size, *progress_args
                    )
                    if inspect.isawaitable(result):
                        await result

    if downloaded != total_size:
        raise IOError(
            f"Telegram stream ended at {downloaded} of {total_size} bytes"
        )

    os.replace(part_path, target_path)
    return target_path


def local_file_stream_factory(file_path: str, chunk_size: int = TELEGRAM_CHUNK_SIZE):
    """Return a repeatable async stream factory for WebDAV upload retries."""

    async def stream():
        with open(file_path, "rb") as source:
            while True:
                chunk = source.read(chunk_size)
                if not chunk:
                    break
                yield chunk
                await asyncio.sleep(0)

    return stream


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
    temp_file_name: str = ""
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
                    if file_size == media_size:
                        logger.info(
                            f"id={message.id} {ui_file_name} "
                            f"{_t('already download,download skipped')}.\n"
                        )
                        # 本地完整时不能直接 Success 返回：WebDAV 场景还要继续上传/重传。
                        if runtime_app.cloud_drive_config.upload_adapter != "webdav":
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
        mark_download_failed(
            node.chat_id,
            message.id,
            ui_file_name,
            media_size,
            node.task_id,
            node.profile_id,
            str(e),
        )
        return DownloadStatus.FailedDownload, None
    if _media is None:
        return DownloadStatus.SkipDownload, None

    message_id = message.id

    # Register as pending download for resume on restart
    add_pending_download(node.chat_id, message_id, ui_file_name, node.profile_id)

    last_error = "下载或 WebDAV 上传重试失败"
    for retry in range(3):

        try:
            # 本地终态文件已完整时直接复用，避免 reset_upload 再次走 Telegram 下载。
            if (
                _is_exist(file_name)
                and media_size > 0
                and os.path.getsize(file_name) == media_size
            ):
                temp_download_path = file_name
                # 本地复用不会再走下载进度回调；这里写入一次完成进度，
                # 让网页仪表盘立刻出现任务，而不是只在 Bot 卡片上“传输中”。
                await update_download_status(
                    media_size,
                    media_size,
                    message_id,
                    ui_file_name,
                    task_start_time,
                    node,
                    client,
                )
            else:
                # 所有适配器先写入持久化 .part 文件，暂停、断网或重启后按 Telegram 分块偏移继续。
                temp_download_path = await download_media_resumable(
                    client,
                    message,
                    temp_file_name,
                    media_size,
                    update_download_status,
                    (
                        message_id,
                        ui_file_name,
                        task_start_time,
                        node,
                        client,
                    ),
                )

            if runtime_app.cloud_drive_config.upload_adapter == "webdav":
                logger.info(f"Uploading completed local file to WebDAV: {ui_file_name}")
                success = await CloudDrive.webdav_upload_stream(
                    runtime_app.cloud_drive_config,
                    runtime_app.save_path,
                    file_name,
                    local_file_stream_factory(temp_download_path),
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
                if not success:
                    last_error = "WebDAV upload failed; local completed file retained"
                    # 单次任务内继续外层重试；全部失败后再进入延迟自动重传。
                    logger.error(
                        f"Message[{message.id}]: WebDAV upload failed "
                        f"(attempt {retry + 1}/3); local completed file retained."
                    )
                    if retry < 2:
                        await asyncio.sleep(RETRY_TIME_OUT)
                        continue
                    break

            # Success handling for standard download (outside try block but inside for loop)
            if temp_download_path:
                if isinstance(temp_download_path, str):
                    _check_download_finish(
                        media_size, temp_download_path, ui_file_name
                    )

                    # 下载完整且云端上传成功后才持久化完成态，失败时保留本地文件供重试。
                    verify_and_save_download(
                        node.chat_id,
                        message.id,
                        ui_file_name,
                        media_size,
                        node.task_id,
                        node.profile_id,
                    )
                    clear_upload_auto_retry_count(
                        node.chat_id, message.id, node.profile_id
                    )

                    await asyncio.sleep(0.5)
                    if temp_download_path != file_name:
                        _move_to_download_path(temp_download_path, file_name)

                # Remove from pending downloads (completed successfully)
                remove_pending_download(node.chat_id, message.id, node.profile_id)
                return DownloadStatus.SuccessDownload, file_name

        except pyrogram.errors.exceptions.bad_request_400.BadRequest:
            last_error = "Telegram 文件引用已过期"
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
            last_error = f"Telegram 限流等待 {wait_err.value} 秒"
            await asyncio.sleep(wait_err.value)
            logger.warning("Message[{}]: FlowWait {}", message.id, wait_err.value)
            _check_timeout(retry, message.id)
        except TypeError:
            last_error = "Telegram 下载超时"
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
            last_error = str(e)
            # pylint: disable = C0301
            logger.error(
                f"Message[{message.id}]: "
                f"{_t('could not be downloaded due to following exception')}:\n[{e}].",
                exc_info=True,
            )
            break

    # 若本机完整文件已在，优先标成上传失败，避免误导为“下载失败”。
    local_complete = False
    try:
        if file_name and _is_exist(file_name) and media_size > 0:
            local_complete = os.path.getsize(file_name) == media_size
        if not local_complete and temp_file_name and _is_exist(temp_file_name) and media_size > 0:
            local_complete = os.path.getsize(temp_file_name) == media_size
    except OSError:
        local_complete = False

    if local_complete and (
        "WebDAV" in (last_error or "") or "upload" in (last_error or "").lower()
    ):
        mark_upload_failed(
            node.chat_id,
            message.id,
            ui_file_name,
            media_size,
            node.task_id,
            node.profile_id,
            last_error,
        )
        # 任务级延迟自动重传：复用本地完整文件，无需用户手动点重置。
        schedule_fn = getattr(runtime_app, "schedule_upload_auto_retry", None)
        if callable(schedule_fn):
            try:
                schedule_fn(
                    node.chat_id,
                    message.id,
                    node.profile_id,
                    ui_file_name,
                    media_size,
                )
            except Exception as schedule_error:
                logger.warning(
                    f"Message[{message.id}]: failed to schedule upload auto-retry: "
                    f"{schedule_error}"
                )
    else:
        mark_download_failed(
            node.chat_id,
            message.id,
            ui_file_name,
            media_size,
            node.task_id,
            node.profile_id,
            last_error,
        )
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
    """Build one account runtime from the shared downloader configuration."""
    runtime_app = Application(CONFIG_NAME, DATA_FILE_NAME, APPLICATION_NAME)
    created_loop = runtime_app.loop
    if created_loop is not app.loop and not created_loop.is_closed():
        created_loop.close()
    runtime_app.loop = app.loop
    asyncio.set_event_loop(app.loop)
    runtime_app.profile_id = profile.get("id")
    # 账号仅提供 Telegram Session；下载规则、云盘和 Bot 行为在所有账号间共享。
    # profile.config 保留为旧数据，但不再参与新 runtime 的配置选择。
    runtime_app.config = copy.deepcopy(app.config or {})
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
    retrying_uploads: set[tuple[str, int, int]] = set()
    scheduled_auto_retries: set[tuple[str, int, int]] = set()

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

    def _build_remote_path_for_retry(
        runtime_app: Application, file_name: str
    ) -> str:
        """Derive WebDAV remote path the same way the dashboard projection does."""
        local_path = (file_name or "").replace("\\", "/")
        save_path = getattr(runtime_app, "save_path", "") or ""
        relative_path = CloudDrive.get_relative_upload_path(
            save_path.replace("\\", "/").rstrip("/"), local_path
        )
        remote_dir = (
            getattr(runtime_app.cloud_drive_config, "remote_dir", "") or ""
        ).rstrip("/")
        if remote_dir:
            return f"{remote_dir}/{relative_path}"
        return relative_path

    async def auto_retry_failed_upload(
        chat_id: int,
        message_id: int,
        profile_id: str,
        file_name: str,
        total_size: int,
        delay_sec: int,
        attempt: int,
    ):
        """Delayed task-level re-queue after a WebDAV upload_failed terminal state."""
        retry_key = (profile_id or "legacy", int(chat_id), int(message_id))
        try:
            logger.info(
                f"Auto-retry upload scheduled in {delay_sec}s "
                f"(attempt {attempt}/{UPLOAD_AUTO_RETRY_MAX}) "
                f"for chat={chat_id} msg={message_id}"
            )
            await asyncio.sleep(delay_sec)

            state = runtimes.get(profile_id)
            if not state or not state.running:
                logger.warning(
                    f"Auto-retry skipped: profile {profile_id} is not running "
                    f"(chat={chat_id} msg={message_id})"
                )
                return

            # 用户中途删除后不再自动重传。
            if get_task_state(chat_id, message_id, profile_id) == "deleted":
                logger.info(
                    f"Auto-retry skipped: task deleted "
                    f"(chat={chat_id} msg={message_id})"
                )
                return

            record = (get_download_result().get(chat_id) or {}).get(message_id) or {}
            if record.get("state") != "upload_failed":
                logger.info(
                    f"Auto-retry skipped: state is {record.get('state')!r} "
                    f"(chat={chat_id} msg={message_id})"
                )
                return

            remote_path = _build_remote_path_for_retry(state.app, file_name)
            result = await reset_failed_upload(
                chat_id,
                message_id,
                profile_id,
                remote_path,
                file_name,
                total_size,
            )
            if result.get("status") == "error":
                logger.warning(
                    f"Auto-retry enqueue failed for chat={chat_id} msg={message_id}: "
                    f"{result.get('message')}"
                )
            else:
                logger.success(
                    f"Auto-retry queued chat={chat_id} msg={message_id} "
                    f"(attempt {attempt}/{UPLOAD_AUTO_RETRY_MAX})"
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception(
                f"Auto-retry worker crashed for chat={chat_id} msg={message_id}: {error}"
            )
        finally:
            scheduled_auto_retries.discard(retry_key)

    def schedule_upload_auto_retry(
        chat_id: int,
        message_id: int,
        profile_id: str,
        file_name: str,
        total_size: int,
    ):
        """Schedule one delayed auto re-upload if budget remains."""
        profile_id = profile_id or "default"
        retry_key = (profile_id, int(chat_id), int(message_id))
        if retry_key in scheduled_auto_retries or retry_key in retrying_uploads:
            return

        used = get_upload_auto_retry_count(chat_id, message_id, profile_id)
        if used >= UPLOAD_AUTO_RETRY_MAX:
            logger.warning(
                f"Auto-retry budget exhausted ({UPLOAD_AUTO_RETRY_MAX}) "
                f"for chat={chat_id} msg={message_id}"
            )
            return

        attempt = bump_upload_auto_retry_count(chat_id, message_id, profile_id)
        delay_sec = UPLOAD_AUTO_RETRY_DELAYS_SEC[
            min(attempt - 1, len(UPLOAD_AUTO_RETRY_DELAYS_SEC) - 1)
        ]
        scheduled_auto_retries.add(retry_key)

        # 把“将自动重试”写进错误文案，网页/Bot 都能看到还在跟进。
        try:
            record = (get_download_result().get(chat_id) or {}).get(message_id)
            if record is not None:
                base_error = record.get("error") or "WebDAV 上传失败；本地文件已保留"
                # 去掉旧的自动重试提示，避免文案叠加
                if "将在" in base_error and "自动重试" in base_error:
                    base_error = base_error.split("；将在", 1)[0]
                record["error"] = (
                    f"{base_error}；将在 {delay_sec}s 后自动重试 "
                    f"({attempt}/{UPLOAD_AUTO_RETRY_MAX})"
                )
                if db.conn:
                    db.save_setting("download_history", get_download_result())
        except Exception:
            pass

        state = runtimes.get(profile_id)
        task = app.loop.create_task(
            auto_retry_failed_upload(
                chat_id,
                message_id,
                profile_id,
                file_name,
                total_size,
                delay_sec,
                attempt,
            )
        )
        if state is not None:
            state.tasks.append(task)

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
        if not profile:
            raise ValueError("profile is required to start an account runtime")
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
        # download_media 通过 runtime_app 回调调度任务级上传自动重试。
        runtime_app.schedule_upload_auto_retry = schedule_upload_auto_retry
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
            return {
                "status": "error",
                "message": "profile_id is required.",
                "profile_id": None,
            }

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
            profile = get_profile(profile)
        future = asyncio.run_coroutine_threadsafe(
            activate_runtime(runtime_client, profile), app.loop
        )
        return future.result(timeout=120)

    def stop_runtime_callback(profile_id: str = None):
        future = asyncio.run_coroutine_threadsafe(
            deactivate_runtime(profile_id, stop_client=True), app.loop
        )
        return future.result(timeout=120)

    async def reset_failed_upload(
        chat_id: int,
        message_id: int,
        profile_id: str,
        remote_path: str,
        file_name: str,
        total_size: int,
    ):
        """Remove one broken WebDAV object and queue its Telegram message again."""
        state = runtimes.get(profile_id)
        if not state or not state.running:
            return {
                "status": "error",
                "message": "该任务所属的 Telegram 账号当前未运行。",
            }
        if state.app.cloud_drive_config.upload_adapter != "webdav":
            return {"status": "error", "message": "当前上传适配器不是 WebDAV。"}

        retry_key = (profile_id or "legacy", int(chat_id), int(message_id))
        if retry_key in retrying_uploads:
            return {"status": "error", "message": "该任务正在重置，请勿重复操作。"}

        retrying_uploads.add(retry_key)
        try:
            # 先确认 Telegram 源消息仍可读取，避免删除云端对象后才发现任务无法重建。
            message = await state.client.get_messages(chat_id, message_id)
            if not message or message.empty:
                return {"status": "error", "message": "Telegram 源消息已不存在。"}

            deleted, delete_outcome = await CloudDrive.webdav_delete_path(
                state.app.cloud_drive_config,
                remote_path,
            )
            if not deleted:
                return {"status": "error", "message": delete_outcome}

            # 云端异常对象清理成功后，保留本地完整文件并重建上传进度，再交给原下载队列复用。
            prepare_download_retry(chat_id, message_id, profile_id)
            remove_upload_status(chat_id, message_id, profile_id)
            register_upload_task(
                chat_id,
                message_id,
                file_name,
                total_size,
                profile_id,
            )
            node = TaskNode(chat_id=chat_id, profile_id=profile_id)
            node.client = state.client
            queued = await add_download_task(message, node, state.queue)
            if not queued:
                mark_download_failed(
                    chat_id,
                    message_id,
                    file_name,
                    total_size,
                    profile_id=profile_id,
                    error="云端文件已重置，但任务重新入队失败",
                )
                return {"status": "error", "message": "任务重新入队失败。"}

            return {
                "status": "queued",
                "message": (
                    "云端异常文件已删除，任务已复用本地文件重新上传。"
                    if delete_outcome == "deleted"
                    else "云端目标已确认无残留，任务已复用本地文件重新上传。"
                ),
                "delete_outcome": delete_outcome,
            }
        except Exception as error:
            logger.exception(
                f"Failed to reset WebDAV upload {chat_id}/{message_id}: {error}"
            )
            return {"status": "error", "message": str(error)}
        finally:
            retrying_uploads.discard(retry_key)

    def retry_upload_callback(
        chat_id: int,
        message_id: int,
        profile_id: str,
        remote_path: str,
        file_name: str,
        total_size: int,
    ):
        future = asyncio.run_coroutine_threadsafe(
            reset_failed_upload(
                chat_id,
                message_id,
                profile_id,
                remote_path,
                file_name,
                total_size,
            ),
            app.loop,
        )
        return future.result(timeout=90)

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
            retry_upload_callback,
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
    if not acquire_instance_lock():
        logger.error("Another telegram_media_downloader instance is already running.")
    elif _check_config():
        main()
