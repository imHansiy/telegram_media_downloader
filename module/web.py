"""web ui for media download"""

import logging
import os
import threading
import asyncio
import inspect
import json
import time
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
import utils
from module.app import Application
from module.db import db
from module.download_stat import (
    DownloadState,
    get_download_result,
    get_download_state,
    get_total_download_speed,
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


def _load_current_config() -> dict:
    """Load current config from DB or in-memory app."""
    current_config = {}
    if db.conn:
        current_config = db.load_setting("config") or {}

    if not current_config and _app_instance:
        current_config = _app_instance.config or {}

    return current_config


def _save_current_config(new_config: dict) -> dict:
    """Persist config and apply immediately where supported."""
    if not db.conn:
        return {"status": "error", "message": "Database not connected"}

    db.save_setting("config", new_config)

    if _app_instance:
        print("DEBUG: [web] Updating in-memory app config from Web UI")
        _app_instance.config = new_config
        _app_instance.assign_config(new_config)
        _sync_web_login_secret()

    return {
        "status": "success",
        "message": (
            "Config saved and applied in memory. Connection-level changes such as "
            "API credentials, bot token, web host, and web port still require "
            "reconnect/restart."
        ),
    }


def _get_telegram_account_status() -> dict:
    """Return Telegram session state for the React UI."""
    saved_session = db.load_setting("session") if db.conn else None
    status = {
        "logged_in": False,
        "session_exists": bool(saved_session),
        "account": None,
    }

    if _client and _client.is_connected:
        try:
            me = _client.me
            if me:
                first_name = me.first_name or ""
                last_name = me.last_name or ""
                full_name = f"{first_name} {last_name}".strip() or me.username or str(me.id)
                status["logged_in"] = True
                status["account"] = {
                    "id": str(me.id),
                    "phoneNumber": me.phone_number or "",
                    "username": f"@{me.username}" if me.username else "",
                    "firstName": full_name,
                    "status": "connected",
                    "sessionName": "database_session",
                    "createdAt": "",
                    "isPremium": getattr(me, "is_premium", False),
                }
        except Exception as e:
            print(f"DEBUG: [account_status] Error getting user info: {e}")

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
    global _app_instance
    _client = client
    _restart_callback = restart_callback
    _start_runtime_callback = start_runtime_callback
    _stop_runtime_callback = stop_runtime_callback
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


@_flask_app.route("/api/account/logout", methods=["POST"])
@login_required
def api_account_logout():
    """Disconnect runtime and clear saved Telegram session."""
    global _client
    try:
        if _stop_runtime_callback:
            _stop_runtime_callback()

        if db.conn:
            db.save_setting("session", None)

        if _client:
            try:
                loop = _app_instance.loop
                if _client.is_connected:
                    future = asyncio.run_coroutine_threadsafe(_client.disconnect(), loop)
                    future.result(timeout=10)
            except Exception as e:
                print(f"DEBUG: [api_account_logout] Error disconnecting client: {e}")
            _client = None

        return jsonify({"success": True, "message": "Session disconnected."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@_flask_app.route("/api/account/connect_saved_session", methods=["POST"])
@login_required
def api_account_connect_saved_session():
    """Start runtime with an already saved session string."""
    global _client
    try:
        if not db.conn:
            return jsonify({"success": False, "message": "Database not connected"}), 503

        saved_session = db.load_setting("session")
        if not saved_session:
            return jsonify({"success": False, "message": "No saved Telegram session found."}), 404

        config_data = _load_current_config()
        api_id = config_data.get("api_id") or _app_instance.api_id
        api_hash = config_data.get("api_hash") or _app_instance.api_hash
        if not api_id or not api_hash:
            return jsonify({"success": False, "message": "api_id or api_hash is missing."}), 400

        loop = _app_instance.loop
        _client = _create_client_from_session(
            api_id,
            api_hash,
            _app_instance.proxy,
            _app_instance.session_file_path,
            loop,
            saved_session,
        )
        runtime_result = (
            _start_runtime_callback(_client)
            if _start_runtime_callback
            else {"status": "not_started", "message": "Runtime callback is not configured."}
        )
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
    phone_number = data.get("phone_number") or data.get("phoneNumber")
    if not phone_number:
        return jsonify({"success": False, "message": "phone_number is required"}), 400

    session["phone_number"] = phone_number

    try:
        config_data = _load_current_config()
        api_id = data.get("api_id") or config_data.get("api_id") or _app_instance.api_id
        api_hash = data.get("api_hash") or config_data.get("api_hash") or _app_instance.api_hash
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

    if db.conn:
        print("DEBUG: [tg_login] Saving session to database...")
        db.save_setting("session", session_string)
        print("DEBUG: [tg_login] Session saved successfully.")
    else:
        print("WARNING: [tg_login] Database not connected, session NOT saved!")

    runtime_result = {"status": "not_started", "message": "Runtime callback is not configured."}
    if _start_runtime_callback:
        try:
            runtime_result = _start_runtime_callback(client)
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
            if _stop_runtime_callback:
                try:
                    stop_result = _stop_runtime_callback()
                    print(f"DEBUG: [tg_login] Runtime stop result: {stop_result}")
                except Exception as e:
                    print(f"DEBUG: [tg_login] Error stopping runtime: {e}")

            # Clear session from database
            if db.conn:
                db.save_setting("session", None)
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

            saved_session = db.load_setting("session")
            if not saved_session:
                return "Error: No saved Telegram session found."

            config = db.load_setting("config") or {}
            api_id = config.get("api_id") or _app_instance.api_id
            api_hash = config.get("api_hash") or _app_instance.api_hash
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
            runtime_result = _start_runtime_callback(_client) if _start_runtime_callback else {
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
        phone_number = request.form.get("phone_number")
        session["phone_number"] = phone_number

        try:
            # 1. Ensure we have api_id and api_hash
            config = db.load_setting("config") or {}
            api_id = config.get("api_id") or _app_instance.api_id
            api_hash = config.get("api_hash") or _app_instance.api_hash

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
        
        if not chat_id or not message_id:
            return jsonify({"success": False, "message": "缺少 chat_id 或 message_id"}), 400
        
        success = remove_download_task(chat_id, message_id)
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

    download_result = get_download_result()
    result = "["
    for chat_id, messages in download_result.items():
        for idx, value in messages.items():
            is_already_down = value["down_byte"] == value["total_size"]

            if already_down and not is_already_down:
                continue

            if not already_down and is_already_down:
                continue

            if result != "[":
                result += ","
            download_speed = format_byte(value["download_speed"]) + "/s"
            result += (
                '{ "chat":"'
                + f"{chat_id}"
                + '", "id":"'
                + f"{idx}"
                + '", "filename":"'
                + os.path.basename(value["file_name"])
                + '", "total_size":"'
                + f"{format_byte(value['total_size'])}"
                + '" ,"download_progress":"'
            )
            result += (
                f"{round(value['down_byte'] / value['total_size'] * 100, 1)}"
                + '" ,"download_speed":"'
                + download_speed
                + '" ,"save_path":"'
                + value["file_name"].replace("\\", "/")
                + '"}'
            )

    result += "]"
    result += "]"
    return result


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
        # A task is ONLY "Completed" if it exists in download_result AND is 100%
        # (This confirms verify_and_save_download was called after WebDAV confirmed success)
        is_truly_finished = False
        if d_item:
            if d_item.get("down_byte", 0) >= d_item.get("total_size", 1):
                is_truly_finished = True
        
        # If it's effectively 100% data transfer but NOT yet marked finished in history,
        # it means it's "Finishing" (waiting for server to close the stream/confirm receipt)
        is_finishing = False
        raw_progress = (down_byte / total_size * 100) if total_size > 0 else 0
        if not is_truly_finished and raw_progress >= 99.9:
            is_finishing = True

        # Filter based on requested list type
        if already_down:
            # Asking for History -> Show only truly physically finished
            if not is_truly_finished:
                continue
        else:
            # Asking for Active -> Show only NOT finished
            if is_truly_finished:
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
        status_text = ""
        if is_truly_finished:
            status_text = "已完成"
        elif is_finishing:
            status_text = "正在完成..."
        elif is_uploading:
            if upload_progress > 0:
                status_text = "上传中"
            else:
                status_text = "准备上传"
        elif download_progress > 0:
            status_text = "下载中"
        else:
            status_text = "等待中"

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
            "state": get_task_state(chat_id, idx) if not already_down else 'finished',
            "status": status_text
        }
        data.append(item)
    return data


@_flask_app.route("/task_control", methods=["POST"])
@login_required
def api_task_control():
    """Control an individual task (pause, resume, delete)"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "请求数据无效"}), 400
        
        chat_id = int(data.get("chat_id", 0))
        message_id = int(data.get("message_id", 0))
        action = data.get("action") # 'pause', 'resume', 'delete'
        
        if not chat_id or not message_id or not action:
            return jsonify({"success": False, "message": "参数不齐全"}), 400
        
        state_map = {
            'pause': 'paused',
            'resume': 'running',
            'delete': 'deleted'
        }
        
        target_state = state_map.get(action)
        if not target_state:
            return jsonify({"success": False, "message": "无效的操作"}), 400
            
        success = set_task_state(chat_id, message_id, target_state)
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
