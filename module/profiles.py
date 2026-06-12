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
        "config": _legacy_value("config", {}) or {},
        "app_data": _legacy_value("data", {}) or {},
        "bot_setting": _legacy_value("bot_setting", {}) or {},
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
                "config": profile.get("config") or {},
                "app_data": profile.get("app_data") or {},
                "bot_setting": profile.get("bot_setting") or {},
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

    active_profile_id = str(store.get("active_profile_id") or "").strip()
    if active_profile_id not in {profile["id"] for profile in normalized}:
        active_profile_id = normalized[0]["id"]

    return {
        "active_profile_id": active_profile_id,
        "profiles": normalized,
    }


def load_store() -> dict:
    """Load and normalize the profile store."""
    store = db.load_setting(PROFILE_STORE_KEY) if db.conn else None
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


def get_active_profile() -> dict:
    store = load_store()
    active_id = store["active_profile_id"]
    for profile in store["profiles"]:
        if profile["id"] == active_id:
            return profile
    return store["profiles"][0]


def _profile_index(store: dict, profile_id: str) -> int:
    for idx, profile in enumerate(store["profiles"]):
        if profile["id"] == profile_id:
            return idx
    raise KeyError(f"Profile {profile_id} not found")


def sync_active_profile_to_legacy() -> dict:
    """Write the active profile into legacy settings used by the current runtime."""
    profile = get_active_profile()
    if db.conn:
        db.save_setting("config", profile.get("config") or {})
        db.save_setting("data", profile.get("app_data") or {})
        db.save_setting("bot_setting", profile.get("bot_setting") or {})
        db.save_setting("session", profile.get("session"))
    return profile


def persist_legacy_to_active() -> dict:
    """Capture legacy settings into the active profile before switching profiles."""
    if not db.conn:
        return get_active_profile()

    return save_active_profile(
        config=_legacy_value("config", {}),
        app_data=_legacy_value("data", {}),
        bot_setting=_legacy_value("bot_setting", {}),
        session=_legacy_value("session", None),
        sync_legacy=False,
    )


def save_active_profile(
    *,
    config: Any = _UNSET,
    app_data: Any = _UNSET,
    bot_setting: Any = _UNSET,
    session: Any = _UNSET,
    account: Any = _UNSET,
    runtime_enabled: Any = _UNSET,
    name: Any = _UNSET,
    sync_legacy: bool = True,
) -> dict:
    """Update fields on the active profile."""
    store = load_store()
    idx = _profile_index(store, store["active_profile_id"])
    profile = copy.deepcopy(store["profiles"][idx])

    if config is not _UNSET:
        profile["config"] = config or {}
    if app_data is not _UNSET:
        profile["app_data"] = app_data or {}
    if bot_setting is not _UNSET:
        profile["bot_setting"] = bot_setting or {}
    if session is not _UNSET:
        profile["session"] = session
    if account is not _UNSET:
        profile["account"] = account
    if runtime_enabled is not _UNSET:
        profile["runtime_enabled"] = bool(runtime_enabled)
    if name is not _UNSET and name:
        profile["name"] = str(name)
    profile["updated_at"] = utc_now()

    store["profiles"][idx] = profile
    save_store(store)

    if sync_legacy and db.conn:
        if config is not _UNSET:
            db.save_setting("config", profile["config"])
        if app_data is not _UNSET:
            db.save_setting("data", profile["app_data"])
        if bot_setting is not _UNSET:
            db.save_setting("bot_setting", profile["bot_setting"])
        if session is not _UNSET:
            db.save_setting("session", profile["session"])

    return profile


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
    if profile_id == store["active_profile_id"]:
        sync_active_profile_to_legacy()
    return profile


def create_profile(
    *,
    name: str | None = None,
    config: dict | None = None,
    app_data: dict | None = None,
    bot_setting: dict | None = None,
    session: str | None = None,
    account: dict | None = None,
    runtime_enabled: bool = False,
    activate: bool = True,
) -> dict:
    """Create a new profile, optionally making it active."""
    store = load_store()
    active = get_active_profile()
    now = utc_now()
    profile = {
        "id": f"profile_{uuid.uuid4().hex[:12]}",
        "name": name or "新账户",
        "config": copy.deepcopy(config if config is not None else active.get("config") or {}),
        "app_data": copy.deepcopy(app_data if app_data is not None else {}),
        "bot_setting": copy.deepcopy(bot_setting if bot_setting is not None else {}),
        "session": session,
        "account": account,
        "runtime_enabled": bool(runtime_enabled),
        "created_at": now,
        "updated_at": now,
    }
    store["profiles"].append(profile)
    if activate:
        store["active_profile_id"] = profile["id"]
    save_store(store)
    if activate:
        sync_active_profile_to_legacy()
    return profile


def activate_profile(profile_id: str, *, persist_current: bool = True) -> dict:
    """Make a profile active and sync it to legacy settings."""
    if persist_current:
        persist_legacy_to_active()

    store = load_store()
    _profile_index(store, profile_id)
    store["active_profile_id"] = profile_id
    save_store(store)
    return sync_active_profile_to_legacy()


def delete_profile(profile_id: str) -> dict:
    """Delete a non-active profile."""
    store = load_store()
    if profile_id == store["active_profile_id"]:
        raise ValueError("Cannot delete the active profile. Switch to another profile first.")
    if len(store["profiles"]) <= 1:
        raise ValueError("Cannot delete the last profile.")

    _profile_index(store, profile_id)
    store["profiles"] = [profile for profile in store["profiles"] if profile["id"] != profile_id]
    return save_store(store)


def clear_profile_session(profile_id: str) -> dict:
    """Clear the saved Telegram session for a profile."""
    return update_profile(profile_id, session=None, account=None)
