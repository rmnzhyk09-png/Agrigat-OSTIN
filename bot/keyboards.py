"""Инлайн-клавиатуры."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def inline_keyboard(rows: list[list[dict]]) -> InlineKeyboardMarkup:
    """Создать инлайн-клавиатуру."""
    keyboard = []
    for row in rows:
        keyboard.append([
            InlineKeyboardButton(text=b["text"], callback_data=b.get("callback_data", ""))
            for b in row
        ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def single_button(text: str, callback_data: str) -> InlineKeyboardMarkup:
    """Одна кнопка."""
    return inline_keyboard([[{"text": text, "callback_data": callback_data}]])
