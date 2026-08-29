"""Справка по тегам."""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

router = Router()


@router.message(Command("tags"))
async def cmd_tags(message: Message):
    """Справка: как работают теги в боте."""
    text = (
        "Теги — темы, по которым классифицируются результаты поиска.\n\n"
        "Разовый поиск: /find, /tag, /monitor — теги спросит бот.\n"
        "Постоянный: /subscribe — теги сохраняются для дайджеста.\n\n"
        "Примеры: криптовалюта, политика, технологии"
    )
    await message.answer(text, parse_mode="HTML")
