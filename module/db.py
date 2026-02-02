import os
import json
import psycopg2
from psycopg2.extras import Json
from loguru import logger


class DB:
    def __init__(self):
        self.conn = None
        self.dsn = os.environ.get("DATABASE_URL")
        if not self.dsn:
            logger.warning(
                "DATABASE_URL environment variable not set. DB functionality disabled."
            )
            return

        try:
            self.conn = psycopg2.connect(self.dsn)
            self._init_db()
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            self.conn = None

    def _init_db(self):
        if not self.conn:
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key VARCHAR(255) PRIMARY KEY,
                        value JSONB
                    );
                """)
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            self.conn.rollback()

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
