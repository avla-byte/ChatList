"""
Экспорт результатов сессии в Markdown / JSON.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from session_state import ResultRow


def _row_dict(r: ResultRow) -> dict:
    d = {
        "model": r.model_name,
        "model_id": r.model_id,
        "response_text": r.response_text,
        "error": r.error,
        "selected": r.selected,
        "ok": r.is_ok,
    }
    return d


def export_json(prompt: str, rows: list[ResultRow]) -> str:
    p = (prompt or "").strip()
    payload = {
        "app": "ChatList",
        "export_version": 1,
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "prompt": p,
        "items": [_row_dict(r) for r in rows],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def export_markdown(prompt: str, rows: list[ResultRow]) -> str:
    p = (prompt or "").strip()
    at = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M UTC")
    out: list[str] = [
        "# ChatList",
        "",
        f"Экспорт: {at}",
        "",
        "## Промт",
        "",
        p,
        "",
        "## Ответы",
        "",
    ]
    for r in rows:
        out.append(f"### {r.model_name}")
        out.append("")
        if r.is_ok:
            out.append((r.response_text or "").rstrip() or "_(пусто)_")
        else:
            err = (r.error or "ошибка").replace("\n", " ")
            out.append(f"**Ошибка:** {err}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
