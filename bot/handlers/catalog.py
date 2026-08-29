"""Команды каталога OSINT-ботов."""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

from ..config import settings
from ..catalog import Catalog

router = Router()


@router.message(Command("catalog"))
async def cmd_catalog(message: Message):
    """Каталог OSINT-ботов."""
    if not settings.catalog_enabled:
        await message.answer("❌ Каталог отключён.")
        return
    
    catalog = Catalog()
    text = catalog.format_groups_list()
    await message.answer(text, parse_mode="HTML")


@router.message(Command("groups"))
async def cmd_groups(message: Message):
    """Список групп."""
    catalog = Catalog()
    text = catalog.format_groups_list()
    await message.answer(text, parse_mode="HTML")


@router.message(Command("bot"))
async def cmd_bot(message: Message):
    """Боты группы."""
    catalog = Catalog()
    
    group_id = message.text.replace("/bot", "").strip().lower()
    if not group_id:
        await message.answer("Пример: /bot telegram или /bot car")
        return
    
    text = catalog.format_group(group_id)
    await message.answer(text, parse_mode="HTML")


@router.message(Command("info"))
async def cmd_info(message: Message):
    """Карточка бота."""
    catalog = Catalog()
    
    query = message.text.replace("/info", "").strip().lower()
    if not query:
        await message.answer("Пример: /info himera")
        return
    
    # Поиск
    results = catalog.search(query, limit=1)
    if not results:
        await message.answer("Бот не найден. Попробуйте /groups")
        return
    
    text = catalog.format_bot(results[0])
    await message.answer(text, parse_mode="HTML")
