"""
Доступ к SQLite. Вся работа с БД — только через этот модуль.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION_KEY = "schema_version"
CURRENT_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class PromptRow:
    id: int
    created_at: str
    body: str
    tags: Optional[str]


@dataclass(frozen=True, slots=True)
class ModelRow:
    id: int
    name: str
    api_url: str
    api_id: str
    api_model: str
    is_active: int


@dataclass(frozen=True, slots=True)
class ResultInsert:
    model_id: int
    prompt_id: Optional[int]
    prompt_snapshot: str
    response_text: str


@dataclass(frozen=True, slots=True)
class ResultSavedRow:
    """Строка из таблицы results с именем модели из join."""

    id: int
    created_at: str
    model_id: int
    model_name: str
    prompt_id: Optional[int]
    prompt_snapshot: str
    response_text: str


def get_connection(path: Path) -> sqlite3.Connection:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error:
        logger.exception("Не удалось включить foreign_keys")
        raise
    return conn


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _tables(conn: sqlite3.Connection) -> set[str]:
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
        )
        return {r[0] for r in cur.fetchall()}
    except sqlite3.Error as e:
        logger.exception("Ошибка чтения sqlite_master: %s", e)
        raise


def init_db(conn: sqlite3.Connection) -> None:
    """Создаёт таблицы и применяет миграции по schema_version в settings."""
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """,
        )
        ver = int(get_setting(conn, SCHEMA_VERSION_KEY) or "0")

        if ver < 1:
            _create_v1_schema(conn)
            set_setting(conn, SCHEMA_VERSION_KEY, "1")
            ver = 1
            logger.info("БД: применена схема версии 1")

        if ver < 2:
            if "models" in _tables(conn):
                cols = {r[1] for r in conn.execute("PRAGMA table_info(models)").fetchall()}
                if "api_model" not in cols:
                    conn.execute(
                        "ALTER TABLE models ADD COLUMN api_model TEXT NOT NULL DEFAULT ''",
                    )
            set_setting(conn, SCHEMA_VERSION_KEY, "2")
            ver = 2
            logger.info("БД: миграция 2 (api_model)")

        if ver < CURRENT_SCHEMA_VERSION:
            set_setting(conn, SCHEMA_VERSION_KEY, str(CURRENT_SCHEMA_VERSION))
    except sqlite3.Error:
        logger.exception("Ошибка init_db")
        raise


def _create_v1_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            body TEXT NOT NULL,
            tags TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_prompts_created ON prompts(created_at);

        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            api_url TEXT NOT NULL,
            api_id TEXT NOT NULL,
            api_model TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_models_active ON models(is_active);

        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            model_id INTEGER NOT NULL,
            prompt_id INTEGER,
            prompt_snapshot TEXT NOT NULL,
            response_text TEXT NOT NULL,
            FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE RESTRICT,
            FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_results_created ON results(created_at);
        CREATE INDEX IF NOT EXISTS idx_results_model ON results(model_id);
        """,
    )


def get_setting(conn: sqlite3.Connection, key: str) -> Optional[str]:
    if not key or not key.strip():
        raise ValueError("key пуст")
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row[0]) if row else None
    except sqlite3.Error:
        logger.exception("Ошибка get_setting(%s)", key)
        raise


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    if not key or not key.strip():
        raise ValueError("key пуст")
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception("Ошибка set_setting")
        raise


# --- prompts ---

def list_prompts(conn: sqlite3.Connection) -> list[PromptRow]:
    try:
        cur = conn.execute(
            "SELECT id, created_at, body, tags FROM prompts ORDER BY created_at DESC",
        )
        return [_row_to_prompt(r) for r in cur.fetchall()]
    except sqlite3.Error:
        logger.exception("list_prompts")
        raise


def get_prompt(conn: sqlite3.Connection, prompt_id: int) -> Optional[PromptRow]:
    try:
        row = conn.execute(
            "SELECT id, created_at, body, tags FROM prompts WHERE id = ?",
            (prompt_id,),
        ).fetchone()
        return _row_to_prompt(row) if row else None
    except sqlite3.Error:
        logger.exception("get_prompt %s", prompt_id)
        raise


def _row_to_prompt(row: sqlite3.Row) -> PromptRow:
    return PromptRow(
        id=row["id"],
        created_at=row["created_at"],
        body=row["body"],
        tags=row["tags"],
    )


def insert_prompt(conn: sqlite3.Connection, body: str, tags: Optional[str] = None) -> int:
    body = (body or "").strip()
    if not body:
        raise ValueError("Текст промта пуст")
    tags_n = (tags or "").strip() or None
    try:
        cur = conn.execute(
            "INSERT INTO prompts (created_at, body, tags) VALUES (?, ?, ?)",
            (_utc_now_iso(), body, tags_n),
        )
        conn.commit()
        return int(cur.lastrowid)
    except sqlite3.Error:
        logger.exception("insert_prompt")
        raise


def update_prompt(
    conn: sqlite3.Connection,
    prompt_id: int,
    body: str,
    tags: Optional[str] = None,
) -> None:
    body = (body or "").strip()
    if not body:
        raise ValueError("Текст промта пуст")
    tags_n = (tags or "").strip() or None
    try:
        cur = conn.execute(
            "UPDATE prompts SET body = ?, tags = ? WHERE id = ?",
            (body, tags_n, prompt_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"Промт id={prompt_id} не найден")
    except sqlite3.Error:
        logger.exception("update_prompt %s", prompt_id)
        raise


def delete_prompt(conn: sqlite3.Connection, prompt_id: int) -> None:
    try:
        cur = conn.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"Промт id={prompt_id} не найден")
    except sqlite3.Error:
        logger.exception("delete_prompt %s", prompt_id)
        raise


# --- models (table name: models) ---

def list_models(conn: sqlite3.Connection, active_only: bool = False) -> list[ModelRow]:
    where = "WHERE is_active = 1" if active_only else ""
    try:
        cur = conn.execute(
            f"SELECT id, name, api_url, api_id, api_model, is_active FROM models {where} "
            f"ORDER BY name ASC",
        )
        return [_row_to_model(r) for r in cur.fetchall()]
    except sqlite3.Error:
        logger.exception("list_models")
        raise


def get_model(conn: sqlite3.Connection, model_id: int) -> Optional[ModelRow]:
    try:
        row = conn.execute(
            "SELECT id, name, api_url, api_id, api_model, is_active "
            "FROM models WHERE id = ?",
            (model_id,),
        ).fetchone()
        return _row_to_model(row) if row else None
    except sqlite3.Error:
        logger.exception("get_model %s", model_id)
        raise


def _row_to_model(row: sqlite3.Row) -> ModelRow:
    return ModelRow(
        id=row["id"],
        name=row["name"],
        api_url=row["api_url"],
        api_id=row["api_id"],
        api_model=row["api_model"] or "",
        is_active=int(row["is_active"]),
    )


def insert_model(
    conn: sqlite3.Connection,
    name: str,
    api_url: str,
    api_id: str,
    api_model: str = "",
    is_active: int = 1,
) -> int:
    name, api_url, api_id = (name or "").strip(), (api_url or "").strip(), (api_id or "").strip()
    if not name or not api_url or not api_id:
        raise ValueError("name, api_url и api_id обязательны")
    if is_active not in (0, 1):
        raise ValueError("is_active должен быть 0 или 1")
    try:
        cur = conn.execute(
            "INSERT INTO models (name, api_url, api_id, api_model, is_active) VALUES (?, ?, ?, ?, ?)",
            (name, api_url, api_id, (api_model or "").strip(), is_active),
        )
        conn.commit()
        return int(cur.lastrowid)
    except sqlite3.Error:
        logger.exception("insert_model")
        raise


def update_model(
    conn: sqlite3.Connection,
    model_id: int,
    name: str,
    api_url: str,
    api_id: str,
    api_model: str,
    is_active: int,
) -> None:
    if model_id < 1:
        raise ValueError("Некорректный model_id")
    name, api_url, api_id = (name or "").strip(), (api_url or "").strip(), (api_id or "").strip()
    if not name or not api_url or not api_id:
        raise ValueError("name, api_url и api_id обязательны")
    if is_active not in (0, 1):
        raise ValueError("is_active должен быть 0 или 1")
    try:
        r = conn.execute(
            "UPDATE models SET name = ?, api_url = ?, api_id = ?, api_model = ?, is_active = ? "
            "WHERE id = ?",
            (name, api_url, api_id, (api_model or "").strip(), is_active, model_id),
        )
        if r.rowcount == 0:
            raise ValueError(f"Модель id={model_id} не найдена")
        conn.commit()
    except sqlite3.Error:
        logger.exception("update_model")
        raise


def delete_model(conn: sqlite3.Connection, model_id: int) -> None:
    try:
        r = conn.execute("DELETE FROM models WHERE id = ?", (model_id,))
        if r.rowcount == 0:
            raise ValueError(f"Модель id={model_id} не найдена")
        conn.commit()
    except sqlite3.Error:
        logger.exception("delete_model")
        raise


# --- results ---

def list_saved_results(
    conn: sqlite3.Connection,
    limit: int = 2000,
) -> list[ResultSavedRow]:
    """Сохранённые ответы, новые сверху. Имя модели из `models`."""
    if limit < 1 or limit > 50_000:
        raise ValueError("limit должен быть в разумных пределах")
    try:
        cur = conn.execute(
            """
            SELECT r.id, r.created_at, r.model_id, m.name, r.prompt_id,
                   r.prompt_snapshot, r.response_text
            FROM results r
            JOIN models m ON m.id = r.model_id
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        out: list[ResultSavedRow] = []
        for row in cur.fetchall():
            out.append(
                ResultSavedRow(
                    id=row["id"],
                    created_at=row["created_at"],
                    model_id=row["model_id"],
                    model_name=row["name"],
                    prompt_id=row["prompt_id"],
                    prompt_snapshot=row["prompt_snapshot"],
                    response_text=row["response_text"],
                ),
            )
        return out
    except sqlite3.Error:
        logger.exception("list_saved_results")
        raise


def insert_results(
    conn: sqlite3.Connection,
    rows: list[ResultInsert],
) -> None:
    if not rows:
        return
    for r in rows:
        if r.model_id < 1 or not (r.prompt_snapshot or "").strip():
            raise ValueError("Каждая запись: нужны model_id и prompt_snapshot")
    now = _utc_now_iso()
    try:
        conn.executemany(
            "INSERT INTO results (created_at, model_id, prompt_id, prompt_snapshot, response_text) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (now, r.model_id, r.prompt_id, r.prompt_snapshot, r.response_text) for r in rows
            ],
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception("insert_results")
        raise
