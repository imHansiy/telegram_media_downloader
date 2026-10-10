"""Read-only task projection shared by Web/SSE and Telegram status cards.

Byte progress is not completion acknowledgement. No caller should infer an
upload from downloaded bytes, or infer an ended workflow from a full stream.
"""

import math


PHASE_LABELS = {
    "pending": "等待中",
    "downloading": "下载中",
    "analyzing": "AI分析中",
    "waiting_upload": "等待上传",
    "uploading": "上传中",
    "finalizing": "确认上传",
    "completed": "已完成",
    "paused": "已暂停",
    "failed": "下载失败",
    "upload_failed": "上传失败",
}


def _number(value):
    try:
        value = float(value or 0)
        return max(0, value) if math.isfinite(value) else 0
    except (ValueError, TypeError):
        return 0


def _percent(done, total):
    return min(100.0, round(done / total * 100, 1)) if total > 0 else 0.0


def project_task_progress(download=None, upload=None, *, control="running"):
    """Project raw records without mutating them or accessing storage/network."""
    download, upload = download or {}, upload or {}
    if download.get("profile_id") and upload.get("profile_id") not in (
        None,
        "",
        download["profile_id"],
    ):
        upload = {}
    total = _number(download.get("total_size") or upload.get("total_bytes"))
    down = _number(
        download.get("down_byte") if download else upload.get("processed_bytes")
    )
    upload_total = _number(upload.get("total_bytes") or total)
    uploaded = _number(upload.get("processed_bytes"))
    state = download.get("state", "")
    failure = (
        state
        if state in ("failed", "upload_failed")
        else (
            "upload_failed"
            if upload.get("state") in ("failed", "upload_failed")
            else ""
        )
    )
    down_complete = total > 0 and down >= total
    upload_full = upload_total > 0 and uploaded >= upload_total
    acknowledged = state in ("completed", "finished") or (
        not download and upload.get("state") == "success"
    )
    # An optional subsequent upload (e.g. rclone) may still be in progress after
    # the local record was saved. Its partial bytes take precedence over that flag.
    completed = acknowledged and (not upload or upload_full) and not failure
    ai_status = str(download.get("ai_status") or "")
    analysis_active = bool(ai_status) and not ai_status.startswith(
        (
            "分析完成",
            "分析未完成",
            "分析失败",
            "分析超时",
            "分析未",
            "待确认",
            "文件超过",
            "分类完成",
            "分类跳过",
        )
    )
    if failure:
        phase = failure
    elif completed:
        phase = "completed"
    elif control == "paused" or state == "paused":
        phase = "paused"
    elif upload:
        phase = "finalizing" if upload_full else "uploading"
    elif down_complete:
        phase = "analyzing" if analysis_active else "waiting_upload"
    elif down > 0:
        phase = "downloading"
    else:
        phase = "pending"
    download_progress = _percent(down, total)
    upload_progress = _percent(uploaded, upload_total) if upload else 0.0
    if completed:
        download_progress = upload_progress = 100.0
    elif failure == "upload_failed" and total:
        download_progress = 100.0
    return {
        "phase": phase,
        "phase_label": PHASE_LABELS[phase],
        "completed": completed,
        "download_progress": download_progress,
        "upload_progress": upload_progress,
        "download_speed": _number(download.get("download_speed"))
        if phase == "downloading"
        else 0,
        "upload_speed": _number(upload.get("upload_speed"))
        if phase == "uploading"
        else 0,
        "download_bytes": min(down, total) if total else down,
        "total_bytes": total,
        "upload_bytes": min(uploaded, upload_total) if upload_total else uploaded,
        "upload_total_bytes": upload_total,
        "error": str(upload.get("error") or download.get("error") or ""),
    }


def task_record_matches(node, message_id, record):
    """Membership wins over a reused display number; legacy fallback is time-bound."""
    if node.profile_id and record.get("profile_id") not in (node.profile_id, None, ""):
        return False
    memberships = (
        node.download_status,
        node.upload_stat_dict,
        node.cloud_drive_upload_stat_dict,
    )
    if any(memberships):
        keys = (message_id, str(message_id))
        if str(message_id).isdigit():
            keys += (int(message_id),)
        return any(key in membership for membership in memberships for key in keys)
    return (
        str(record.get("task_id", "")) == str(node.task_id)
        and _number(record.get("created_at") or record.get("start_time"))
        >= node.created_at
    )
