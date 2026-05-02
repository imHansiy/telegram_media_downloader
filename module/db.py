import os
import threading
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json

load_dotenv()

from loguru import logger


class DB:
    def __init__(self):
        self.conn = None
        self.dsn = os.environ.get("DATABASE_URL")
        self._heartbeat_thread = None
        self._stop_heartbeat = threading.Event()
        self.keepalive_app_name = os.environ.get(
            "DB_KEEPALIVE_APP_NAME", "telegram_media_downloader"
        )
        self.keepalive_interval_seconds = self._load_keepalive_interval()
        self.last_keepalive_at = None
        self.last_keepalive_error = None

        if not self.dsn:
            logger.warning(
                "DATABASE_URL environment variable not set. DB functionality disabled."
            )
            return

        try:
            self.conn = psycopg2.connect(self.dsn)
            self._init_db()
            self._start_heartbeat()
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            self.conn = None

    def _init_db(self):
        if not self.conn:
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS settings (
                        key VARCHAR(255) PRIMARY KEY,
                        value JSONB
                    );
                """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_keepalive (
                        app_name VARCHAR(255) PRIMARY KEY,
                        heartbeat_at TIMESTAMPTZ NOT NULL
                    );
                """
                )
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            self.conn.rollback()

    def _load_keepalive_interval(self):
        raw_interval = os.environ.get("DB_KEEPALIVE_INTERVAL_SECONDS", "14400")
        try:
            interval = int(raw_interval)
        except ValueError:
            logger.warning(
                f"Invalid DB_KEEPALIVE_INTERVAL_SECONDS={raw_interval!r}, fallback to 14400."
            )
            return 14400

        if interval < 60:
            logger.warning(
                f"DB_KEEPALIVE_INTERVAL_SECONDS={interval} is too small, using 60 seconds."
            )
            return 60
        return interval

    def _start_heartbeat(self):
        """Start keepalive thread to verify the database is still writable and readable."""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._stop_heartbeat.clear()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
        logger.info(
            "Database keepalive started "
            f"(table: app_keepalive, interval: {self.keepalive_interval_seconds}s)"
        )

    def _heartbeat_loop(self):
        """Keepalive loop - write to app_keepalive and read it back."""
        while not self._stop_heartbeat.is_set():
            self._ping()
            if self._stop_heartbeat.wait(self.keepalive_interval_seconds):
                break

    def _ping(self):
        """Write and read the keepalive row to verify real database access."""
        if not self.conn:
            return
        try:
            self._run_keepalive_check()
            self.last_keepalive_at = datetime.now(timezone.utc)
            self.last_keepalive_error = None
            logger.debug("Database keepalive write/read success")
        except Exception as e:
            self.last_keepalive_error = str(e)
            logger.warning(f"Database keepalive write/read failed: {e}")
            self._reconnect()

    def _reconnect(self):
        """Attempt to reconnect to database"""
        try:
            if self.conn:
                try:
                    self.conn.close()
                except:
                    pass
            self.conn = psycopg2.connect(self.dsn)
            self._init_db()
            logger.info("Database reconnected successfully")
        except Exception as e:
            logger.error(f"Database reconnect failed: {e}")
            self.conn = None

    def _run_keepalive_check(self):
        expected_heartbeat_at = None

        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_keepalive (app_name, heartbeat_at)
                VALUES (%s, NOW())
                ON CONFLICT (app_name) DO UPDATE
                SET heartbeat_at = EXCLUDED.heartbeat_at
                RETURNING heartbeat_at
                """,
                (self.keepalive_app_name,),
            )
            row = cur.fetchone()
            if not row or not row[0]:
                raise RuntimeError("keepalive write did not return heartbeat_at")
            expected_heartbeat_at = row[0]

        self.conn.commit()

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT heartbeat_at FROM app_keepalive WHERE app_name = %s",
                (self.keepalive_app_name,),
            )
            row = cur.fetchone()

        if not row or not row[0]:
            raise RuntimeError("keepalive read did not find app_keepalive row")
        if row[0] != expected_heartbeat_at:
            raise RuntimeError("keepalive readback does not match the last write")

    def stop_heartbeat(self):
        """Stop heartbeat thread"""
        self._stop_heartbeat.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)

    def get_heartbeat_status(self):
        """Get heartbeat status"""
        is_alive = self._heartbeat_thread and self._heartbeat_thread.is_alive()
        is_connected = self.conn is not None
        return {
            "heartbeat_active": is_alive,
            "connected": is_connected,
            "status": "ok"
            if (is_alive and is_connected and self.last_keepalive_error is None)
            else "error",
            "keepalive_table": "app_keepalive",
            "keepalive_app_name": self.keepalive_app_name,
            "keepalive_interval_seconds": self.keepalive_interval_seconds,
            "last_keepalive_at": (
                self.last_keepalive_at.isoformat() if self.last_keepalive_at else None
            ),
            "last_keepalive_error": self.last_keepalive_error,
        }

    def load_setting(self, key):
        if not self.conn:
            return None
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
                result = cur.fetchone()
                if result:
                    return result[0]
        except Exception as e:
            logger.error(f"Failed to load setting {key}: {e}")
            self.conn.rollback()
        return None

    def save_setting(self, key, value):
        if not self.conn:
            return
        try:
            with self.conn.cursor() as cur:
                if value is None:
                    # Delete the setting when value is None
                    cur.execute("DELETE FROM settings WHERE key = %s", (key,))
                    print(f"DEBUG: [db] Deleted setting: {key}")
                else:
                    cur.execute(
                        """
                        INSERT INTO settings (key, value)
                        VALUES (%s, %s)
                        ON CONFLICT (key) DO UPDATE
                        SET value = EXCLUDED.value
                    """,
                        (key, Json(value)),
                    )
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to save setting {key}: {e}")
            self.conn.rollback()


db = DB()
