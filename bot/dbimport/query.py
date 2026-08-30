"""Поиск по импортированной БД при запросах пользователя.

Источник выбирается автоматически:
- Supabase (если SUPABASE_URL + service role key настроены) — через PostgREST;
- иначе локальное зеркало в собственной БД бота (таблица db_records).
"""
import asyncio
import logging
import re
from typing import Optional

import httpx
from sqlalchemy import select

from ..config import settings
from ..db.database import SyncSessionLocal
from ..db.models import DbRecord
from .store import SupabaseStore

logger = logging.getLogger(__name__)

PLATFORM = "db"

_SELECT = "id,checksum,section,source,author,text,url,date"
_ORDER = "date.desc.nullslast"
# Окно последних записей, по которому ищем локально (для SQLite нет Unicode-lower)
_LOCAL_WINDOW = 5000


def _sanitize(value: str) -> str:
    """Чистим строку запроса для безопасного фильтра PostgREST/ilike."""
    return re.sub(r"[*%_(),;()\\]", " ", (value or "")).strip()


def _to_item(row: dict) -> dict:
    return {
        "id": row.get("checksum") or row.get("id"),
        "post_id": str(row.get("checksum") or row.get("id") or ""),
        "platform": PLATFORM,
        "author": row.get("author") or "",
        "url": row.get("url") or "",
        "text": (row.get("text") or "")[:1500],
        "published_at": row.get("date") or "",
        "score": 0.0,
    }


# ---------- Supabase ----------

async def _search_supabase(query: str, mode: str, limit: int) -> list[dict]:
    sb = SupabaseStore()
    q = _sanitize(query)
    if not (sb.enabled and q):
        return []

    if mode == "account":
        joined = f"author.ilike.*{q}*"
    else:
        joined = f"(section.ilike.*{q}*,text.ilike.*{q}*,source.ilike.*{q}*)"

    params = {"select": _SELECT, "or": f"({joined})",
              "order": _ORDER, "limit": str(limit)}
    async with httpx.AsyncClient(timeout=30, headers=sb._headers) as client:
        r = await client.get(sb.url + "/db_records", params=params)
        r.raise_for_status()
        return [_to_item(row) for row in r.json()]


# ---------- локальное зеркало ----------

def _search_local(query: str, mode: str, limit: int) -> list[dict]:
    """Поиск по локальному зеркалу.

    Фильтруем в Python: SQLite-функция lower() не знает кириллицу,
    поэтому case-insensitive поиск по Unicode делаем на стороне кода.
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    with SyncSessionLocal() as session:
        rows = session.execute(
            select(DbRecord).order_by(DbRecord.id.desc()).limit(_LOCAL_WINDOW)
        ).scalars().all()
    out: list[dict] = []
    for r in rows:
        haystack = " ".join(x for x in (r.text, r.section, r.source, r.author)
                            if x).lower()
        if q not in haystack:
            continue
        out.append(_to_item({
            "id": r.id, "checksum": r.checksum, "section": r.section,
            "source": r.source, "author": r.author, "text": r.text,
            "url": r.url, "date": r.date,
        }))
        if len(out) >= limit:
            break
    return out


# ---------- точка входа ----------

async def search_imported(query: str, mode: str = "query", limit: int = 20) -> list[dict]:
    """Записи из импортированной БД, подходящие под запрос."""
    if not (query or "").strip():
        return []
    sb = SupabaseStore()
    if sb.enabled:
        try:
            return await _search_supabase(query, mode, limit)
        except (httpx.HTTPError, ValueError) as ex:
            logger.warning("supabase search fallback to local: %s", ex)
    return await asyncio.to_thread(_search_local, query, mode, limit)