"""Поиск по каталогу."""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

from ..catalog import Catalog

router = Router()


@router.message(Command("search"))
async def cmd_search(message: Message):
    """Поиск в каталоге."""
    query = message.text.replace("/search", "").strip()
    
    if not query:
        await message.answer(
            "Формат: /search &lt;запрос&gt;\nПример: /search телефон"
        )
        return

    catalog = Catalog()
    results = catalog.search(query, limit=8)

    if not results:
        await message.answer("Ничего не найдено. Примеры запросов: телефон, ФИО, VIN, telegram")
        return

    lines = [f"<b>Найдено: {len(results)}</b>", ""]
    for bot in results:
        lines.append(
            f"- <a href=\"{bot['url']}\">{bot['name']}</a> — "
            f"{catalog.group_name(catalog._group_of(bot))}"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")
