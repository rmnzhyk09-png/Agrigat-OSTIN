"""Интеграция DataTech OSINT API (https://datatech.work/public-api/data/search).

Параллельный поиск по внешним базам (телефоны/ФИО/email). Подключение включается
заданием DATATECH_API_KEY (+ опционально DATATECH_BASE_URL) в окружении.

ВНИМАНИЕ: точная схема запроса/ответа зависит от провайдера DataTech. Несколько
распространённых форм поддерживаются из коробки; при необходимости подогнать под
реальный пример — см. DATATECH_MODE и поля ниже.

Реализована best-effort: при любых ошибках (недоступность, HTTP!=200, неизвестная
схема) возвращает []; боту это не ломает.
"""
import asyncio
import json
import logging
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

PLATFORM = "datatech"

DEFAULT_BASE_URL = "https://datatech.work/public-api/data/search"

# Включено ли подключение
def _enabled() -> bool:
    return bool(getattr(settings, "datatech_api_key", None))


async def _request(query: str) -> list[dict]:
    """Один запрос к DataTech; возвращает список сырых записей."""
    base = (getattr(settings, "datatech_base_url", None) or DEFAULT_BASE_URL).strip()
    key = settings.datatech_api_key
    mode = (getattr(settings, "datatech_mode", None) or "auto").strip()

    headers = {
        "Authorization": f"Bearer {key}",
        "X-API-Key": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        status = None
        text = ""
        # 1) POST JSON {query}
        try:
            r = await client.post(base, json={"query": query}, headers=headers)
            status, text = r.status_code, r.text
            if r.status_code < 400 and r.text.strip():
                return _normalize(r.json())
        except httpx.HTTPError as ex:
            logger.debug("datatech POST query: %s", ex)

        # 2) GET с телефоном/словом в query-параметрах
        try:
            params = {}
            digits = "".join(c for c in query if c.isdigit())
            if digits:
                params["phone"] = digits
            params["q"] = query
            r = await client.get(base, params=params, headers=headers)
            if r.status_code < 400 and r.text.strip():
                return _normalize(r.json())
        except httpx.HTTPError as ex:
            logger.debug("datatech GET: %s", ex)

        # 3) POST form-encoded
        try:
            r = await client.post(base, data={"query": query}, headers={**headers,
                                   "Content-Type": "application/x-www-form-urlencoded"})
            if r.status_code < 400 and r.text.strip():
                return _normalize(r.json())
        except httpx.HTTPError as ex:
            logger.debug("datatech POST form: %s", ex)

        logger.info("datatech: endpoint %s -> HTTP %s (mode=%s)", base, status, mode)
        return []


def _normalize(payload) -> list[dict]:
    """Приводит ответ DataTech к списку записей с текстом карточки."""
    if payload is None:
        return []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        # разные варианты конверта ответа
        rows = payload.get("data") or payload.get("results") or payload.get("items") \
            or payload.get("records") or payload.get("rows")
        if rows is None:
            rows = [payload]
    else:
        return []
    if not isinstance(rows, list):
        rows = [rows]
    return rows


def _row_to_item(row) -> dict:
    """Сырая запись DataTech -> item бота."""
    if isinstance(row, str):
        return {"platform": PLATFORM, "author": row, "text": row, "url": "", "score": 0.7}
    if not isinstance(row, dict):
        return {"platform": PLATFORM, "author": str(row), "text": str(row),
                "url": "", "score": 0.7}
    lines = []
    for key, val in row.items():
        if val is None or val == "":
            continue
        if isinstance(val, (list, dict)):
            try:
                val = json.dumps(val, ensure_ascii=False)[:300]
            except Exception:
                val = str(val)
        # пропускаем служебные
        if str(key).lower() in ("id", "score", "_id", "@id"):
            continue
        lines.append(f"{str(key).capitalize()}: {val}")

    return {
        "platform": PLATFORM,
        "author": str(row.get("name") or row.get("full_name") or row.get("fio")
                       or row.get("query") or ""),
        "url": str(row.get("url") or row.get("profile_url") or ""),
        "text": "\n".join(lines)[:1500],
        "published_at": "",
        "score": 0.9,
    }


async def search_datatech(query: str, limit: int = 10) -> list[dict]:
    """Параллельный поиск по внешним базам DataTech."""
    if not _enabled():
        return []
    q = (query or "").strip()
    if not q:
        return []
    try:
        rows = await _request(q)
    except Exception as ex:
        logger.warning("datatech search error: %s", ex)
        return []
    items = [_row_to_item(r) for r in rows]
    seen: set = set()
    out = []
    for it in items:
        key = f"{it.get('url')}|{it.get('text')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= limit:
            break
    return out
