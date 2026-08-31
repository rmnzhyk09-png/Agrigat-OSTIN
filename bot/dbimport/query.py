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
from ..db.models import DbProfile, DbRecord
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


# ---------- поиск профилей ----------

def _search_profiles_local(query: str, limit: int = 5) -> list[dict]:
    """Ищет профили (db_profiles) по ФИО / телефону / email / ИНН."""
    q = (query or "").strip()
    if not q:
        return []
    qlower = q.lower()
    digits = "".join(c for c in q if c.isdigit())

    out: list[dict] = []
    with SyncSessionLocal() as session:
        # По ФИО (частично)
        try:
            name_rows = session.query(DbProfile).filter(
                DbProfile.full_name.ilike(f"%{q}%")
            ).limit(limit).all()
            out.extend(name_rows)
        except Exception as ex:
            logger.debug("profile name search: %s", ex)

        # По телефону / email / ИНН
        extra_rows: list[DbProfile] = []
        if digits:
            extra_rows += session.query(DbProfile).filter(
                DbProfile.phones.contains(digits)
            ).limit(limit).all()
        if "@" in q:
            extra_rows += session.query(DbProfile).filter(
                DbProfile.emails.contains(qlower)
            ).limit(limit).all()
        if q.isdigit() and len(q) in (10, 12):
            extra_rows += session.query(DbProfile).filter(
                DbProfile.inn == q
            ).limit(limit).all()

    ids = {p.id for p in out}
    for p in extra_rows:
        if p.id not in ids:
            ids.add(p.id)
            out.append(p)
    if len(out) > limit:
        out = out[:limit]

    result: list[dict] = []
    for p in out:
        result.append(_profile_row_to_item(p))
    return result


def _profile_row_to_item(p: DbProfile) -> dict:
    """Превращает строку DbProfile в запись-находку с полной карточкой."""
    phones = p.phones if isinstance(p.phones, list) else []
    emails = p.emails if isinstance(p.emails, list) else []

    url = p.vk_url or p.telegram.get("url") if isinstance(p.telegram, dict) else p.vk_url
    text_parts = []
    if p.full_name:
        text_parts.append(f"ФИО: {p.full_name}")
    if p.date_of_birth:
        text_parts.append(f"Дата рождения: {p.date_of_birth}")
    for ph in phones:
        text_parts.append(f"Телефон: {ph}")
    for e in emails:
        text_parts.append(f"Email: {e}")
    if p.registration_address:
        text_parts.append(f"Адрес: {p.registration_address}")
    if p.inn:
        text_parts.append(f"ИНН: {p.inn}")
    if p.snils:
        text_parts.append(f"СНИЛС: {p.snils}")
    if p.passport_series and p.passport_number:
        text_parts.append(f"Паспорт: {p.passport_series} {p.passport_number}")
    if p.vehicles:
        vehicles = p.vehicles if isinstance(p.vehicles, list) else []
        for v in vehicles[:5]:
            if isinstance(v, dict):
                plate = v.get("plate", "")
                make = v.get("make", "")
                text_parts.append(f"Авто: {make} {plate}".strip())
    if p.court_cases_count:
        text_parts.append(f"Судебных дел: {p.court_cases_count}")
    if p.enforcement_debt_total:
        text_parts.append(f"Долги приставам: {p.enforcement_debt_total} руб.")
    if p.criminal_record:
        text_parts.append("Судимость: есть")
    if p.bankruptcy_status:
        text_parts.append(f"Банкротство: {p.bankruptcy_status}")
    if p.exit_ban:
        text_parts.append("Ограничение на выезд: да")
    if p.current_employer:
        text_parts.append(f"Работа: {p.current_employer}")
    if p.businesses:
        biz = p.businesses if isinstance(p.businesses, list) else []
        for b in biz[:5]:
            if isinstance(b, dict):
                text_parts.append(f"Бизнес: {b.get('name', '')} ({b.get('role', '')})")

    text = "\n".join(text_parts)
    return {
        "id": f"profile:{p.id}",
        "post_id": f"profile:{p.id}",
        "platform": "profile",
        "author": p.full_name or "",
        "url": url or "",
        "text": text,
        "published_at": p.date_of_birth or "",
        "score": 1.0 if p.full_name else 0.8,
        "profile": {
            "full_name": p.full_name,
            "phones": phones,
            "emails": emails,
            "telegram": p.telegram,
            "vk_url": p.vk_url,
            "instagram_url": p.instagram_url,
            "registration_address": p.registration_address,
            "inn": p.inn,
            "snils": p.snils,
            "confidence": p.overall_confidence,
            "completeness": p.completeness_score,
        },
    }


async def search_profiles(query: str, limit: int = 5) -> list[dict]:
    """Профили людей из db_profiles по запросу (ФИО/телефон/email/ИНН)."""
    if not (query or "").strip():
        return []
    return await asyncio.to_thread(_search_profiles_local, query, limit)