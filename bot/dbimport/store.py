"""Сохранение проанализированной БД: локальное зеркало + Supabase.

Supabase подключается напрямую через PostgREST (без доп. зависимостей).
Таблицы создаются автоматически, если задан SUPABASE_ACCESS_TOKEN
(Management API) или вручную — из файла supabase/schema.sql.
"""
import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy import select

from ..config import settings
from ..db.database import SyncSessionLocal, sync_engine
from ..db.models import Base, DbImport, DbProfile, DbRecord, DbSection
from .analyzer import analyze_records, count_sections
from .parser import parse_file
from .profiles import build_profile_from_record, completeness_score, confidence_level, merge_profiles

logger = logging.getLogger(__name__)

CHUNK = 200  # записей на один POST в Supabase

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "supabase" / "schema.sql"


def _config_hint() -> str:
    """Что именно не хватает для Supabase и как это починить."""
    if not settings.supabase_url:
        return ("Supabase не настроен: не задан <code>SUPABASE_URL</code>.\n"
                "На Render: Dashboard → Environment → "
                "<code>SUPABASE_URL=https://&lt;реф&gt;.supabase.co</code> → Deploy.")
    return ("Supabase не настроен: не задан <code>SUPABASE_SERVICE_ROLE_KEY</code>.\n"
            "Ключ: Supabase → Project Settings → API → <code>service_role</code> (секрет).\n"
            "Добавь на Render → Environment → Deploy. Затем выполни "
            "<code>supabase/schema.sql</code> в SQL Editor Supabase.")


def _checksum(rec: dict) -> str:
    raw = f"{rec.get('source')}|{rec.get('author')}|{rec.get('text')}|" \
          f"{rec.get('url')}|{rec.get('date')}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


class SupabaseError(Exception):
    pass


class SupabaseStore:
    """PostgREST-клиент для таблиц db_imports / db_sections / db_records."""

    def __init__(self):
        self.enabled = bool(settings.supabase_url and settings.supabase_service_key)
        self.url = settings.supabase_url.rstrip("/") + "/rest/v1" if self.enabled else ""
        self.key = settings.supabase_service_key
        self.pat = settings.supabase_pat
        self.ref = settings.supabase_project_ref
        self._headers = ({"apikey": self.key, "Authorization": f"Bearer {self.key}",
                          "Content-Type": "application/json"} if self.enabled else {})

    # ---------- таблицы ----------

    async def ensure_schema(self, client: httpx.AsyncClient) -> tuple[bool, str]:
        """Проверяет наличие db_sections; при отсутствии создаёт таблицы."""
        if not self.enabled:
            return False, "Supabase не настроен (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY)"
        try:
            r = await client.get(f"{self.url}/db_sections", params={"select": "id", "limit": "1"})
            if r.status_code < 400:
                return True, ""
        except httpx.HTTPError as ex:
            return False, f"Supabase недоступен: {ex}"

        # Таблиц нет — пробуем создать через Management API (нужен PAT + ref)
        if self.pat and self.ref:
            ok, msg = await self._create_tables()
            if ok:
                return True, ""
            return False, msg
        return False, ("Таблицы db_imports/db_sections/db_records не найдены. "
                       "Запусти supabase/schema.sql в SQL Editor Supabase.")

    async def _create_tables(self) -> tuple[bool, str]:
        sql = self._read_schema()
        if not sql:
            return False, "Не найден supabase/schema.sql"
        url = f"https://api.supabase.com/v1/projects/{self.ref}/database/query"
        auth = {"Authorization": f"Bearer {self.pat}"}
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                r = await client.post(url, json={"query": sql}, headers=auth)
            except httpx.HTTPError as ex:
                return False, f"Management API недоступен: {ex}"
        if r.status_code >= 400:
            return False, f"Ошибка создания таблиц ({r.status_code}): {r.text[:200]}"
        logger.info("supabase: таблицы созданы автоматически")
        return True, ""

    @staticmethod
    def _read_schema() -> str:
        try:
            return SCHEMA_FILE.read_text(encoding="utf-8")
        except OSError:
            return ""

    # ---------- данные ----------

    async def list_sections(self, client: httpx.AsyncClient) -> set[str]:
        r = await client.get(self.url + "/db_sections", params={"select": "name"})
        r.raise_for_status()
        return {row.get("name") for row in r.json()}

    async def create_section(self, client: httpx.AsyncClient, name: str) -> None:
        r = await client.post(self.url + "/db_sections",
                              json=[{"name": name}],
                              params={"on_conflict": "name"},
                              headers={**self._headers, "Prefer": "resolution=ignore-duplicates,return=minimal"})
        r.raise_for_status()

    async def insert_import(self, client: httpx.AsyncClient, payload: dict) -> int:
        r = await client.post(self.url + "/db_imports", json=[payload],
                              headers={**self._headers, "Prefer": "return=representation"})
        r.raise_for_status()
        data = r.json()
        return (data[0].get("id") if isinstance(data, list) and data else 0)

    async def insert_records(self, client: httpx.AsyncClient,
                             records: list[dict]) -> tuple[int, int]:
        """Вставка порциями; дубли отсекаются по checksum (on_conflict).

        Возвращает (вставлено_новых, пропущено_дублей).
        """
        inserted = skipped = 0
        for start in range(0, len(records), CHUNK):
            rows = []
            for rec in records[start:start + CHUNK]:
                rows.append({
                    "import_id": rec.get("_import_id", 0),
                    "section": rec.get("section") or "Прочее",
                    "source": rec.get("source", "") or "",
                    "author": rec.get("author", "") or "",
                    "text": rec.get("text", "") or "",
                    "url": rec.get("url", "") or "",
                    "date": rec.get("date", "") or "",
                    "checksum": _checksum(rec),
                    "raw": json.dumps(rec, ensure_ascii=False),
                })
            headers = {**self._headers,
                       "Prefer": "resolution=ignore-duplicates,return=representation"}
            r = await client.post(self.url + "/db_records",
                                  params={"on_conflict": "checksum"},
                                  json=rows, headers=headers)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list) and data:
                inserted += len(data)
            skipped += len(rows) - (len(data) if isinstance(data, list) else 0)
        return inserted, skipped

    async def insert_profiles(self, client: httpx.AsyncClient,
                              profiles: list[dict]) -> tuple[int, int]:
        """Записывает профили людей в Supabase db_profiles.

        Дубли пропускаются по full_name (имя-ключ). Возвращает
        (вставлено_новых, пропущено_дублей).
        """
        named = [p for p in profiles if (p.get("full_name") or "").strip()]
        if not named:
            return 0, len(profiles)
        # Проверяем, что таблица db_profiles существует (иначе нет смысла
        # долбить по каждой фамилии — вернём 404 за каждый POST).
        try:
            probe = await client.get(self.url + "/db_profiles",
                                     params={"select": "full_name", "limit": "1"})
            if probe.status_code == 404:  # таблицы нет — профили пропускаем
                return 0, len(named)
        except httpx.HTTPError:
            return 0, len(named)
        # имеющиеся full_name в Supabase
        existing: set[str] = set()
        try:
            r = await client.get(self.url + "/db_profiles",
                                 params={"select": "full_name"},
                                 headers={**self._headers, "Range-Unit": "items",
                                          "Range": "0-9999"})
            if r.status_code < 400:
                for row in r.json():
                    if row.get("full_name"):
                        existing.add(str(row["full_name"]).strip().lower())
        except httpx.HTTPError:
            pass

        inserted = skipped = 0
        batch: list[dict] = []
        BATCH = 200
        async def flush_batch():
            nonlocal batch, inserted
            if not batch:
                return
            r = await client.post(self.url + "/db_profiles", json=batch,
                                  headers={**self._headers, "Prefer": "return=minimal"})
            if r.status_code < 400:
                inserted += len(batch)
            else:
                logger.warning("supabase insert profiles batch: %s %s",
                               r.status_code, r.text[:200])
            batch = []

        for p in named:
            name = str(p.get("full_name") or "").strip()
            if not name:
                continue
            if name.lower() in existing:
                skipped += 1
                continue
            existing.add(name.lower())
            payload = {
                "full_name": name,
                "surname": p.get("surname") or "",
                "first_name": p.get("first_name") or "",
                "patronymic": p.get("patronymic") or "",
                "gender": p.get("gender") or "",
                "date_of_birth": p.get("date_of_birth") or "",
                "citizenship": p.get("citizenship") or "",
                "place_of_birth": p.get("place_of_birth") or "",
                "passport_series": p.get("passport_series") or "",
                "passport_number": p.get("passport_number") or "",
                "passport_issued_by": p.get("passport_issued_by") or "",
                "inn": p.get("inn") or "",
                "snils": p.get("snils") or "",
                "driver_license": p.get("driver_license") or "",
                "registration_address": p.get("registration_address") or "",
                "actual_address": p.get("actual_address") or "",
                "phones": p.get("phones") or [],
                "emails": p.get("emails") or [],
                "telegram": p.get("telegram") or "",
                "social_handles": p.get("social_handles") or [],
                "relatives": p.get("relatives") or [],
                "business_partners": p.get("business_partners") or [],
                "vk_url": p.get("vk_url") or "",
                "instagram_url": p.get("instagram_url") or "",
                "facebook_url": p.get("facebook_url") or "",
                "real_estate": p.get("real_estate") or [],
                "vehicles": p.get("vehicles") or [],
                "court_cases": p.get("court_cases") or [],
                "court_cases_count": len(p.get("court_cases") or []),
                "enforcement_proceedings": p.get("enforcement_proceedings") or [],
                "criminal_record": bool(p.get("criminal_record")),
                "tax_debt_total": p.get("tax_debt_total") or None,
                "bankruptcy_status": p.get("bankruptcy_status") or "",
                "account_arrests": p.get("account_arrests") or [],
                "current_employer": p.get("current_employer") or "",
                "employer_inn": p.get("employer_inn") or "",
                "position": p.get("position") or "",
                "businesses": p.get("businesses") or [],
                "exit_ban": bool(p.get("exit_ban")),
                "disqualified": bool(p.get("disqualified")),
                "efrsb_status": p.get("efrsb_status") or "",
                "source_files": p.get("source_files") or [],
                "import_ids": p.get("import_ids") or [],
                "overall_confidence": p.get("overall_confidence") or "",
            }
            batch.append(payload)
            if len(batch) >= BATCH:
                await flush_batch()

        await flush_batch()
        return inserted, skipped


    # ---------- Supabase Storage (хранение загруженных файлов) ----------

    @property
    def storage_base(self) -> str:
        return settings.supabase_url.rstrip("/") + "/storage/v1"

    @staticmethod
    def _storage_headers(*, upload: bool = False):
        h = {
            "apikey": settings.supabase_service_key,
            "Authorization": f"Bearer {settings.supabase_service_key}",
        }
        if upload:
            h["Content-Type"] = "application/octet-stream"
        return h

    async def _ensure_bucket(self) -> bool:
        """Создаёт бакет для файлов (через Management API), если задан PAT+ref.

        Если ключей менеджмента нет — считаем, что бакет уже создан вручную.
        """
        if not (self.pat and self.ref):
            return True
        url = f"https://api.supabase.com/v1/projects/{self.ref}/storage/buckets"
        auth = {"Authorization": f"Bearer {self.pat}"}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(url, headers=auth)
                names = {b.get("name") for b in r.json()} if r.status_code < 400 else set()
                if settings.supabase_bucket not in names:
                    r2 = await client.post(url, headers=auth,
                                           json={"id": settings.supabase_bucket,
                                                 "name": settings.supabase_bucket,
                                                 "public": settings.supabase_bucket_public,
                                                 "file_size_limit": None,
                                                 "allowed_mime_types": None})
                    if r2.status_code >= 400:
                        logger.warning("supabase: создать бакет %s: %s",
                                       settings.supabase_bucket, r2.text[:200])
                        return False
        except httpx.HTTPError as ex:
            logger.warning("supabase storage (bucket): %s", ex)
            return False
        return True

    async def upload_file(self, path: Path, remote_name: str) -> str:
        """Загружает файл в Supabase Storage. Возвращает URL (публичный).

        Если бакет приватный — вернёт путь внутри бакета (object path).
        """
        try:
            return await self._upload_file(path, remote_name)
        except (httpx.HTTPError, OSError) as ex:
            logger.warning("supabase upload %s: %s", remote_name, ex)
            return ""

    async def _upload_file(self, path: Path, remote_name: str) -> str:
        await self._ensure_bucket()
        data = path.read_bytes()
        obj_url = f"{self.storage_base}/object/{settings.supabase_bucket}/{remote_name}"
        async with httpx.AsyncClient(timeout=300,
                                     headers=self._storage_headers(upload=True)) as client:
            r = await client.post(obj_url, params={"upsert": "true"}, content=data)
            if r.status_code >= 400:
                logger.warning("supabase upload status=%s %s",
                               r.status_code, r.text[:200])
                return ""
        if settings.supabase_bucket_public:
            return (f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/"
                    f"{settings.supabase_bucket}/{remote_name}")
        return f"{settings.supabase_bucket}/{remote_name}"


# ---------- проверка при старте ----------

async def check_supabase() -> tuple[bool, str]:
    """Проверка подключения и наличия таблиц. Вызывается при старте бота."""
    sb = SupabaseStore()
    if not sb.enabled:
        return False, _config_hint()
    try:
        async with httpx.AsyncClient(timeout=30, headers=sb._headers) as client:
            return await sb.ensure_schema(client)
    except httpx.HTTPError as ex:
        return False, f"Supabase недоступен: {ex}"


# ---------- локальное зеркало ----------

def _dedupe(records: list[dict], known: set[str]) -> tuple[list[dict], int]:
    """Оставляет только записи с новым checksum.

    Возвращает (свежие_записи, сколько_пропущено_дублей).
    """
    seen = set(known)
    fresh: list[dict] = []
    skipped = 0
    for rec in records:
        c = _checksum(rec)
        if c in seen:
            skipped += 1
            continue
        seen.add(c)
        fresh.append(rec)
    return fresh, skipped


def _known_checksums() -> set[str]:
    """Все checksum уже хранящиеся локально (для обычного отбора дублей)."""
    try:
        Base.metadata.create_all(sync_engine)
    except Exception:
        return set()
    try:
        with SyncSessionLocal() as session:
            return set(session.execute(select(DbRecord.checksum)).scalars().all())
    except Exception:
        return set()


def _mirror_local(meta: dict, records: list[dict], sections_found: int,
                  sections_new: int) -> tuple[int, int, int]:
    """Записывает в локальное зеркало только НОВЫЕ записи (дубли пропускаются).

    Возвращает (import_id, добавлено, пропущено_дублей).
    """
    try:
        Base.metadata.create_all(sync_engine)
    except Exception:
        pass
    with SyncSessionLocal() as session:
        imp = DbImport(file_name=meta.get("filename", ""),
                       format=meta.get("format", ""),
                       rows_total=meta.get("rows_kept", len(records)),
                       sections_found=sections_found, sections_new=sections_new)
        session.add(imp)
        session.flush()
        import_id = imp.id

        known = set(session.execute(select(DbRecord.checksum)).scalars().all())
        fresh, skipped = _dedupe(records, known)

        seen_sections = set(session.execute(select(DbSection.name)).scalars().all())
        for rec in fresh:
            name = rec.get("section") or "Прочее"
            if name not in seen_sections:
                session.add(DbSection(name=name))
                seen_sections.add(name)
        session.add_all([
            DbRecord(import_id=import_id, section=rec.get("section") or "Прочее",
                     source=(rec.get("source") or "")[:255],
                     author=(rec.get("author") or "")[:255],
                     text=rec.get("text", "") or "",
                     url=(rec.get("url") or "")[:500],
                     date=(rec.get("date") or "")[:100],
                     checksum=_checksum(rec),
                     raw=json.dumps(rec, ensure_ascii=False))
            for rec in fresh
        ])
        session.commit()
        return import_id, len(fresh), skipped


# ---------- профили ----------

def _build_merged_profiles(records: list[dict], filename: str = "") -> list[dict]:
    """Извлекает профили из записей и объединяет дубли по ФИО.

    Возвращает список merged-профилей (dict). Без сохранения в БД.
    """
    if not records:
        return []
    profiles: list[dict] = []
    for rec in records:
        p = build_profile_from_record(rec, filename)
        if p.get("full_name") or p.get("phones"):
            profiles.append(p)
    if not profiles:
        return []

    by_name: dict[str, list[dict]] = {}
    unnamed: list[dict] = []
    for p in profiles:
        name = (p.get("full_name") or "").strip().lower()
        if name:
            by_name.setdefault(name, []).append(p)
        else:
            unnamed.append(p)

    merged: list[dict] = []
    for name, group in by_name.items():
        merged.append(merge_profiles(group))
    merged.extend(unnamed)
    return merged


def _extract_and_save_profiles(records: list[dict], import_id: int,
                                filename: str = "") -> int:
    """Извлекает профили из записей и сохраняет/обновляет в db_profiles.

    Возвращает количество созданных/обновлённых профилей.
    """
    merged = _build_merged_profiles(records, filename)
    if not merged:
        return 0

    saved = 0
    try:
        with SyncSessionLocal() as session:
            Base.metadata.create_all(sync_engine)
            for prof in merged:
                name = (prof.get("full_name") or "").strip()
                phones = prof.get("phones") or []
                existing = None

                # Ищем существующий профиль по ФИО или телефону
                if name:
                    existing = session.query(DbProfile).filter(
                        DbProfile.full_name == name
                    ).first()
                if not existing and phones:
                    for p in phones:
                        existing = session.query(DbProfile).filter(
                            DbProfile.phones.contains(p)
                        ).first()
                        if existing:
                            break

                score = completeness_score(prof)
                conf = confidence_level(score)

                if existing:
                    # Обновляем существующий профиль
                    for k, v in prof.items():
                        if k in ("source_files", "import_ids"):
                            old = getattr(existing, k) or []
                            if isinstance(old, list) and isinstance(v, list):
                                setattr(existing, k, old + v)
                            elif isinstance(v, list):
                                setattr(existing, k, v)
                        elif k == "phones" or k == "emails":
                            old = getattr(existing, k) or []
                            if isinstance(old, list) and isinstance(v, list):
                                setattr(existing, k, list(set(old + v)))
                            elif isinstance(v, list):
                                setattr(existing, k, v)
                        elif not getattr(existing, k):
                            setattr(existing, k, v)
                    existing.completeness_score = str(score)
                    existing.overall_confidence = conf
                    existing.raw_profile = prof
                    existing.updated_at = __import__("datetime").datetime.utcnow()
                else:
                    # Создаём новый профиль
                    prof["completeness_score"] = str(score)
                    prof["overall_confidence"] = conf
                    prof["import_ids"] = [import_id]
                    # снимок без self-ссылки (иначе JSON-сериализация падает)
                    prof["raw_profile"] = {k: v for k, v in prof.items()}
                    if filename:
                        prof["source_files"] = [filename]

                    row = DbProfile(
                        full_name=prof.get("full_name"),
                        surname=prof.get("surname"),
                        first_name=prof.get("first_name"),
                        patronymic=prof.get("patronymic"),
                        maiden_name=prof.get("maiden_name"),
                        gender=prof.get("gender"),
                        date_of_birth=prof.get("date_of_birth"),
                        age=prof.get("age"),
                        citizenship=prof.get("citizenship"),
                        place_of_birth=prof.get("place_of_birth"),
                        passport_series=prof.get("passport_series"),
                        passport_number=prof.get("passport_number"),
                        passport_issued_by=prof.get("passport_issued_by"),
                        passport_issue_date=prof.get("passport_issue_date"),
                        inn=prof.get("inn"),
                        snils=prof.get("snils"),
                        driver_license=prof.get("driver_license"),
                        military_id=prof.get("military_id"),
                        registration_address=prof.get("registration_address"),
                        registration_postal_code=prof.get("registration_postal_code"),
                        actual_address=prof.get("actual_address"),
                        phones=prof.get("phones"),
                        emails=prof.get("emails"),
                        telegram=prof.get("telegram"),
                        social_handles=prof.get("social_handles"),
                        family_status=prof.get("family_status"),
                        relatives=prof.get("relatives"),
                        business_partners=prof.get("business_partners"),
                        vk_url=prof.get("vk_url"),
                        instagram_url=prof.get("instagram_url"),
                        facebook_url=prof.get("facebook_url"),
                        real_estate=prof.get("real_estate"),
                        vehicles=prof.get("vehicles"),
                        driver_license_status=prof.get("driver_license_status"),
                        court_cases=prof.get("court_cases"),
                        court_cases_count=len(prof.get("court_cases") or []),
                        court_debt_total=prof.get("court_debt_total"),
                        enforcement_proceedings=prof.get("enforcement_proceedings"),
                        enforcement_debt_total=prof.get("enforcement_debt_total"),
                        criminal_record=prof.get("criminal_record", False),
                        tax_debt_total=prof.get("tax_debt_total"),
                        bankruptcy_status=prof.get("bankruptcy_status"),
                        account_arrests=prof.get("account_arrests"),
                        current_employer=prof.get("current_employer"),
                        employer_inn=prof.get("employer_inn"),
                        position=prof.get("position"),
                        businesses=prof.get("businesses"),
                        exit_ban=prof.get("exit_ban", False),
                        disqualified=prof.get("disqualified", False),
                        efrsb_status=prof.get("efrsb_status"),
                        source_files=prof.get("source_files"),
                        import_ids=prof.get("import_ids"),
                        overall_confidence=conf,
                        completeness_score=str(score),
                        raw_profile=prof,
                    )
                    session.add(row)
                saved += 1
            session.commit()
    except Exception:
        logger.exception("profile save error")
    return saved


# ---------- точка входа ----------

async def _push_profiles_supabase(records: list[dict], filename: str = "",
                                  sb: "SupabaseStore" = None) -> int:
    """Собирает merged-профили из записей и пишет их в Supabase db_profiles.

    Возвращает число вставленных профилей. Лучший-effort: при ошибке не роняет импорт.
    """
    if not records:
        return 0
    merged = await asyncio.to_thread(_build_merged_profiles, records, filename)
    if not merged:
        return 0
    if sb is None:
        sb = SupabaseStore()
    if not sb.enabled:
        return 0
    try:
        async with httpx.AsyncClient(timeout=60, headers=sb._headers) as client:
            ins, _dup = await sb.insert_profiles(client, merged)
            return ins
    except (httpx.HTTPError, SupabaseError) as ex:
        logger.warning("supabase profile push error: %s", ex)
        return 0


async def import_database_file(path, filename: str = "") -> dict:
    """Полный прогон: парсинг → классификация → сохранение. Возвращает отчёт."""
    try:
        meta, records = await asyncio.to_thread(parse_file, path, filename)
    except Exception as ex:
        logger.exception("parse error")
        return {"error": f"Не удалось разобрать файл: {ex}"}

    if not records:
        return {"error": "В файле не найдено записей с текстом.",
                "meta": meta}

    await asyncio.to_thread(analyze_records, records)
    sections = count_sections(records)

    # Сухая защита от дублей: дополняем базу только тем, чего в ней ещё нет.
    known = await asyncio.to_thread(_known_checksums)
    fresh, duplicates = _dedupe(records, known)

    sb = SupabaseStore()
    remote_note = ""
    new_sections: list[str] = []
    try:
        if sb.enabled and fresh:
            async with httpx.AsyncClient(timeout=60,
                                         headers=sb._headers) as client:
                ok, msg = await sb.ensure_schema(client)
                if not ok:
                    remote_note = msg
                else:
                    known_remote = await sb.list_sections(client)
                    new_sections = [s for s in sections if s not in known_remote]
                    for name in new_sections:
                        await sb.create_section(client, name)
                    payload = {"file_name": meta.get("filename", ""),
                               "format": meta.get("format", ""),
                               "rows_total": len(records),
                               "sections_found": len(sections),
                               "sections_new": len(new_sections)}
                    await sb.insert_import(client, payload)
                    ins, dup = await sb.insert_records(client, fresh)
                    remote_note = (f"Supabase: +{ins} новых, "
                                   f"{dup} дублей, "
                                   f"{len(new_sections)} новых разделов")
        else:
            remote_note = _config_hint()
    except (httpx.HTTPError, SupabaseError) as ex:
        logger.warning("supabase write error: %s", ex)
        remote_note = f"Ошибка записи в Supabase: {ex}"

    # Зеркало в собственную БД всегда (для резюме и офлайн-реестра).
    # В него тоже попадают только новые записи; дубли пропускаются.
    profiles_saved = 0
    local_import_id = 0
    try:
        if fresh:
            local_import_id, _added, _skipped = await asyncio.to_thread(
                _mirror_local, meta, fresh, len(sections), len(new_sections))
            # Извлекаем профили из записей (локальное зеркало)
            profiles_saved = await asyncio.to_thread(
                _extract_and_save_profiles, fresh, local_import_id, filename,
            )
    except Exception:
        logger.exception("local mirror error")

    # Профили людей — в Supabase db_profiles (для поиска профилей через бота).
    # Пишет только новые (дубли по full_name пропускаются).
    profiles_remote = 0
    try:
        if fresh and sb.enabled:
            profiles_remote = await _push_profiles_supabase(fresh, filename, sb)
    except Exception:
        logger.exception("supabase profile push error")

    return {
        **meta,
        "total": len(records),
        "added": len(fresh),
        "duplicates": duplicates,
        "sections": sections,
        "new_sections": new_sections,
        "remote_note": remote_note,
        "supabase_configured": sb.enabled,
        "profiles_created": profiles_saved,
        "profiles_remote": profiles_remote,
    }