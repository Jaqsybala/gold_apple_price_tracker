"""PostgreSQL persistence for price watches (DATABASE_URL)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from psycopg import connect
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    url TEXT NOT NULL,
    last_price INTEGER NOT NULL,
    title TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (chat_id, url)
);
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def require_database_url() -> str:
    """Normalized PostgreSQL connection URI or raise RuntimeError."""
    raw = (os.environ.get("DATABASE_URL") or "").strip()
    if not raw:
        raise RuntimeError(
            "Задайте переменную окружения DATABASE_URL — строка подключения к PostgreSQL, "
            "например postgresql://USER:PASSWORD@localhost:5432/goldapple"
        )
    if raw.startswith("postgres://"):
        return "postgresql://" + raw[len("postgres://") :]
    return raw


def init_db() -> None:
    url = require_database_url()
    with connect(url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA)


def add_watch(chat_id: int, url: str, last_price: int, title: str | None) -> tuple[bool, int | None]:
    """
    Insert watch. Returns (created, id).
    If duplicate (chat_id, url), returns (False, existing_id).
    """
    now = _utc_now()
    durl = require_database_url()
    with connect(durl, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO watches (chat_id, url, last_price, title, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (chat_id, url, last_price, title, now, now),
                )
                row = cur.fetchone()
                return True, int(row["id"]) if row else None
            except UniqueViolation:
                conn.rollback()
                cur.execute(
                    "SELECT id FROM watches WHERE chat_id = %s AND url = %s",
                    (chat_id, url),
                )
                row = cur.fetchone()
                return False, int(row["id"]) if row else None


def list_watches_for_chat(chat_id: int) -> list[dict]:
    durl = require_database_url()
    with connect(durl, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, url, last_price, title, created_at, updated_at
                FROM watches WHERE chat_id = %s ORDER BY id
                """,
                (chat_id,),
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def all_watches() -> list[dict]:
    durl = require_database_url()
    with connect(durl, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, chat_id, url, last_price, title, created_at, updated_at
                FROM watches ORDER BY id
                """
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def update_watch_price(watch_id: int, new_price: int, title: str | None = None) -> None:
    now = _utc_now()
    durl = require_database_url()
    with connect(durl, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            if title is not None:
                cur.execute(
                    """
                    UPDATE watches SET last_price = %s, title = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (new_price, title, now, watch_id),
                )
            else:
                cur.execute(
                    "UPDATE watches SET last_price = %s, updated_at = %s WHERE id = %s",
                    (new_price, now, watch_id),
                )


def delete_watch(chat_id: int, watch_id: int) -> bool:
    durl = require_database_url()
    with connect(durl, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM watches WHERE id = %s AND chat_id = %s",
                (watch_id, chat_id),
            )
            return cur.rowcount > 0
