"""Слежение за аккаунтами в реальном времени: /watch, /unwatch."""
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command, CommandObject

from ..db import repo

router = Router()


@router.message(Command("watch"))
async def cmd_watch(message: Message, command: CommandObject):
    """Слежение за аккаунтом: /watch @durov"""
    await run_watch(message, (command.args or "").strip())


async def run_watch(message: Message, query: str):
    """Добавить слежение (используется /watch и «живой» кнопкой меню)."""
    query = (query or "").strip()
    if not query or query.lower() == "list":
        watches = await repo.list_watches(message.from_user.id)
        if not watches:
            await message.answer(
                "Формат: /watch &lt;ник или ссылка&gt;\n"
                "Пример: /watch @durov\n\n"
                "Бот проверяет аккаунт каждые 20 минут (Mastodon, Bluesky, Reddit) "
                "и присылает новые посты."
            )
        else:
            lines = ["<b>Слежение:</b>"]
            for w in watches:
                lines.append(f"- <code>{w}</code>")
            lines.append("\nУдалить: /unwatch &lt;ник&gt; или /unwatch all")
            await message.answer("\n".join(lines), parse_mode="HTML")
        return

    ok, msg = await repo.add_watch(message.from_user.id, query[:250])
    prefix = "Слежение добавлено." if ok else ""
    note = ("\nПроверка каждые 20 минут. Новые посты придут сообщением."
            if ok else "")
    await message.answer(" ".join(x for x in (prefix, msg, note) if x).strip())


@router.message(Command("unwatch"))
async def cmd_unwatch(message: Message, command: CommandObject):
    """Удалить слежение: /unwatch @durov или /unwatch all."""
    query = (command.args or "").strip()
    if not query:
        await message.answer("Формат: /unwatch &lt;ник&gt;\nУдалить всё: /unwatch all")
        return
    removed = await repo.remove_watch(message.from_user.id, query[:250])
    await message.answer("Слежение удалено." if removed else "Такого слежения нет.")
