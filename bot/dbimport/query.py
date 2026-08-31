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
from sqlalchemy import or_, select

from ..config import settings
from ..db.database import SyncSessionLocal
from ..db.models import DbProfile, DbRecord
from .store import SupabaseStore
from .schema import extract_emails, extract_phones

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


# ---------- перекрёстные связи ----------

_INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")
_PLATE_RE = re.compile(r"\b([А-ЯЁA-Z]{1}\d{3}[А-ЯЁA-Z]{2}\d{2,3})\b")
_CAR_INN_RE = re.compile(r"(?:" + _PLATE_RE.pattern + r"|" + _INN_RE.pattern + r")")


def _collect_identifiers(records: list[dict], profiles: list[dict]) -> dict:
    """Собирает телефоны/email/ИНН/номера авто/адреса из найденного.

    Возвращает {phones: [...], emails: [...], inns: [...], plates: [...],
    addresses: [...]} — уникальные, для последующего поиска связей.
    """
    phones: set[str] = set()
    emails: set[str] = set()
    inns: set[str] = set()
    plates: set[str] = set()
    addresses: set[str] = set()

    for it in records:
        text = " ".join(x for x in (it.get("text"), it.get("author"), it.get("url")) if x) or ""
        phones.update(extract_phones(text))
        emails.update(extract_emails(text))
        inns.update(_INN_RE.findall(text))
        plates.update(_PLATE_RE.findall(text))

    for p in profiles:
        prof = (p.get("profile") or p) if isinstance(p, dict) else {}
        phones.update(prof.get("phones") or [])
        emails.update(prof.get("emails") or [])
        if prof.get("inn"):
            inns.add(prof["inn"])
        if prof.get("registration_address"):
            addresses.add(prof["registration_address"].strip().lower())
        # Телефоны/email из текста карточки
        text = p.get("text") or ""
        phones.update(extract_phones(text))
        emails.update(extract_emails(text))
        plates.update(_PLATE_RE.findall(text))
        inns.update(_INN_RE.findall(text))

    return {
        "phones": sorted(p for p in phones if p),
        "emails": sorted(e for e in emails if e),
        "inns": sorted(i for i in inns if i),
        "plates": sorted(x.upper() for x in plates if x),
        "addresses": sorted(a for a in addresses if a),
    }


def _search_related_local(identifiers: dict, hit_profile_ids: set,
                          hit_record_ids: set) -> list[dict]:
    """Ищет другие записи/профили, связанные по телефонам/email/ИНН/авто/адресу."""
    related: dict[str, dict] = {}
    seen_ids: set = set()

    phones = identifiers.get("phones") or []
    emails = identifiers.get("emails") or []
    inns = identifiers.get("inns") or []
    plates = identifiers.get("plates") or []
    addresses = identifiers.get("addresses") or []

    with SyncSessionLocal() as session:
        # 1) Профили, делящие контакт с найденными (кроме уже выданных)
        profiles_related: list[DbProfile] = []
        if phones or emails or inns:
            name_conds = []
            phone_conds = []
            for p in phones:
                phone_conds.append(DbProfile.phones.contains(p))
            for e in emails:
                phone_conds.append(DbProfile.emails.contains(e.lower()))
            for inn in inns:
                phone_conds.append(DbProfile.inn == inn)
            if phone_conds:
                profiles_related += session.query(DbProfile).filter(
                    or_(*phone_conds)
                ).all()

        # По общему адресу — люди, проживающие по тому же адресу.
        # SQLite lower() не знает кириллицу, поэтому фильтруем в Python.
        if addresses:
            try:
                all_profiles = session.query(DbProfile).all()
                for addr in addresses:
                    for p in all_profiles:
                        if p.id in hit_profile_ids or p.id in seen_ids:
                            continue
                        stored = (p.registration_address or "").strip().lower()
                        if stored and addr and addr in stored:
                            seen_ids.add(p.id)
                            related[f"profile:{p.id}"] = _profile_row_to_item(p)
            except Exception:
                pass

        for p in profiles_related:
            if p.id in hit_profile_ids or p.id in seen_ids:
                continue
            seen_ids.add(p.id)
            related[f"profile:{p.id}"] = _profile_row_to_item(p)

        # 2) Записи (db_records), содержащие те же контакты/ИНН/номер авто
        record_terms = list(phones) + list(emails) + list(inns) + list(plates)
        if record_terms:
            conds = []
            for term in record_terms[:20]:
                conds.append(DbRecord.text.like(f"%{term}%"))
                conds.append(DbRecord.author.like(f"%{term}%"))
            rows = session.query(DbRecord).filter(or_(*conds)) \
                .order_by(DbRecord.id.desc()).limit(300).all()
            for r in rows:
                if r.id in hit_record_ids:
                    continue
                related[f"db:{r.checksum}"] = _to_item({
                    "id": r.id, "checksum": r.checksum, "section": r.section,
                    "source": r.source, "author": r.author, "text": r.text,
                    "url": r.url, "date": r.date,
                })
                if len(related) > 150:
                    break

    return list(related.values())


async def search_related(records: list[dict], profiles: list[dict]) -> list[dict]:
    """Связанные записи/профили: перекрёстные данные по контактам и ИНН.

    Принимает найденные записи и профили, вытаскивает из них телефоны/email/
    ИНН/номера авто/адреса и ищет другие сущности, которые их разделяют.
    """
    identifiers = _collect_identifiers(records, profiles)
    hit_profile_ids = {str(p.get("id", "")).replace("profile:", "") for p in profiles}
    hit_record_ids = {str(r.get("id", "")) for r in records}
    hit_profile_ids = {x for x in hit_profile_ids if x.isdigit()}
    hit_record_ids = {x for x in hit_record_ids if x.isdigit()}

    has_identifiers = bool(identifiers["phones"] or identifiers["emails"]
                           or identifiers["inns"] or identifiers["plates"]
                           or identifiers["addresses"])
    if not has_identifiers:
        return []

    return await asyncio.to_thread(
        _search_related_local, identifiers, hit_profile_ids, hit_record_ids)