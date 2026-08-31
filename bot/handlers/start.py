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
from .monitor import QUERY_HINT, MonitorState, cmd_monitor
from .catalog import cmd_catalog
from .history import cmd_history
from .subscribe import cmd_subscribe

router = Router()

# Главное меню: постоянная reply-клавиатура с кнопками-действиями.
# Текст кнопки на русском, обработчик живёт в handlers/menu.py.
RU_BUTTONS = {
    "🔍 Поиск": "find",
    "#️⃣ По тегу": "tag",
    "👤 По аккаунту": "account",
    "🌐 Веб-поиск": "web",
    "📰 Каталог": "catalog",
    "📜 История": "history",
    "📡 Монитор": "monitor",
    "👁 Слежение": "watch",
    "📬 Дайджест": "subscribe",
    "❓ Помощь": "help",
}

MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔍 Поиск"), KeyboardButton(text="🌐 Веб-поиск")],
        [KeyboardButton(text="#️⃣ По тегу"), KeyboardButton(text="👤 По аккаунту")],
        [KeyboardButton(text="📡 Монитор"), KeyboardButton(text="👁 Слежение")],
        [KeyboardButton(text="📰 Каталог"), KeyboardButton(text="📜 История")],
        [KeyboardButton(text="📬 Дайджест"), KeyboardButton(text="❓ Помощь")],
    ],
    resize_keyboard=True,
)


def _start_menu() -> InlineKeyboardMarkup:
    """Инлайн-меню быстрых действий под приветствием.

    callback_data вида "menu:<действие>" обрабатывается ниже в process_menu.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔍 Пробив по фразе", callback_data="menu:find"),
            InlineKeyboardButton(text="#️⃣ Пробив по тегу", callback_data="menu:tag"),
        ],
        [
            InlineKeyboardButton(text="👤 Пробив по аккаунту", callback_data="menu:account"),
            InlineKeyboardButton(text="🌐 Пробив в сети", callback_data="menu:web"),
        ],
        [
            InlineKeyboardButton(text="📰 Каталог пробива", callback_data="menu:catalog"),
            InlineKeyboardButton(text="📜 История операций", callback_data="menu:history"),
        ],
        [InlineKeyboardButton(text="❓ Все команды", callback_data="menu:help")],
    ])


# Кнопка инлайн-меню → диалог поиска (см. monitor.QUERY_HINT).
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
        "🕵️ <b>AGRIGAT OSTIN</b> — пробив-терминал: мониторинг публичных "
        "источников, импорт баз данных, поиск по аккаунтам, никам и фразам.\n\n"
        "<b>🔍 Поиск</b>\n"
        "/find &lt;запрос&gt; — пробив по фразе (можно по полю: телефон:, инн:, паспорт:)\n"
        "/tag &lt;тег&gt; — пробив по хештегу\n"
        "/web &lt;запрос&gt; — пробив в сети (Google)\n"
        "/monitor — пробив: сразу вводите запрос (с префиксом поля при нужде)\n\n"
        "<b>👁 Слежение</b>\n"
        "/watch &lt;ник&gt; — следить за целью\n"
        "/unwatch &lt;ник&gt; — снять слежение\n\n"
        "<b>🧰 Инструменты</b>\n"
        "/import — импорт базы (CSV/JSON/SQLite/XLSX/SQL/ZIP/RAR/7z)\n"
        "/rss &lt;url&gt; — RSS-лента\n"
        "/scrape &lt;url&gt; — снять содержимое страницы\n\n"
        "<b>📰 Каталог</b>\n"
        "/catalog — список групп\n"
        "/bot &lt;группа&gt; — боты группы\n"
        "/info &lt;название&gt; — карточка бота\n"
        "/search &lt;запрос&gt; — поиск по каталогу\n\n"
        "<b>📬 Служба</b>\n"
        "/subscribe — ежедневный дайджест\n"
        "/unsubscribe — выключить дайджест\n"
        "/history — история операций\n"
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


async def dispatch_menu(message: Message, state: FSMContext, action: str):
    """Общий диспетчер действий меню (инлайн-кнопки и reply-клавиатура)."""
    if action == "help":
        await _send_help(message)

    elif action == "web":
        await message.answer(
            "Формат: /web &lt;запрос&gt;\nПример: /web лучшие ноутбуки 2026",
            parse_mode="HTML",
        )

    elif action == "catalog":
        await cmd_catalog(message)

    elif action == "history":
        await cmd_history(message)

    elif action == "monitor":
        await cmd_monitor(message, state)

    elif action == "watch":
        await message.answer(
            "Формат: /watch &lt;ник или ссылка&gt;\nПример: /watch @durov\n\n"
            "Бот проверяет аккаунт каждые 20 минут и пришлёт новые посты."
        )

    elif action == "subscribe":
        await cmd_subscribe(message, state)

    elif action in MENU_MODES:
        mode = MENU_MODES[action]
        await state.update_data(mode=mode)
        await state.set_state(MonitorState.query)
        await message.answer(QUERY_HINT)

    else:
        await message.answer(f"Неизвестное действие: {action}")


@router.callback_query(F.data.startswith("menu:"))
async def process_menu(callback: CallbackQuery, state: FSMContext):
    """Обработка нажатий инлайн-меню главного экрана."""
    action = (callback.data or "").split(":", 1)[-1]
    await dispatch_menu(callback.message, state, action)
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
