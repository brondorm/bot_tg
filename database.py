from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


class Database:
    def __init__(self, path: Path | str = "data/bot.db") -> None:
        self.path = Path(path)
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    full_name TEXT,
                    direction TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    content TEXT,
                    file_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_user
                ON messages(user_id, created_at)
                """
            )

    @contextmanager
    def _get_connection(self) -> Iterable[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def add_message(
        self,
        *,
        user_id: int,
        username: Optional[str],
        full_name: Optional[str],
        direction: str,
        message_type: str,
        content: Optional[str],
        file_id: Optional[str] = None,
    ) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO messages (
                    user_id, username, full_name, direction, message_type, content, file_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, username, full_name, direction, message_type, content, file_id),
            )

    def list_clients(self) -> List[Tuple[int, Optional[str], Optional[str], str]]:
        query = (
            """
            WITH latest AS (
                SELECT user_id, MAX(created_at) AS last_message
                FROM messages
                GROUP BY user_id
            )
            SELECT
                latest.user_id,
                (
                    SELECT username
                    FROM messages m2
                    WHERE m2.user_id = latest.user_id
                        AND m2.username IS NOT NULL
                        AND m2.username <> ''
                    ORDER BY m2.created_at DESC
                    LIMIT 1
                ) AS username,
                (
                    SELECT full_name
                    FROM messages m3
                    WHERE m3.user_id = latest.user_id
                        AND m3.full_name IS NOT NULL
                        AND m3.full_name <> ''
                    ORDER BY m3.created_at DESC
                    LIMIT 1
                ) AS full_name,
                latest.last_message
            FROM latest
            ORDER BY latest.last_message DESC
            """
        )
        with self._get_connection() as conn:
            cur = conn.execute(query)
            return [(row[0], row[1], row[2], row[3]) for row in cur.fetchall()]

    def get_history(self, user_id: int, limit: int = 20) -> List[Tuple[str, str, str, str]]:
        query = (
            """
            SELECT direction, message_type, COALESCE(content, file_id) as body, created_at
            FROM messages
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """
        )
        with self._get_connection() as conn:
            cur = conn.execute(query, (user_id, limit))
            rows = cur.fetchall()
            rows.reverse()
            return [(row[0], row[1], row[2] or "", row[3]) for row in rows]
