"""Универсальный веб-поиск: Serper.dev / SerpAPI / Brave Search API.

Ключи — в .env: SERPER_API_KEY, SERPAPI_API_KEY, BRAVE_API_KEY.
Без ключей коллекторы сообщают, что недоступны (не падают).
"""
import logging
from typing import Callable, Optional

import httpx

from .base import BaseCollector

logger = logging.getLogger(__name__)

UA = {"User-Agent": "socmon-bot/1.0"}


def _period_tbs(days: int) -> Optional[str]:
    if days <= 1:
        return "qdr:d"
    if days <= 7:
        return "qdr:w"
    if days <= 30:
        return "qdr:m"
    if days <= 90:
        return "qdr:y"
    return None


class _WebSearch(BaseCollector):
    """Общая база для поисковых движков."""

    platforms: list[str] = []

    async def collect(self, username: str, limit: int = 20, period_days: int = 30,
                      progress_callback=None, mode: str = "query") -> list[dict]:
        query = username.lstrip("#") if mode == "tag" else username
        data = await self._search(query, limit, period_days)
        items: list[dict] = []
        for pos, (url, title, snippet) in enumerate(data[:limit]):
            items.append({
                "id": url, "post_id": url,
                "platform": self.name, "author": url.split("/")[2] if "/" in url else "",
                "url": url, "text": f"{title}\n{snippet}"[:1500],
                "published_at": "", "score": 1.0 / (pos + 1),
            })
        if progress_callback:
            await progress_callback(len(items), 0, self.name)
        return items


class SerperCollector(_WebSearch):
    """Google через Serper.dev (2500 бесплатных запросов на старте)."""

    name = "google"
    platforms = ["google", "serper", "веб", "web"]

    async def is_available(self) -> bool:
        from ..config import settings
        return bool(settings.serper_api_key)

    async def _search(self, query: str, limit: int, period_days: int) -> list[tuple]:
        from ..config import settings
        payload: dict = {"q": query, "num": min(limit, 100)}
        tbs = _period_tbs(period_days)
        if tbs:
            payload["tbs"] = tbs
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post("https://google.serper.dev/search",
                                      json=payload,
                                      headers={"X-API-KEY": settings.serper_api_key})
                if r.status_code != 200:
                    logger.warning("serper: HTTP %s", r.status_code)
                    return []
                data = r.json()
        except Exception as ex:
            logger.warning("serper: %s", ex)
            return []
        return [(it.get("link", ""), it.get("title", ""), it.get("snippet", ""))
                for it in data.get("organic", [])]


class SerpApiCollector(_WebSearch):
    """Google через SerpAPI (100 бесплатных запросов/месяц)."""

    name = "serpapi"
    platforms = ["serpapi"]

    async def is_available(self) -> bool:
        from ..config import settings
        return bool(settings.serpapi_api_key)

    async def _search(self, query: str, limit: int, period_days: int) -> list[tuple]:
        from ..config import settings
        params: dict = {"engine": "google", "q": query, "num": min(limit, 100),
                        "api_key": settings.serpapi_api_key}
        tbs = _period_tbs(period_days)
        if tbs:
            params["tbs"] = tbs
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                r = await client.get("https://serpapi.com/search.json", params=params)
                if r.status_code != 200:
                    logger.warning("serpapi: HTTP %s", r.status_code)
                    return []
                data = r.json()
        except Exception as ex:
            logger.warning("serpapi: %s", ex)
            return []
        return [(it.get("link", ""), it.get("title", ""), it.get("snippet", ""))
                for it in data.get("organic_results", [])]


class BraveCollector(_WebSearch):
    """Brave Search API (2000 бесплатных запросов/месяц)."""

    name = "brave"
    platforms = ["brave"]

    async def is_available(self) -> bool:
        from ..config import settings
        return bool(settings.brave_api_key)

    async def _search(self, query: str, limit: int, period_days: int) -> list[tuple]:
        from ..config import settings
        params: dict = {"q": query, "count": min(limit, 20)}
        if period_days <= 1:
            params["freshness"] = "pd"
        elif period_days <= 7:
            params["freshness"] = "pw"
        elif period_days <= 30:
            params["freshness"] = "pm"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get("https://api.search.brave.com/res/v1/web/search",
                                     params=params,
                                     headers={"X-Subscription-Token": settings.brave_api_key})
                if r.status_code != 200:
                    logger.warning("brave: HTTP %s", r.status_code)
                    return []
                data = r.json()
        except Exception as ex:
            logger.warning("brave: %s", ex)
            return []
        return [(it.get("url", ""), it.get("title", ""),
                 it.get("description", ""))
                for it in data.get("web", {}).get("results", [])]
