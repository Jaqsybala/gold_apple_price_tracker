"""SQLite persistence for price watches."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    last_price INTEGER NOT NULL,
    title TEXT,
    updated_at REAL NOT NULL,
    UNIQUE(chat_id, url)
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    # timeout: ждём снятия блокировки (Windows, OneDrive, второй экземпляр бота, редактор БД).
    conn = sqlite3.connect(str(db_path.resolve()), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def add_watch(db_path: Path, chat_id: int, url: str, last_price: int, title: str | None) -> tuple[bool, int | None]:
    """
    Insert watch. Returns (created, id).
    If duplicate (chat_id, url), returns (False, existing_id).
    """
    now = time.time()
    with _connect(db_path) as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO watches (chat_id, url, last_price, title, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (chat_id, url, last_price, title, now),
            )
            conn.commit()
            return True, int(cur.lastrowid)
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT id FROM watches WHERE chat_id = ? AND url = ?",
                (chat_id, url),
            ).fetchone()
            return False, int(row["id"]) if row else None


def list_watches_for_chat(db_path: Path, chat_id: int) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, url, last_price, title, updated_at FROM watches WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def all_watches(db_path: Path) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, chat_id, url, last_price, title, updated_at FROM watches ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def update_watch_price(db_path: Path, watch_id: int, new_price: int, title: str | None = None) -> None:
    now = time.time()
    with _connect(db_path) as conn:
        if title is not None:
            conn.execute(
                """
                UPDATE watches SET last_price = ?, title = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_price, title, now, watch_id),
            )
        else:
            conn.execute(
                "UPDATE watches SET last_price = ?, updated_at = ? WHERE id = ?",
                (new_price, now, watch_id),
            )
        conn.commit()


def delete_watch(db_path: Path, chat_id: int, watch_id: int) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM watches WHERE id = ? AND chat_id = ?",
            (watch_id, chat_id),
        )
        conn.commit()
        return cur.rowcount > 0
