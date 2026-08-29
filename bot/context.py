"""Глобальный контекст."""
from typing import Optional

from aiogram import Bot

bot: Optional[Bot] = None
scheduler = None

__all__ = ["bot", "scheduler"]
