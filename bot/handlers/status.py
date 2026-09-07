"""Статус задач и реестр импортированных баз."""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from sqlalchemy import select, func, and_

from ..db.database import AsyncSessionLocal
from ..db.models import Job, DbImport, DbRecord, DbProfile

router = Router()


@router.message(Command("status"))
async def cmd_status(message: Message):
    """Статус активных задач."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Job)
            .filter_by(user_id=message.from_user.id)
            .order_by(Job.created_at.desc())
            .limit(5)
        )
        jobs = res.scalars().all()
    
    if not jobs:
        await message.answer("Задач нет.")
        return

    status_names = {
        "pending": "ожидание",
        "processing": "выполняется",
        "done": "выполнена",
        "error": "ошибка",
    }

    lines = ["<b>Журнал задач (5):</b>", ""]
    for j in jobs:
        status = status_names.get(j.status, j.status)
        date = j.created_at.strftime("%d.%m.%Y %H:%M")
        lines.append(f"- {date} — {j.job_type} ({status})")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("databases"))
async def cmd_databases(message: Message):
    """Реестр: по каким базам идёт пробив (импорты + счётчики)."""
    lines = ["🗄️ <b>Боевой реестр баз данных</b>", ""]
    totals = {"records": 0, "profiles": 0}

    async with AsyncSessionLocal() as session:
        records_cnt = select(DbImport.id, DbImport.file_name, DbImport.format,
                             DbImport.rows_total, DbImport.sections_found,
                             DbImport.sections_new).order_by(DbImport.id.desc())
        imports = list((await session.execute(records_cnt)).all())

        # Сколько записей реально хранится по каждому импорту (de-дуп).
        actual = dict((await session.execute(
            select(DbRecord.import_id, func.count(DbRecord.id))
            .group_by(DbRecord.import_id)
        )).all())

        profiles_total = await session.scalar(
            select(func.count(DbProfile.id)))

    imports_total = len(imports)
    db_total = sum(actual.values())
    if not imports:
        await message.answer("🗄️ Реестр пуст. Загрузите первую базу через /import.")
        return

    lines.append(f"• Импортов файлов: <b>{imports_total}</b>")
    lines.append(f"• Уникальных записей в зеркале: <b>{db_total:,}</b>"
                 .replace(",", " "))
    lines.append(f"• Профилей людей извлечено: <b>{profiles_total:,}</b>"
                 .replace(",", " "))
    lines.append("")

    for imp in imports:
        n = actual.get(imp.id, 0)
        marker = "•"
        name = imp.file_name or f"import #{imp.id}"
        fmt = (imp.format or "").upper()
        lines.append(f"{marker} <code>{name}</code> "
                     f"({fmt}, {n:,} зап.)".replace(",", " "))
        if imp.sections_found:
            lines.append(f"   разделов: {imp.sections_found}, "
                         f"новых: {imp.sections_new}")
    lines.append("")
    lines.append("Сканирование идёт по всем импортам; результаты видны в "
                 "/find и `/profile`.")

    await message.answer("\n".join(lines), parse_mode="HTML")
