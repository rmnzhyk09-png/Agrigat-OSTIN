"""Фоновые задачи."""
import asyncio
import logging
from typing import Optional

from .collectors import collect_all, get_collector, scraper
from .analysis import classify_items, analyze_sentiment
from .reporting import generate_summary

logger = logging.getLogger(__name__)

# Источники, дающие посты конкретного аккаунта (для /watch)
ACCOUNT_SOURCES = ["mastodon", "bluesky", "reddit"]


async def collect_account(query: str, limit: int = 10) -> list[dict]:
    """Лёгкий сбор постов конкретного аккаунта (без поисковых коллекторов)."""
    items: list[dict] = []
    for name in ACCOUNT_SOURCES:
        collector = get_collector(name)
        if not collector:
            continue
        try:
            if not await collector.is_available():
                continue
            got = await collector.collect(query, limit=limit, period_days=7,
                                          mode="account")
            items.extend(got)
        except Exception as ex:
            logger.debug("watch %s: %s", name, ex)
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return items


async def run_monitoring(nickname: str, tags: list[str], progress_callback=None,
                         mode: str = "query") -> dict:
    """
    Запустить мониторинг по всем доступным платформам.

    Args:
        nickname: Запрос, никнейм или тег
        tags: Теги для классификации результатов
        mode: "query" (глобальный поиск) | "account" (по аккаунту) | "tag" (по хештегу)

    Returns:
        {items, summary, stats} или {error}
    """
    items = await collect_all(nickname, limit=50, period_days=30,
                              progress_callback=progress_callback, mode=mode)

    # Глубокий парсинг: полные тексты топовых страниц из веб-поиска
    if mode == "query":
        web_urls = [it["url"] for it in items
                    if it.get("platform") in ("google", "serpapi", "brave")
                    and it.get("url", "").startswith("http")][:3]
        if web_urls:
            try:
                pages = await scraper.scrape_urls(web_urls, max_pages=3)
                if progress_callback and pages:
                    await progress_callback(len(pages), 0, "deep-parse")
                seen = {it.get("url") for it in items}
                items.extend(p for p in pages if p.get("url") not in seen)
            except Exception as ex:
                logger.warning("глубокий парсинг: %s", ex)

    if not items:
        return {"error": "Ничего не найдено (проверьте API-ключи или попробуйте другой запрос)"}

    items = classify_items(items, tags)
    items = analyze_sentiment(items)
    summary = generate_summary(items, tags)

    stats = {
        "total": len(items),
        "by_platform": {},
        "by_tag": {},
        "sentiment": {"positive": 0, "neutral": 0, "negative": 0},
    }
    for item in items:
        platform = item.get("platform", "unknown")
        stats["by_platform"][platform] = stats["by_platform"].get(platform, 0) + 1
        for tag in item.get("tags", []):
            stats["by_tag"][tag] = stats["by_tag"].get(tag, 0) + 1
        sentiment = item.get("sentiment", "neutral")
        stats["sentiment"][sentiment] = stats["sentiment"].get(sentiment, 0) + 1

    return {"items": items, "summary": summary, "stats": stats}
