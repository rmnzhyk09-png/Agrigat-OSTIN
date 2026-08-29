"""История поиска."""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from sqlalchemy import select

from ..db.database import AsyncSessionLocal
from ..db.models import SearchHistory

router = Router()


@router.message(Command("history"))
async def cmd_history(message: Message):
    """История поиска."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(SearchHistory)
            .filter_by(user_id=message.from_user.id)
            .order_by(SearchHistory.created_at.desc())
            .limit(10)
        )
        history = res.scalars().all()

    if not history:
        await message.answer("История пуста. Запустите /find, /tag или /web.")
        return

    lines = ["<b>История поиска (10):</b>", ""]
    for h in history:
        date = h.created_at.strftime("%d.%m.%Y %H:%M")
        lines.append(f"- {date} — <code>{h.query}</code> ({h.results_count})")

    await message.answer("\n".join(lines), parse_mode="HTML")
