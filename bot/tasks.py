"""Фоновые задачи."""
import asyncio
import logging
from typing import Optional

from .collectors import collect_all, get_collector, scraper
from .analysis import classify_items, analyze_sentiment
from .reporting import generate_summary
from .dbimport.query import search_imported, search_profiles, search_related
from .services.blackbird import search_blackbird, PLATFORM as BB_PLATFORM
from .services.datatech import search_datatech, PLATFORM as DT_PLATFORM

logger = logging.getLogger(__name__)

# Источники, дающие посты конкретного аккаунта (для /watch)
ACCOUNT_SOURCES = ["mastodon", "bluesky", "reddit"]


def _want_blackbird(nickname: str, mode: str, field: str) -> bool:
    """Нужно ли запускать Blackbird для этого запроса.

    Запускаем только для username/email/имени/телефона — не для длинных фраз.
    """
    q = (nickname or "").strip()
    if not q:
        return False
    if field and field not in ("name", "email", "phone"):
        return False
    # длинная фраза (соцсети-поиск) — не для Blackbird
    if len(q.split()) > 3:
        return False
    return True


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
                         mode: str = "query", field: str = "") -> dict:
    """
    Запустить мониторинг по всем доступным платформам.

    Args:
        nickname: Запрос, никнейм или тег
        tags: Теги для классификации результатов
        mode: "query" (глобальный поиск) | "account" (по аккаунту) | "tag" (по хештегу)
        field: Тип идентификатора из запроса (имя/телефон/инн/паспорт/email/авто/снилс)

    Returns:
        {items, summary, stats} или {error}
    """
    items = await collect_all(nickname, limit=50, period_days=30,
                              progress_callback=progress_callback, mode=mode)

    # Добавляем записи из импортированной БД (Supabase / локальное зеркало)
    db_found_records: list[dict] = []
    db_found_profiles: list[dict] = []
    try:
        db_items = await search_imported(nickname, mode=mode, limit=20, field=field)
        seen = {(it.get("url") or f"{it.get('platform')}:{it.get('id')}")
                for it in items}
        for it in db_items:
            key = it.get("url") or f"db:{it.get('id')}"
            if key not in seen:
                seen.add(key)
                items.append(it)
            db_found_records.append(it)
        if db_items:
            logger.info("db search: +%s записей из импортированной БД", len(items))

        # Добавляем полные карточки профилей (ФИО/телефон/email/ИНН)
        profile_items = await search_profiles(nickname, limit=5, field=field)
        for it in profile_items:
            key = f"profile:{it.get('id')}"
            if key not in seen:
                seen.add(key)
                items.append(it)
            db_found_profiles.append(it)

        # Перекрёстные связи: другие профили/записи, делящие контакт/ИНН/авто
        try:
            related = await search_related(db_found_records + db_found_profiles,
                                           db_found_profiles)
            for it in related:
                key = it.get("url") or f"{it.get('platform')}:{it.get('id')}"
                if key not in seen:
                    seen.add(key)
                    items.append(it)
            if related:
                logger.info("related search: +%s связанных записей", len(related))
        except Exception as rex:
            logger.warning("перекрёстный поиск: %s", rex)
    except Exception as ex:
        logger.warning("поиск по импортированной БД: %s", ex)

    # Blackbird — OSINT reverse username/email поиск (если настроен BLACKBIRD_DIR).
    # Запускаем в отдельном потоке (внутри — subprocess, не блокируя цикл событий).
    if _want_blackbird(nickname, mode, field):
        try:
            bb_items = await asyncio.to_thread(
                search_blackbird, nickname,
                as_email=("@" in nickname),
            )
            seen = {it.get("url") or f"{it.get('platform')}:{it.get('id')}"
                    for it in items}
            for it in bb_items:
                key = it.get("url") or f"{BB_PLATFORM}:{it.get('id')}"
                if key in seen:
                    continue
                seen.add(key)
                items.append(it)
            if bb_items:
                logger.info("blackbird: +%s найденных аккаунтов", len(bb_items))
        except Exception as ex:
            logger.warning("blackbird: %s", ex)

    # DataTech — параллельный поиск по внешним базам (если задан DATATECH_API_KEY).
    if nickname and mode in ("query", "profile") and not field:
        try:
            dt_items = await search_datatech(nickname, limit=10)
            seen = {it.get("url") or f"{it.get('platform')}:{it.get('id')}"
                    for it in items}
            for it in dt_items:
                key = it.get("url") or f"{DT_PLATFORM}:{it.get('id')}"
                if key in seen:
                    continue
                seen.add(key)
                items.append(it)
            if dt_items:
                logger.info("datatech: +%s записей", len(dt_items))
        except Exception as ex:
            logger.warning("datatech: %s", ex)

    # Глубокий парсинг: полные тексты топовых страниц из веб-поиска
    if mode == "query" and not field:
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
