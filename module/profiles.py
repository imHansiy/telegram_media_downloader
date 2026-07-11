"""Profile storage for multiple Telegram accounts and app configs."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any

from module.db import db

PROFILE_STORE_KEY = "profiles"
DEFAULT_PROFILE_ID = "default"

_UNSET = object()


def utc_now() -> str:
    """Return a stable UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _legacy_value(key: str, default: Any = None) -> Any:
    if not db.conn:
        return default
    value = db.load_setting(key)
    return default if value is None else value


def _profile_from_legacy() -> dict:
    now = utc_now()
    return {
        "id": DEFAULT_PROFILE_ID,
        "name": "默认账户",
        "app_data": _legacy_value("data", {}) or {},
        "session": _legacy_value("session", None),
        "account": None,
        "runtime_enabled": bool(_legacy_value("session", None)),
        "created_at": now,
        "updated_at": now,
    }


def _normalize_store(store: dict | None) -> dict:
    if not isinstance(store, dict):
        store = {}

    profiles = store.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        profiles = [_profile_from_legacy()]

    normalized = []
    seen = set()
    for idx, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            continue
        profile_id = str(profile.get("id") or "").strip() or (
            DEFAULT_PROFILE_ID if idx == 0 else f"profile_{uuid.uuid4().hex[:10]}"
        )
        if profile_id in seen:
            profile_id = f"profile_{uuid.uuid4().hex[:10]}"
        seen.add(profile_id)

        normalized.append(
            {
                "id": profile_id,
                "name": profile.get("name") or ("默认账户" if idx == 0 else profile_id),
                "app_data": profile.get("app_data") or {},
                "session": profile.get("session"),
                "account": profile.get("account"),
                "runtime_enabled": bool(
                    profile.get(
                        "runtime_enabled",
                        bool(profile.get("session")) if "runtime_enabled" not in profile else False,
                    )
                ),
                "created_at": profile.get("created_at") or utc_now(),
                "updated_at": profile.get("updated_at") or utc_now(),
            }
        )

    if not normalized:
        normalized = [_profile_from_legacy()]

    # 所有已保存账号都是平等的运行单元；旧版 active_profile_id 仅用于迁移，
    # 归一化后不再保留，避免“查看某账号”改变其它账号的配置或运行状态。
    return {"profiles": normalized}


def load_store() -> dict:
    """Load and normalize the profile store."""
    store = db.load_setting(PROFILE_STORE_KEY) if db.conn else None
    if db.conn and isinstance(store, dict):
        legacy_profiles = store.get("profiles") or []
        # 老版本把下载器配置复制到每个账号；全局配置缺失时先提升一份再清理副本。
        legacy_config = next(
            (
                item.get("config")
                for item in legacy_profiles
                if isinstance(item, dict) and item.get("config")
            ),
            None,
        )
        if legacy_config and not db.load_setting("config"):
            db.save_setting("config", legacy_config)
        legacy_bot_setting = next(
            (
                item.get("bot_setting")
                for item in legacy_profiles
                if isinstance(item, dict) and item.get("bot_setting")
            ),
            None,
        )
        if legacy_bot_setting and not db.load_setting("bot_setting"):
            db.save_setting("bot_setting", legacy_bot_setting)
    normalized = _normalize_store(store)
    if db.conn and store != normalized:
        db.save_setting(PROFILE_STORE_KEY, normalized)
    return normalized


def save_store(store: dict) -> dict:
    """Persist the normalized profile store."""
    normalized = _normalize_store(store)
    if db.conn:
        db.save_setting(PROFILE_STORE_KEY, normalized)
    return normalized


def get_profiles() -> list[dict]:
    return load_store()["profiles"]


def get_profile(profile_id: str) -> dict:
    """Return one account profile by its stable identifier."""
    store = load_store()
    return store["profiles"][_profile_index(store, profile_id)]


def _profile_index(store: dict, profile_id: str) -> int:
    for idx, profile in enumerate(store["profiles"]):
        if profile["id"] == profile_id:
            return idx
    raise KeyError(f"Profile {profile_id} not found")


def update_profile(profile_id: str, **fields) -> dict:
    """Update a profile by id."""
    store = load_store()
    idx = _profile_index(store, profile_id)
    profile = copy.deepcopy(store["profiles"][idx])
    for key, value in fields.items():
        if value is not _UNSET:
            profile[key] = value
    profile["updated_at"] = utc_now()
    store["profiles"][idx] = profile
    save_store(store)
    return profile


def create_profile(
    *,
    name: str | None = None,
    app_data: dict | None = None,
    session: str | None = None,
    account: dict | None = None,
    runtime_enabled: bool = False,
) -> dict:
    """Create an account profile without changing any other account."""
    store = load_store()
    now = utc_now()
    profile = {
        "id": f"profile_{uuid.uuid4().hex[:12]}",
        "name": name or "新账户",
        "app_data": copy.deepcopy(app_data if app_data is not None else {}),
        "session": session,
        "account": account,
        "runtime_enabled": bool(runtime_enabled),
        "created_at": now,
        "updated_at": now,
    }
    store["profiles"].append(profile)
    save_store(store)
    return profile


def delete_profile(profile_id: str) -> dict:
    """Delete a stopped profile while keeping at least one account slot."""
    store = load_store()
    if len(store["profiles"]) <= 1:
        raise ValueError("Cannot delete the last profile.")

    _profile_index(store, profile_id)
    store["profiles"] = [profile for profile in store["profiles"] if profile["id"] != profile_id]
    return save_store(store)


def clear_profile_session(profile_id: str) -> dict:
    """Clear the saved Telegram session for a profile."""
    return update_profile(profile_id, session=None, account=None)
