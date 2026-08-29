"""Интерфейс коллектора данных."""
from abc import ABC, abstractmethod
from typing import Optional


class BaseCollector(ABC):
    """Базовый класс для коллекторов данных из соцсетей."""

    name: str = ""
    platforms: list[str] = []

    @abstractmethod
    async def is_available(self) -> bool:
        """Проверяет, настроен ли коллектор (API-ключ и т.д.)."""

    @abstractmethod
    async def collect(
        self,
        username: str,
        limit: int = 20,
        period_days: int = 30,
        progress_callback=None,
        mode: str = "query",
    ) -> list[dict]:
        """
        Собрать данные.

        Args:
            username: Поисковый запрос, никнейм или тег
            limit: Максимум постов
            period_days: Период сбора
            progress_callback: Callback для прогресса (count, total, platform)
            mode: Режим поиска — "query" (глобальный), "account" (по аккаунту), "tag" (по хештегу)

        Returns:
            Список постов: [{id, text, url, author, published_at, platform}]
        """

    async def validate_username(self, username: str) -> bool:
        """Проверяет формат никнейма."""
        return len(username) > 1 and len(username) < 50
