"""
Рассылка промта в активные модели: чтение из БД, сеть, сбор строк для UI.
"""
from __future__ import annotations

import logging
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3
from dataclasses import dataclass
from typing import Callable, Optional

import db
import network
from session_state import ResultRow, ResultSession

logger = logging.getLogger(__name__)

_MAX_WORKERS = 8


@dataclass
class PromptAssistResult:
    original_prompt: str
    improved_prompt: str
    alternatives: list[str]
    adaptations: dict[str, str]


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


def improve_prompt(
    conn: sqlite3.Connection,
    user_prompt: str,
    assistant_model_id: Optional[int] = None,
) -> PromptAssistResult:
    p = (user_prompt or "").strip()
    if len(p) < 5:
        raise ValueError("Введите более подробный промт (минимум 5 символов).")
    active = db.list_models(conn, active_only=True)
    if not active:
        raise RuntimeError("Нет активных моделей для AI-ассистента.")
    m: Optional[db.ModelRow] = None
    if assistant_model_id is not None:
        m = next((x for x in active if x.id == assistant_model_id), None)
        if m is None:
            raise RuntimeError(
                "Выбранная модель ассистента недоступна. "
                "Проверьте настройку и активность модели.",
            )
    if m is None:
        m = active[0]
    logger.info("AI-ассистент: модель id=%s name=%s", m.id, m.name)

    system_prompt = (
        "Ты ассистент по улучшению пользовательских промтов. "
        "Ответь строго JSON-объектом без markdown и без комментариев. "
        "Формат: {"
        "\"improved_prompt\": \"...\", "
        "\"alternatives\": [\"...\", \"...\"], "
        "\"adaptations\": {\"code\": \"...\", \"analysis\": \"...\", \"creative\": \"...\"}"
        "}. "
        "Сохраняй исходный смысл. Удали лишнюю воду. "
        "alternatives дай 2-3 варианта. "
        "Если адаптация не нужна, верни пустую строку."
    )
    ok, text, public = network.post_openai_compatible_chat_messages(
        m.api_url,
        m.api_id,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": p},
        ],
        m.api_model,
    )
    if not ok:
        raise RuntimeError(public or text or "Ошибка AI-ассистента")
    return _parse_prompt_assistant_response(p, text or "")


def _parse_prompt_assistant_response(original_prompt: str, response_text: str) -> PromptAssistResult:
    t = (response_text or "").strip()
    if not t:
        raise RuntimeError("AI-ассистент вернул пустой ответ.")
    data = _try_extract_json(t)
    if isinstance(data, dict):
        improved = str(data.get("improved_prompt") or "").strip()
        alts_raw = data.get("alternatives")
        alternatives: list[str] = []
        if isinstance(alts_raw, list):
            for item in alts_raw:
                s = str(item or "").strip()
                if s:
                    alternatives.append(s)
        adapt_raw = data.get("adaptations")
        adaptations: dict[str, str] = {}
        if isinstance(adapt_raw, dict):
            for key in ("code", "analysis", "creative"):
                v = str(adapt_raw.get(key) or "").strip()
                if v:
                    adaptations[key] = v
        if improved:
            return PromptAssistResult(
                original_prompt=original_prompt,
                improved_prompt=improved,
                alternatives=alternatives[:3],
                adaptations=adaptations,
            )
    # fallback: показываем ответ как улучшенный промт
    logger.warning("AI-ассистент вернул неструктурированный формат, применен fallback")
    return PromptAssistResult(
        original_prompt=original_prompt,
        improved_prompt=t,
        alternatives=[],
        adaptations={},
    )


def _try_extract_json(text: str) -> Optional[dict]:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None
