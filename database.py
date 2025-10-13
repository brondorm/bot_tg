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
            SELECT m.user_id, m.username, m.full_name, MAX(m.created_at) as last_message
            FROM messages m
            GROUP BY m.user_id, m.username, m.full_name
            ORDER BY last_message DESC
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
