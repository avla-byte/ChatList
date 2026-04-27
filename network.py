"""
HTTP-вызовы к API. Ключи подставляются из окружения по имени, не логируются.
"""
from __future__ import annotations

import json
import logging
import re
import os
from typing import Any, Optional, Tuple
from urllib.parse import urlunparse, urlparse

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=15.0)


def _validate_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        raise ValueError("URL пуст")
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("Нужен корректный http(s) URL")
    return u


def _ensure_chat_completions_path(api_url: str) -> str:
    """
    Если в БД записали базовый путь (/v1 или /api/v1) без /chat/completions,
    ответ часто оказывается HTML, а не JSON. Добавляем сегмент автоматически.
    """
    p = urlparse((api_url or "").strip())
    if not p.scheme or not p.netloc:
        return api_url
    path = (p.path or "").rstrip("/")
    if "chat/completions" in (p.path or ""):
        return (api_url or "").strip()
    if path in ("/v1", "/api/v1"):
        new_path = path + "/chat/completions"
        fixed = urlunparse((p.scheme, p.netloc, new_path, p.params, p.query, p.fragment))
        logger.info("URL дополнен до chat/completions: %s -> %s", (api_url or "").strip(), fixed)
        return fixed
    return (api_url or "").strip()


def _public_message_from_error_body(status: int, raw_text: str) -> str:
    """Короткое сообщение для UI из JSON-ошибки (OpenAI / OpenRouter)."""
    t = (raw_text or "").strip()[:4000]
    if not t:
        return f"Ошибка API (код {status})"
    try:
        data = json.loads(t)
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and err.get("message") is not None:
                return str(err["message"])[:800]
            if isinstance(err, str):
                return err[:800]
    except json.JSONDecodeError:
        pass
    return f"Ошибка API (код {status})"


def _redact_headers_for_log(h: httpx.Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in h.items():
        if k.lower() in ("authorization", "proxy-authorization"):
            out[k] = "(скрыто)"
        else:
            out[k] = v
    return out


def resolve_api_key(env_var: str) -> str:
    name = (env_var or "").strip()
    if not name:
        raise ValueError("api_id (имя переменной) пусто")
    val = os.environ.get(name, "").strip()
    if not val:
        raise KeyError(
            f"Переменная окружения {name!r} не задана или пуста. "
            f"Создайте .env с ключом или задайте переменную в системе.",
        )
    return val


def post_openai_compatible_chat(
    api_url: str,
    env_var: str,
    user_prompt: str,
    api_model: str,
) -> Tuple[bool, str, Optional[str]]:
    """
    POST в стиле OpenAI Chat Completions: JSON с model и messages.
    api_url — полный URL, например https://api.openai.com/v1/chat/completions
    Возврат: (ok, text_or_error_detail, public_error)
    """
    p = (user_prompt or "").strip()
    if not p:
        raise ValueError("Пустой промт")
    u = _ensure_chat_completions_path(_validate_url(api_url))
    model_name = (api_model or "").strip()
    if not model_name:
        return False, "В настройке модели не задано поле «модель API» (api_model).", "Не задана модель API"

    try:
        key = resolve_api_key(env_var)
    except (KeyError, ValueError) as e:
        msg = str(e)
        logger.warning("Ключ API: %s", msg)
        return False, msg, msg

    body: dict[str, Any] = {
        "model": model_name,
        "messages": [{"role": "user", "content": p}],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }
    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT, follow_redirects=True) as client:
            r = client.post(u, headers=headers, json=body)
    except httpx.RequestError as e:
        err = f"Сетевая ошибка: {e}"
        logger.exception("RequestError: %s", e)
        return False, err, "Не удалось выполнить HTTP-запрос"

    log_headers = _redact_headers_for_log(httpx.Headers(r.headers)) if r else {}
    if r.status_code < 200 or r.status_code >= 300:
        err_body = (r.text or "")[:2000]
        try:
            err_body = re.sub(
                r"(sk-[a-zA-Z0-9-]{20,})",
                "sk-***",
                err_body,
            )
        except re.error:
            pass
        logger.warning(
            "API ответ: status=%s, body=%s",
            r.status_code,
            err_body,
        )
        public = _public_message_from_error_body(r.status_code, err_body)
        return (
            False,
            f"HTTP {r.status_code}: {err_body or r.reason_phrase}",
            public,
        )

    try:
        data = r.json()
    except json.JSONDecodeError as e:
        t = (r.text or "")[:2000]
        is_html = (r.text or "").lstrip()[:20].lower().startswith(("<!doctype", "<html", "<!htm"))
        logger.warning("JSON decode: %s, is_html=%s, body_prefix=%r", e, is_html, (r.text or "")[:120])
        if is_html or "<html" in (r.text or "")[:200].lower():
            return (
                False,
                "Сервер вернул HTML, а не JSON. Обычно в URL нет /chat/completions. "
                "Укажите полный endpoint, напр. https://openrouter.ai/api/v1/chat/completions",
                "Ответ не JSON: проверьте URL (нужен …/chat/completions)",
            )
        return (
            False,
            f"Ответ не JSON: {t!r}"[:2000],
            "Некорректный ответ API",
        )

    content = _extract_message_content(data)
    if not content and isinstance(data, dict) and "error" in data:
        err = data.get("error")
        msg = err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)[:2000]
        return False, msg, "API вернуло ошибку"

    if content is None:
        return (
            False,
            f"Неожиданная структура ответа: {json.dumps(data, ensure_ascii=False)[:2000]}",
            "Некорректный ответ API",
        )

    return True, str(content), None


def _extract_message_content(data: Any) -> Optional[str]:
    """OpenAI-стиль: choices[0].message.content."""
    if not isinstance(data, dict):
        return None
    choices = data.get("choices")
    if not choices or not isinstance(choices, list) or not choices:
        return None
    c0 = choices[0] if isinstance(choices[0], dict) else None
    if not c0:
        return None
    msg = c0.get("message")
    if isinstance(msg, dict) and "content" in msg and msg["content"] is not None:
        return str(msg["content"])
    if "text" in c0 and c0["text"] is not None:
        return str(c0["text"])
    return None
