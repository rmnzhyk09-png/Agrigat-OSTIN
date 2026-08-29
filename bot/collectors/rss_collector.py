"""RSS-ленты новостей и блогов (feedparser)."""
import asyncio
import logging
from datetime import datetime, timezone

import feedparser
import httpx
from bs4 import BeautifulSoup

from .base import BaseCollector

logger = logging.getLogger(__name__)

UA = {"User-Agent": "socmon-bot/1.0"}


def _clean_html(html_text: str, limit: int = 1200) -> str:
    text = BeautifulSoup(html_text or "", "html.parser").get_text(" ", strip=True)
    return " ".join(text.split())[:limit]


class RssCollector(BaseCollector):
    """RSS-ленты из .env (RSS_FEEDS, через запятую) — участвуют в /find."""

    name = "rss"
    platforms = ["rss", "лента", "новости"]

    async def is_available(self) -> bool:
        from ..config import settings
        return bool(settings.rss_feeds)

    async def collect(self, username: str, limit: int = 20, period_days: int = 30,
                      progress_callback=None, mode: str = "query") -> list[dict]:
        from ..config import settings
        tokens = [w for w in username.lower().replace("#", " ").split() if len(w) > 3]
        if not tokens:
            if progress_callback:
                await progress_callback(0, 0, "rss")
            return []
        cutoff = datetime.now(timezone.utc).timestamp() - period_days * 86400
        items: list[dict] = []
        for feed_url in settings.rss_feeds:
            if len(items) >= limit:
                break
            try:
                async with httpx.AsyncClient(timeout=20, headers=UA, follow_redirects=True) as client:
                    resp = await client.get(feed_url)
                parsed = await asyncio.to_thread(feedparser.parse, resp.content)
            except Exception as ex:
                logger.debug("RSS %s: %s", feed_url, ex)
                continue
            feed_title = (parsed.feed.get("title") if parsed.feed else None) or feed_url
            for entry in parsed.entries[:40]:
                title = entry.get("title") or ""
                summary = _clean_html(entry.get("summary") or entry.get("description") or "")
                haystack = f"{title} {summary}".lower()
                if not any(tok in haystack for tok in tokens):
                    continue
                published = None
                if entry.get("published_parsed"):
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if published and published.timestamp() < cutoff:
                    continue
                link = entry.get("link") or ""
                if not link:
                    continue
                items.append({
                    "id": link, "post_id": link,
                    "platform": "rss", "author": feed_title[:100],
                    "url": link, "text": f"{title}\n\n{summary}",
                    "published_at": published.isoformat() if published else "",
                    "score": 0.5,
                })
                if len(items) >= limit:
                    break
        if progress_callback:
            await progress_callback(len(items), 0, "rss")
        return items[:limit]


async def parse_feed(url: str, limit: int = 10) -> dict:
    """Разово распарсить ленту по URL (для команды /rss)."""
    async with httpx.AsyncClient(timeout=20, headers=UA, follow_redirects=True) as client:
        resp = await client.get(url)
    parsed = await asyncio.to_thread(feedparser.parse, resp.content)
    feed_title = (parsed.feed.get("title") if parsed.feed else None) or url
    entries = []
    for entry in parsed.entries[:limit]:
        title = entry.get("title") or "(без заголовка)"
        summary = _clean_html(entry.get("summary") or "", 300)
        link = entry.get("link") or ""
        published = ""
        if entry.get("published_parsed"):
            published = datetime(*entry.published_parsed[:6],
                                 tzinfo=timezone.utc).strftime("%d.%m.%Y")
        entries.append({"title": title, "summary": summary, "url": link,
                        "published": published})
    return {"title": feed_title, "entries": entries}
