"""Private library activity, independent of download history and media deletion.

The current web login is a single administrator. Scope includes that login ID and
the configured drive, so changing drives cannot mix favorites or viewing history.
"""

import copy
import hashlib
import json
import math
import os
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from .library import clean_path


class ActivityStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = {"paused": False, "entries": {}}
        if self.path.exists():
            # Do not silently overwrite a damaged personal collection.
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or not isinstance(saved.get("entries"), dict):
                raise ValueError("收藏与历史存储格式异常，请恢复备份后重试")
            self.data = saved

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def forget_paths(self, paths):
        """Remove favorites AND both histories only for confirmed deleted resources."""
        with self.lock:
            data = copy.deepcopy(self.data)
            for path in paths:
                data["entries"].pop(clean_path(path), None)
            self._save(data)

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        try:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        finally:
            if temp.exists():
                temp.unlink()
        self.data = data

    def update(self, action, *, path=None, entry=None, value=None,
               position=None, duration=None, history_kind="all", scope="all"):
        if action not in ("favorite", "open", "progress", "remove", "clear", "pause"):
            raise ValueError("未知的记录操作")
        if history_kind not in ("all", "opened", "watched"):
            raise ValueError("未知的历史类型")
        if scope not in ("all", "adult", "movie", "other"):
            raise ValueError("未知的资源库")
        if action in ("favorite", "pause") and not isinstance(value, bool):
            raise ValueError("状态必须为布尔值")
        if action == "progress":
            for number in (position, duration):
                if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
                    raise ValueError("播放进度无效")
            if duration <= 0 or duration > 604800 or position < 0:
                raise ValueError("播放进度无效")
        if action not in ("pause", "clear"):
            path = clean_path(path)
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            data = copy.deepcopy(self.data)
            records = data["entries"]
            if action == "pause":
                data["paused"] = value
            elif action in ("clear", "remove"):
                for key in list(records):
                    record = records[key]
                    category = record.get("entry", {}).get("analysis", {}).get("category", "other")
                    in_scope = (scope == "all" or category == scope or
                                (scope == "other" and category not in ("adult", "movie")))
                    if (action == "remove" and key != path) or not in_scope:
                        continue
                    if history_kind in ("all", "opened"):
                        record.pop("opened_at", None)
                    if history_kind in ("all", "watched"):
                        for field in ("watched_at", "position", "duration", "completed"):
                            record.pop(field, None)
            elif action in ("open", "progress") and data.get("paused"):
                return self.snapshot()
            else:
                record = records.setdefault(path, {})
                if entry:
                    snapshot = copy.deepcopy(entry)
                    snapshot.pop("activity", None)
                    record["entry"] = snapshot
                if action == "favorite":
                    record["favorite"] = value
                    record["favorited_at"] = now if value else ""
                elif action == "open":
                    record["opened_at"] = now
                else:
                    record.update(watched_at=now, position=min(position, duration),
                                  duration=duration, completed=position >= duration * .95)
            data["entries"] = {
                key: record for key, record in records.items()
                if record.get("favorite") or record.get("opened_at") or record.get("watched_at")
            }
            # Only publish in-memory success after durable persistence succeeds.
            self._save(data)
            return copy.deepcopy(data)


@lru_cache(maxsize=16)
def _activity_for_scope(scope):
    digest = hashlib.sha256(scope.encode()).hexdigest()[:24]
    # User data is NOT disposable thumbnail/cache data.
    return ActivityStore(Path("data") / "library-activity" / f"{digest}.json")


def activity_store(drive, user_id):
    return _activity_for_scope(json.dumps([
        str(user_id or "root"),
        *[str(getattr(drive, key, "") or "") for key in
          ("webdav_url", "remote_dir", "webdav_username")],
    ]))


def with_activity(entries, state, view="all", excluded=()):
    result = copy.deepcopy({path: entry for path, entry in entries.items() if path not in excluded})
    for path, record in state["entries"].items():
        if path in excluded:
            continue
        if path not in result and view != "all" and record.get("entry"):
            result[path] = copy.deepcopy(record["entry"])
        if path in result:
            result[path]["activity"] = {k: v for k, v in record.items() if k != "entry"}
    return result
