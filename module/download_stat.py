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
_task_states: dict = {} # { (chat_id, message_id): 'running' | 'paused' | 'deleted' }
_pending_downloads: dict = {}  # {(chat_id, message_id): {"chat_id": x, "message_id": y, "file_name": z}}


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
                if task_speed > 0 and down_byte < total_size and (cur_time - end_time) < 5.0:
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


def add_pending_download(chat_id: int, message_id: int, file_name: str = ""):
    """Register a download as pending (for resume on restart)"""
    global _pending_downloads
    key = f"{chat_id}_{message_id}"
    _pending_downloads[key] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "file_name": file_name,
        "started_at": time.time()
    }
    _save_pending_downloads()


def remove_pending_download(chat_id: int, message_id: int):
    """Remove a download from pending list (after completion)"""
    global _pending_downloads
    key = f"{chat_id}_{message_id}"
    if key in _pending_downloads:
        del _pending_downloads[key]
        _save_pending_downloads()


def get_pending_downloads() -> list:
    """Get list of pending downloads for resume"""
    return list(_pending_downloads.values())


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
                        print(f"DEBUG: [stat] Converted Windows path to Linux-style: {item['file_name']}")
                
                # Clean up pending downloads that are already completed in download_history
                to_remove = []
                for key, item in _pending_downloads.items():
                    chat_id = item.get("chat_id")
                    msg_id = item.get("message_id")
                    
                    # Check if this task is already completed in download_history
                    if chat_id in _download_result and msg_id in _download_result[chat_id]:
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
                    print(f"DEBUG: [stat] Removed {len(to_remove)} already-completed items from pending")
                    _save_pending_downloads()
                
                remaining = len(_pending_downloads)
                print(f"DEBUG: [stat] Loaded {loaded_count} pending downloads, {remaining} remaining for resume")
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
    key = (chat_id, message_id)
    state = _task_states.get(key, 'running')
    
    if state == 'deleted':
        client.stop_transmission()
        return

    # Global or local pause check
    while state == 'paused' or get_download_state() == DownloadState.StopDownload:
        if node.is_stop_transmission:
            client.stop_transmission()
        await asyncio.sleep(1)
        # Re-check state
        state = _task_states.get(key, 'running')
        if state == 'deleted':
            client.stop_transmission()
            return
    # -----------------------

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
            "created_at": cur_time,
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


def verify_and_save_download(chat_id: int, message_id: int, file_name: str = "", total_size: int = 0, task_id: int = 0):
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
                "down_byte": u_info.get("total_bytes", total_size) if u_info else total_size,
                "total_size": u_info.get("total_bytes", total_size) if u_info else total_size,
                "file_name": u_info.get("file_name", file_name) if u_info else file_name,
                "download_speed": 0,
                "start_time": time.time(),
                "end_time": time.time(),
                "task_id": task_id,
            }
            print(f"DEBUG: [stat] Created new history record for chat={chat_id}, msg={message_id}, file={file_name}, size={total_size}")
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
            print(f"DEBUG: [stat] Updated existing record for chat={chat_id}, msg={message_id}")
        
        # Save to DB
        if db.conn:
            db.save_setting("download_history", _download_result)
            print(f"DEBUG: [stat] Saved history to DB, total chats={len(_download_result)}")
            
        # Clean up task state (no longer needed once completed)
        global _task_states
        if (chat_id, message_id) in _task_states:
            del _task_states[(chat_id, message_id)]
            if db.conn:
                to_save = {f"{c}:{m}": s for (c, m), s in _task_states.items()}
                db.save_setting("task_states", to_save)
            
    except Exception as e:
        print(f"Error saving download history: {e}")


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

    # Load pending downloads for resume
    _load_pending_downloads()

    # Load individual task states (paused/running)
    global _task_states
    try:
        if db.conn:
            saved_states = db.load_setting("task_states")
            if saved_states:
                # Convert "chat_id:msg_id" back to (chat_id, msg_id) tuple
                restored_states = {}
                for key_str, state in saved_states.items():
                    try:
                        c_id, m_id = map(int, key_str.split(":"))
                        restored_states[(c_id, m_id)] = state
                    except:
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


def remove_download_task(chat_id: int, message_id: int):
    """Remove a specific download task from history"""
    global _download_result
    
    removed = False
    
    # Remove from download_result
    if chat_id in _download_result and message_id in _download_result[chat_id]:
        del _download_result[chat_id][message_id]
        
        # Clean up empty chat entries
        if not _download_result[chat_id]:
            del _download_result[chat_id]
        
        if db.conn:
            db.save_setting("download_history", _download_result)
        
        removed = True
    
    # Also remove from upload_result
    from module.upload_stat import remove_upload_status
    remove_upload_status(chat_id, message_id)
    
    return removed


def set_task_state(chat_id: int, message_id: int, state: str):
    """Set the control state for an individual task.
    
    state: 'running' | 'paused' | 'deleted'
    """
    global _task_states
    
    if state == 'running':
        # Remove if it was explicitly set to something else, default is running
        if (chat_id, message_id) in _task_states:
            del _task_states[(chat_id, message_id)]
    else:
        _task_states[(chat_id, message_id)] = state
        
    # If state is deleted, also remove from pending downloads
    if state == 'deleted':
        remove_pending_download(chat_id, message_id)

    # Save to DB
    if db.conn:
        # Convert tuple keys to strings for JSON
        to_save = {f"{c}:{m}": s for (c, m), s in _task_states.items()}
        db.save_setting("task_states", to_save)
        
    return True


def get_task_state(chat_id: int, message_id: int):
    """Get the current state of a task"""
    return _task_states.get((chat_id, message_id), 'running')


# Initialize on module load
init_stat()
