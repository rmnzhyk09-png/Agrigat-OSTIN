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
# Локальный поиск по зеркалу: по умолчанию сканируем ВСЁ зеркало (не обрезаем),
# иначе импортированные записи молча пропадают из выдачи. Можно ограничить
# через DB_LOCAL_WINDOW (N последних записей) для ускорения fallback-поиска.
def _window() -> int | None:
    w = getattr(settings, "db_local_window", 0) or 0
    return None if w <= 0 else w


def _sanitize(value: str) -> str:
    """Чистим строку запроса для безопасного фильтра PostgREST/ilike."""
    return re.sub(r"[*%_(),;()\\]", " ", (value or "")).strip()


# Синонимы полей -> канонический ключ поиска
FIELD_ALIASES = {
    "имя": "name", "фио": "name", "фам": "name", "фамилия": "name",
    "name": "name", "иван": "name",
    "тел": "phone", "телефон": "phone", "номер": "phone", "т": "phone",
    "phone": "phone",
    "инн": "inn", "иин": "inn",
    "паспорт": "passport", "пасп": "passport", "паспорту": "passport",
    "снилс": "snils", "снилы": "snils",
    "почта": "email", "мэйл": "email", "mail": "email", "email": "email",
    "авто": "auto", "автомобиль": "auto", "госномер": "auto",
    "машина": "auto", "номер авто": "auto",
}

_FIELD_RE = re.compile(
    r"^\s*(?P<field>[а-яёa-z0-9]+)\s*[:=]\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)


def parse_search_field(query: str):
    """Разбирает запрос вида 'телефон: 9001234567' или 'инн=7701234567'.

    Возвращает кортеж (field, value):
      field: name/phone/email/inn/passport/snils/auto или "" (автоопределение)
      value: само значение без префикса
    """
    text = (query or "").strip()
    if not text:
        return "", text
    m = _FIELD_RE.match(text)
    if m:
        key = m.group("field").strip().lower()
        if key in FIELD_ALIASES:
            return FIELD_ALIASES[key], m.group("value").strip()
    return "", text



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

async def _search_supabase(query: str, mode: str, limit: int,
                           field: str = "") -> list[dict]:
    sb = SupabaseStore()
    q = _sanitize(query)
    if not (sb.enabled and q):
        return []

    params: dict = {"select": _SELECT, "limit": str(limit)}

    if field == "phone":
        params["or"] = f"(text.ilike.*{q}*,author.ilike.*{q}*)"
    elif field == "name":
        params["or"] = f"(author.ilike.*{q}*,text.ilike.*{q}*)"
    elif field in ("email", "inn", "passport", "auto"):
        params["or"] = f"(text.ilike.*{q}*,author.ilike.*{q}*)"
    elif mode == "account":
        params["or"] = f"(author.ilike.*{q}*,text.ilike.*{q}*)"
    else:
        params["or"] = (f"(section.ilike.*{q}*,text.ilike.*{q}*,"
                        f"source.ilike.*{q}*,author.ilike.*{q}*)")

    async with httpx.AsyncClient(timeout=60, headers=sb._headers) as client:
        r = await client.get(sb.url + "/db_records", params=params)
        r.raise_for_status()
        return [_to_item(row) for row in r.json()]


# ---------- локальное зеркало ----------

def _haystack_for(r, field: str) -> str:
    """Поле записи, по которому ищем (в зависимости от выбранного тега)."""
    parts = []
    if field == "phone":
        # Телефоны нормализуются в text 'Телефон: +7...'
        parts.append(r.text or "")
    elif field == "name":
        parts.append(r.author or "")
        parts.append(r.text or "")
    elif field == "email":
        parts.append(r.text or "")
    elif field == "inn":
        parts.append(r.text or "")
    elif field == "passport":
        parts.append(r.text or "")
    elif field == "auto":
        parts.append(r.text or "")
    else:
        parts = [r.text, r.section, r.source, r.author]
    return " ".join(x for x in parts if x).lower()


def _search_local(query: str, mode: str, limit: int, field: str = "") -> list[dict]:
    """Поиск по локальному зеркалу.

    Фильтруем в Python: SQLite-функция lower() не знает кириллицу,
    поэтому case-insensitive поиск по Unicode делаем на стороне кода.
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    with SyncSessionLocal() as session:
        stmt = select(DbRecord).order_by(DbRecord.id.desc())
        w = _window()
        if w:
            stmt = stmt.limit(w)
        rows = session.execute(stmt).scalars().all()
    out: list[dict] = []
    for r in rows:
        haystack = _haystack_for(r, field)
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

async def search_imported(query: str, mode: str = "query", limit: int = 20,
                          field: str = "") -> list[dict]:
    """Записи из импортированной БД, подходящие под запрос."""
    q = (query or "").strip()
    if not q:
        return []
    parsed_field, parsed_val = parse_search_field(q)
    if parsed_field:
        field = parsed_field
        q = parsed_val
    if not q:
        q = (query or "").strip()
    sb = SupabaseStore()
    if sb.enabled:
        try:
            return await _search_supabase(q, mode, limit, field)
        except (httpx.HTTPError, ValueError) as ex:
            logger.warning("supabase search fallback to local: %s", ex)
    return await asyncio.to_thread(_search_local, q, mode, limit, field)


# ---------- поиск профилей ----------

def _search_profiles_local(query: str, limit: int = 5, field: str = "") -> list[dict]:
    """Ищет профили (db_profiles) по ФИО / телефону / email / ИНН.

    Если задан field (имя/телефон/email/инн/паспорт/авто), ищет строго по нему.
    """
    q = (query or "").strip()
    if not q:
        return []
    qlower = q.lower()
    digits = "".join(c for c in q if c.isdigit())

    out: list[dict] = []
    with SyncSessionLocal() as session:
        name_rows: list[DbProfile] = []
        extra_rows: list[DbProfile] = []

        if field == "name":
            name_rows = session.query(DbProfile).filter(
                DbProfile.full_name.ilike(f"%{q}%")
            ).limit(limit).all()
        elif field in ("phone", "тел", "телефон"):
            if digits:
                extra_rows = session.query(DbProfile).filter(
                    DbProfile.phones.contains(digits)
                ).limit(limit).all()
        elif field in ("email", "почта"):
            extra_rows = session.query(DbProfile).filter(
                DbProfile.emails.contains(qlower)
            ).limit(limit).all()
        elif field in ("inn", "иин"):
            extra_rows = session.query(DbProfile).filter(
                DbProfile.inn == q
            ).limit(limit).all()
        elif field == "passport":
            extra_rows = session.query(DbProfile).filter(
                (DbProfile.passport_series.like(f"%{q}%")) |
                (DbProfile.passport_number.like(f"%{q}%"))
            ).limit(limit).all()
        elif field in ("auto", "авто", "госномер"):
            # ищем по госномеру в JSON vehicles через текст карточки (в Python ниже)
            pass
        elif field == "snils":
            extra_rows = session.query(DbProfile).filter(
                DbProfile.snils == q
            ).limit(limit).all()
        else:
            # без тега — ищем везде (как раньше)
            try:
                name_rows = session.query(DbProfile).filter(
                    DbProfile.full_name.ilike(f"%{q}%")
                ).limit(limit).all()
            except Exception as ex:
                logger.debug("profile name search: %s", ex)
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

        out.extend(name_rows)

        # Поиск по госномеру авто (JSON) — фильтруем в Python
        if field in ("auto", "авто", "госномер") or (field in ("", "имя") and not (name_rows or extra_rows)):
            plate_key = q.upper().replace(" ", "")
            try:
                all_profiles = session.query(DbProfile).all()
                for p in all_profiles:
                    vehicles = p.vehicles if isinstance(p.vehicles, list) else []
                    for v in vehicles:
                        if isinstance(v, dict) and plate_key and \
                           plate_key in str(v.get("plate") or "").upper():
                            if p.id not in {x.id for x in out} and \
                               p.id not in {x.id for x in extra_rows}:
                                extra_rows.append(p)
                            break
            except Exception as ex:
                logger.debug("profile auto search: %s", ex)

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


_PROFILE_SELECT = ("full_name,phones,emails,inn,snils,date_of_birth,registration_address,"
                   "vk_url,instagram_url,telegram,overall_confidence,completeness_score,"
                   "passport_series,passport_number,vehicles,court_cases_count,"
                   "enforcement_debt_total,criminal_record,bankruptcy_status,exit_ban,"
                   "current_employer,businesses,id")


def _profile_row_to_item_supabase(row: dict) -> dict:
    """Превращает строку db_profiles (Supabase) в запись-находку."""
    phones = row.get("phones") if isinstance(row.get("phones"), list) else []
    emails = row.get("emails") if isinstance(row.get("emails"), list) else []
    full_name = row.get("full_name") or ""
    telegram = row.get("telegram")
    url = row.get("vk_url") or (telegram.get("url") if isinstance(telegram, dict) else "") or ""

    text_parts = []
    if full_name:
        text_parts.append(f"ФИО: {full_name}")
    if row.get("date_of_birth"):
        text_parts.append(f"Дата рождения: {row['date_of_birth']}")
    for ph in phones:
        text_parts.append(f"Телефон: {ph}")
    for e in emails:
        text_parts.append(f"Email: {e}")
    if row.get("registration_address"):
        text_parts.append(f"Адрес: {row['registration_address']}")
    if row.get("inn"):
        text_parts.append(f"ИНН: {row['inn']}")
    if row.get("passport_series") or row.get("passport_number"):
        text_parts.append(f"Паспорт: {row.get('passport_series') or ''} {row.get('passport_number') or ''}".strip())
    vehicles = row.get("vehicles") if isinstance(row.get("vehicles"), list) else []
    for v in vehicles[:5]:
        if isinstance(v, dict):
            plate = v.get("plate", "")
            make = v.get("make", "")
            text_parts.append(f"Авто: {make} {plate}".strip())
    if row.get("court_cases_count"):
        text_parts.append(f"Судебных дел: {row['court_cases_count']}")
    if row.get("enforcement_debt_total"):
        text_parts.append(f"Долги приставам: {row['enforcement_debt_total']} руб.")
    if row.get("criminal_record"):
        text_parts.append("Судимость: есть")
    if row.get("bankruptcy_status"):
        text_parts.append(f"Банкротство: {row['bankruptcy_status']}")
    if row.get("exit_ban"):
        text_parts.append("Ограничение на выезд: да")
    if row.get("current_employer"):
        text_parts.append(f"Работа: {row['current_employer']}")
    biz = row.get("businesses") if isinstance(row.get("businesses"), list) else []
    for b in biz[:5]:
        if isinstance(b, dict):
            text_parts.append(f"Бизнес: {b.get('name', '')} ({b.get('role', '')})")

    text = "\n".join(text_parts)
    return {
        "id": f"profile:{row.get('id')}",
        "post_id": f"profile:{row.get('id')}",
        "platform": "profile",
        "author": full_name or "",
        "url": url or "",
        "text": text,
        "published_at": row.get("date_of_birth") or "",
        "score": 1.0 if full_name else 0.8,
        "profile": {
            "full_name": full_name,
            "phones": phones,
            "emails": emails,
            "telegram": telegram,
            "vk_url": row.get("vk_url") or "",
            "instagram_url": row.get("instagram_url") or "",
            "registration_address": row.get("registration_address") or "",
            "inn": row.get("inn") or "",
            "snils": row.get("snils") or "",
            "confidence": row.get("overall_confidence") or "",
            "completeness": row.get("completeness_score") or "",
        },
    }


async def _search_profiles_supabase(query: str, limit: int = 5,
                                    field: str = "") -> list[dict]:
    """Ищет профили в Supabase db_profiles через PostgREST."""
    sb = SupabaseStore()
    q = (query or "").strip()
    if not (sb.enabled and q):
        return []
    qs = _sanitize(q)
    digits = "".join(c for c in q if c.isdigit())

    params: dict = {"select": _PROFILE_SELECT, "limit": str(limit)}
    if field in ("phone", "тел", "телефон") and digits:
        # Номера хранятся в массиве phones, возможно с ведущим '+'.
        # cs (array-contains) требует ТОЧНОГО совпадения элемента; строковые
        # значения в PostgREST-массивах надо писать в кавычках (JSON-токен),
        # поэтому ищем и '7999...', и '+7999...' через or.
        p = f"{digits}"
        params["or"] = f'(phones.cs.["{p}"],phones.cs.["+{p}"])'
    elif field in ("email", "почта"):
        params["emails"] = f'cs.["{q.lower()}"]'
    elif field in ("name", "имя", "фио", "фамилия"):
        params["full_name"] = f"ilike.*{qs}*"
    elif field in ("inn", "иин"):
        params["inn"] = f"eq.{qs}"
    elif field == "snils":
        params["snils"] = f"eq.{qs}"
    elif field == "passport":
        params["or"] = f"(passport_series.ilike.*{qs}*,passport_number.ilike.*{qs}*)"
    elif field in ("auto", "авто", "госномер"):
        # госномер/vin в JSON vehicles — фильтруем в Python из расширенной выборки
        qplate = q.upper().replace(" ", "")
        params2 = dict(params)
        params2["select"] = _PROFILE_SELECT
        try:
            async with httpx.AsyncClient(timeout=30, headers=sb._headers) as client:
                r = await client.get(sb.url + "/db_profiles", params={
                    **params2, "limit": "200"})
                r.raise_for_status()
            out = []
            for row in r.json():
                vehicles = row.get("vehicles") if isinstance(row.get("vehicles"), list) else []
                for v in vehicles:
                    if isinstance(v, dict) and qplate and \
                       qplate in str(v.get("plate") or "").upper():
                        out.append(_profile_row_to_item_supabase(row))
                        break
                if len(out) >= limit:
                    break
            return out
        except (httpx.HTTPError, ValueError) as ex:
            logger.debug("supabase profile auto: %s", ex)
            return []
    else:
        # без тега — ищем по ФИО / имени / телефону / email
        ors = [f"full_name.ilike.*{qs}*"]
        if digits:
            ors.append(f'phones.cs.["{digits}"]')
            ors.append(f'phones.cs.["+{digits}"]')
        if "@" in q:
            ors.append(f'emails.cs.["{q.lower()}"]')
        params["or"] = f"({','.join(ors)})"

    try:
        async with httpx.AsyncClient(timeout=30, headers=sb._headers) as client:
            r = await client.get(sb.url + "/db_profiles", params=params)
            r.raise_for_status()
            rows = r.json()
            if not isinstance(rows, list):
                return []
            return [_profile_row_to_item_supabase(row) for row in rows]
    except (httpx.HTTPError, ValueError) as ex:
        logger.debug("supabase profile search: %s", ex)
        return []


async def search_profiles(query: str, limit: int = 5, field: str = "") -> list[dict]:
    """Профили людей из db_profiles по запросу (ФИО/телефон/email/ИНН и др.).

    field — тип идентификатора: name/phone/email/inn/passport/snils/auto.
    Приоритет — Supabase (db_profiles); иначе локальное зеркало на сервере.
    """
    q = (query or "").strip()
    if not q:
        return []
    parsed_field, parsed_val = parse_search_field(q)
    if parsed_field:
        field = parsed_field
        q = parsed_val
    if not q:
        q = (query or "").strip()
    sb = SupabaseStore()
    if sb.enabled:
        try:
            res = await _search_profiles_supabase(q, limit, field)
            if res:
                return res
            return []
        except (httpx.HTTPError, ValueError) as ex:
            logger.warning("supabase profile search fallback to local: %s", ex)
    return await asyncio.to_thread(_search_profiles_local, q, limit, field)


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


async def _search_related_supabase(identifiers: dict, hit_profile_ids: set,
                                   hit_record_ids: set) -> list[dict]:
    """Связанные записи/профили в Supabase по телефонам/email/ИНН/авто."""
    sb = SupabaseStore()
    if not sb.enabled:
        return []
    phones = identifiers.get("phones") or []
    emails = identifiers.get("emails") or []
    inns = identifiers.get("inns") or []
    plates = identifiers.get("plates") or []
    terms = [t for t in (list(phones) + list(emails) + list(inns) + list(plates)) if t][:20]
    if not terms:
        return []

    related: dict[str, dict] = {}

    async with httpx.AsyncClient(timeout=30, headers=sb._headers) as client:
        # 1) профили Supabase, делящие телефон/email/ИНН
        ors = []
        for p in phones:
            digits = "".join(c for c in p if c.isdigit())
            if digits:
                ors.append(f"phones.cs.[{digits}]")
        for e in emails:
            if e:
                ors.append(f"emails.cs.[{e.lower()}]")
        for inn in inns:
            if inn:
                ors.append(f"inn.eq.{inn}")
        if ors:
            try:
                r = await client.get(sb.url + "/db_profiles",
                                     params={"select": _PROFILE_SELECT,
                                             "or": f"({','.join(ors)})",
                                             "limit": "100"})
                if r.status_code < 400:
                    for row in r.json():
                        pid = str(row.get("id"))
                        if pid in hit_profile_ids or f"profile:{pid}" in related:
                            continue
                        related[f"profile:{pid}"] = _profile_row_to_item_supabase(row)
            except (httpx.HTTPError, ValueError) as ex:
                logger.debug("related profiles: %s", ex)

        # 2) записи db_records Supabase, содержащие те же контакты/ИНН/номер
        if terms:
            ors_rec = []
            for t in terms:
                ors_rec.append(f"text.ilike.*{_sanitize(t)}*")
                ors_rec.append(f"author.ilike.*{_sanitize(t)}*")
            try:
                r = await client.get(sb.url + "/db_records",
                                     params={"select": _SELECT,
                                             "or": f"({','.join(ors_rec)})",
                                             "limit": "100"})
                if r.status_code < 400:
                    for row in r.json():
                        ck = str(row.get("checksum") or row.get("id"))
                        if ck in hit_record_ids or f"db:{ck}" in related:
                            continue
                        related[f"db:{ck}"] = _to_item(row)
            except (httpx.HTTPError, ValueError) as ex:
                logger.debug("related records: %s", ex)

    return list(related.values())


async def search_related(records: list[dict], profiles: list[dict]) -> list[dict]:
    """Связанные записи/профили: перекрёстные данные по контактам и ИНН.

    Принимает найденные записи и профили, вытаскивает из них телефоны/email/
    ИНН/номера авто/адреса и ищет другие сущности, которые их разделяют.
    Приоритет — Supabase (после заливки папки источник истины там).
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

    sb = SupabaseStore()
    if sb.enabled:
        try:
            res = await _search_related_supabase(identifiers, hit_profile_ids,
                                                hit_record_ids)
            if res:
                return res
        except (httpx.HTTPError, ValueError) as ex:
            logger.warning("supabase related fallback to local: %s", ex)

    return await asyncio.to_thread(
        _search_related_local, identifiers, hit_profile_ids, hit_record_ids)