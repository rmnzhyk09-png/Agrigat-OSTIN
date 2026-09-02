"""Админ-команды владельца бота.

/dbreset — полная очистка импортированных БД перед новым заливом данных.
Доступна только владельцу (BOT_OWNER_ID в .env); требует двойного
подтверждения, т.к. действие необратимо.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from ..config import settings
from ..services.db_reset import format_report, wipe_database

logger = logging.getLogger(__name__)
router = Router()

CONFIRM_YES = "dbreset:yes"
CONFIRM_NO = "dbreset:no"

# Флаг «очистка уже идёт» по user_id — защита от случайного двойного запуска.
_running: set[int] = set()


def _is_owner(user_id: int) -> bool:
    return bool(settings.owner_id) and user_id == settings.owner_id


@router.message(Command("dbreset"))
async def db_reset_cmd(message: Message):
    user_id = message.from_user.id
    if not settings.owner_id:
        await message.answer(
            "Владелец не настроен: задайте <code>BOT_OWNER_ID</code> в "
            "<code>.env</code> (ваш Telegram user id) и перезапустите бота."
        )
        return
    if not _is_owner(user_id):
        await message.answer("Эта команда доступна только владельцу бота.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Да, очистить всё",
                              callback_data=CONFIRM_YES)],
        [InlineKeyboardButton(text="Отмена", callback_data=CONFIRM_NO)],
    ])
    await message.answer(
        "<b>Полная очистка импортированных баз</b>\n\n"
        "Будут удалены <b>безвозвратно</b>:\n"
        "• записи, разделы, факты импорта и профили людей\n"
        "  (локальное зеркало + Supabase)\n"
        "• файлы в бакете Supabase Storage (<code>SUPABASE_BUCKET</code>)\n\n"
        "После очистки можно заливать данные заново — дубли от прошлых "
        "заливов мешать не будут.\n\n"
        "Подтвердите действие:",
        reply_markup=kb,
    )


@router.callback_query(F.data.in_({CONFIRM_YES, CONFIRM_NO}))
async def confirm_reset(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not _is_owner(user_id):
        await callback.answer("Команда доступна только владельцу.",
                              show_alert=True)
        return

    if callback.data == CONFIRM_NO:
        await callback.answer()
        await callback.message.edit_text("Отменено. Данные не тронуты.")
        return

    if user_id in _running:
        await callback.answer("Очистка уже выполняется, подождите…",
                              show_alert=True)
        return

    _running.add(user_id)
    try:
        await callback.answer("Начинаю очистку…")
        report = await wipe_database()
        try:
            await callback.message.edit_text(format_report(report))
        except Exception:
            await callback.message.answer(format_report(report))
    except Exception as ex:
        logger.exception("dbreset: сбой очистки")
        await callback.message.edit_text(f"Ошибка при очистке: {ex}")
    finally:
        _running.discard(user_id)