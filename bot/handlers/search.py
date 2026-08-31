"""Поиск по каталогу и по импортированной БД."""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

from ..catalog import Catalog

router = Router()


@router.message(Command("search"))
async def cmd_search(message: Message):
    """Поиск в каталоге и по импортированной БД."""
    query = message.text.replace("/search", "").strip()

    if not query:
        await message.answer(
            "Формат: /search &lt;запрос&gt;\nПример: /search телефон\n\n"
            "Ищет и в каталоге инструментов, и в импортированной БД "
            "(записи + профили людей)."
        )
        return

    lines = []
    found = 0

    # 1. Поиск по каталогу инструментов
    catalog = Catalog()
    results = catalog.search(query, limit=8)
    if results:
        lines.append(f"<b>Каталог: {len(results)}</b>")
        for bot in results:
            lines.append(
                f"- <a href=\"{bot['url']}\">{bot['name']}</a> — "
                f"{catalog.group_name(catalog._group_of(bot))}"
            )
        found += len(results)
        lines.append("")

    # 2. Поиск по импортированной БД (записи)
    from ..dbimport.query import search_imported, search_profiles
    try:
        db_items = await search_imported(query, mode="query", limit=10)
        if db_items:
            lines.append(f"<b>Импортированная БД: {len(db_items)}</b>")
            for it in db_items[:10]:
                head = it.get("text") or ""
                if len(head) > 80:
                    head = head[:80] + "…"
                author = (it.get("author") or "").strip()
                author = f" — {author}" if author else ""
                url = it.get("url") or ""
                if url.startswith("http"):
                    lines.append(f"- <a href=\"{url}\">{head}</a>{author}")
                else:
                    lines.append(f"- {head}{author}")
            found += len(db_items)
            lines.append("")
    except Exception as ex:
        lines.append(f"<i>Ошибка поиска в БД: {ex}</i>")

    # 3. Поиск по профилям людей
    try:
        profiles = await search_profiles(query, limit=5)
        if profiles:
            lines.append(f"<b>Профили людей: {len(profiles)}</b>")
            for p in profiles[:5]:
                name = p.get("author") or "Без имени"
                phones = p.get("profile", {}).get("phones") or []
                phone_str = phones[0] if phones else "—"
                conf = p.get("profile", {}).get("confidence") or ""
                conf_str = f" · <i>{conf}</i>" if conf else ""
                lines.append(f"- <b>{name}</b> · {phone_str}{conf_str}")
            found += len(profiles)
            lines.append("")
            lines.append("Подробная карточка: <code>/profile &lt;ФИО&gt;</code>")
    except Exception as ex:
        lines.append(f"<i>Ошибка поиска профилей: {ex}</i>")

    if not lines:
        await message.answer("Ничего не найдено. Примеры запросов: телефон, ФИО, VIN, telegram")
        return

    header = f"<b>Найдено по «{query}»: {found}</b>\n\n"
    await message.answer(header + "\n".join(lines), parse_mode="HTML")
