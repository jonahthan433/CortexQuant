"""Durable Telegram inbox with deduplication by channel and message ID."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable


class TelegramInbox:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS telegram_inbox (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    raw_text TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending'
                        CHECK (state IN ('pending', 'processed')),
                    UNIQUE (channel_id, message_id)
                )
            """)
        os.chmod(self.database_path, 0o600)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        try:
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
            for suffix in ("", "-wal", "-shm"):
                artifact = Path(str(self.database_path) + suffix)
                if artifact.exists():
                    os.chmod(artifact, 0o600)

    def enqueue(self, channel_id: int, message_id: int, raw_text: str, received_at: str) -> bool:
        """Persist a new update; returns False when the idempotency key already exists."""
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO telegram_inbox "
                "(channel_id, message_id, raw_text, received_at) VALUES (?, ?, ?, ?)",
                (channel_id, message_id, raw_text, received_at),
            )
            return cursor.rowcount == 1

    def state(self, channel_id: int, message_id: int):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM telegram_inbox WHERE channel_id=? AND message_id=?",
                (channel_id, message_id),
            ).fetchone()
        return row[0] if row else None

    def pending(self) -> Iterable[tuple[int, int, str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT channel_id, message_id, raw_text, received_at "
                "FROM telegram_inbox WHERE state='pending' ORDER BY sequence"
            ).fetchall()
        return rows

    def mark_processed(self, channel_id: int, message_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE telegram_inbox SET state='processed' "
                "WHERE channel_id=? AND message_id=?",
                (channel_id, message_id),
            )
