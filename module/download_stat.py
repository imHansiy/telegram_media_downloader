"""Download Stat"""
import asyncio
import os
import time
from enum import Enum

from pyrogram import Client

from module.app import TaskNode
from module.db import db


class DownloadState(Enum):
    """Download state"""

    Downloading = 1
    StopDownload = 2


_download_result: dict = {}
_total_download_speed: int = 0
_total_download_size: int = 0
_last_download_time: float = time.time()
_download_state: DownloadState = DownloadState.Downloading
_task_states: dict = (
    {}
)  # { (profile_id, chat_id, message_id): 'running' | 'paused' | 'deleted' }
_pending_downloads: dict = (
    {}
)  # {(chat_id, message_id): {"chat_id": x, "message_id": y, "file_name": z}}


def get_download_result() -> dict:
    """get global download result"""
    return _download_result


def get_total_download_speed() -> int:
    """get total download speed. Uses sum of active task speeds as fallback."""
    global _total_download_speed

    cur_time = time.time()

    # If no download activity for more than 5 seconds, reset to 0
    if _total_download_speed > 0 and cur_time - _last_download_time > 5.0:
        _total_download_speed = 0

    # Fallback: If calculated speed is 0, but we have active downloads with speeds,
    # use the sum of individual task speeds instead
    if _total_download_speed == 0 and _download_result:
        total_from_tasks = 0
        for chat_msgs in _download_result.values():
            for task_info in chat_msgs.values():
                task_speed = task_info.get("download_speed", 0)
                end_time = task_info.get("end_time", 0)
                down_byte = task_info.get("down_byte", 0)
                total_size = task_info.get("total_size", 1)
                # Only count incomplete tasks updated within last 5 seconds
                if (
                    task_speed > 0
                    and down_byte < total_size
                    and (cur_time - end_time) < 5.0
                ):
                    total_from_tasks += int(task_speed)
        if total_from_tasks > 0:
            return total_from_tasks

    return _total_download_speed


def get_download_state() -> DownloadState:
    """get download state"""
    return _download_state


# pylint: disable = W0603
def set_download_state(state: DownloadState):
    """set download state"""
    global _download_state
    _download_state = state
    if db.conn:
        try:
            db.save_setting("download_state", state.value)
            print(f"DEBUG: [stat] Saved download state: {state.name}")
        except Exception as e:
            print(f"Error saving download state: {e}")


def _pending_key(chat_id: int, message_id: int, profile_id: str = None) -> str:
    profile_part = profile_id or "legacy"
    return f"{profile_part}:{chat_id}_{message_id}"


def _task_key(chat_id: int, message_id: int, profile_id: str = None):
    return (profile_id or "legacy", int(chat_id), int(message_id))


def _legacy_task_key(chat_id: int, message_id: int):
    return (int(chat_id), int(message_id))


def _task_key_to_string(key) -> str:
    if len(key) == 3:
        profile_id, chat_id, message_id = key
        return f"{profile_id}:{chat_id}:{message_id}"
    chat_id, message_id = key
    return f"{chat_id}:{message_id}"


def _save_task_states():
    if db.conn:
        to_save = {
            _task_key_to_string(key): state for key, state in _task_states.items()
        }
        db.save_setting("task_states", to_save)


def add_pending_download(
    chat_id: int, message_id: int, file_name: str = "", profile_id: str = None
):
    """Register a download as pending (for resume on restart)"""
    global _pending_downloads
    key = _pending_key(chat_id, message_id, profile_id)
    _pending_downloads[key] = {
        "profile_id": profile_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "file_name": file_name,
        "started_at": time.time(),
    }
    _save_pending_downloads()
    # 网页删除会把任务记为 deleted；用户重新提交同一消息时必须清掉旧锁，
    # 否则进度回调会立刻当“已取消”处理，Bot 卡片卡住且网页看不到任务。
    if get_task_state(chat_id, message_id, profile_id) == "deleted":
        set_task_state(chat_id, message_id, "running", profile_id)


def remove_pending_download(chat_id: int, message_id: int, profile_id: str = None):
    """Remove a download from pending list (after completion)"""
    global _pending_downloads
    key = _pending_key(chat_id, message_id, profile_id)
    legacy_key = f"{chat_id}_{message_id}"
    if key in _pending_downloads:
        del _pending_downloads[key]
        _save_pending_downloads()
    elif legacy_key in _pending_downloads:
        del _pending_downloads[legacy_key]
        _save_pending_downloads()


def get_pending_downloads(profile_id: str = None) -> list:
    """Get list of pending downloads for resume"""
    if profile_id is None:
        return list(_pending_downloads.values())
    return [
        item
        for item in _pending_downloads.values()
        if item.get("profile_id") in (profile_id, None)
    ]


def _save_pending_downloads():
    """Save pending downloads to database"""
    if db.conn:
        try:
            db.save_setting("pending_downloads", _pending_downloads)
        except Exception as e:
            print(f"Error saving pending downloads: {e}")


def _load_pending_downloads():
    """Load pending downloads from database"""
    global _pending_downloads
    if db.conn:
        try:
            saved = db.load_setting("pending_downloads")
            if saved:
                _pending_downloads = saved
                loaded_count = len(_pending_downloads)

                # Path conversion for cross-OS resumes (e.g., Windows to Linux)
                is_linux = os.name != "nt"
                for key, item in _pending_downloads.items():
                    fname = item.get("file_name", "")
                    if is_linux and "\\" in fname and ":" in fname:
                        # Converting potential Windows absolute path to something safer on Linux
                        # We'll try to find the filename part or treat the whole thing as a string
                        # Better yet, if it contains the current save_path, make it match.
                        # For now, just fix the slashes so it doesn't look like a single weird file
                        item["file_name"] = fname.replace("\\", "/")
                        print(
                            f"DEBUG: [stat] Converted Windows path to Linux-style: {item['file_name']}"
                        )

                # Clean up pending downloads that are already completed in download_history
                to_remove = []
                for key, item in _pending_downloads.items():
                    chat_id = item.get("chat_id")
                    msg_id = item.get("message_id")

                    # Check if this task is already completed in download_history
                    if (
                        chat_id in _download_result
                        and msg_id in _download_result[chat_id]
                    ):
                        d_item = _download_result[chat_id][msg_id]
                        down_byte = d_item.get("down_byte", 0)
                        total_size = d_item.get("total_size", 1)
                        if down_byte >= total_size:
                            # Already completed, mark for removal
                            to_remove.append(key)

                # Remove completed items from pending
                for key in to_remove:
                    del _pending_downloads[key]

                if to_remove:
                    print(
                        f"DEBUG: [stat] Removed {len(to_remove)} already-completed items from pending"
                    )
                    _save_pending_downloads()

                remaining = len(_pending_downloads)
                print(
                    f"DEBUG: [stat] Loaded {loaded_count} pending downloads, {remaining} remaining for resume"
                )
        except Exception as e:
            print(f"Error loading pending downloads: {e}")


async def update_download_status(
    down_byte: int,
    total_size: int,
    message_id: int,
    file_name: str,
    start_time: float,
    node: TaskNode,
    client: Client,
):
    """update_download_status"""
    cur_time = time.time()
    # pylint: disable = W0603
    global _total_download_speed
    global _total_download_size
    global _last_download_time

    if node.is_stop_transmission:
        client.stop_transmission()

    chat_id = node.chat_id

    # --- Per-task Control ---
    state = get_task_state(chat_id, message_id, node.profile_id)

    if state == "deleted":
        client.stop_transmission()
        return

    # Global or local pause check
    while state == "paused" or get_download_state() == DownloadState.StopDownload:
        if node.is_stop_transmission:
            client.stop_transmission()
        await asyncio.sleep(1)
        # Re-check state
        state = get_task_state(chat_id, message_id, node.profile_id)
        if state == "deleted":
            client.stop_transmission()
            return
    # -----------------------

    if not _download_result.get(chat_id):
        _download_result[chat_id] = {}

    if _download_result[chat_id].get(message_id):
        record = _download_result[chat_id][message_id]
        # 旧版完成记录和流式上传记录没有完整的瞬时速度字段；续传时就地升级，
        # 避免历史数据让已经识别成功的媒体任务在首次进度回调中失败。
        last_download_byte = record.get("down_byte", 0)
        last_time = record.get("end_time", cur_time)
        download_speed = record.get("download_speed", 0)
        each_second_total_download = record.get("each_second_total_download", 0)
        end_time = record.get("end_time", cur_time)

        _total_download_size += down_byte - last_download_byte
        each_second_total_download += down_byte - last_download_byte

        if cur_time - last_time >= 1.0:
            download_speed = int(each_second_total_download / (cur_time - last_time))
            end_time = cur_time
            each_second_total_download = 0

        download_speed = max(download_speed, 0)

        record.update(
            {
                "down_byte": down_byte,
                "total_size": total_size,
                "file_name": file_name,
                "start_time": record.get("start_time", start_time),
                "created_at": record.get("created_at", start_time),
                "end_time": end_time,
                "download_speed": download_speed,
                "each_second_total_download": each_second_total_download,
                "task_id": node.task_id,
                "profile_id": node.profile_id,
                # 失败任务重新开始传输后回到活动态，旧错误不再污染当前状态。
                "state": "downloading",
                "error": "",
            }
        )
    else:
        each_second_total_download = down_byte
        _download_result[chat_id][message_id] = {
            "down_byte": down_byte,
            "total_size": total_size,
            "file_name": file_name,
            "start_time": start_time,
            "end_time": cur_time,
            "created_at": cur_time,
            "download_speed": down_byte / (cur_time - start_time),
            "each_second_total_download": each_second_total_download,
            "task_id": node.task_id,
            "profile_id": node.profile_id,
            "state": "downloading",
            "error": "",
        }
        _total_download_size += down_byte

    if cur_time - _last_download_time >= 1.0:
        # update speed
        _total_download_speed = int(
            _total_download_size / (cur_time - _last_download_time)
        )
        _total_download_speed = max(_total_download_speed, 0)
        _total_download_size = 0
        _last_download_time = cur_time


def verify_and_save_download(
    chat_id: int,
    message_id: int,
    file_name: str = "",
    total_size: int = 0,
    task_id: int = 0,
    profile_id: str = None,
):
    """Mark download as complete and save to DB.

    For streaming uploads, the download record may not exist in _download_result,
    so we need to create it from available info or upload_result.
    """
    global _download_result

    try:
        # Import here to avoid circular import
        from module.upload_stat import get_upload_result

        upload_result = get_upload_result()

        # Check if we have a record in download_result
        if chat_id not in _download_result:
            _download_result[chat_id] = {}

        if message_id not in _download_result[chat_id]:
            # No download record - this is likely a streaming upload
            # Try to get info from upload_result
            u_info = None
            if chat_id in upload_result and message_id in upload_result[chat_id]:
                u_info = upload_result[chat_id][message_id]

            # Create a new record
            import time

            _download_result[chat_id][message_id] = {
                "down_byte": u_info.get("total_bytes", total_size)
                if u_info
                else total_size,
                "total_size": u_info.get("total_bytes", total_size)
                if u_info
                else total_size,
                "file_name": u_info.get("file_name", file_name)
                if u_info
                else file_name,
                "download_speed": 0,
                "start_time": time.time(),
                "end_time": time.time(),
                "task_id": task_id,
                "profile_id": profile_id or (u_info or {}).get("profile_id"),
                "state": "completed",
                "error": "",
            }
            print(
                f"DEBUG: [stat] Created new history record for chat={chat_id}, msg={message_id}, file={file_name}, size={total_size}"
            )
        else:
            # Existing record - ensure it's marked as 100%
            item = _download_result[chat_id][message_id]
            if item["down_byte"] != item["total_size"]:
                item["down_byte"] = item["total_size"]
            # Update end_time
            import time

            item["end_time"] = time.time()
            if "task_id" not in item:
                item["task_id"] = task_id
            item["profile_id"] = profile_id or item.get("profile_id")
            item["state"] = "completed"
            item["error"] = ""
            print(
                f"DEBUG: [stat] Updated existing record for chat={chat_id}, msg={message_id}"
            )

        # Save to DB
        if db.conn:
            db.save_setting("download_history", _download_result)
            print(
                f"DEBUG: [stat] Saved history to DB, total chats={len(_download_result)}"
            )

        # Clean up task state (no longer needed once completed)
        global _task_states
        task_key = _task_key(chat_id, message_id, profile_id)
        legacy_key = _legacy_task_key(chat_id, message_id)
        changed = False
        if task_key in _task_states:
            del _task_states[task_key]
            changed = True
        if legacy_key in _task_states:
            del _task_states[legacy_key]
            changed = True
        if changed:
            _save_task_states()

    except Exception as e:
        print(f"Error saving download history: {e}")


def _persist_terminal_failure(
    chat_id: int,
    message_id: int,
    *,
    state: str,
    file_name: str = "",
    total_size: int = 0,
    task_id: int = 0,
    profile_id: str = None,
    error: str = "",
    local_complete: bool = False,
):
    """Write a terminal failure row shared by download/upload failure paths."""
    global _download_result

    now = time.time()
    chat_tasks = _download_result.setdefault(chat_id, {})
    item = chat_tasks.setdefault(
        message_id,
        {
            "down_byte": 0,
            "total_size": total_size,
            "file_name": file_name or f"message-{message_id}",
            "download_speed": 0,
            "start_time": now,
            "created_at": now,
        },
    )

    # 下载或云盘上传最终失败后保留最后进度和文件信息，用户才能在失败筛选中定位任务。
    if file_name:
        item["file_name"] = file_name
    if total_size:
        item["total_size"] = total_size
    # 本地下载已完整、仅云盘失败时，进度条保持 100%，避免被误判成“没下完”。
    if local_complete and item.get("total_size", 0) > 0:
        item["down_byte"] = item["total_size"]
    item.update(
        {
            "end_time": now,
            "download_speed": 0,
            "task_id": task_id or item.get("task_id", 0),
            "profile_id": profile_id or item.get("profile_id"),
            "state": state,
            "error": error or item.get("error") or "下载或上传失败",
        }
    )

    if db.conn:
        db.save_setting("download_history", _download_result)
    return item


def mark_download_failed(
    chat_id: int,
    message_id: int,
    file_name: str = "",
    total_size: int = 0,
    task_id: int = 0,
    profile_id: str = None,
    error: str = "",
):
    """Persist a terminal Telegram-download failure for the dashboard."""
    return _persist_terminal_failure(
        chat_id,
        message_id,
        state="failed",
        file_name=file_name,
        total_size=total_size,
        task_id=task_id,
        profile_id=profile_id,
        error=error or "下载失败",
        local_complete=False,
    )


def mark_upload_failed(
    chat_id: int,
    message_id: int,
    file_name: str = "",
    total_size: int = 0,
    task_id: int = 0,
    profile_id: str = None,
    error: str = "",
):
    """Persist local-complete / cloud-upload failure separately from download failure.

    业务上本机媒体已落盘，只是 WebDAV 上传未成功；仪表盘应显示“上传失败”，
    重置时只重传，不重新从 Telegram 拉完整文件（有本地完整文件时）。
    """
    return _persist_terminal_failure(
        chat_id,
        message_id,
        state="upload_failed",
        file_name=file_name,
        total_size=total_size,
        task_id=task_id,
        profile_id=profile_id,
        error=error or "WebDAV 上传失败；本地文件已保留",
        local_complete=True,
    )


def is_retryable_failure_state(state: str) -> bool:
    """Whether a history row can be reset and re-queued for upload."""
    return state in ("failed", "upload_failed")


def get_upload_auto_retry_count(
    chat_id: int, message_id: int, profile_id: str = None
) -> int:
    """How many delayed auto-upload retries have already been scheduled/used."""
    item = (_download_result.get(chat_id) or {}).get(message_id) or {}
    if profile_id and item.get("profile_id") not in (profile_id, None, ""):
        return 0
    return int(item.get("upload_auto_retry_count") or 0)


def bump_upload_auto_retry_count(
    chat_id: int, message_id: int, profile_id: str = None
) -> int:
    """Increment delayed auto-upload retry counter and return the new value."""
    chat_tasks = _download_result.setdefault(chat_id, {})
    item = chat_tasks.setdefault(message_id, {})
    if profile_id and item.get("profile_id") not in (profile_id, None, ""):
        item["profile_id"] = profile_id
    next_count = int(item.get("upload_auto_retry_count") or 0) + 1
    item["upload_auto_retry_count"] = next_count
    item["upload_auto_retry_at"] = time.time()
    if db.conn:
        db.save_setting("download_history", _download_result)
    return next_count


def clear_upload_auto_retry_count(
    chat_id: int, message_id: int, profile_id: str = None
) -> None:
    """Clear delayed auto-upload counters after success or manual reset."""
    item = (_download_result.get(chat_id) or {}).get(message_id)
    if not item:
        return
    if profile_id and item.get("profile_id") not in (profile_id, None, ""):
        return
    if "upload_auto_retry_count" not in item and "upload_auto_retry_at" not in item:
        return
    item.pop("upload_auto_retry_count", None)
    item.pop("upload_auto_retry_at", None)
    if db.conn:
        db.save_setting("download_history", _download_result)


def prepare_download_retry(
    chat_id: int, message_id: int, profile_id: str = None
) -> dict:
    """Move one failed task back to the queue without discarding its local file."""
    chat_tasks = _download_result.get(chat_id, {})
    item = chat_tasks.get(message_id)
    if not item:
        raise KeyError("任务不存在")
    if profile_id and item.get("profile_id") not in (profile_id, None):
        raise PermissionError("任务不属于当前账号")
    if not is_retryable_failure_state(item.get("state", "")):
        raise ValueError("只有失败或上传失败任务可以重置上传")

    # 重置只清理失败终态；已下载字节数和本地文件继续保留，重新入队后优先复用完整文件。
    item["state"] = "pending"
    item["error"] = ""
    item["download_speed"] = 0
    item["end_time"] = time.time()
    if db.conn:
        db.save_setting("download_history", _download_result)
    set_task_state(chat_id, message_id, "running", profile_id)
    return item


def init_stat():
    """Initialize statistics from database"""
    global _download_result
    try:
        if db.conn:
            saved = db.load_setting("download_history")
            if saved:
                # Keys in JSON are strings, but we need int keys for chat_id and message_id
                # to match the rest of the application logic.
                restored = {}
                incomplete_count = 0
                for chat_id_str, messages in saved.items():
                    chat_id = int(chat_id_str)
                    restored[chat_id] = {}
                    for msg_id_str, info in messages.items():
                        msg_id = int(msg_id_str)
                        # 成功记录、下载失败、上传失败终态都要跨重启保留；
                        # 普通未完成进度由 pending_downloads 负责续传。
                        if (
                            info.get("down_byte", 0) >= info.get("total_size", 1)
                            or is_retryable_failure_state(info.get("state", ""))
                        ):
                            restored[chat_id][msg_id] = info
                        else:
                            incomplete_count += 1

                # Remove empty chat entries
                restored = {k: v for k, v in restored.items() if v}

                _download_result = restored
                completed_count = sum(len(v) for v in restored.values())
                print(f"DEBUG: [stat] Loaded {completed_count} completed items from DB")
                if incomplete_count > 0:
                    print(
                        f"DEBUG: [stat] Cleaned {incomplete_count} incomplete/stale download records"
                    )
                    # Save the cleaned history back to DB
                    db.save_setting("download_history", _download_result)
            else:
                _download_result = {}
    except Exception as e:
        print(f"Error loading download history: {e}")
        _download_result = {}

    # Load download state
    global _download_state
    try:
        if db.conn:
            state_val = db.load_setting("download_state")
            if state_val is not None:
                _download_state = DownloadState(int(state_val))
                print(f"DEBUG: [stat] Restored download state: {_download_state.name}")
    except Exception as e:
        print(f"Error loading download state: {e}")

    # Load pending downloads for resume
    _load_pending_downloads()

    # Load individual task states (paused/running)
    global _task_states
    try:
        if db.conn:
            saved_states = db.load_setting("task_states")
            if saved_states:
                # Convert both legacy "chat_id:msg_id" and
                # "profile_id:chat_id:msg_id" keys back to tuples.
                restored_states = {}
                for key_str, state in saved_states.items():
                    try:
                        parts = key_str.split(":")
                        if len(parts) == 3:
                            profile_id, c_id, m_id = parts
                            restored_states[(profile_id, int(c_id), int(m_id))] = state
                        elif len(parts) == 2:
                            c_id, m_id = map(int, parts)
                            restored_states[(c_id, m_id)] = state
                    except Exception:
                        continue
                _task_states = restored_states
                print(f"DEBUG: [stat] Loaded {len(_task_states)} task states from DB")
    except Exception as e:
        print(f"Error loading task states: {e}")


def clear_download_history():
    """Clear all completed downloads from history"""
    global _download_result

    # Keep only incomplete downloads (active tasks)
    active = {}
    for chat_id, messages in _download_result.items():
        active_msgs = {}
        for msg_id, info in messages.items():
            if info.get("down_byte", 0) < info.get("total_size", 1):
                active_msgs[msg_id] = info
        if active_msgs:
            active[chat_id] = active_msgs

    _download_result = active

    if db.conn:
        db.save_setting("download_history", _download_result)

    # Also clear upload history
    from module.upload_stat import clear_upload_history

    clear_upload_history()

    return True


def remove_download_task(chat_id: int, message_id: int, profile_id: str = None):
    """Remove a specific download task from history"""
    global _download_result

    removed = False

    # Remove from download_result
    if chat_id in _download_result and message_id in _download_result[chat_id]:
        item = _download_result[chat_id][message_id]
        if profile_id and item.get("profile_id") not in (profile_id, None):
            return False

        del _download_result[chat_id][message_id]

        # Clean up empty chat entries
        if not _download_result[chat_id]:
            del _download_result[chat_id]

        if db.conn:
            db.save_setting("download_history", _download_result)

        removed = True

    # Also remove from upload_result
    from module.upload_stat import remove_upload_status

    remove_upload_status(chat_id, message_id, profile_id)

    return removed


def set_task_state(
    chat_id: int, message_id: int, state: str, profile_id: str = None
):
    """Set the control state for an individual task.

    state: 'running' | 'paused' | 'deleted'
    """
    global _task_states
    key = _task_key(chat_id, message_id, profile_id)
    legacy_key = _legacy_task_key(chat_id, message_id)

    if state == "running":
        # Remove if it was explicitly set to something else, default is running
        if key in _task_states:
            del _task_states[key]
        if legacy_key in _task_states:
            del _task_states[legacy_key]
    else:
        _task_states[key] = state

    # If state is deleted, also remove from pending downloads
    if state == "deleted":
        remove_pending_download(chat_id, message_id, profile_id)

    # Save to DB
    _save_task_states()

    return True


def get_task_state(chat_id: int, message_id: int, profile_id: str = None):
    """Get the current state of a task"""
    key = _task_key(chat_id, message_id, profile_id)
    legacy_key = _legacy_task_key(chat_id, message_id)
    if key in _task_states:
        return _task_states[key]
    if legacy_key in _task_states:
        return _task_states[legacy_key]
    return "running"


# Initialize on module load
init_stat()
