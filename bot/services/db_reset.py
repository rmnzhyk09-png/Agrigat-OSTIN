"""Полная очистка импортированных БД для нового залива данных.

Удаляет ВСЁ, что хранит результаты импорта:
- локальное зеркало: db_imports, db_sections, db_records, db_profiles
- Supabase (PostgREST): те же таблицы удалённо
- Supabase Storage: все объекты в бакете (SUPABASE_BUCKET)

Таблицы не дропаются — просто очищаются, поэтому новые заливы работают
как с чистой базой (защита от дублей по checksum начинает с нуля).
Вызывать ТОЛЬКО из админ-команды с подтверждением.
"""
import logging
from typing import Optional

import httpx
from sqlalchemy import delete

from ..config import settings
from ..db.database import SyncSessionLocal
from ..db.models import DbImport, DbProfile, DbRecord, DbSection
from ..dbimport.store import SupabaseStore

logger = logging.getLogger(__name__)

_LOCAL_TABLES = [
    (DbProfile, "profiles"),
    (DbRecord, "records"),
    (DbSection, "sections"),
    (DbImport, "imports"),
]

_REMOTE_TABLES = {
    "profiles": "db_profiles",
    "records": "db_records",
    "sections": "db_sections",
    "imports": "db_imports",
}

# Порядок: сначала дочерние таблицы, потом родительские (без FK можно в любом,
# но так безопаснее для будущих связей).
_LOCAL_ORDER = ["records", "sections", "imports", "profiles"]


def _wipe_local(session_factory=SyncSessionLocal) -> dict:
    """Очищает локальное зеркало. Возвращает число удалённых строк по таблицам.

    session_factory в параметре — для тестов с временной БД.
    """
    counts = {key: 0 for _, key in _LOCAL_TABLES}
    try:
        with session_factory() as session:
            for key in _LOCAL_ORDER:
                model = next(m for m, k in _LOCAL_TABLES if k == key)
                res = session.execute(delete(model))
                counts[key] = res.rowcount or 0
            session.commit()
    except Exception:
        logger.exception("dbreset: локальное зеркало не очищено")
        counts["_error"] = "ошибка локального зеркала (см. лог)"
    return counts


async def _wipe_remote(client: httpx.AsyncClient, sb: SupabaseStore) -> dict:
    """Очищает таблицы db_* в Supabase. Возвращает число удалённых строк."""
    counts = {key: 0 for key in _REMOTE_TABLES}
    for key, table in _REMOTE_TABLES.items():
        try:
            # Сколько было строк
            r = await client.get(
                f"{sb.url}/{table}",
                params={"select": "id", "limit": "1"},
                headers={**sb._headers, "Prefer": "count=exact"},
            )
            total = 0
            if r.status_code < 400:
                cr = r.headers.get("Content-Range") or r.headers.get("content-range") or ""
                if cr.count("/") == 1:
                    try:
                        total = int(cr.rsplit("/", 1)[1])
                    except ValueError:
                        total = 0
            # Снять все строки целиком. PostgREST не разрешает DELETE без условия, а
# фильтр требует значение ТИПА ключа (uuid у db_profiles/db_records,
# bigint у db_sections/db_imports). Поэтому пробуем по очереди подходящие
# sentinel-значения; первый успешный HTTP в диапазоне 2xx снимает все строки.
            sentinels = ["00000000-0000-0000-0000-000000000000", "0", "-1", "nope"]
            deleted = False
            for sentinel in sentinels:
                d = await client.delete(
                    f"{sb.url}/{table}",
                    params={"id": f"neq.{sentinel}"},
                    headers={**sb._headers, "Prefer": "return=minimal"},
                )
                if d.status_code < 400:
                    deleted = True
                    break
            if not deleted:
                logger.warning("dbreset: удалить все строки %s: последняя попытка %s (%s)",
                               table, d.status_code, d.text[:200])
                counts[key] = 0
            else:
                counts[key] = total
        except httpx.HTTPError as ex:
            logger.warning("dbreset: %s недоступен: %s", table, ex)
            counts[key] = 0
            counts["_error"] = "часть таблиц в Supabase не очищена (см. лог)"
    return counts


async def _wipe_storage(client: httpx.AsyncClient, sb: SupabaseStore,
                        bucket: str) -> tuple[int, Optional[str]]:
    """Удаляет все объекты в бакете storage. (best effort)."""
    if not bucket:
        return 0, None
    removed = 0
    offset = 0
    try:
        while True:
            r = await client.post(
                f"{sb.storage_base}/object/list/{bucket}",
                json={"prefix": "", "limit": 1000, "offset": offset},
                headers=sb._storage_headers(),
            )
            if r.status_code >= 400:
                return removed, f"список объектов: {r.status_code} {r.text[:150]}"
            objects = r.json()
            if not isinstance(objects, list) or not objects:
                break
            names = [o.get("name") for o in objects if o.get("name")]
            if names:
                rm = await client.post(
                    f"{sb.storage_base}/object/{bucket}/remove",
                    json=names,
                    headers=sb._storage_headers(),
                )
                if rm.status_code < 400:
                    removed += len(names)
                else:
                    logger.warning("dbreset: remove storage: %s %s",
                                   rm.status_code, rm.text[:150])
            if len(objects) < 1000:
                break
            offset += len(objects)
    except httpx.HTTPError as ex:
        return removed, f"storage недоступен: {ex}"
    except Exception as ex:
        return removed, f"storage: {ex}"
    return removed, None


async def wipe_database() -> dict:
    """Полная очистка импорта: локально + Supabase + Storage."""
    report: dict = {"local": _wipe_local()}

    sb = SupabaseStore()
    if not sb.enabled:
        report["remote"] = None
        report["notes"] = ["Supabase не настроен — удалённые данные остались только в Supabase"]
        return report

    remote: dict = {key: 0 for key in _REMOTE_TABLES}
    notes: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=60, headers=sb._headers) as client:
            remote = await _wipe_remote(client, sb)
            storage_removed, err = await _wipe_storage(client, sb, settings.supabase_bucket)
            report["storage_removed"] = storage_removed
            if err:
                notes.append(err)
    except httpx.HTTPError as ex:
        notes.append(f"Supabase недоступен: {ex}")

    report["remote"] = remote
    if "storage_removed" not in report:
        report["storage_removed"] = 0
    if remote.get("_error"):
        notes.append(remote.pop("_error"))
    report["notes"] = notes
    return report


def format_report(report: dict) -> str:
    """Человекочитаемый отчёт об очистке для сообщения в Telegram."""
    local = report.get("local") or {}
    remote = report.get("remote")
    notes = report.get("notes") or []

    lines = ["<b>✅ База очищена</b>\n"]
    lines.append("Локальное зеркало:")
    for key, title in (("profiles", "профили"), ("records", "записи"),
                       ("sections", "разделы"), ("imports", "импорты")):
        n = local.get(key, 0)
        lines.append(f"• {title}: <b>{n}</b>")
    if local.get("_error"):
        lines.append(f"⚠️ {local['_error']}")

    if remote is None:
        lines.append("\nSupabase: не настроен (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY)")
    else:
        lines.append("\nSupabase:")
        for key, title in (("profiles", "профили"), ("records", "записи"),
                           ("sections", "разделы"), ("imports", "импорты")):
            lines.append(f"• {title}: <b>{remote.get(key, 0)}</b>")
        lines.append(f"• файлы в Storage: <b>{report.get('storage_removed', 0)}</b>")

    if notes:
        lines.append("\nЗамечания:")
        for n in notes:
            lines.append(f"• {n}")

    lines.append("\nТеперь можно заливать данные заново — защита от дублей "
                 "начнёт с нуля.")
    return "\n".join(lines)