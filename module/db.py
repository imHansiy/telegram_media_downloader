import os
import threading
import time

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
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            self.conn.rollback()

    def _start_heartbeat(self):
        """Start heartbeat thread to keep database connection alive"""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._stop_heartbeat.clear()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
        logger.info("Database heartbeat started (interval: 60s)")

    def _heartbeat_loop(self):
        """Heartbeat loop - ping database every 60 seconds"""
        while not self._stop_heartbeat.is_set():
            time.sleep(60)
            self._ping()

    def _ping(self):
        """Ping database to keep connection alive"""
        if not self.conn:
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT 1")
            logger.debug("Database heartbeat ping success")
        except Exception as e:
            logger.warning(f"Database heartbeat ping failed: {e}")
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
            logger.info("Database reconnected successfully")
        except Exception as e:
            logger.error(f"Database reconnect failed: {e}")
            self.conn = None

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
            "status": "ok" if (is_alive and is_connected) else "error"
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
