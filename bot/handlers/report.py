"""Последний отчёт."""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from sqlalchemy import select

from ..db.database import AsyncSessionLocal
from ..db.models import Report

router = Router()


@router.message(Command("report"))
async def cmd_report(message: Message):
    """Последний отчёт."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Report)
            .filter_by(user_id=message.from_user.id)
            .order_by(Report.created_at.desc())
            .limit(1)
        )
        report = res.scalar_one_or_none()
    
    if not report:
        await message.answer("Отчётов нет. Запустите /find или /monitor.")
        return

    data = report.data or {}
    summary = (data.get("summary") or "").replace("<b>", "").replace("</b>", "")
    stats = data.get("stats") or {}
    date = report.created_at.strftime("%d.%m.%Y %H:%M")
    text = (
        f"<b>Отчёт от {date}</b>\n"
        f"Упоминаний: {stats.get('total', '—')}\n\n"
        f"{summary[:2000]}"
    )
    await message.answer(text, parse_mode="HTML")
