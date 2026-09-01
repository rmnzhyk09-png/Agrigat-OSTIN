"""Обработчики /start, /help и /cancel.

Главное меню бота Agrigat Ostin: текст справки + два вида клавиатур:
- ReplyKeyboard (MENU) — постоянные кнопки команд внизу экрана;
- InlineKeyboard (_start_menu) — быстрые действия под приветствием.

Кнопки «живые»: «Веб-поиск», «Blackbird», «Профиль», «Слежение» не печатают
формат команды, а сразу спрашивают недостающее (запрос/ник) и выполняют действие.
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
from aiogram.fsm.state import State, StatesGroup

# Переиспользуем диалог мониторинга: кнопки меню сразу задают режим поиска,
# поэтому пользователю не нужно проходить шаг «выбор режима».
from .monitor import QUERY_HINT, MonitorState, cmd_monitor, field_chips, run_blackbird
from .catalog import cmd_catalog
from .history import cmd_history
from .subscribe import cmd_subscribe
from .tools import run_web
from .watch import run_watch
from .profile import run_profile
from .import_db import cmd_import

router = Router()

# Главное меню: постоянная reply-клавиатура с кнопками-действиями.
# Текст кнопки на русском, обработчик живёт в handlers/menu.py.
RU_BUTTONS = {
    "🔎 Пробив": "find",
    "🌐 Веб-поиск": "web",
    "📥 Импорт": "import",
    "👤 Профиль": "profile",
    "🕊 Blackbird": "blackbird",
    "📡 Монитор": "monitor",
    "👁 Слежение": "watch",
    "📬 Дайджест": "subscribe",
    "📰 Каталог": "catalog",
    "📜 История": "history",
    "❓ Помощь": "help",
}

MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔎 Пробив"), KeyboardButton(text="🌐 Веб-поиск")],
        [KeyboardButton(text="📥 Импорт"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🕊 Blackbird"), KeyboardButton(text="📡 Монитор")],
        [KeyboardButton(text="👁 Слежение"), KeyboardButton(text="📬 Дайджест")],
        [KeyboardButton(text="📰 Каталог"), KeyboardButton(text="📜 История")],
        [KeyboardButton(text="❓ Помощь")],
    ],
    resize_keyboard=True,
)


class Capture(StatesGroup):
    """Ожидание одного значения от «живой» кнопки (запрос/ник)."""
    value = State()


# Подсказки «живых» кнопок: что отправить пользователю.
CAPTURE_HINTS = {
    "web": "🌐 <b>Веб-поиск</b>: отправьте запрос одним сообщением.",
    "profile": "👤 <b>Профиль</b>: отправьте ФИО, телефон или email.",
    "blackbird": "🕊 <b>Blackbird</b>: отправьте ник или email для поиска аккаунтов.",
    "watch": "👁 <b>Слежение</b>: отправьте ник или ссылку (пример: @durov).",
}


def _start_menu() -> InlineKeyboardMarkup:
    """Инлайн-меню быстрых действий под приветствием.

    callback_data вида "menu:<действие>" обрабатывается ниже в process_menu.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔎 Пробив", callback_data="menu:find"),
            InlineKeyboardButton(text="🌐 Веб-поиск", callback_data="menu:web"),
        ],
        [
            InlineKeyboardButton(text="📥 Импорт БД", callback_data="menu:import"),
            InlineKeyboardButton(text="👤 Профиль", callback_data="menu:profile"),
        ],
        [
            InlineKeyboardButton(text="🕊 Blackbird", callback_data="menu:blackbird"),
            InlineKeyboardButton(text="📡 Монитор", callback_data="menu:monitor"),
        ],
        [
            InlineKeyboardButton(text="📰 Каталог", callback_data="menu:catalog"),
            InlineKeyboardButton(text="❓ Все команды", callback_data="menu:help"),
        ],
    ])


# Кнопка инлайн-меню → диалог поиска (см. monitor.QUERY_HINT).
MENU_MODES = {
    "find": "query",     # 🔎 глобальный поиск по фразе
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
        "<b>🔎 Поиск</b>\n"
        "/find &lt;запрос&gt; — пробив по фразе (можно по полю: телефон:, инн:, паспорт:)\n"
        "/tag &lt;тег&gt; — пробив по хештегу\n"
        "/web &lt;запрос&gt; — пробив в сети (Google)\n"
        "/monitor — пробив: сразу вводите запрос (кнопки полей внизу)\n"
        "/blackbird — аккаунты по нику/email (OSINT)\n"
        "/profile &lt;ФИО/телефон&gt; — карточка профиля из импортированных БД\n\n"
        "<b>👁 Слежение</b>\n"
        "/watch &lt;ник&gt; — следить за целью\n"
        "/unwatch &lt;ник&gt; — снять слежение\n\n"
        "<b>🧰 Импорт</b>\n"
        "/import — файлом (CSV/JSON/SQLite/XLSX/SQL/ZIP/RAR/7z)\n"
        "/import_url &lt;url&gt; — по прямой ссылке (обходит лимит 20 МБ)\n\n"
        "<b>📰 Каталог</b>\n"
        "/catalog — список групп\n"
        "/search &lt;запрос&gt; — поиск по каталогу\n\n"
        "<b>📬 Служба</b>\n"
        "/subscribe — ежедневный дайджест\n"
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

    elif action == "import":
        await cmd_import(message)

    elif action == "catalog":
        await cmd_catalog(message)

    elif action == "history":
        await cmd_history(message)

    elif action == "monitor":
        await cmd_monitor(message, state)

    elif action == "subscribe":
        await cmd_subscribe(message, state)

    elif action in CAPTURE_HINTS:
        # «Живая кнопка»: спрашиваем значение, затем выполняем действие.
        await state.update_data(intent=action)
        await state.set_state(Capture.value)
        await message.answer(CAPTURE_HINTS[action], parse_mode="HTML")

    elif action in MENU_MODES:
        mode = MENU_MODES[action]
        await state.update_data(mode=mode)
        await state.set_state(MonitorState.query)
        await message.answer(QUERY_HINT, reply_markup=field_chips())

    else:
        await message.answer(f"Неизвестное действие: {action}")


@router.message(Capture.value)
async def process_capture(message: Message, state: FSMContext):
    """Значение для «живой» кнопки → выполнение действия."""
    text = (message.text or "").strip()
    data = await state.get_data()
    intent = data.get("intent", "")
    await state.clear()

    if not text or text.startswith("/"):
        await message.answer("Отправьте текст или /cancel.")
        return

    if intent == "web":
        await run_web(message, text)
    elif intent == "profile":
        await run_profile(message, text)
    elif intent == "blackbird":
        await run_blackbird(message, text)
    elif intent == "watch":
        await run_watch(message, text)
    else:
        await message.answer("Неизвестное действие.")


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
