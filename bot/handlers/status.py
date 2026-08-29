"""Статус задач."""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from sqlalchemy import select

from ..db.database import AsyncSessionLocal
from ..db.models import Job

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
