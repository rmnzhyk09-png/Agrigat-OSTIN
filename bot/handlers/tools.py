"""Инструменты: веб-поиск (/web), RSS (/rss), скрапер (/scrape)."""
import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..collectors.scraper import DomainScraper
from ..collectors.rss_collector import parse_feed
from ..tasks import run_monitoring

logger = logging.getLogger(__name__)
router = Router()

_scraper = DomainScraper()

KEY_HINTS = (
    "Веб-поиск недоступен: нет ключа. Добавьте в .env (одно из):\n"
    "<code>SERPER_API_KEY</code> — serper.dev, 2500 запросов бесплатно\n"
    "<code>BRAVE_API_KEY</code> — brave.com/search/api, 2000 запросов/мес\n"
    "<code>SERPAPI_API_KEY</code> — serpapi.com, 100 запросов/мес"
)


@router.message(Command("web"))
async def cmd_web(message: Message, command: CommandObject):
    """Универсальный веб-поиск: /web запрос."""
    await run_web(message, (command.args or "").strip())


async def run_web(message: Message, query: str):
    """Веб-поиск по тексту (используется /web и «живой» кнопкой меню)."""
    query = (query or "").strip()
    if not query:
        await message.answer(
            "Формат: /web &lt;запрос&gt;\nПример: /web лучшие ноутбуки 2026\n\n" + KEY_HINTS
        )
        return

    from ..config import settings
    engines = [n for n, k in (("Serper", settings.serper_api_key),
                              ("Brave", settings.brave_api_key),
                              ("SerpAPI", settings.serpapi_api_key)) if k]
    if not engines:
        await message.answer(KEY_HINTS)
        return

    status = await message.answer(f"Веб-поиск ({', '.join(engines)}): {query}…")
    result = await run_monitoring(query, tags=[query], mode="query")

    if "error" in result:
        await status.edit_text(result["error"])
        return
    await status.delete()

    items = result["items"]
    web_items = [it for it in items if it["platform"] in ("google", "serpapi", "brave", "web", "db")]
    if not web_items:
        await message.answer("Ничего не найдено. Попробуйте изменить запрос.")
        return

    from .fmt_menu import send_findings
    await send_findings(message, f"Веб-поиск: {query}", web_items, result)

    from ..db import repo
    try:
        await repo.record_search(message.from_user.id, f"[web] {query}", len(web_items))
        await repo.record_job(message.from_user.id, "web", result["stats"])
    except Exception:
        pass


@router.message(Command("rss"))
async def cmd_rss(message: Message, command: CommandObject):
    """RSS-лента: /rss https://habr.com/ru/rss/best/daily/"""
    url = (command.args or "").strip()
    if not url.startswith("http"):
        await message.answer(
            "Формат: /rss &lt;url&gt;\nПример: /rss https://habr.com/ru/rss/best/daily/\n\n"
            "Постоянные ленты: RSS_FEEDS в .env — участвуют в /find."
        )
        return

    status = await message.answer("Чтение ленты…")
    try:
        feed = await parse_feed(url, limit=10)
    except Exception as ex:
        await status.edit_text(f"Лента недоступна: {ex}")
        return
    await status.delete()

    if not feed["entries"]:
        await message.answer("В ленте нет записей.")
        return

    lines = [f"<b>{_esc(feed['title'])}</b>", ""]
    for i, e in enumerate(feed["entries"], 1):
        date = f" ({e['published']})" if e["published"] else ""
        lines.append(
            f"{i}. <a href=\"{e['url']}\">{_esc(e['title'])}</a>{date}\n"
            f"    {_esc(e['summary'][:120])}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("scrape"))
async def cmd_scrape(message: Message, command: CommandObject):
    """Скрапер страницы: /scrape https://example.com"""
    url = (command.args or "").strip()
    if not url.startswith("http"):
        await message.answer(
            "Формат: /scrape &lt;url&gt;\nПример: /scrape https://example.com\n\n"
            "Возвращает заголовок, текст и ссылки страницы.\n"
            "Ограничения: robots.txt, страницы с авторизацией не обрабатываются."
        )
        return

    status = await message.answer("Обработка страницы…")
    item = await _scraper.scrape_page(url)
    await status.delete()

    if not item:
        await message.answer(
            "Страница не обработана: robots.txt запрещает, требуется вход или это не HTML."
        )
        return

    text = item["text"]
    title = item["title"]
    body = text.replace(title, "", 1).strip()[:1500]
    links = "\n".join(f"- {l}" for l in item["links"][:10])
    await message.answer(
        f"<b>{_esc(title)}</b>\n{item['url']}\n\n"
        f"{_esc(body)}\n\n"
        + (f"<b>Ссылки:</b>\n{links}" if links else ""),
        parse_mode="HTML", disable_web_page_preview=True,
    )


# ---------- вспомогательное ----------

def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
