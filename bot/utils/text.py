"""Утилиты для работы с текстом."""
import html
import re


def escape_html(text) -> str:
    """Экранировать HTML (и в тексте, и внутри атрибутов типа href="...").

    Экранирует & < > " ', чтобы пользовательский/внешний контент не ломал
    разметку parse_mode="HTML" и не инжектил теги.
    """
    if text is None:
        return ""
    return html.escape(str(text), quote=True)


def parse_tags(text: str) -> list[str]:
    """Разобрать теги из текста."""
    return [t.strip() for t in text.replace(",", "\n").split("\n") if t.strip() and t.strip() != "#"]


def parse_batch_input(text: str) -> list[str]:
    """Разобрать никнеймы из текста."""
    return [line.strip() for line in text.split("\n") if line.strip()]
