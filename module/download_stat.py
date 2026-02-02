"""Download Stat"""
import asyncio
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


def get_download_result() -> dict:
    """get global download result"""
    return _download_result


def get_total_download_speed() -> int:
    """get total download speed"""
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

    while get_download_state() == DownloadState.StopDownload:
        if node.is_stop_transmission:
            client.stop_transmission()
        await asyncio.sleep(1)

    if not _download_result.get(chat_id):
        _download_result[chat_id] = {}

    if _download_result[chat_id].get(message_id):
        last_download_byte = _download_result[chat_id][message_id]["down_byte"]
        last_time = _download_result[chat_id][message_id]["end_time"]
        download_speed = _download_result[chat_id][message_id]["download_speed"]
        each_second_total_download = _download_result[chat_id][message_id][
            "each_second_total_download"
        ]
        end_time = _download_result[chat_id][message_id]["end_time"]

        _total_download_size += down_byte - last_download_byte
        each_second_total_download += down_byte - last_download_byte

        if cur_time - last_time >= 1.0:
            download_speed = int(each_second_total_download / (cur_time - last_time))
            end_time = cur_time
            each_second_total_download = 0

        download_speed = max(download_speed, 0)

        _download_result[chat_id][message_id]["down_byte"] = down_byte
        _download_result[chat_id][message_id]["end_time"] = end_time
        _download_result[chat_id][message_id]["download_speed"] = download_speed
        _download_result[chat_id][message_id][
            "each_second_total_download"
        ] = each_second_total_download
    else:
        each_second_total_download = down_byte
        _download_result[chat_id][message_id] = {
            "down_byte": down_byte,
            "total_size": total_size,
            "file_name": file_name,
            "start_time": start_time,
            "end_time": cur_time,
            "download_speed": down_byte / (cur_time - start_time),
            "each_second_total_download": each_second_total_download,
            "task_id": node.task_id,
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


def verify_and_save_download(chat_id: int, message_id: int):
    """Mark download as complete and save to DB"""
    try:
        if not _download_result.get(chat_id):
            return
            
        if _download_result[chat_id].get(message_id):
            # Ensure it's marked as 100%
            item = _download_result[chat_id][message_id]
            if item["down_byte"] != item["total_size"]:
                item["down_byte"] = item["total_size"]
                
            if db.conn:
                db.save_setting("download_history", _download_result)
                # print(f"DEBUG: [stat] Saved history for message {message_id}")
    except Exception as e:
        print(f"Error saving download history: {e}")
        _total_download_size = 0
        _last_download_time = cur_time


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
                        # Only keep completed downloads (100%)
                        # Incomplete downloads are cleared since they can't be resumed after restart
                        if info.get("down_byte", 0) >= info.get("total_size", 1):
                            restored[chat_id][msg_id] = info
                        else:
                            incomplete_count += 1
                
                # Remove empty chat entries
                restored = {k: v for k, v in restored.items() if v}
                
                _download_result = restored
                completed_count = sum(len(v) for v in restored.values())
                print(f"DEBUG: [stat] Loaded {completed_count} completed items from DB")
                if incomplete_count > 0:
                    print(f"DEBUG: [stat] Cleaned {incomplete_count} incomplete/stale download records")
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


# Initialize on module load
init_stat()

