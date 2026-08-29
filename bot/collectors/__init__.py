"""Коллекторы соцсетей: реальные источники публичных данных.

Формат элемента: {id, post_id, platform, text, url, author,
published_at (ISO-строка), score}
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import quote

import httpx

from .base import BaseCollector

logger = logging.getLogger(__name__)

UA = {"User-Agent": "socmon-bot/1.0 (monitoring; +https://github.com)"}


def _iso(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _since(period_days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=period_days)


class _HTTP(BaseCollector):
    """Общий асинхронный HTTP-клиент."""

    async def _get_json(self, url: str, params: dict | None = None,
                        headers: dict | None = None) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=20, headers={**UA, **(headers or {})}) as client:
                r = await client.get(url, params=params)
                if r.status_code != 200:
                    logger.warning("%s: HTTP %s", self.name, r.status_code)
                    return None
                return r.json()
        except Exception as ex:
            logger.warning("%s: %s", self.name, ex)
            return None


class GitHubCollector(_HTTP):
    """GitHub: issues и репозитории по запросу. Работает без токена."""

    name = "github"
    platforms = ["github", "гитхаб"]

    async def is_available(self) -> bool:
        return True

    async def collect(self, username: str, limit: int = 20, period_days: int = 30,
                      progress_callback=None, mode: str = "query") -> list[dict]:
        from ..config import settings
        headers = {"Accept": "application/vnd.github+json"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        items: list[dict] = []
        issues = await self._get_json("https://api.github.com/search/issues", {
            "q": f"{username} created:>{_since(period_days).date().isoformat()}",
            "sort": "updated", "per_page": min(limit, 50),
        }, headers)
        for it in (issues or {}).get("items", []):
            items.append({
                "id": it.get("id"), "post_id": str(it.get("id")),
                "platform": "github", "author": (it.get("user") or {}).get("login", ""),
                "url": it.get("html_url", ""), "text": (it.get("title", "") + "\n"
                + (it.get("body") or ""))[:1500],
                "published_at": _iso(_parse_iso(it.get("created_at"))),
                "score": float(it.get("comments") or 0),
            })
        repos = await self._get_json("https://api.github.com/search/repositories", {
            "q": f"{username} created:>{_since(period_days).date().isoformat()}",
            "sort": "stars", "per_page": min(limit, 20),
        }, headers)
        for it in (repos or {}).get("items", [])[: max(0, limit - len(items))]:
            items.append({
                "id": it.get("id"), "post_id": str(it.get("id")),
                "platform": "github", "author": ((it.get("owner") or {}).get("login", "")),
                "url": it.get("html_url", ""),
                "text": f"{it.get('full_name', '')} — {it.get('description') or ''}"[:1500],
                "published_at": _iso(_parse_iso(it.get("created_at"))),
                "score": float(it.get("stargazers_count") or 0),
            })
        if progress_callback:
            await progress_callback(len(items), 0, "github")
        return items[:limit]


class RedditCollector(_HTTP):
    """Reddit: публичный поиск (без ключей) или OAuth (с ключами)."""

    name = "reddit"
    platforms = ["reddit", "реддит"]

    def __init__(self):
        self._token: str | None = None

    async def is_available(self) -> bool:
        return True

    async def _oauth_json(self, url: str, params: dict) -> Optional[dict]:
        from ..config import settings
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                if not self._token:
                    r = await client.post(
                        "https://www.reddit.com/api/v1/access_token",
                        auth=(settings.reddit_client_id, settings.reddit_client_secret),
                        headers=UA, data={"grant_type": "client_credentials"})
                    if r.status_code == 200:
                        self._token = r.json().get("access_token")
                if not self._token:
                    return None
                r = await client.get(url, params=params, headers={
                    **UA, "Authorization": f"Bearer {self._token}"})
                return r.json() if r.status_code == 200 else None
        except Exception as ex:
            logger.warning("reddit oauth: %s", ex)
            return None

    async def collect(self, username: str, limit: int = 20, period_days: int = 30,
                      progress_callback=None, mode: str = "query") -> list[dict]:
        from ..config import settings
        # Режимы: по аккаунту — author:, по тегу — точная фраза, иначе обычный поиск
        if mode == "account":
            q = f"author:{username.lstrip('@')}"
        elif mode == "tag":
            q = f'"{username.lstrip("#")}"'
        else:
            q = username
        params = {"q": q, "sort": "new", "limit": min(limit, 50), "t": "month"}
        if settings.reddit_client_id and settings.reddit_client_secret:
            data = await self._oauth_json("https://oauth.reddit.com/search", params)
        else:
            # Публичный endpoint часто отдаёт 403 без OAuth — пробуем, но не надеемся
            data = await self._get_json("https://www.reddit.com/search.json", params)
            if data is None:
                logger.info("reddit: публичный поиск недоступен (403). "
                            "Добавь REDDIT_CLIENT_ID/SECRET в .env для OAuth")
                return []
        items: list[dict] = []
        for it in (data or {}).get("data", {}).get("children", []):
            d = it.get("data", {})
            items.append({
                "id": d.get("id"), "post_id": d.get("id", ""),
                "platform": "reddit", "author": d.get("author", ""),
                "url": "https://www.reddit.com" + (d.get("permalink") or ""),
                "text": (d.get("title", "") + "\n" + (d.get("selftext") or ""))[:1500],
                "published_at": _iso(datetime.fromtimestamp(d.get("created_utc", 0),
                                     tz=timezone.utc)) if d.get("created_utc") else "",
                "score": float(d.get("score") or 0),
            })
        if progress_callback:
            await progress_callback(len(items), 0, "reddit")
        return items[:limit]


class VKCollector(_HTTP):
    """VK: newsfeed.search по сервисному токену."""

    name = "vk"
    platforms = ["vk", "вк", "вконтакте"]

    async def is_available(self) -> bool:
        from ..config import settings
        return bool(settings.vk_service_token)

    async def collect(self, username: str, limit: int = 20, period_days: int = 30,
                      progress_callback=None, mode: str = "query") -> list[dict]:
        from ..config import settings
        query = username.lstrip("#") if mode == "tag" else username
        data = await self._get_json("https://api.vk.com/method/newsfeed.search", {
            "q": query, "count": min(limit, 100),
            "start_time": int(_since(period_days).timestamp()),
            "v": "5.199", "access_token": settings.vk_service_token,
        })
        items: list[dict] = []
        for it in (data or {}).get("response", {}).get("items", []):
            owner, pid = it.get("owner_id"), it.get("id")
            likes = (it.get("likes") or {}).get("count", 0)
            items.append({
                "id": f"{owner}_{pid}", "post_id": f"{owner}_{pid}",
                "platform": "vk", "author": str(it.get("from_id") or ""),
                "url": f"https://vk.com/wall{owner}_{pid}",
                "text": (it.get("text") or "")[:1500],
                "published_at": _iso(datetime.fromtimestamp(it.get("date", 0),
                                     tz=timezone.utc)) if it.get("date") else "",
                "score": float(likes),
            })
        if progress_callback:
            await progress_callback(len(items), 0, "vk")
        return items[:limit]


class TwitterCollector(_HTTP):
    """X/Twitter: официальный API v2 (нужен bearer-токен)."""

    name = "x"
    platforms = ["x", "twitter", "твиттер"]

    async def is_available(self) -> bool:
        from ..config import settings
        return bool(settings.twitter_bearer_token)

    async def collect(self, username: str, limit: int = 20, period_days: int = 30,
                      progress_callback=None, mode: str = "query") -> list[dict]:
        from ..config import settings
        if mode == "tag":
            query = f"#{username.lstrip('#')}"
        elif mode == "account":
            query = f"from:{username.lstrip('@')}"
        else:
            query = username
        try:
            async with httpx.AsyncClient(timeout=20, headers={
                    "Authorization": f"Bearer {settings.twitter_bearer_token}"}) as client:
                r = await client.get("https://api.x.com/2/tweets/search/recent", params={
                    "query": query, "max_results": min(max(limit, 10), 100),
                    "tweet.fields": "created_at,public_metrics,author_id",
                })
                if r.status_code != 200:
                    logger.warning("x: HTTP %s — тариф API не даёт чтение?", r.status_code)
                    return []
                data = r.json()
        except Exception as ex:
            logger.warning("x: %s", ex)
            return []
        items = []
        for t in data.get("data", []):
            tid = t.get("id", "")
            m = t.get("public_metrics", {})
            items.append({
                "id": tid, "post_id": tid, "platform": "x",
                "author": t.get("author_id", ""),
                "url": f"https://x.com/i/web/status/{tid}",
                "text": (t.get("text") or "")[:1500],
                "published_at": _iso(_parse_iso(t.get("created_at"))),
                "score": float(m.get("like_count", 0) + m.get("retweet_count", 0)),
            })
        if progress_callback:
            await progress_callback(len(items), 0, "x")
        return items[:limit]


class BlueskyCollector(_HTTP):
    """Bluesky: публичный поиск постов (без ключей)."""

    name = "bluesky"
    platforms = ["bluesky", "блюскай"]

    async def is_available(self) -> bool:
        return True

    async def collect(self, username: str, limit: int = 20, period_days: int = 30,
                      progress_callback=None, mode: str = "query") -> list[dict]:
        if mode == "tag":
            q = f"#{username.lstrip('#')}"
        elif mode == "account":
            q = f"from:{username.lstrip('@')}"
        else:
            q = username
        data = await self._get_json(
            "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
            {"q": q, "limit": min(limit, 100)})
        items: list[dict] = []
        since = _since(period_days)
        for p in (data or {}).get("posts", []):
            rec = p.get("record", {})
            dt = _parse_iso(rec.get("createdAt"))
            if dt and dt < since:
                continue
            handle = (p.get("author") or {}).get("handle", "")
            uri = p.get("uri", "")
            items.append({
                "id": uri, "post_id": uri.rsplit("/", 1)[-1],
                "platform": "bluesky", "author": handle,
                "url": f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}"
                       if handle else "",
                "text": (rec.get("text") or "")[:1500],
                "published_at": _iso(dt),
                "score": float((p.get("likeCount") or 0) + (p.get("repostCount") or 0)),
            })
        if progress_callback:
            await progress_callback(len(items), 0, "bluesky")
        return items[:limit]


class MastodonCollector(_HTTP):
    """Mastodon: хештег-лента и посты аккаунта (без ключей)."""

    name = "mastodon"
    platforms = ["mastodon", "мастодон"]

    async def is_available(self) -> bool:
        return True

    async def collect(self, username: str, limit: int = 20, period_days: int = 30,
                      progress_callback=None, mode: str = "query") -> list[dict]:
        base = "https://mastodon.social"
        items: list[dict] = []
        query = username.strip().lstrip("@")
        if "/" in username:  # ссылка на аккаунт
            query = username.rstrip("/").rsplit("/", 1)[-1]

        if mode == "tag":
            return await self._tag_timeline(query.lstrip("#"), limit, progress_callback)

        if mode == "query" and " " in username:
            # Глобальный поиск статусов требует авторизации — пропускаем многословные запросы
            if progress_callback:
                await progress_callback(0, 0, "mastodon")
            return []

        # account-режим или одиночное слово в query-режиме
        statuses = None
        if query:
            acct = await self._get_json(f"{base}/api/v1/accounts/lookup",
                                        {"acct": query})
            if acct and acct.get("id"):
                statuses = await self._get_json(
                    f"{base}/api/v1/accounts/{acct['id']}/statuses",
                    {"limit": min(limit, 40), "exclude_replies": "true"})
                author = acct.get("display_name") or query
                for st in statuses or []:
                    dt = _parse_iso(st.get("created_at"))
                    items.append({
                        "id": st.get("id"), "post_id": str(st.get("id")),
                        "platform": "mastodon", "author": author,
                        "url": st.get("url", ""),
                        "text": _strip_html(st.get("content") or "")[:1500],
                        "published_at": _iso(dt),
                        "score": float((st.get("favourites_count") or 0)
                                       + (st.get("reblogs_count") or 0)),
                    })
        if not items and query:
            return await self._tag_timeline(query.lstrip("#"), limit, progress_callback)
        if progress_callback:
            await progress_callback(len(items), 0, "mastodon")
        return items[:limit]

    async def _tag_timeline(self, tag: str, limit: int,
                            progress_callback=None) -> list[dict]:
        """Публичная хештег-лента Mastodon (без ключей)."""
        tag = quote(tag.strip().lstrip("#"))
        timeline = await self._get_json(
            f"https://mastodon.social/api/v1/timelines/tag/{tag}",
            {"limit": min(limit, 40)})
        items: list[dict] = []
        for st in timeline or []:
            dt = _parse_iso(st.get("created_at"))
            items.append({
                "id": st.get("id"), "post_id": str(st.get("id")),
                "platform": "mastodon",
                "author": (st.get("account") or {}).get("acct", ""),
                "url": st.get("url", ""),
                "text": _strip_html(st.get("content") or "")[:1500],
                "published_at": _iso(dt),
                "score": float((st.get("favourites_count") or 0)
                               + (st.get("reblogs_count") or 0)),
            })
        if progress_callback:
            await progress_callback(len(items), 0, "mastodon")
        return items[:limit]


class HackerNewsCollector(_HTTP):
    """Hacker News: Algolia API (без ключей)."""

    name = "hackernews"
    platforms = ["hackernews", "hn", "хакерньюс"]

    async def is_available(self) -> bool:
        return True

    async def collect(self, username: str, limit: int = 20, period_days: int = 30,
                      progress_callback=None, mode: str = "query") -> list[dict]:
        data = await self._get_json("https://hn.algolia.com/api/v1/search", {
            "query": username, "hitsPerPage": min(limit, 50),
            "numericFilters": f"created_at_i>{int(_since(period_days).timestamp())}",
        })
        items: list[dict] = []
        for h in (data or {}).get("hits", []):
            dt = _parse_iso(h.get("created_at"))
            items.append({
                "id": h.get("objectID"), "post_id": h.get("objectID", ""),
                "platform": "hackernews", "author": h.get("author", ""),
                "url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "text": (h.get("title") or h.get("story_title") or "") + "\n"
                        + (h.get("comment_text") or h.get("story_text") or "")[:1200],
                "published_at": _iso(dt),
                "score": float(h.get("points") or 0),
            })
        if progress_callback:
            await progress_callback(len(items), 0, "hackernews")
        return items[:limit]


class YouTubeCollector(_HTTP):
    """YouTube: Data API v3 (нужен ключ)."""

    name = "youtube"
    platforms = ["youtube", "ютуб"]

    async def is_available(self) -> bool:
        from ..config import settings
        return bool(getattr(settings, "youtube_api_key", ""))

    async def collect(self, username: str, limit: int = 20, period_days: int = 30,
                      progress_callback=None, mode: str = "query") -> list[dict]:
        from ..config import settings
        key = getattr(settings, "youtube_api_key", "")
        data = await self._get_json("https://www.googleapis.com/youtube/v3/search", {
            "part": "snippet", "q": username, "order": "date", "type": "video",
            "maxResults": min(limit, 50), "key": key,
        })
        items: list[dict] = []
        for it in (data or {}).get("items", []):
            sn = it.get("snippet", {})
            vid = (it.get("id") or {}).get("videoId")
            if not vid:
                continue
            items.append({
                "id": vid, "post_id": vid, "platform": "youtube",
                "author": sn.get("channelTitle", ""),
                "url": f"https://www.youtube.com/watch?v={vid}",
                "text": (sn.get("title", "") + "\n" + sn.get("description", ""))[:1500],
                "published_at": _iso(_parse_iso(sn.get("publishedAt"))),
                "score": 0.0,
            })
        if progress_callback:
            await progress_callback(len(items), 0, "youtube")
        return items[:limit]


def _strip_html(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", " ", text or "").strip()


# Реестр коллекторов
from .websearch import SerperCollector, SerpApiCollector, BraveCollector
from .rss_collector import RssCollector
from .scraper import DomainScraper

scraper = DomainScraper()

_registry: list[BaseCollector] = [
    GitHubCollector(),
    RedditCollector(),
    BlueskyCollector(),
    MastodonCollector(),
    HackerNewsCollector(),
    VKCollector(),
    TwitterCollector(),
    YouTubeCollector(),
    RssCollector(),
    SerperCollector(),
    SerpApiCollector(),
    BraveCollector(),
]


def get_collectors() -> list[BaseCollector]:
    """Все зарегистрированные коллекторы."""
    return list(_registry)


def get_collector(platform: str) -> Optional[BaseCollector]:
    """Коллектор по имени платформы."""
    p = platform.lower()
    for c in _registry:
        if p == c.name or p in [x.lower() for x in c.platforms]:
            return c
    return None


async def collect_all(query: str, limit: int = 50, period_days: int = 30,
                      progress_callback: Optional[Callable] = None,
                      mode: str = "query") -> list[dict]:
    """Опросить все доступные коллекторы и объединить результаты.

    mode: "query" — глобальный поиск по фразе, "account" — по аккаунту,
          "tag" — по хештегу.
    """
    all_items: list[dict] = []
    seen: set = set()
    for collector in _registry:
        try:
            if not await collector.is_available():
                continue
            items = await collector.collect(query, limit=limit,
                                            period_days=period_days,
                                            progress_callback=progress_callback,
                                            mode=mode)
            for it in items:
                key = it.get("url") or f"{it.get('platform')}:{it.get('id')}"
                if key in seen:
                    continue
                seen.add(key)
                all_items.append(it)
        except Exception as ex:
            logger.warning("коллектор %s упал: %s", collector.name, ex)
    # без даты (веб-поиск) — считаем свежими, чтобы не вырезались лимитом
    all_items.sort(key=lambda x: x.get("published_at") or "9999", reverse=True)
    return all_items[:limit]
