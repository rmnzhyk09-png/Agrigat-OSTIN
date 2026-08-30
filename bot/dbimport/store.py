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
from ..db.models import Base, DbImport, DbRecord, DbSection
from .analyzer import analyze_records, count_sections
from .parser import parse_file

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

    async def insert_records(self, client: httpx.AsyncClient, records: list[dict]) -> int:
        """Вставка порциями; дубли отсекаются по checksum (on_conflict)."""
        inserted = 0
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
                       "Prefer": "resolution=ignore-duplicates,return=minimal"}
            r = await client.post(self.url + "/db_records",
                                  params={"on_conflict": "checksum"},
                                  json=rows, headers=headers)
            r.raise_for_status()
            inserted += len(rows)
        return inserted


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

def _mirror_local(meta: dict, records: list[dict], sections_found: int,
                  sections_new: int) -> int:
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

        known = set(session.execute(select(DbSection.name)).scalars().all())
        for rec in records:
            name = rec.get("section") or "Прочее"
            if name not in known:
                session.add(DbSection(name=name))
                known.add(name)
        session.add_all([
            DbRecord(import_id=import_id, section=rec.get("section") or "Прочее",
                     source=(rec.get("source") or "")[:255],
                     author=(rec.get("author") or "")[:255],
                     text=rec.get("text", "") or "",
                     url=(rec.get("url") or "")[:500],
                     date=(rec.get("date") or "")[:100],
                     checksum=_checksum(rec),
                     raw=json.dumps(rec, ensure_ascii=False))
            for rec in records
        ])
        session.commit()
        return import_id


# ---------- точка входа ----------

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

    sb = SupabaseStore()
    remote_note = ""
    new_sections: list[str] = []
    try:
        if sb.enabled:
            async with httpx.AsyncClient(timeout=60,
                                         headers=sb._headers) as client:
                ok, msg = await sb.ensure_schema(client)
                if not ok:
                    remote_note = msg
                else:
                    known = await sb.list_sections(client)
                    new_sections = [s for s in sections if s not in known]
                    for name in new_sections:
                        await sb.create_section(client, name)
                    payload = {"file_name": meta.get("filename", ""),
                               "format": meta.get("format", ""),
                               "rows_total": len(records),
                               "sections_found": len(sections),
                               "sections_new": len(new_sections)}
                    await sb.insert_import(client, payload)
                    await sb.insert_records(client, records)
                    remote_note = (f"Supabase: {len(records)} записей, "
                                   f"{len(new_sections)} новых разделов")
        else:
            remote_note = _config_hint()
    except (httpx.HTTPError, SupabaseError) as ex:
        logger.warning("supabase write error: %s", ex)
        remote_note = f"Ошибка записи в Supabase: {ex}"

    # Зеркало в собственную БД всегда (для резюме и офлайн-реестра)
    try:
        await asyncio.to_thread(_mirror_local, meta, records,
                                len(sections), len(new_sections))
    except Exception:
        logger.exception("local mirror error")

    return {
        **meta,
        "total": len(records),
        "sections": sections,
        "new_sections": new_sections,
        "remote_note": remote_note,
        "supabase_configured": sb.enabled,
    }