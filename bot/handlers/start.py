"""Обработчики /start, /help и /cancel.

Главное меню бота Agrigat Ostin: текст справки + два вида клавиатур:
- ReplyKeyboard (MENU) — постоянные кнопки команд внизу экрана;
- InlineKeyboard (_start_menu) — быстрые действия под приветствием.
"""
from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

# Переиспользуем диалог мониторинга: кнопки меню сразу задают режим поиска,
# поэтому пользователю не нужно проходить шаг «выбор режима».
from .monitor import MODE_HINTS, MonitorState
from .catalog import cmd_catalog
from .history import cmd_history

router = Router()

# Главное меню: постоянная клавиатура с командами.
# Текст кнопки = команда: нажатие отправляет её как обычное сообщение.
MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="/find"), KeyboardButton(text="/web")],
        [KeyboardButton(text="/tag"), KeyboardButton(text="/monitor")],
        [KeyboardButton(text="/watch"), KeyboardButton(text="/catalog")],
        [KeyboardButton(text="/rss"), KeyboardButton(text="/scrape")],
        [KeyboardButton(text="/subscribe"), KeyboardButton(text="/help")],
    ],
    resize_keyboard=True,
)


def _start_menu() -> InlineKeyboardMarkup:
    """Инлайн-меню быстрых действий под приветствием.

    callback_data вида "menu:<действие>" обрабатывается ниже в process_menu.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔍 Глобальный поиск", callback_data="menu:find"),
            InlineKeyboardButton(text="#️⃣ Поиск по тегу", callback_data="menu:tag"),
        ],
        [
            InlineKeyboardButton(text="👤 Поиск по аккаунту", callback_data="menu:account"),
            InlineKeyboardButton(text="🌐 Веб-поиск", callback_data="menu:web"),
        ],
        [
            InlineKeyboardButton(text="📰 Каталог OSINT", callback_data="menu:catalog"),
            InlineKeyboardButton(text="📜 История запросов", callback_data="menu:history"),
        ],
        [InlineKeyboardButton(text="❓ Все команды", callback_data="menu:help")],
    ])


# Кнопка инлайн-меню → режим диалога мониторинга (см. monitor.MODE_HINTS).
MENU_MODES = {
    "find": "query",     # 🔍 глобальный поиск по фразе
    "tag": "tag",        # #️⃣ поиск по хештегу
    "account": "account" # 👤 поиск по аккаунту/нику
}


async def _send_help(message: Message):
    """Текст справки + главное меню (reply-клавиатура команд)."""
    # Сохраняем пользователя для дайджеста и статистики
    from ..db import repo
    try:
        await repo.upsert_user(message.from_user)
    except Exception:
        pass

    text = (
        "<b>Agrigat Ostin</b> — мониторинг публичных источников.\n\n"
        "<b>Поиск</b>\n"
        "/find &lt;запрос&gt; — глобальный поиск\n"
        "/tag &lt;тег&gt; — поиск по хештегу\n"
        "/web &lt;запрос&gt; — веб-поиск (Google)\n"
        "/monitor — выбор режима поиска\n\n"
        "<b>Слежение</b>\n"
        "/watch &lt;ник&gt; — наблюдать за аккаунтом\n"
        "/unwatch &lt;ник&gt; — прекратить наблюдение\n\n"
        "<b>Инструменты</b>\n"
        "/rss &lt;url&gt; — RSS-лента\n"
        "/scrape &lt;url&gt; — содержимое страницы\n"
        "/import — импорт файла БД (CSV/JSON/SQLite/XLSX)\n\n"
        "<b>Каталог</b>\n"
        "/catalog — список групп\n"
        "/bot &lt;группа&gt; — боты группы\n"
        "/info &lt;название&gt; — карточка бота\n"
        "/search &lt;запрос&gt; — поиск по каталогу\n\n"
        "<b>Сервис</b>\n"
        "/subscribe — ежедневный дайджест\n"
        "/unsubscribe — отключить дайджест\n"
        "/history — история запросов\n"
        "/report — последний отчёт\n"
        "/status — журнал задач\n"
        "/cancel — отменить действие"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=MENU)


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Приветствие: справка (reply-клавиатура) + инлайн-меню быстрых действий."""
    await _send_help(message)
    await message.answer("Быстрые действия 👇", reply_markup=_start_menu())


@router.callback_query(F.data.startswith("menu:"))
async def process_menu(callback: CallbackQuery, state: FSMContext):
    """Обработка нажатий инлайн-меню главного экрана."""
    action = (callback.data or "").split(":", 1)[-1]

    if action == "help":
        # Полный список команд тем же текстом, что и /help
        await _send_help(callback.message)

    elif action == "web":
        # Веб-поиск идёт отдельным обработчиком в tools.py — даём формат
        await callback.message.answer(
            "Формат: /web &lt;запрос&gt;\nПример: /web лучшие ноутбуки 2026",
            parse_mode="HTML",
        )

    elif action == "catalog":
        # Показываем группы каталога OSINT-ботов (тот же вывод, что /catalog)
        await cmd_catalog(callback.message)

    elif action == "history":
        # История последних запросов (тот же вывод, что /history)
        await cmd_history(callback.message)

    elif action in MENU_MODES:
        # Сразу переходим к вводу запроса, минуя шаг «выбор режима»
        mode = MENU_MODES[action]
        await state.update_data(mode=mode)
        await state.set_state(MonitorState.query)
        await callback.message.answer(MODE_HINTS.get(mode, MODE_HINTS["query"]))

    # Подтверждаем нажатие, чтобы у кнопки не «крутились часы»
    await callback.answer()


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Справка по командам."""
    await _send_help(message)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Отмена текущего диалога (поиск, подписка и т.д.)."""
    current = await state.get_state()
    if current is None:
        await message.answer("Нет активного действия.", reply_markup=MENU)
        return
    await state.clear()
    await message.answer("Отменено.", reply_markup=MENU)
