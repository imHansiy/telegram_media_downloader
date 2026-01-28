"""web ui for media download"""

import logging
import os
import threading
import asyncio
import json
from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
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
)
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


def run_web_server(app: Application):
    """
    Runs a web server using the Flask framework.
    """

    get_flask_app().run(
        app.web_host, app.web_port, debug=app.debug_web, use_reloader=False
    )


# pylint: disable = W0603
def init_web(app: Application, client: Client = None, restart_callback=None):
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
    global _app_instance
    _client = client
    _restart_callback = restart_callback
    _app_instance = app

    if app.web_login_secret:
        web_login_users = {"root": app.web_login_secret}
    else:
        _flask_app.config["LOGIN_DISABLED"] = True
    if app.debug_web:
        threading.Thread(target=run_web_server, args=(app,)).start()
    else:
        threading.Thread(
            target=get_flask_app().run, daemon=True, args=(app.web_host, app.web_port)
        ).start()


@_flask_app.route("/")
@login_required
def index():
    """index"""
    return render_template("index.html", download_state=get_download_state())


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
            new_config = json.loads(request.form.get("config"))
            if db.conn:
                db.save_setting("config", new_config)
                # Ideally trigger a reload or restart, but for now just save
                return jsonify(
                    {
                        "status": "success",
                        "message": "Config saved to DB. Please restart container.",
                    }
                )
            else:
                return jsonify({"status": "error", "message": "Database not connected"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})

    # Load current config
    current_config = {}
    if db.conn:
        current_config = db.load_setting("config")

    # If DB config is empty, fallback to current in-memory config
    if not current_config and _app_instance:
        current_config = _app_instance.config

    return render_template("config.html", config=json.dumps(current_config, indent=2))


# --- Telegram Login Routes ---


@_flask_app.route("/tg_login", methods=["GET", "POST"])
@login_required
def tg_login():
    if request.method == "POST":
        phone_number = request.form.get("phone_number")
        session["phone_number"] = phone_number
        try:
            if not _client.is_connected:
                future = asyncio.run_coroutine_threadsafe(
                    _client.connect(), _client.loop
                )
                future.result()  # Wait for connection

            future = asyncio.run_coroutine_threadsafe(
                _client.send_code(phone_number), _client.loop
            )
            sent_code = future.result()

            session["phone_code_hash"] = sent_code.phone_code_hash
            return redirect(url_for("tg_code"))
        except Exception as e:
            return f"Error: {e}"

    return """
    <form method="post">
        Phone Number (with country code): <input type="text" name="phone_number">
        <input type="submit" value="Send Code">
    </form>
    """


@_flask_app.route("/tg_code", methods=["GET", "POST"])
@login_required
def tg_code():
    if request.method == "POST":
        code = request.form.get("code")
        phone_number = session.get("phone_number")
        phone_code_hash = session.get("phone_code_hash")

        try:
            future = asyncio.run_coroutine_threadsafe(
                _client.sign_in(phone_number, phone_code_hash, code), _client.loop
            )
            future.result()

            # Save session
            future_s = asyncio.run_coroutine_threadsafe(
                _client.export_session_string(), _client.loop
            )
            s = future_s.result()

            if db.conn:
                db.save_setting("session", s)

            return "Login Successful! Session saved. Please restart the container."
        except errors.SessionPasswordNeeded:
            return redirect(url_for("tg_password"))
        except Exception as e:
            return f"Error: {e}"

    return """
    <form method="post">
        Code: <input type="text" name="code">
        <input type="submit" value="Sign In">
    </form>
    """


@_flask_app.route("/tg_password", methods=["GET", "POST"])
@login_required
def tg_password():
    if request.method == "POST":
        password = request.form.get("password")
        try:
            future = asyncio.run_coroutine_threadsafe(
                _client.check_password(password), _client.loop
            )
            future.result()

            # Save session
            future_s = asyncio.run_coroutine_threadsafe(
                _client.export_session_string(), _client.loop
            )
            s = future_s.result()

            if db.conn:
                db.save_setting("session", s)

            return "Login Successful! Session saved. Please restart the container."
        except Exception as e:
            return f"Error: {e}"

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
    return (
        '{ "download_speed" : "'
        + format_byte(get_total_download_speed())
        + '/s" , "upload_speed" : "0.00 B/s" } '
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
    return result
