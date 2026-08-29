"""Скрапер сайтов: публичные страницы с уважением к robots.txt.

- перед загрузкой проверяется robots.txt (с кэшем)
- честный User-Agent, только публичный HTML, без обхода авторизации
"""
import asyncio
import logging
import re
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .base import BaseCollector

logger = logging.getLogger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (compatible; socmon-bot/1.0; +monitoring)"}

AUTH_MARKERS = (
    "sign in to continue", "log in to read", "authwall", "paywall",
    "подпишитесь, чтобы читать", "войдите, чтобы прочитать", "login required",
)


class DomainScraper(BaseCollector):
    """Скрапинг публичных страниц по URL."""

    name = "scraper"
    platforms = ["scraper", "скрапер", "сайт"]

    def __init__(self) -> None:
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    async def is_available(self) -> bool:
        return True

    async def collect(self, username: str, limit: int = 20, period_days: int = 30,
                      progress_callback=None, mode: str = "query") -> list[dict]:
        # Скрапер работает по конкретным URL (см. scrape_page/scrape_urls),
        # в поиске по запросу не участвует.
        return []

    # ---------- robots.txt ----------

    def _robots_ok_sync(self, domain: str, path: str) -> bool:
        if domain in self._robots_cache:
            rp = self._robots_cache[domain]
            if rp is None:
                return True  # robots.txt недоступен — нет явного запрета
            return (rp.can_fetch("*", f"https://{domain}{path}")
                    or rp.can_fetch("Mozilla", f"https://{domain}{path}"))
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"https://{domain}/robots.txt")
        try:
            rp.read()
        except Exception:
            self._robots_cache[domain] = None
            return True
        self._robots_cache[domain] = rp
        return (rp.can_fetch("*", f"https://{domain}{path}")
                or rp.can_fetch("Mozilla", f"https://{domain}{path}"))

    # ---------- скрапинг ----------

    async def scrape_page(self, url: str) -> dict | None:
        """Загрузить и распарсить одну страницу. None, если запрещено/пусто."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if not domain:
            return None
        try:
            allowed = await asyncio.to_thread(self._robots_ok_sync, domain, parsed.path)
        except Exception:
            allowed = False
        if not allowed:
            logger.info("robots.txt запрещает %s — пропускаю", url)
            return None
        try:
            async with httpx.AsyncClient(timeout=20, headers=UA, follow_redirects=True) as client:
                resp = await client.get(url)
        except Exception as ex:
            logger.warning("scrape %s: %s", url, ex)
            return None
        if resp.status_code != 200 or "html" not in resp.headers.get("content-type", ""):
            return None
        return await asyncio.to_thread(self._extract_sync, resp.text, url, domain)

    def _extract_sync(self, html: str, url: str, domain: str) -> dict | None:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "form", "nav", "footer", "aside"]):
            tag.decompose()
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        body = soup.get_text(" ", strip=True)
        text = " ".join(body.split())
        if not title or len(text) < 120:
            return None
        if any(marker in text[:500].lower() for marker in AUTH_MARKERS):
            logger.info("authwall на %s — пропускаю", url)
            return None

        links = [a.get("href", "") for a in soup.find_all("a", href=True)]
        links = [l for l in links if l.startswith("http")][:20]

        published = ""
        for sel in ('meta[property="article:published_time"]',
                    'meta[name="datePublished"]', 'time[datetime]'):
            node = soup.select_one(sel)
            if node:
                published = node.get("content") or node.get("datetime") or ""
                if published:
                    break

        return {
            "id": url, "post_id": url, "platform": "web",
            "author": domain, "url": url,
            "text": f"{title}\n\n{text[:2000]}",
            "published_at": published, "score": 0.0,
            "title": title, "links": links,
        }

    async def scrape_urls(self, urls: list[str], max_pages: int = 5) -> list[dict]:
        """Скрапить несколько страниц (для /find: топ ссылок из веб-поиска)."""
        results: list[dict] = []
        seen_domains: set[str] = set()
        for url in urls:
            if len(results) >= max_pages:
                break
            domain = urlparse(url).netloc.lower()
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            item = await self.scrape_page(url)
            if item:
                results.append(item)
        return results


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()
