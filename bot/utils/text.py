"""Утилиты для работы с текстом."""
import re


def escape_html(text: str) -> str:
    """Экранировать HTML."""
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse_tags(text: str) -> list[str]:
    """Разобрать теги из текста."""
    return [t.strip() for t in text.replace(",", "\n").split("\n") if t.strip() and t.strip() != "#"]


def parse_batch_input(text: str) -> list[str]:
    """Разобрать никнеймы из текста."""
    return [line.strip() for line in text.split("\n") if line.strip()]
