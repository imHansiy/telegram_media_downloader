
"""Upload Stat"""
import re
import time
from typing import Optional, Dict
from module.db import db

_upload_result: Dict = {}
_total_upload_speed: int = 0
_last_upload_time: float = time.time()
_total_uploaded_diff: int = 0

def register_upload_task(chat_id: int, message_id: int, file_name: str, total_bytes: int):
    """
    Pre-register an upload task so it shows in UI immediately.
    Call this BEFORE starting upload to ensure visibility even if upload fails fast.
    """
    global _upload_result
    if chat_id not in _upload_result:
        _upload_result[chat_id] = {}
    
    # Only register if not already present (don't overwrite progress)
    if message_id not in _upload_result[chat_id]:
        _upload_result[chat_id][message_id] = {
            "processed_bytes": 0,
            "total_bytes": total_bytes,
            "upload_speed": 0,
            "file_name": file_name,
            "eta": "计算中...",
            "chat_id": chat_id,
            "message_id": message_id,
            "updated_at": time.time(),
            "state": "waiting"  # waiting, uploading, retrying, failed, success
        }

def update_task_state(chat_id: int, message_id: int, state: str):
    """Update task state (waiting, uploading, retrying, failed, success)"""
    global _upload_result
    if chat_id in _upload_result and message_id in _upload_result[chat_id]:
        _upload_result[chat_id][message_id]["state"] = state
        _upload_result[chat_id][message_id]["updated_at"] = time.time()

def get_upload_result() -> Dict:
    """get global upload result"""
    return _upload_result

def get_total_upload_speed() -> int:
    """get total upload speed. Uses sum of active task speeds as fallback."""
    global _total_upload_speed
    
    cur_time = time.time()
    
    # If no upload activity for more than 5 seconds, reset to 0
    if _total_upload_speed > 0 and cur_time - _last_upload_time > 5.0:
        _total_upload_speed = 0
    
    # Fallback: If calculated speed is 0, but we have active uploads with speeds,
    # use the sum of individual task speeds instead
    if _total_upload_speed == 0 and _upload_result:
        total_from_tasks = 0
        for chat_msgs in _upload_result.values():
            for task_info in chat_msgs.values():
                task_speed = task_info.get("upload_speed", 0)
                task_updated = task_info.get("updated_at", 0)
                # Only count tasks updated within last 5 seconds
                if task_speed > 0 and (cur_time - task_updated) < 5.0:
                    total_from_tasks += task_speed
        if total_from_tasks > 0:
            return total_from_tasks
    
    return _total_upload_speed

def _parse_size_str(size_str: str) -> int:
    """Parse size string like '1.5MB' or '100k' to bytes"""
    if not size_str:
        return 0
    
    size_str = size_str.upper().strip()
    match = re.search(r"([\d\.]+)\s*([KMGTPEZY]?I?B?)", size_str)
    if not match:
        return 0
        
    value = float(match.group(1))
    unit = match.group(2)
    
    multiplier = 1
    if "K" in unit:
        multiplier = 1024
    elif "M" in unit:
        multiplier = 1024 ** 2
    elif "G" in unit:
        multiplier = 1024 ** 3
    elif "T" in unit:
        multiplier = 1024 ** 4
    elif "P" in unit:
        multiplier = 1024 ** 5
        
    return int(value * multiplier)

def update_upload_status(
    chat_id: int,
    message_id: int,
    transferred_bytes: int,
    total_bytes: int,
    current_speed: int, # bytes/s
    file_name: str,
    eta: Optional[str] = None
):
    """
    Update upload status for a specific task.
    Call this from WebDAV/Telegram/Aligo upload callbacks.
    """
    global _total_upload_speed, _last_upload_time, _total_uploaded_diff

    cur_time = time.time()
    
    if chat_id not in _upload_result:
        _upload_result[chat_id] = {}
        
    prev_transferred = 0
    if message_id in _upload_result[chat_id]:
        prev_transferred = _upload_result[chat_id][message_id].get("processed_bytes", 0)
    
    # Calculate diff for global speed
    # Only accumulate positive progress to avoid glitches on retries
    if transferred_bytes > prev_transferred:
        _total_uploaded_diff += (transferred_bytes - prev_transferred)
    
    # Store current state
    _upload_result[chat_id][message_id] = {
        "processed_bytes": transferred_bytes,  # mapped to down_byte in download_stat for consistency? No, let's use clear names
        "total_bytes": total_bytes,
        "upload_speed": current_speed,
        "file_name": file_name,
        "eta": eta if eta else "0s",
        "chat_id": chat_id,
        "message_id": message_id,
        "updated_at": cur_time
    }
    
    # Global Speed Calculation (tick every 1s)
    if cur_time - _last_upload_time >= 1.0:
        if cur_time > _last_upload_time:
            _total_upload_speed = int(_total_uploaded_diff / (cur_time - _last_upload_time))
        _total_uploaded_diff = 0
        _last_upload_time = cur_time


def update_upload_status_str(
    chat_id: int,
    message_id: int,
    transferred_p: str, # e.g. "1.5G / 2.0G"
    percentage: str,    # e.g. "75%"
    speed: str,         # e.g. "3.5MB/s"
    eta: str,
    file_name: str
):
    """
    Update upload status from string-based input (e.g. Rclone).
    """
    # Parse speed
    current_speed = _parse_size_str(speed.replace("/s", ""))
    
    # Parse transferred bytes
    parts = transferred_p.split("/")
    transferred_bytes = 0
    total_bytes = 0
    if len(parts) >= 1:
        transferred_bytes = _parse_size_str(parts[0])
    if len(parts) >= 2:
        total_bytes = _parse_size_str(parts[1])
        
    update_upload_status(
        chat_id, 
        message_id, 
        transferred_bytes, 
        total_bytes, 
        current_speed, 
        file_name,
        eta
    )

def remove_upload_status(chat_id: int, message_id: int):
    """Remove upload status when finished"""
    if chat_id in _upload_result and message_id in _upload_result[chat_id]:
        del _upload_result[chat_id][message_id]
        # Clean up empty chat dict
        if not _upload_result[chat_id]:
            del _upload_result[chat_id]


def clear_upload_history():
    """Clear all completed uploads from history.
    Keep only incomplete uploads (active tasks)."""
    global _upload_result
    
    cur_time = time.time()
    active = {}
    for chat_id, messages in _upload_result.items():
        active_msgs = {}
        for msg_id, info in messages.items():
            processed = info.get("processed_bytes", 0)
            total = info.get("total_bytes", 1)
            updated = info.get("updated_at", 0)
            # Keep if not complete AND recently updated (within 30 seconds)
            if processed < total and (cur_time - updated) < 30.0:
                active_msgs[msg_id] = info
        if active_msgs:
            active[chat_id] = active_msgs
    
    _upload_result = active
