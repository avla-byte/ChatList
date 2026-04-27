"""
Рассылка промта в активные модели: чтение из БД, сеть, сбор строк для UI.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3
from typing import Callable, Optional

import db
import network
from session_state import ResultRow, ResultSession

logger = logging.getLogger(__name__)

_MAX_WORKERS = 8


def _one_model(
    m: db.ModelRow,
    user_prompt: str,
) -> ResultRow:
    ok, text, public = network.post_openai_compatible_chat(
        m.api_url,
        m.api_id,
        user_prompt,
        m.api_model,
    )
    if ok:
        return ResultRow(
            model_id=m.id,
            model_name=m.name,
            response_text=(text or "").strip(),
            error=None,
            selected=False,
        )
    detail = (public or text or "Ошибка")
    return ResultRow(
        model_id=m.id,
        model_name=m.name,
        response_text="",
        error=detail,
        selected=False,
    )


def run_prompt_parallel(
    conn: sqlite3.Connection,
    user_prompt: str,
    progress: Optional[Callable[[int, int], None]] = None,
) -> list[ResultRow]:
    p = (user_prompt or "").strip()
    if not p:
        raise ValueError("Пустой промт")
    try:
        active = db.list_models(conn, active_only=True)
    except Exception:
        logger.exception("list_models")
        raise
    if not active:
        logger.info("Нет активных моделей (is_active=1)")
        return []

    n = len(active)
    done = 0
    results: list[ResultRow | None] = [None] * n
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, n)) as ex:
        futs = {ex.submit(_one_model, m, p): (i, m) for i, m in enumerate(active)}
        for fut in as_completed(futs):
            i, _ = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                m = active[i]
                results[i] = ResultRow(
                    model_id=m.id,
                    model_name=m.name,
                    response_text="",
                    error=f"Сбой: {e!s}",
                    selected=False,
                )
                logger.exception("model id=%s", m.id)
            done += 1
            if progress:
                try:
                    progress(done, n)
                except Exception:
                    pass

    return [r for r in results if r is not None]


def fill_session(
    conn: sqlite3.Connection,
    session: ResultSession,
    user_prompt: str,
    source_prompt_id: Optional[int],
    progress: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Сбрасывает не нужно: вызывающий сначала session.clear() или replace."""
    rows = run_prompt_parallel(conn, user_prompt, progress=progress)
    session.replace((user_prompt or "").strip(), source_prompt_id, rows)
