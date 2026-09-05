"""web ui for media download"""

import logging
import copy
import os
import threading
import asyncio
import inspect
import json
import mimetypes
import re
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    Response,
    stream_with_context,
    send_from_directory,
)
from flask_login import LoginManager, UserMixin, login_required, login_user
from pyrogram import Client, errors
import requests
import utils
from module.app import Application
from module.bot import (
    cancel_bot_tasks_for_message,
    get_download_bot_diagnostics,
    get_download_bot_webhook_secret,
    handle_download_bot_webhook_update,
)
from module.db import db
from module.profiles import (
    clear_profile_session,
    create_profile,
    delete_profile,
    get_profile,
    get_profiles,
    update_profile,
)
from module.download_stat import (
    DownloadState,
    get_download_result,
    get_download_state,
    get_total_download_speed,
    is_retryable_failure_state,
    set_download_state,
    clear_download_history,
    remove_download_task,
    set_task_state,
    get_task_state,
)
from module.upload_stat import get_upload_result, get_total_upload_speed
from module.cloud_drive import CloudDrive
from utils.crypto import AesBase64
from utils.format import format_byte

log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

_flask_app = Flask(__name__)

_flask_app.secret_key = "tdl"
_login_manager = LoginManager()
_login_manager.login_view = "login"
_login_manager.init_app(_flask_app)
web_login_users: dict = {}
deAesCrypt = AesBase64("1234123412ABCDEF", "ABCDEF1234123412")
_client: Client = None
_app_instance: Application = None
_restart_callback = None
_start_runtime_callback = None
_stop_runtime_callback = None
_runtime_status_callback = None
_update_runtime_config_callback = None
_retry_upload_callback = None


class User(UserMixin):
    """Web Login User"""

    def __init__(self):
        self.sid = "root"

    @property
    def id(self):
        """ID"""
        return self.sid


@_login_manager.user_loader
def load_user(_):
    """
    Load a user object from the user ID.

    Returns:
        User: The user object.
    """
    return User()


def get_flask_app() -> Flask:
    """get flask app instance"""
    return _flask_app


def _render_spa():
    """Serve the built React application."""
    spa_dir = os.path.join(_flask_app.static_folder, "newui")
    index_file = os.path.join(spa_dir, "index.html")
    if os.path.exists(index_file):
        return send_from_directory(spa_dir, "index.html")
    return (
        "New UI has not been built yet. Run `npm install && npm run build` in webui/.",
        503,
    )


def _clean_scalar(value) -> str:
    """Normalize scalar values coming from JSON, env vars, or stored config."""
    if value is None:
        return ""
    return str(value).strip()


def _resolve_api_credentials(data: dict | None = None, config_data: dict | None = None):
    """Return clean Telegram API credentials and whether request supplied them."""
    data = data or {}
    config_data = config_data or {}
    request_api_id = _clean_scalar(data.get("api_id") or data.get("apiId"))
    request_api_hash = _clean_scalar(data.get("api_hash") or data.get("apiHash"))
    request_supplied = bool(request_api_id or request_api_hash)
    if request_supplied:
        return request_api_id, request_api_hash, bool(request_api_id and request_api_hash)

    api_id = request_api_id or _clean_scalar(config_data.get("api_id"))
    api_hash = request_api_hash or _clean_scalar(config_data.get("api_hash"))

    if _app_instance:
        api_id = api_id or _clean_scalar(_app_instance.api_id)
        api_hash = api_hash or _clean_scalar(_app_instance.api_hash)

    return api_id, api_hash, request_supplied and bool(request_api_id and request_api_hash)


def _load_current_config() -> dict:
    """Load the downloader configuration shared by every Telegram account."""
    current_config = {}
    if db.conn:
        current_config = db.load_setting("config") or {}

    if not current_config and _app_instance:
        current_config = _app_instance.config or {}

    return current_config


def _save_current_config(new_config: dict) -> dict:
    """Persist the shared downloader config and apply it to every runtime."""
    if not db.conn:
        return {"status": "error", "message": "Database not connected"}

    db.save_setting("config", new_config)

    if _app_instance:
        print("DEBUG: [web] Updating shared in-memory config from Web UI")
        _app_instance.config = new_config
        _app_instance.assign_config(new_config)
        _sync_web_login_secret()

    if _update_runtime_config_callback:
        # 运行中的账号全部热更新；未运行账号会在下次启动时读取同一份全局配置。
        for profile in get_profiles():
            _update_runtime_config_callback(profile["id"], new_config)

    return {
        "status": "success",
        "message": (
            "Config saved and applied in memory. Connection-level changes such as "
            "API credentials, bot token, web host, and web port still require "
            "reconnect/restart."
        ),
    }


def _bot_access_from_config(config: dict | None) -> dict:
    """Extract Bot submitter access settings from one profile config."""
    config = config or {}
    configured_mode = config.get("bot_download_access_mode")
    allowed_users = config.get("allowed_user_ids") or []
    if not isinstance(allowed_users, list):
        allowed_users = []

    if configured_mode in ("self", "allowed", "public"):
        mode = configured_mode
    elif config.get("bot_allow_public_download"):
        mode = "public"
    elif allowed_users:
        mode = "allowed"
    else:
        mode = "self"

    return {
        "mode": mode,
        "allowedUsers": [str(item) for item in allowed_users],
    }


def _apply_bot_access_to_config(
    config: dict | None, mode: str, allowed_users: list[str]
) -> dict:
    next_config = copy.deepcopy(config or {})
    next_config["bot_download_access_mode"] = mode
    next_config["bot_allow_public_download"] = mode == "public"
    next_config["allowed_user_ids"] = allowed_users
    return next_config


def _save_profile_config(profile_id: str, new_config: dict) -> dict:
    """Compatibility adapter: former profile settings now update shared config."""
    get_profile(profile_id)
    return _save_current_config(new_config)


def _account_payload_from_user(me) -> dict:
    """Build a serializable Telegram account payload."""
    first_name = getattr(me, "first_name", None) or ""
    last_name = getattr(me, "last_name", None) or ""
    full_name = f"{first_name} {last_name}".strip() or getattr(me, "username", None) or str(me.id)
    return {
        "id": str(me.id),
        "phoneNumber": getattr(me, "phone_number", None) or "",
        "username": f"@{me.username}" if getattr(me, "username", None) else "",
        "firstName": full_name,
        "isPremium": getattr(me, "is_premium", False),
    }


def _get_client_account_meta(client: Client = None) -> dict | None:
    """Read account metadata from a connected Pyrogram client."""
    client = client or _client
    if not client:
        return None

    try:
        me = getattr(client, "me", None)
        if not me and getattr(client, "is_connected", False) and _app_instance:
            future = asyncio.run_coroutine_threadsafe(client.get_me(), _app_instance.loop)
            me = future.result(timeout=30)
        if me:
            return _account_payload_from_user(me)
    except Exception as e:
        print(f"DEBUG: [account_meta] Error getting user info: {e}")

    return None


def _profile_to_account(
    profile: dict,
    runtime_info: dict | None = None,
    shared_config: dict | None = None,
) -> dict:
    """Convert a profile into the React account card shape."""
    runtime_info = runtime_info or {}
    runtime_account = runtime_info.get("account") or {}
    account = runtime_account or (profile.get("account") or {})
    profile_name = profile.get("name") or account.get("firstName") or "Telegram Profile"
    is_running = bool(runtime_info.get("running"))

    return {
        "id": profile["id"],
        "profileId": profile["id"],
        "profileName": profile_name,
        "userId": str(account.get("id") or ""),
        "phoneNumber": account.get("phoneNumber") or "",
        "username": account.get("username") or "",
        "firstName": account.get("firstName") or profile_name,
        "status": "connected" if is_running else "disconnected",
        "sessionName": "running_session" if is_running else "saved_session",
        "createdAt": profile.get("created_at") or "",
        "hasSession": bool(profile.get("session")),
        "isRunning": is_running,
        "runtimeStatus": runtime_info.get("status") or "stopped",
        "runtimeMessage": runtime_info.get("message") or "",
        "botRunning": bool(runtime_info.get("bot_started")),
        "runtimeEnabled": bool(profile.get("runtime_enabled")),
        "botAccess": _bot_access_from_config(shared_config),
    }


def _get_telegram_account_status() -> dict:
    """Return Telegram session state for the React UI."""
    profiles = get_profiles() if db.conn else []
    shared_config = _load_current_config()
    runtime_status = _runtime_status_callback() if _runtime_status_callback else {}
    accounts = [
        _profile_to_account(
            profile,
            runtime_status.get(profile["id"]),
            shared_config,
        )
        for profile in profiles
    ]
    running_accounts = [item for item in accounts if item["isRunning"]]
    representative_account = (running_accounts or accounts or [None])[0]

    status = {
        "logged_in": bool(running_accounts),
        "session_exists": any(item.get("hasSession") for item in accounts),
        "account": representative_account,
        "accounts": accounts,
        "botReceiver": get_download_bot_diagnostics(ensure_polling=True),
    }

    return status


def run_web_server(app: Application):
    """
    Runs a web server using the Flask framework.
    """
    _flask_app.config["TEMPLATES_AUTO_RELOAD"] = True
    get_flask_app().run(
        app.web_host, app.web_port, debug=True, use_reloader=True
    )


# pylint: disable = W0603
def init_web(
    app: Application,
    client: Client = None,
    restart_callback=None,
    start_runtime_callback=None,
    stop_runtime_callback=None,
    runtime_status_callback=None,
    update_runtime_config_callback=None,
    retry_upload_callback=None,
):
    """
    Set the value of the users variable.

    Args:
        users: The list of users to set.

    Returns:
        None.
    """
    global web_login_users
    global _client
    global _restart_callback
    global _start_runtime_callback
    global _stop_runtime_callback
    global _runtime_status_callback
    global _update_runtime_config_callback
    global _retry_upload_callback
    global _app_instance
    _client = client
    _restart_callback = restart_callback
    _start_runtime_callback = start_runtime_callback
    _stop_runtime_callback = stop_runtime_callback
    _runtime_status_callback = runtime_status_callback
    _update_runtime_config_callback = update_runtime_config_callback
    _retry_upload_callback = retry_upload_callback
    _app_instance = app

    if app.web_login_secret:
        web_login_users = {"root": app.web_login_secret}
    else:
        _flask_app.config["LOGIN_DISABLED"] = True
    _flask_app.config["TEMPLATES_AUTO_RELOAD"] = True
    if app.debug_web:
        threading.Thread(target=run_web_server, args=(app,)).start()
    else:
        threading.Thread(
            target=get_flask_app().run, daemon=True, args=(app.web_host, app.web_port)
        ).start()


@_flask_app.route("/")
@_flask_app.route("/files")
@_flask_app.route("/accounts")
@login_required
def index():
    """index"""
    return _render_spa()


@_flask_app.route("/login", methods=["GET", "POST"])
def login():
    """login"""
    if request.method == "POST":
        password = request.json.get("password")
        if password:
            if web_login_users.get("root") == deAesCrypt.decrypt(password):
                user = User()
                login_user(user)
                return jsonify({"code": "1"})

        return jsonify({"code": "0"})
    return render_template("login.html")


@_flask_app.route("/config", methods=["GET", "POST"])
@login_required
def config():
    """Config Editor"""
    if request.method == "POST":
        try:
            payload = request.get_json(silent=True)
            if payload is None:
                new_config = json.loads(request.form.get("config"))
            else:
                new_config = payload.get("config", payload)

            return jsonify(_save_current_config(new_config))
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})

    return _render_spa()


@_flask_app.route("/test_webdav", methods=["POST"])
@login_required
def test_webdav():
    """Test WebDAV connection"""
    try:
        data = request.json
        url = data.get("url")
        username = data.get("username")
        password = data.get("password")
        
        # Create a new loop for this thread to run the async task
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success, message = loop.run_until_complete(CloudDrive.test_webdav_connection(url, username, password))
        finally:
            loop.close()

        return jsonify({"success": success, "message": message})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@_flask_app.route("/clear_config_cache", methods=["GET", "POST"])
@login_required
def clear_config_cache():
    """Clear config cache from database and reload local config"""
    if request.method == "POST":
        try:
            if db.conn:
                # 1. Clear database cache
                db.save_setting("config", None)
                db.save_setting("data", None)
                print("DEBUG: [clear_config_cache] Config and data cache cleared from database")
                
                # 2. CRITICAL FIX: Reload local config.yaml into memory immediately
                # This prevents the old in-memory config from overwriting the DB when app exits
                if _app_instance:
                    import yaml as _yaml
                    try:
                        with open(_app_instance.config_file, encoding="utf-8") as f:
                            local_config = _yaml.load(f.read(), Loader=_yaml.SafeLoader)
                            if local_config:
                                print("DEBUG: [clear_config_cache] Reloading in-memory config from local file")
                                _app_instance.config = local_config
                                _app_instance.assign_config(local_config)
                    except Exception as load_err:
                        print(f"ERROR: [clear_config_cache] Failed to reload local config: {load_err}")

                return render_template(
                    'action_result.html',
                    success=True,
                    title="修复成功 & 已重新加载!",
                    message=(
                        "<p>1. 数据库缓存已清除。</p>"
                        "<p>2. 本地 config.yaml 已加载到内存。</p>"
                        "<p>普通配置已立即生效；连接级设置仍需重新连接或重启。</p>"
                    ),
                )
            else:
                return "Error: Database not connected"
        except Exception as e:
            import traceback
            traceback.print_exc()
            return f"Error: {str(e)}"
    
    return render_template('action_result.html', success=False, title="修复配置循环", message="<p>这将清除数据库缓存并强制加载您的本地 config.yaml 文件。</p><p>这修复了旧设置在重启后不断回滚的问题。</p><form method='post'><button type='submit' class='btn btn-danger mt-4'>🚀 修复配置 & 清除缓存</button></form>")


@_flask_app.route("/restart", methods=["POST"])
@login_required
def restart():
    """Request an application restart from the configured supervisor."""
    if not _restart_callback:
        return jsonify({"success": False, "message": "Restart callback is not configured."}), 503

    threading.Thread(target=_restart_callback, daemon=True).start()
    return jsonify(
        {
            "success": True,
            "message": "Restart requested. On Render/Docker, the process supervisor should start it again.",
        }
    )


@_flask_app.route("/api/bootstrap")
@login_required
def api_bootstrap():
    """Return initial data for the React app."""
    return jsonify(
        {
            "version": utils.__version__,
            "db": db.get_heartbeat_status(),
            "config": _load_current_config(),
            "account": _get_telegram_account_status(),
        }
    )


@_flask_app.route("/api/config", methods=["GET", "POST"])
@login_required
def api_config():
    """JSON config endpoint for the React app."""
    if request.method == "GET":
        return jsonify({"config": _load_current_config()})

    payload = request.get_json(silent=True) or {}
    new_config = payload.get("config", payload)
    if not isinstance(new_config, dict):
        return jsonify({"status": "error", "message": "Invalid config payload"}), 400

    return jsonify(_save_current_config(new_config))


@_flask_app.route("/api/account/status")
@login_required
def api_account_status():
    """Return Telegram account/session status."""
    return jsonify(_get_telegram_account_status())


_WEBDAV_PREVIEW_RESPONSE_HEADERS = (
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
    "ETag",
    "Last-Modified",
)
_WEBDAV_INLINE_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/avif",
    "image/bmp",
}
_WEBDAV_PREVIEW_RANGE_CHUNK_SIZE = 4 * 1024 * 1024


def _bounded_webdav_preview_range(range_header: str) -> str:
    """Cap open-ended preview ranges so browsers can quickly request MP4 tail metadata."""
    match = re.fullmatch(r"bytes=(\d+)-", (range_header or "").strip())
    if not match:
        return range_header
    start = int(match.group(1))
    end = start + _WEBDAV_PREVIEW_RANGE_CHUNK_SIZE - 1
    return f"bytes={start}-{end}"


def _is_webdav_inline_type(content_type: str) -> bool:
    """Only render media types that cannot execute same-origin page scripts."""
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    return (
        normalized_type in _WEBDAV_INLINE_IMAGE_TYPES
        or normalized_type.startswith("video/")
        or normalized_type.startswith("audio/")
        or normalized_type in {"application/pdf", "text/plain"}
    )


@_flask_app.route("/api/webdav/browse", methods=["GET"])
@login_required
def api_webdav_browse():
    """List one real WebDAV directory (PROPFIND depth 1) under the configured root.

    Query: ?path=<relative path inside remote_dir, empty for root>.
    Returns {success, path, folders:[{name,path}], files:[{name,path,size,modified,content_type}]}
    """
    if not _app_instance or not getattr(_app_instance, "cloud_drive_config", None):
        return jsonify({"success": False, "message": "WebDAV 未初始化"}), 503

    drive_config = _app_instance.cloud_drive_config
    remote_root = (drive_config.remote_dir or "").strip("/")
    rel_path = (request.args.get("path") or "").strip().strip("/")
    # 浏览限定在 remote_dir 之内；rel_path 为空即根目录。
    full_path = f"{remote_root}/{rel_path}".strip("/") if rel_path else remote_root
    try:
        remote_url = CloudDrive.build_webdav_remote_url(drive_config, full_path)
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400

    auth = None
    if drive_config.webdav_username:
        auth = (drive_config.webdav_username, drive_config.webdav_password)

    try:
        resp = requests.request(
            "PROPFIND",
            remote_url,
            headers={"Depth": "1", "User-Agent": "TelegramMediaDownloader/1.0"},
            auth=auth,
            timeout=(10, 60),
        )
    except requests.RequestException as error:
        return jsonify({"success": False, "message": f"WebDAV 连接失败: {error}"}), 502

    if resp.status_code == 404:
        return jsonify({
            "success": True,
            "path": rel_path,
            "folders": [],
            "files": [],
            "message": "目录不存在或为空",
        })
    if resp.status_code not in (200, 207):
        return jsonify({
            "success": False,
            "message": f"WebDAV 返回状态 {resp.status_code}",
        }), 502

    # 解析 multistatus XML：第一个 response 是目录自身，其余为子项。
    import xml.etree.ElementTree as ET

    folders = []
    files = []
    try:
        root_xml = ET.fromstring(resp.content)
    except ET.ParseError:
        return jsonify({"success": False, "message": "WebDAV 响应解析失败"}), 502

    def _localname(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    def _child_text(node, name: str):
        for child in node.iter():
            if _localname(child.tag) == name:
                return (child.text or "").strip()
        return ""

    # PROPFIND 返回的 href 是 URL 编码的全路径（含 webdav_url 前缀），
    # 只取 remote_root 之后的部分作为展示/导航用的相对路径。
    root_prefix = urllib.parse.quote(full_path, safe="/").rstrip("/") + "/"

    for response_el in root_xml.iter():
        if _localname(response_el.tag) != "response":
            continue
        href_raw = ""
        for child in response_el:
            if _localname(child.tag) == "href":
                href_raw = (child.text or "").strip()
                break
        if not href_raw:
            continue
        href_decoded = urllib.parse.unquote(href_raw)
        # 跳过目录自身（href 与请求路径相同，即以 full_path 结尾且无更多段）
        rel_from_root = href_decoded
        if full_path and full_path + "/" in href_decoded:
            rel_from_root = href_decoded.split(full_path + "/", 1)[1]
        elif full_path and href_decoded.rstrip("/").endswith("/" + full_path):
            continue  # the collection itself
        name = rel_from_root.rstrip("/")
        if not name:
            continue

        is_collection = False
        size_text = ""
        modified_text = ""
        content_type = ""
        for propstat in response_el.iter():
            if _localname(propstat.tag) == "resourcetype":
                if any(_localname(x.tag) == "collection" for x in propstat.iter()):
                    is_collection = True
            elif _localname(propstat.tag) == "getcontentlength":
                size_text = (propstat.text or "").strip()
            elif _localname(propstat.tag) == "getlastmodified":
                modified_text = (propstat.text or "").strip()
            elif _localname(propstat.tag) == "getcontenttype":
                content_type = (propstat.text or "").strip()

        entry_rel = f"{rel_path}/{name}".strip("/") if rel_path else name
        if is_collection or href_raw.endswith("/"):
            if name:
                folders.append({"name": name, "path": entry_rel})
        else:
            try:
                size = int(size_text) if size_text else 0
            except ValueError:
                size = 0
            files.append({
                "name": name,
                "path": entry_rel,
                "size": size,
                "modified": modified_text,
                "content_type": content_type,
            })

    folders.sort(key=lambda item: item["name"])
    files.sort(key=lambda item: item["name"])
    return jsonify({
        "success": True,
        "path": rel_path,
        "root": remote_root,
        "folders": folders,
        "files": files,
    })


@_flask_app.route("/api/webdav/summary", methods=["GET"])
@login_required
def api_webdav_summary():
    """Look up the AI summary for a real WebDAV file by its relative upload path.

    The file browser lists live WebDAV entries; summaries live on download
    history records. Match by relative_path (channel/.../file).
    """
    rel_path = (request.args.get("path") or "").strip().strip("/")
    if not rel_path:
        return jsonify({"success": False, "message": "path is required"}), 400

    download_result = get_download_result()
    remote_dir = ""
    if _app_instance and hasattr(_app_instance, "cloud_drive_config"):
        remote_dir = (_app_instance.cloud_drive_config.remote_dir or "").strip("/")

    match_name = os.path.basename(rel_path)
    for chat_id, messages in download_result.items():
        for msg_id, record in (messages or {}).items():
            file_name = (record or {}).get("file_name") or ""
            base_name = os.path.basename(file_name.replace("\\", "/"))
            if base_name != match_name:
                continue
            # 以远程根为参照的相对路径要一致，避免同名文件误配。
            record_rel = CloudDrive.get_relative_upload_path(
                getattr(_app_instance, "save_path", "") if _app_instance else "",
                file_name,
            )
            if record_rel != rel_path:
                continue
            return jsonify({
                "success": True,
                "ai_summary": (record or {}).get("ai_summary") or "",
                "ai_status": (record or {}).get("ai_status") or "",
                "ai_tags": (record or {}).get("ai_tags") or [],
            })
    return jsonify({"success": True, "ai_summary": "", "ai_status": "", "ai_tags": []})


@_flask_app.route("/api/webdav/preview", methods=["GET", "HEAD"])
@login_required
def api_webdav_preview():
    """Proxy an authenticated WebDAV object for same-origin browser preview."""
    if not _app_instance or not getattr(_app_instance, "cloud_drive_config", None):
        return jsonify({"success": False, "message": "WebDAV 未初始化"}), 503

    drive_config = _app_instance.cloud_drive_config
    remote_path = request.args.get("path", "")
    try:
        remote_url = CloudDrive.build_webdav_remote_url(
            drive_config, remote_path
        )
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400

    upstream_headers = {
        "Accept-Encoding": "identity",
        "User-Agent": "TelegramMediaDownloader/1.0",
    }
    # 视频拖动、断点读取和浏览器缓存校验都由 WebDAV 源站决定，代理只透传必要请求头。
    for header_name in (
        "Range",
        "If-Range",
        "If-None-Match",
        "If-Modified-Since",
    ):
        if request.headers.get(header_name):
            upstream_headers[header_name] = request.headers[header_name]
    if request.args.get("download") != "1" and upstream_headers.get("Range"):
        # MP4 的 moov 常位于文件尾部；有限分块促使浏览器尽快请求尾部元数据，而非先下载整部视频。
        upstream_headers["Range"] = _bounded_webdav_preview_range(
            upstream_headers["Range"]
        )

    auth = None
    if drive_config.webdav_username:
        auth = (
            drive_config.webdav_username,
            drive_config.webdav_password,
        )

    upstream = None
    try:
        upstream = requests.request(
            request.method,
            remote_url,
            headers=upstream_headers,
            auth=auth,
            stream=True,
            allow_redirects=True,
            timeout=(10, 3600),
        )
    except requests.RequestException as error:
        return jsonify(
            {"success": False, "message": f"WebDAV 预览连接失败: {error}"}
        ), 502

    if upstream.status_code not in (200, 206, 304):
        status_code = upstream.status_code
        upstream.close()
        if status_code == 401:
            message = "WebDAV 鉴权失败"
        elif status_code == 404:
            message = "WebDAV 文件不存在"
        elif status_code == 416:
            message = "WebDAV 不接受该字节范围"
        else:
            message = f"WebDAV 返回状态 {status_code}"
        return jsonify({"success": False, "message": message}), status_code

    guessed_type = mimetypes.guess_type(remote_path)[0]
    content_type = upstream.headers.get("Content-Type") or guessed_type or "application/octet-stream"
    force_download = request.args.get("download") == "1" or not _is_webdav_inline_type(
        content_type
    )
    filename = remote_path.replace("\\", "/").rstrip("/").split("/")[-1] or "download"
    disposition = "attachment" if force_download else "inline"

    response_headers = {
        "Content-Type": content_type if not force_download else "application/octet-stream",
        "Content-Disposition": (
            f"{disposition}; filename*=UTF-8''{urllib.parse.quote(filename, safe='')}"
        ),
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
    }
    for header_name in _WEBDAV_PREVIEW_RESPONSE_HEADERS:
        if upstream.headers.get(header_name):
            response_headers[header_name] = upstream.headers[header_name]
    if upstream.status_code == 206 and "Accept-Ranges" not in response_headers:
        response_headers["Accept-Ranges"] = "bytes"

    if request.method == "HEAD" or upstream.status_code == 304:
        upstream.close()
        return Response(
            status=upstream.status_code,
            headers=response_headers,
        )

    def generate_webdav_content():
        try:
            for chunk in upstream.iter_content(chunk_size=256 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(
        stream_with_context(generate_webdav_content()),
        status=upstream.status_code,
        headers=response_headers,
        direct_passthrough=True,
    )


@_flask_app.route("/api/telegram/webhook/<secret>", methods=["POST"])
def api_telegram_webhook(secret):
    """Receive Telegram Bot API webhook updates."""

    if not secret or secret != get_download_bot_webhook_secret():
        return jsonify({"ok": False}), 404

    payload = request.get_json(silent=True) or {}
    response_payload = handle_download_bot_webhook_update(payload)
    if response_payload:
        return jsonify(response_payload)
    return Response(status=204)


@_flask_app.route("/api/profiles", methods=["POST"])
@login_required
def api_profiles_create():
    """Create a Telegram account/config profile."""
    try:
        if not db.conn:
            return jsonify({"success": False, "message": "Database not connected"}), 503

        data = request.get_json(silent=True) or {}
        profile = create_profile(
            name=data.get("name") or data.get("profile_name") or "新账户",
        )

        return jsonify(
            {
                "success": True,
                "message": "Profile created.",
                "profile": profile,
                "account": _get_telegram_account_status(),
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/api/profiles/update", methods=["POST", "PATCH"])
@_flask_app.route("/api/profiles/<profile_id>", methods=["PATCH"])
@login_required
def api_profiles_update(profile_id=None):
    """Update profile metadata."""
    try:
        if not db.conn:
            return jsonify({"success": False, "message": "Database not connected"}), 503

        data = request.get_json(silent=True) or {}
        profile_id = profile_id or data.get("profile_id") or data.get("profileId")
        name = (data.get("name") or data.get("profile_name") or "").strip()
        if not profile_id:
            return jsonify({"success": False, "message": "profile_id is required"}), 400
        if not name:
            return jsonify({"success": False, "message": "name is required"}), 400

        profile = update_profile(profile_id, name=name)
        return jsonify(
            {
                "success": True,
                "message": "Profile updated.",
                "profile": profile,
                "account": _get_telegram_account_status(),
            }
        )
    except KeyError:
        return jsonify({"success": False, "message": "Profile not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/api/profiles/delete", methods=["POST"])
@_flask_app.route("/api/profiles/<profile_id>", methods=["DELETE"])
@login_required
def api_profiles_delete(profile_id=None):
    """Delete a non-active profile."""
    try:
        if not db.conn:
            return jsonify({"success": False, "message": "Database not connected"}), 503

        data = request.get_json(silent=True) or {}
        profile_id = profile_id or data.get("profile_id") or data.get("profileId")
        if not profile_id:
            return jsonify({"success": False, "message": "profile_id is required"}), 400

        runtime_status = _runtime_status_callback() if _runtime_status_callback else {}
        if runtime_status.get(profile_id, {}).get("running"):
            return jsonify(
                {
                    "success": False,
                    "message": "账号正在运行，请先停止后台任务再删除。",
                }
            ), 409

        delete_profile(profile_id)
        return jsonify(
            {
                "success": True,
                "message": "Profile deleted.",
                "account": _get_telegram_account_status(),
            }
        )
    except KeyError:
        return jsonify({"success": False, "message": "Profile not found"}), 404
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/api/profiles/activate", methods=["POST"])
@login_required
def api_profiles_activate():
    """Compatibility endpoint for old clients; account selection is now local UI state."""
    try:
        if not db.conn:
            return jsonify({"success": False, "message": "Database not connected"}), 503

        data = request.get_json(silent=True) or {}
        profile_id = data.get("profile_id") or data.get("profileId")
        if not profile_id:
            return jsonify({"success": False, "message": "profile_id is required"}), 400

        get_profile(profile_id)

        return jsonify(
            {
                "success": True,
                "message": "Account selection no longer changes backend state.",
                "runtime": {
                    "status": "selected",
                    "message": "账号选择仅用于当前页面操作。",
                },
                "account": _get_telegram_account_status(),
            }
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/api/profiles/start", methods=["POST"])
@login_required
def api_profiles_start():
    """Start one profile runtime."""
    try:
        if not db.conn:
            return jsonify({"success": False, "message": "Database not connected"}), 503
        if not _start_runtime_callback:
            return jsonify({"success": False, "message": "Runtime callback is not configured."}), 503

        data = request.get_json(silent=True) or {}
        profile_id = data.get("profile_id") or data.get("profileId")
        if not profile_id:
            return jsonify({"success": False, "message": "profile_id is required"}), 400

        profile = next((item for item in get_profiles() if item["id"] == profile_id), None)
        if not profile:
            return jsonify({"success": False, "message": "Profile not found"}), 404
        if not profile.get("session"):
            return jsonify({"success": False, "message": "No saved Telegram session found."}), 404

        runtime_result = _start_runtime_callback(None, profile)
        return jsonify(
            {
                "success": runtime_result.get("status") != "error",
                "runtime": runtime_result,
                "account": _get_telegram_account_status(),
            }
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/api/profiles/bot_access", methods=["POST"])
@_flask_app.route("/api/profiles/<profile_id>/bot_access", methods=["POST", "PATCH"])
@_flask_app.route("/api/bot/access", methods=["POST", "PATCH"])
@login_required
def api_profiles_bot_access(profile_id=None):
    """Save process-wide Bot submitter access settings."""
    try:
        if not db.conn:
            return jsonify({"success": False, "message": "Database not connected"}), 503

        data = request.get_json(silent=True) or {}
        profile_id = profile_id or data.get("profile_id") or data.get("profileId")

        mode = data.get("mode") or data.get("bot_download_access_mode") or "self"
        if mode not in ("self", "allowed", "public"):
            return jsonify({"success": False, "message": "Invalid access mode"}), 400

        allowed_users = data.get("allowedUsers", data.get("allowed_user_ids", []))
        if not isinstance(allowed_users, list):
            allowed_users = []
        allowed_users = [str(item).strip() for item in allowed_users if str(item).strip()]

        if profile_id:
            get_profile(profile_id)

        next_config = _apply_bot_access_to_config(
            _load_current_config(), mode, allowed_users
        )
        result = (
            _save_profile_config(profile_id, next_config)
            if profile_id
            else _save_current_config(next_config)
        )
        if result.get("status") == "error":
            return jsonify({"success": False, **result}), 500

        return jsonify(
            {
                "success": True,
                "message": "Bot access saved.",
                "config": next_config,
                "botAccess": _bot_access_from_config(next_config),
                "account": _get_telegram_account_status(),
            }
        )
    except KeyError:
        return jsonify({"success": False, "message": "Profile not found"}), 404
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/api/profiles/stop", methods=["POST"])
@login_required
def api_profiles_stop():
    """Stop one profile runtime."""
    try:
        if not _stop_runtime_callback:
            return jsonify({"success": False, "message": "Runtime callback is not configured."}), 503

        data = request.get_json(silent=True) or {}
        profile_id = data.get("profile_id") or data.get("profileId")
        if not profile_id:
            return jsonify({"success": False, "message": "profile_id is required"}), 400

        runtime_result = _stop_runtime_callback(profile_id)
        return jsonify(
            {
                "success": True,
                "runtime": runtime_result,
                "account": _get_telegram_account_status(),
            }
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/api/account/logout", methods=["POST"])
@login_required
def api_account_logout():
    """Disconnect runtime and clear saved Telegram session."""
    global _client
    try:
        data = request.get_json(silent=True) or {}
        profile_id = data.get("profile_id") or data.get("profileId")
        if not profile_id and db.conn:
            saved_profiles = get_profiles()
            if len(saved_profiles) == 1:
                profile_id = saved_profiles[0]["id"]
        if not profile_id:
            return jsonify({"success": False, "message": "profile_id is required"}), 400

        if _stop_runtime_callback:
            _stop_runtime_callback(profile_id)

        if db.conn:
            clear_profile_session(profile_id)

        return jsonify(
            {
                "success": True,
                "message": "Session disconnected.",
                "account": _get_telegram_account_status(),
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/api/account/connect_saved_session", methods=["POST"])
@login_required
def api_account_connect_saved_session():
    """Start runtime with an already saved session string."""
    try:
        if not db.conn:
            return jsonify({"success": False, "message": "Database not connected"}), 503
        if not _start_runtime_callback:
            return jsonify({"success": False, "message": "Runtime callback is not configured."}), 503

        data = request.get_json(silent=True) or {}
        requested_profile_id = data.get("profile_id") or data.get("profileId")
        if not requested_profile_id:
            return jsonify({"success": False, "message": "profile_id is required"}), 400
        profile = get_profile(requested_profile_id)

        saved_session = profile.get("session")
        if not saved_session:
            return jsonify({"success": False, "message": "No saved Telegram session found."}), 404

        runtime_result = _start_runtime_callback(None, profile)
        return jsonify({"success": True, "runtime": runtime_result, "account": _get_telegram_account_status()})
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/api/account/send_code", methods=["POST"])
@login_required
def api_account_send_code():
    """Send Telegram login code."""
    global _client
    data = request.get_json(silent=True) or {}
    phone_number = _clean_scalar(data.get("phone_number") or data.get("phoneNumber"))
    if not phone_number:
        return jsonify({"success": False, "message": "phone_number is required"}), 400

    create_profile_flag = bool(data.get("create_profile", True))
    target_profile_id = data.get("profile_id") or data.get("profileId")
    session["phone_number"] = phone_number
    session["login_create_profile"] = create_profile_flag
    session["login_profile_id"] = target_profile_id
    session["login_profile_name"] = data.get("profile_name") or data.get("profileName") or phone_number

    try:
        if target_profile_id:
            if _stop_runtime_callback:
                _stop_runtime_callback(target_profile_id)
            if _client:
                try:
                    loop = _app_instance.loop
                    if _client.is_connected:
                        future = asyncio.run_coroutine_threadsafe(_client.disconnect(), loop)
                        future.result(timeout=10)
                except Exception as e:
                    print(f"DEBUG: [api_account_send_code] Error disconnecting active client: {e}")
                _client = None

        if target_profile_id and not create_profile_flag:
            get_profile(target_profile_id)

        config_data = _load_current_config()
        api_id, api_hash, request_api_supplied = _resolve_api_credentials(data, config_data)
        if not api_id or not api_hash:
            return jsonify({"success": False, "message": "api_id or api_hash is missing."}), 400

        loop = _app_instance.loop
        if not _client:
            future = asyncio.run_coroutine_threadsafe(
                _create_and_connect_client(
                    api_id,
                    api_hash,
                    _app_instance.proxy,
                    _app_instance.session_file_path,
                    loop,
                ),
                loop,
            )
            _client = future.result(timeout=60)
        elif not _client.is_connected:
            future = asyncio.run_coroutine_threadsafe(_client.connect(), loop)
            future.result(timeout=30)

        future = asyncio.run_coroutine_threadsafe(_send_code(_client, phone_number), loop)
        sent_code = future.result(timeout=30)
        session["phone_code_hash"] = sent_code.phone_code_hash
        if request_api_supplied:
            session["login_api_id"] = api_id
            session["login_api_hash"] = api_hash
        return jsonify({"success": True, "message": "Verification code sent."})
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/api/account/verify_code", methods=["POST"])
@login_required
def api_account_verify_code():
    """Verify Telegram login code."""
    data = request.get_json(silent=True) or {}
    code = data.get("code")
    phone_number = session.get("phone_number")
    phone_code_hash = session.get("phone_code_hash")

    if not code:
        return jsonify({"success": False, "message": "code is required"}), 400
    if not phone_number or not phone_code_hash:
        return jsonify({"success": False, "message": "Login session expired."}), 400
    if not _client:
        return jsonify({"success": False, "message": "Client not initialized."}), 400

    try:
        loop = _app_instance.loop
        future = asyncio.run_coroutine_threadsafe(
            _sign_in_wrapper(_client, phone_number, phone_code_hash, code), loop
        )
        future.result(timeout=30)

        _, runtime_result = _save_session_and_start_runtime(_client)
        return jsonify(
            {
                "success": True,
                "needs_password": False,
                "runtime": runtime_result,
                "account": _get_telegram_account_status(),
            }
        )
    except errors.SessionPasswordNeeded:
        return jsonify({"success": True, "needs_password": True})
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/api/account/verify_password", methods=["POST"])
@login_required
def api_account_verify_password():
    """Verify Telegram two-step password."""
    data = request.get_json(silent=True) or {}
    password = data.get("password")
    if not password:
        return jsonify({"success": False, "message": "password is required"}), 400
    if not _client:
        return jsonify({"success": False, "message": "Client not initialized."}), 400

    try:
        loop = _app_instance.loop
        future = asyncio.run_coroutine_threadsafe(
            _check_password_wrapper(_client, password), loop
        )
        future.result(timeout=30)

        _, runtime_result = _save_session_and_start_runtime(_client)
        return jsonify(
            {
                "success": True,
                "runtime": runtime_result,
                "account": _get_telegram_account_status(),
            }
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


# --- Telegram Login Routes ---


async def _create_and_connect_client(api_id, api_hash, proxy, workdir, loop):
    """Create and connect client on the main event loop."""
    from module.pyrogram_extension import HookClient
    import asyncio

    print(
        f"DEBUG: _create_and_connect_client running on loop: {id(asyncio.get_running_loop())}"
    )
    print(f"DEBUG: Passed loop: {id(loop)}")

    client = HookClient(
        "media_downloader",
        api_id=int(api_id),
        api_hash=api_hash,
        proxy=proxy,
        workdir=workdir,
        in_memory=True,
        loop=loop,
        no_updates=True,
    )
    print(f"DEBUG: Client created with loop: {id(client.loop)}")
    await client.connect()
    return client


def _create_client_from_session(api_id, api_hash, proxy, workdir, loop, session_string):
    """Create a client from an already saved session string."""
    from module.pyrogram_extension import HookClient

    return HookClient(
        "media_downloader",
        api_id=int(api_id),
        api_hash=api_hash,
        proxy=proxy,
        workdir=workdir,
        start_timeout=_app_instance.start_timeout if _app_instance else 60,
        session_string=session_string,
        in_memory=False,
        loop=loop,
        no_updates=True,
    )


async def _send_code(client, phone_number):
    """Send code on the main event loop."""
    return await client.send_code(phone_number)


async def _sign_in_wrapper(client, phone_number, phone_code_hash, code):
    """Sign in wrapper to debug loop issues."""
    import asyncio

    current_loop = asyncio.get_running_loop()
    print(f"DEBUG: _sign_in_wrapper running. Current Loop: {id(current_loop)}")
    print(f"DEBUG: client.loop: {id(client.loop)}")

    if getattr(client, "loop", None) and client.loop != current_loop:
        print(
            f"CRITICAL: Loop mismatch! Client loop: {id(client.loop)} vs Current: {id(current_loop)}"
        )
        # Try to patch it?
        # client.loop = current_loop

    if not client.is_connected:
        print("DEBUG: Client disconnected. Reconnecting...")
        await client.connect()

    return await client.sign_in(phone_number, phone_code_hash, code)


async def _check_password_wrapper(client, password):
    """Check password wrapper to avoid event loop conflicts."""
    import asyncio

    current_loop = asyncio.get_running_loop()
    print(f"DEBUG: _check_password_wrapper running. Current Loop: {id(current_loop)}")
    print(f"DEBUG: client.loop: {id(client.loop)}")

    if not client.is_connected:
        print("DEBUG: Client disconnected. Reconnecting...")
        await client.connect()

    return await client.check_password(password)


def _sync_web_login_secret():
    """Apply web login password changes without restarting the process."""
    global web_login_users
    if not _app_instance:
        return

    if _app_instance.web_login_secret:
        web_login_users = {"root": _app_instance.web_login_secret}
        _flask_app.config["LOGIN_DISABLED"] = False
    else:
        web_login_users = {}
        _flask_app.config["LOGIN_DISABLED"] = True


def _save_session_and_start_runtime(client):
    """Persist Telegram session and start runtime tasks if a callback is available."""
    print("DEBUG: [tg_login] Calling export_session_string...")
    session_string = client.export_session_string()
    if inspect.isawaitable(session_string):
        future = asyncio.run_coroutine_threadsafe(session_string, _app_instance.loop)
        session_string = future.result(timeout=30)
    print(
        "DEBUG: [tg_login] Session string length: "
        f"{len(session_string) if session_string else 0}"
    )

    account_meta = _get_client_account_meta(client) or {}
    display_name = (
        session.pop("login_profile_name", None)
        or account_meta.get("firstName")
        or account_meta.get("username")
        or "Telegram Profile"
    )
    target_profile_id = session.pop("login_profile_id", None)
    create_new_profile = bool(session.pop("login_create_profile", False))
    login_api_id = session.pop("login_api_id", None)
    login_api_hash = session.pop("login_api_hash", None)
    profile_config = _load_current_config()
    if login_api_id and login_api_hash:
        profile_config = dict(profile_config)
        profile_config["api_id"] = login_api_id
        profile_config["api_hash"] = login_api_hash
        # Telegram API 凭据属于下载器部署，新增账号时更新全局配置供所有 runtime 使用。
        _save_current_config(profile_config)

    profile = None
    if db.conn:
        print("DEBUG: [tg_login] Saving session to target profile...")
        if create_new_profile or not target_profile_id:
            profile = create_profile(
                name=display_name,
                session=session_string,
                account=account_meta,
                runtime_enabled=True,
            )
        else:
            profile = update_profile(
                target_profile_id,
                name=display_name,
                session=session_string,
                account=account_meta,
                runtime_enabled=True,
            )
        print("DEBUG: [tg_login] Profile session saved successfully.")
    else:
        print("WARNING: [tg_login] Database not connected, session NOT saved!")

    runtime_result = {"status": "not_started", "message": "Runtime callback is not configured."}
    if _start_runtime_callback and profile:
        try:
            runtime_result = _start_runtime_callback(client, profile)
        except Exception as e:
            runtime_result = {
                "status": "error",
                "message": f"Session saved, but runtime activation failed: {e}",
            }

    return session_string, runtime_result


def _render_login_success(runtime_result):
    status = runtime_result.get("status") if isinstance(runtime_result, dict) else None
    message = runtime_result.get("message") if isinstance(runtime_result, dict) else str(runtime_result)

    if status in {"started", "already_running"}:
        return render_template(
            "action_result.html",
            success=True,
            title="Telegram 登录成功",
            message=f"<p>Session 已保存。</p><p>{message}</p>",
        )

    return render_template(
        "action_result.html",
        success=True,
        title="Telegram 登录成功",
        message=(
            "<p>Session 已保存。</p>"
            f"<p>{message}</p>"
            "<p>如果后台任务尚未启动，请使用重启按钮或重启容器。</p>"
        ),
    )


@_flask_app.route("/tg_login", methods=["GET", "POST"])
@login_required
def tg_login():
    global _client
    
    # Handle logout action
    if request.method == "POST" and request.form.get("action") == "logout":
        print("DEBUG: [tg_login] Logout requested")
        try:
            profile_id = request.form.get("profile_id")
            if not profile_id:
                return "Error: profile_id is required"
            if _stop_runtime_callback:
                try:
                    stop_result = _stop_runtime_callback(profile_id)
                    print(f"DEBUG: [tg_login] Runtime stop result: {stop_result}")
                except Exception as e:
                    print(f"DEBUG: [tg_login] Error stopping runtime: {e}")

            # Clear session from database
            if db.conn:
                clear_profile_session(profile_id)
                print("DEBUG: [tg_login] Session cleared from database")
            
            # Disconnect and clear client
            if _client:
                try:
                    loop = _app_instance.loop
                    if _client.is_connected:
                        future = asyncio.run_coroutine_threadsafe(_client.disconnect(), loop)
                        future.result(timeout=10)
                        print("DEBUG: [tg_login] Client disconnected")
                except Exception as e:
                    print(f"DEBUG: [tg_login] Error disconnecting client: {e}")
                _client = None
            
            flash("Logged out successfully. You can now login with a different account.", "success")
            return redirect(url_for("tg_login"))
        except Exception as e:
            import traceback
            traceback.print_exc()
            return f"Logout Error: {str(e)}"

    if request.method == "POST" and request.form.get("action") == "connect_saved_session":
        try:
            if not db.conn:
                return "Error: Database not connected"

            profile_id = request.form.get("profile_id")
            if not profile_id:
                return "Error: profile_id is required"
            profile = get_profile(profile_id)
            saved_session = profile.get("session")
            if not saved_session:
                return "Error: No saved Telegram session found."

            config = _load_current_config()
            api_id, api_hash, _ = _resolve_api_credentials(config_data=config)
            if not api_id or not api_hash:
                return "Error: api_id or api_hash not found. Please set them in Config page first."

            loop = _app_instance.loop
            _client = _create_client_from_session(
                api_id,
                api_hash,
                _app_instance.proxy,
                _app_instance.session_file_path,
                loop,
                saved_session,
            )
            runtime_result = _start_runtime_callback(_client, profile) if _start_runtime_callback else {
                "status": "not_started",
                "message": "Runtime callback is not configured.",
            }
            return _render_login_success(runtime_result)
        except Exception as e:
            import traceback

            traceback.print_exc()
            return f"Connect Saved Session Error: {str(e)}"
    
    # Handle new login
    if request.method == "POST" and request.form.get("phone_number"):
        phone_number = _clean_scalar(request.form.get("phone_number"))
        session["phone_number"] = phone_number
        session["login_create_profile"] = True
        session["login_profile_id"] = None
        session["login_profile_name"] = phone_number

        try:
            if _client:
                try:
                    loop = _app_instance.loop
                    if _client.is_connected:
                        future = asyncio.run_coroutine_threadsafe(_client.disconnect(), loop)
                        future.result(timeout=10)
                except Exception as e:
                    print(f"DEBUG: [tg_login] Error disconnecting active client: {e}")
                _client = None

            # 1. Ensure we have api_id and api_hash
            config = _load_current_config()
            api_id, api_hash, _ = _resolve_api_credentials(config_data=config)

            if not api_id or not api_hash:
                return "Error: api_id or api_hash not found. Please set them in Config page first."

            # 2. Initialize and connect client on the main loop
            # This ensures all internal asyncio objects are bound to the correct loop
            loop = _app_instance.loop

            if not _client:
                future = asyncio.run_coroutine_threadsafe(
                    _create_and_connect_client(
                        api_id,
                        api_hash,
                        _app_instance.proxy,
                        _app_instance.session_file_path,
                        loop,
                    ),
                    loop,
                )
                _client = future.result(timeout=60)
            elif not _client.is_connected:
                future = asyncio.run_coroutine_threadsafe(_client.connect(), loop)
                future.result(timeout=30)

            # 3. Send Code
            future = asyncio.run_coroutine_threadsafe(
                _send_code(_client, phone_number), loop
            )
            sent_code = future.result(timeout=30)

            session["phone_code_hash"] = sent_code.phone_code_hash
            return redirect(url_for("tg_code"))
        except Exception as e:
            import traceback

            traceback.print_exc()
            return f"Telegram Login Error: {str(e)}"

    return _render_spa()


@_flask_app.route("/tg_code", methods=["GET", "POST"])
@login_required
def tg_code():
    if request.method == "POST":
        code = request.form.get("code")
        phone_number = session.get("phone_number")
        phone_code_hash = session.get("phone_code_hash")

        print(f"DEBUG: [tg_code] Received code: {code}")
        print(f"DEBUG: [tg_code] phone_number: {phone_number}")
        print(f"DEBUG: [tg_code] phone_code_hash: {phone_code_hash}")

        if not phone_number or not phone_code_hash:
            print("ERROR: [tg_code] Missing phone_number or phone_code_hash in session!")
            return "Error: Session expired. Please start login again."

        if not _client:
            print("ERROR: [tg_code] _client is None!")
            return "Error: Client not initialized. Please start login again."

        try:
            loop = _app_instance.loop
            print(f"DEBUG: [tg_code] Using loop: {id(loop)}")
            print(f"DEBUG: [tg_code] _client type: {type(_client)}")
            print(f"DEBUG: [tg_code] _client.loop: {id(_client.loop)}")
            print(f"DEBUG: [tg_code] _client.is_connected: {_client.is_connected}")

            # Sign in
            print("DEBUG: [tg_code] Calling _sign_in_wrapper...")
            future = asyncio.run_coroutine_threadsafe(
                _sign_in_wrapper(_client, phone_number, phone_code_hash, code), loop
            )
            print("DEBUG: [tg_code] Waiting for sign_in result...")
            sign_in_result = future.result(timeout=30)
            print(f"DEBUG: [tg_code] sign_in result: {sign_in_result}")

            _, runtime_result = _save_session_and_start_runtime(_client)
            return _render_login_success(runtime_result)
        except errors.SessionPasswordNeeded:
            print("DEBUG: [tg_code] Two-step verification required, redirecting to password page.")
            return redirect(url_for("tg_password"))
        except Exception as e:
            import traceback
            print(f"ERROR: [tg_code] Exception occurred: {type(e).__name__}: {str(e)}")
            traceback.print_exc()
            return f"Telegram Sign-in Error: {str(e)}"

    return render_template("tg_code.html")


@_flask_app.route("/tg_password", methods=["GET", "POST"])
@login_required
def tg_password():
    if request.method == "POST":
        password = request.form.get("password")
        print(f"DEBUG: [tg_password] Received password (length: {len(password) if password else 0})")

        if not _client:
            print("ERROR: [tg_password] _client is None!")
            return "Error: Client not initialized. Please start login again."

        try:
            loop = _app_instance.loop
            print(f"DEBUG: [tg_password] Using loop: {id(loop)}")
            print(f"DEBUG: [tg_password] _client.is_connected: {_client.is_connected}")

            print("DEBUG: [tg_password] Calling _check_password_wrapper...")
            future = asyncio.run_coroutine_threadsafe(
                _check_password_wrapper(_client, password), loop
            )
            print("DEBUG: [tg_password] Waiting for check_password result...")
            result = future.result(timeout=30)
            print(f"DEBUG: [tg_password] check_password result: {result}")

            _, runtime_result = _save_session_and_start_runtime(_client)
            return _render_login_success(runtime_result)
        except Exception as e:
            import traceback
            print(f"ERROR: [tg_password] Exception occurred: {type(e).__name__}: {str(e)}")
            traceback.print_exc()
            return f"Telegram Password Error: {str(e)}"

    return """
    <form method="post">
        Two-Step Verification Password: <input type="password" name="password">
        <input type="submit" value="Submit Password">
    </form>
    """


@_flask_app.route("/get_download_status")
@login_required
def get_download_speed():
    """Get download speed"""
    download_speed = get_total_download_speed()
    upload_speed = get_total_upload_speed()
    
    # In streaming mode: if download speed is 0 but upload speed is not,
    # use upload speed as download speed (data flows simultaneously)
    if download_speed == 0 and upload_speed > 0:
        download_speed = upload_speed
    
    return (
        '{ "download_speed" : "'
        + format_byte(download_speed)
        + '/s" , "upload_speed" : "'
        + format_byte(upload_speed)
        + '/s" } '
    )


@_flask_app.route("/set_download_state", methods=["POST"])
@login_required
def web_set_download_state():
    """Set download state"""
    state = request.args.get("state")

    if state == "continue" and get_download_state() is DownloadState.StopDownload:
        set_download_state(DownloadState.Downloading)
        return "pause"

    if state == "pause" and get_download_state() is DownloadState.Downloading:
        set_download_state(DownloadState.StopDownload)
        return "continue"

    return state


@_flask_app.route("/get_app_version")
def get_app_version():
    """Get telegram_media_downloader version"""
    return utils.__version__


@_flask_app.route("/get_db_status")
@login_required
def get_db_status():
    """Get database heartbeat status"""
    return jsonify(db.get_heartbeat_status())


@_flask_app.route("/clear_history", methods=["POST"])
@login_required
def api_clear_history():
    """Clear all completed download history"""
    try:
        clear_download_history()
        return jsonify({"success": True, "message": "历史记录已清空"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/remove_task", methods=["POST"])
@login_required
def api_remove_task():
    """Remove a specific task from history"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "请求数据无效"}), 400
        
        chat_id = int(data.get("chat_id", 0))
        message_id = int(data.get("message_id", 0))
        profile_id = data.get("profile_id") or data.get("profileId")
        
        if not chat_id or not message_id:
            return jsonify({"success": False, "message": "缺少 chat_id 或 message_id"}), 400
        
        success = remove_download_task(chat_id, message_id, profile_id)
        if success:
            return jsonify({"success": True, "message": "任务已删除"})
        else:
            return jsonify({"success": False, "message": "任务不存在"}), 404
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/get_download_list")
@login_required
def get_download_list():
    """get download list"""
    if request.args.get("already_down") is None:
        return "[]"

    already_down = request.args.get("already_down") == "true"
    return jsonify(_get_formatted_list(already_down))


def _get_formatted_list(already_down=False):
    """Helper to get formatted list data"""
    download_result = get_download_result()
    upload_result = get_upload_result()
    data = []

    # 1. Collect all unique (chat_id, message_id) pairs
    all_tasks = set()
    for cid, msgs in download_result.items():
        for mid in msgs.keys():
            all_tasks.add((cid, mid))
    for cid, msgs in upload_result.items():
        for mid in msgs.keys():
            all_tasks.add((cid, mid))

    # 2. Iterate and format
    for chat_id, idx in all_tasks:
        d_item = download_result.get(chat_id, {}).get(idx)
        u_item = upload_result.get(chat_id, {}).get(idx)
        
        # Use whatever is available as base
        base_item = d_item if d_item else u_item
        if not base_item:
            continue
        profile_id = (d_item or {}).get("profile_id") or (u_item or {}).get(
            "profile_id"
        )
        task_record_state = (u_item or {}).get("state") or (d_item or {}).get(
            "state"
        )
        is_failed = is_retryable_failure_state(task_record_state or "")
        is_upload_failed = task_record_state == "upload_failed"

        # Extract basic info
        file_name = base_item.get("file_name", "Unknown")
        total_size = base_item.get("total_size") or base_item.get("total_bytes", 0)
        
        # --- Download Status ---
        down_byte = 0
        download_speed_val = 0
        if d_item:
            down_byte = d_item.get("down_byte", 0)
            download_speed_val = d_item.get("download_speed", 0)
        elif u_item:
            # If no download record but uploading (Streaming), assume download matches upload
            down_byte = u_item.get("processed_bytes", 0)
            # Streaming mode: download speed equals upload speed (data flows simultaneously)
            download_speed_val = u_item.get("upload_speed", 0)
        
        # --- Upload Status ---
        upload_speed_val = 0
        upload_processed = 0
        upload_total = total_size
        is_uploading = False
        
        if u_item:
            is_uploading = True
            upload_speed_val = u_item.get("upload_speed", 0)
            upload_processed = u_item.get("processed_bytes", 0)
            upload_total = u_item.get("total_bytes", total_size)
            
        # --- Progress Calculation ---
        download_progress = 0
        if total_size > 0:
            download_progress = min(round(down_byte / total_size * 100, 1), 100.0)
            
        upload_progress = 0
        if is_uploading:
            if upload_total > 0:
                upload_progress = min(round(upload_processed / upload_total * 100, 1), 100.0)
        else:
            # Fallback simulation for non-uploading tasks (old logic)
            if download_progress >= 100:
                upload_progress = 100.0
            else:
                upload_progress = min(download_progress, 99.9)

        # --- Activity/Completion Check ---
        download_complete = bool(
            d_item
            and d_item.get("down_byte", 0) >= d_item.get("total_size", 1)
        )
        # WebDAV 上传是下载任务的第二阶段；下载到 100% 但上传未完成时仍属于活动任务。
        # 下载记录还是 pending（未走完上传确认流程）时，即使内存里没有上传记录，
        # 也不能当成已完成——那是断电/崩溃留下的半程任务，需要等 resume 重跑。
        upload_complete = bool(
            task_record_state == "completed"
            or (
                u_item
                and upload_total > 0
                and upload_processed >= upload_total
            )
        )
        is_truly_finished = download_complete and upload_complete
        
        # If it's effectively 100% data transfer but NOT yet marked finished in history,
        # it means it's "Finishing" (waiting for server to close the stream/confirm receipt)
        is_finishing = False
        if download_complete and is_uploading and upload_progress >= 99.9:
            is_finishing = True

        # Filter based on requested list type
        if already_down:
            # 云盘归档只包含成功完成的记录；失败记录留在任务仪表盘供重试或删除。
            if not is_truly_finished or is_failed:
                continue
        else:
            # 活动接口同时承载失败终态，让仪表盘能够展示失败原因和后续操作。
            if is_truly_finished and not is_failed:
                continue

        # --- Strings Formatting ---
        download_speed_str = format_byte(download_speed_val) + "/s"
        upload_speed_str = format_byte(upload_speed_val) + "/s"
        
        # Paths
        local_path = file_name.replace("\\", "/")
        remote_path = local_path
        config_save_path = ""
        relative_path = CloudDrive.get_relative_upload_path("", local_path)
        if _app_instance and hasattr(_app_instance, 'cloud_drive_config'):
            cloud_cfg = _app_instance.cloud_drive_config
            if hasattr(_app_instance, "save_path"):
                config_save_path = _app_instance.save_path.replace("\\", "/").rstrip("/")

            relative_path = CloudDrive.get_relative_upload_path(
                config_save_path, local_path
            )
            if hasattr(cloud_cfg, 'remote_dir') and cloud_cfg.remote_dir:
                remote_path = f"{cloud_cfg.remote_dir.rstrip('/')}/{relative_path}"

        # Valid Time
        created_ts = base_item.get("created_at") or base_item.get("start_time") or time.time()
        completed_ts = base_item.get("end_time") or base_item.get("updated_at") or created_ts
        
        beijing_tz = timezone(timedelta(hours=8))
        created_at_fmt = datetime.fromtimestamp(created_ts, tz=beijing_tz).strftime("%Y-%m-%d %H:%M:%S")
        completed_at_fmt = datetime.fromtimestamp(completed_ts, tz=beijing_tz).strftime("%Y-%m-%d %H:%M:%S")

        # Custom progress string for finishing state
        display_download_progress = str(download_progress)
        display_upload_progress = str(upload_progress)
        if is_finishing:
             display_download_progress = "Finishing..."
             display_upload_progress = "Finishing..."

        # Determine status text
        # 本机已完整、仅云盘失败时单独文案，便于仪表盘区分“下载失败”和“上传失败”。
        # AI 分类阶段（抽帧/转写/识图）发生在下载完成后、上传开始前，优先展示。
        ai_status = (d_item or {}).get("ai_status") or ""
        status_text = ""
        if is_upload_failed:
            status_text = "上传失败"
        elif is_failed:
            status_text = "失败"
        elif is_truly_finished:
            status_text = "已完成"
        elif ai_status:
            status_text = f"AI {ai_status}"
        elif is_uploading:
            if is_finishing:
                status_text = "正在完成..."
            elif upload_progress > 0:
                status_text = "上传中"
            else:
                status_text = "准备上传"
        elif download_progress > 0:
            status_text = "下载中"
        else:
            status_text = "等待中"

        # 上传失败时本机进度应为 100%，避免 UI 看起来像没下完。
        if is_upload_failed and total_size > 0:
            download_progress = 100.0
            display_download_progress = "100.0"

        projected_state = (
            task_record_state
            if is_failed
            else (
                get_task_state(chat_id, idx, profile_id)
                if not already_down
                else "finished"
            )
        )

        item = {
            "chat": str(chat_id),
            "id": str(idx),
            "filename": os.path.basename(file_name),
            "total_size": format_byte(total_size),
            "download_progress": display_download_progress,
            "upload_progress": display_upload_progress,
            "download_speed": download_speed_str if not is_finishing else "0.0b/s",
            "upload_speed": upload_speed_str if not is_finishing else "0.0b/s",
            "save_path": local_path,
            "remote_path": remote_path,
            "relative_path": relative_path,
            "created_at": created_at_fmt,
            "completed_at": completed_at_fmt if is_truly_finished else None,
            "created_ts": created_ts,
            "completed_ts": completed_ts if is_truly_finished else None,
            "profile_id": profile_id,
            "profileId": profile_id,
            "state": projected_state,
            "status": status_text,
            "ai_status": ai_status,
            "ai_summary": (d_item or {}).get("ai_summary") or "",
            "ai_tags": (d_item or {}).get("ai_tags") or [],
            "error": (u_item or {}).get("error") or (d_item or {}).get("error") or "",
        }
        data.append(item)
    return data


def _delete_webdav_resource(remote_path: str) -> tuple[bool, str]:
    """Delete one server-derived remote object from the active WebDAV root."""
    if not _app_instance:
        return False, "应用运行态尚未就绪"
    cloud_config = _app_instance.cloud_drive_config
    if cloud_config.upload_adapter != "webdav":
        return False, "当前上传适配器不是 WebDAV"

    future = asyncio.run_coroutine_threadsafe(
        CloudDrive.webdav_delete_path(cloud_config, remote_path),
        _app_instance.loop,
    )
    try:
        return future.result(timeout=90)
    except TimeoutError:
        future.cancel()
        return False, "WebDAV 删除超时"


@_flask_app.route("/task_control", methods=["POST"])
@login_required
def api_task_control():
    """Control an individual task (pause, resume, reset upload, delete)."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "请求数据无效"}), 400
        
        chat_id = int(data.get("chat_id", 0))
        message_id = int(data.get("message_id", 0))
        profile_id = data.get("profile_id") or data.get("profileId")
        action = data.get("action") # 'pause', 'resume', 'reset_upload', 'delete'
        
        if not chat_id or not message_id or not action:
            return jsonify({"success": False, "message": "参数不齐全"}), 400
        
        if action == "reset_upload":
            task_record = get_download_result().get(chat_id, {}).get(message_id)
            if not task_record:
                return jsonify({"success": False, "message": "任务不存在"}), 404
            if not is_retryable_failure_state(task_record.get("state", "")):
                return jsonify(
                    {"success": False, "message": "只有失败或上传失败任务可以重置上传"}
                ), 409
            if profile_id and task_record.get("profile_id") not in (profile_id, None):
                return jsonify({"success": False, "message": "任务不属于当前账号"}), 403
            if not _retry_upload_callback:
                return jsonify(
                    {"success": False, "message": "下载运行态尚未就绪"}
                ), 503

            matched_task = next(
                (
                    item
                    for item in _get_formatted_list(already_down=False)
                    if int(item["chat"]) == chat_id
                    and int(item["id"]) == message_id
                    and (
                        not profile_id
                        or item.get("profile_id") in (profile_id, None)
                    )
                ),
                None,
            )
            if not matched_task:
                return jsonify({"success": False, "message": "失败任务不可用"}), 409

            effective_profile_id = profile_id or task_record.get("profile_id")
            result = _retry_upload_callback(
                chat_id,
                message_id,
                effective_profile_id,
                matched_task["remote_path"],
                task_record.get("file_name") or matched_task["filename"],
                int(task_record.get("total_size") or 0),
            )
            if result.get("status") == "error":
                return jsonify({"success": False, "message": result["message"]}), 409
            return jsonify(
                {
                    "success": True,
                    "message": result.get("message")
                    or "云端异常文件已重置，任务已重新入队。",
                    "reset": result,
                }
            )

        if action == "delete":
            # 先通知正在运行的 worker 停止，再清理下载历史、待处理项和上传状态。
            # 仅写入 deleted 控制状态会被 SSE 从历史记录再次投影，前端看起来像“没反应”。
            # Bot 状态卡片由独立的 task_node 维护，必须同步取消，否则会继续刷新旧进度。
            set_task_state(chat_id, message_id, "deleted", profile_id)
            cancel_bot_tasks_for_message(chat_id, message_id, profile_id)
            remove_download_task(chat_id, message_id, profile_id)
            return jsonify({"success": True, "message": "任务已取消并删除"})

        if action == "delete_remote":
            # 远程路径只能由服务端任务投影生成，禁止信任客户端传入的路径。
            matched_task = next(
                (
                    item
                    for item in (
                        _get_formatted_list(already_down=False)
                        + _get_formatted_list(already_down=True)
                    )
                    if int(item["chat"]) == chat_id
                    and int(item["id"]) == message_id
                    and (
                        not profile_id
                        or item.get("profile_id") in (profile_id, None)
                    )
                ),
                None,
            )
            if not matched_task:
                return jsonify({"success": False, "message": "任务不存在"}), 404

            deleted, delete_message = _delete_webdav_resource(
                matched_task["remote_path"]
            )
            if not deleted:
                return jsonify(
                    {"success": False, "message": delete_message}
                ), 409

            set_task_state(chat_id, message_id, "deleted", profile_id)
            cancel_bot_tasks_for_message(chat_id, message_id, profile_id)
            remove_download_task(chat_id, message_id, profile_id)
            return jsonify(
                {"success": True, "message": "任务及远程资源已删除"}
            )

        state_map = {
            'pause': 'paused',
            'resume': 'running',
        }
        
        target_state = state_map.get(action)
        if not target_state:
            return jsonify({"success": False, "message": "无效的操作"}), 400
            
        success = set_task_state(chat_id, message_id, target_state, profile_id)
        return jsonify({"success": True, "message": f"任务已{action}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/stream")
@login_required
def stream():
    """Server-Sent Events for Dashboard"""
    def generate():
        history_tick = 0
        while True:
            try:
                # 1. Status (Speed)
                speed_data = {
                    "download_speed": format_byte(get_total_download_speed()) + "/s",
                    "upload_speed": format_byte(get_total_upload_speed()) + "/s"
                }

                # 2. Active Tasks
                active_tasks = _get_formatted_list(already_down=False)

                payload = {
                    "type": "update",
                    "status": speed_data,
                    "tasks": active_tasks
                }

                # 3. History (Every 5 seconds)
                if history_tick % 5 == 0:
                    history = _get_formatted_list(already_down=True)
                    payload["history"] = history
                
                history_tick += 1

                yield f"data: {json.dumps(payload)}\n\n"
                
                time.sleep(1)
            except Exception as e:
                print(f"Stream Error: {e}")
                # Send empty or error event to keep alive or reconnect
                yield f"event: error\ndata: {str(e)}\n\n"
                time.sleep(5)

    return Response(stream_with_context(generate()), mimetype='text/event-stream')
