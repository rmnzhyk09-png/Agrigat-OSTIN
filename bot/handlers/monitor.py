"""Мониторинг: глобальный поиск, поиск по тегам и по аккаунту.

Вместо выбора «режима пробива» пользователь сразу пишет, по какому полю искать,
и добавляет префикс в запросе:
    /find имя:Иванов
    /find телефон:9001234567
    /find инн:7701234567
    /find паспорт:4512
    /find email:ivan@mail.ru
    /find авто:А123БВ77
Без префикса срабатывает автоопределение (ищем по всему).
"""
import logging
import asyncio

from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..tasks import run_monitoring
from ..dbimport.query import parse_search_field
from ..services.blackbird import search_blackbird
from ..services import himera
from .fmt_menu import send_findings

router = Router()

MODE_TITLES = {
    "query": "Пробив по фразе",
    "tag": "Пробив по тегу",
    "account": "Пробив по аккаунту",
}

FIELD_TITLES = {
    "name": "📛 Имя / ФИО",
    "phone": "📞 Телефон",
    "email": "✉️ Email",
    "inn": "🆔 ИНН",
    "passport": "🪪 Паспорт",
    "snils": "🧾 СНИЛС",
    "auto": "🚗 Госномер авто",
}

# Кнопки-«чипы» поля поиска (показываются под подсказкой).
FIELD_CHIP_ORDER = [
    ("name", "email"),
    ("phone", "inn"),
    ("passport", "auto"),
]

# Быстрые теги для классификации — выбор вместо свободного ввода.
QUICK_TAGS = ["Финансы", "ИТ", "Авто", "Люди", "Долги", "Новости"]
TAG_EMOJI = {
    "Финансы": "💸",
    "ИТ": "🤖",
    "Авто": "🚗",
    "Люди": "🧑",
    "Долги": "⚖️",
    "Новости": "🌍",
}


def field_chips() -> InlineKeyboardMarkup:
    """Клавиатура быстрого выбора поля поиска."""
    rows = [
        [InlineKeyboardButton(text=FIELD_TITLES[f], callback_data=f"qm:field:{f}")
         for f in pair]
        for pair in FIELD_CHIP_ORDER
    ]
    rows.append([InlineKeyboardButton(text="🔎 Глобальный поиск",
                                      callback_data="qm:field:")])
    rows.append([
        InlineKeyboardButton(text="❌ Отмена", callback_data="mon:cancel"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tags_chips() -> InlineKeyboardMarkup:
    """Клавиатура быстрых тегов + пропуск."""
    rows = []
    for i in range(0, len(QUICK_TAGS), 2):
        pair = QUICK_TAGS[i:i + 2]
        rows.append([
            InlineKeyboardButton(text=f"{TAG_EMOJI[t]} {t}",
                                 callback_data=f"qm:tag:{t}")
            for t in pair
        ])
    rows.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="mon:backfield"),
        InlineKeyboardButton(text="🚫 Пропустить", callback_data="mon:skiptags"),
    ])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="mon:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

QUERY_HINT = (
    "Отправьте цель поиска. Можно сразу указать, по какому полю искать:\n\n"
    "• <code>имя:Иванов</code> или <code>фио:Петров</code>\n"
    "• <code>телефон:9001234567</code> или <code>тел:+7 900 123-45-67</code>\n"
    "• <code>инн:7701234567</code>\n"
    "• <code>паспорт:4512345678</code>\n"
    "• <code>снилс:12345678901</code>\n"
    "• <code>email:ivan@mail.ru</code>\n"
    "• <code>авто:А123БВ77</code>\n\n"
    "Без префикса — обычный глобальный поиск:\n"
    "<code>нейросети для бизнеса</code>"
)


class MonitorState(StatesGroup):
    """Состояния диалога мониторинга."""
    query = State()
    fieldvalue = State()
    tags = State()


def _finish_title(field: str, default: str, query: str) -> str:
    if field:
        return f"{FIELD_TITLES.get(field, 'Поиск')}: {query}"
    return f"{MODE_TITLES.get(default, 'Поиск')}: {query}"


async def _finish(message: Message, query: str, tags: list[str],
                  mode: str = "query", field: str = ""):
    """Прогнать мониторинг и показать сводку + выбор формата сохранения."""
    tags_line = ", ".join(tags) if tags else "—"
    status = await message.answer(
        f"Цель: <b>{query}</b>\nТеги: {tags_line}\nСобираю данные…"
    )

    result = await run_monitoring(query, tags, mode=mode, field=field)

    if "error" in result:
        await status.edit_text(result["error"])
        return

    await status.delete()
    title = _finish_title(field, mode, query)
    # После поиска — отмечаем специализированный (Himera) пробив и остаток пакета.
    used = himera.himera_budget_used()
    if field and used > 0:
        left = himera.himera_budget_left()
        await message.answer(
            f"🧾 Himera: потрачено запросов <b>{used}</b>/<b>{used + left}</b>"
        )
    await send_findings(message, title, result["items"], result)
    from ..db import repo
    try:
        tag = field or f"[{mode}]"
        await repo.record_search(message.from_user.id, f"[{tag}] {query}",
                                 len(result["items"]))
        await repo.record_job(message.from_user.id, f"monitor:{tag}", result["stats"])
        await repo.record_report(message.from_user.id, result["summary"], result["stats"])
    except Exception as ex:
        logging.getLogger(__name__).warning("не записано в БД: %s", ex)


# ---------- Быстрые команды ----------

@router.message(Command("find"))
async def cmd_find(message: Message, command: CommandObject):
    """Глобальный поиск по запросу: /find [поле:значение]."""
    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            "Формат: /find &lt;запрос&gt;\n\n"
            "Можно указать поле для точного поиска:\n"
            "имя:Иванов · телефон:9001234567 · инн:7701234567 · "
            "паспорт:4512 · email:ivan@mail.ru · авто:А123БВ77\n\n"
            "Пример: <code>/find телефон:9001234567</code>"
        )
        return
    field, value = parse_search_field(raw)
    await _finish(message, value, tags=[value], mode="query", field=field)


@router.message(Command("tag"))
async def cmd_tag(message: Message, command: CommandObject):
    """Поиск по хештегу: /tag тег."""
    query = (command.args or "").strip().lstrip("#")
    if not query:
        await message.answer("Формат: /tag &lt;тег&gt;\nПример: /tag криптовалюта")
        return
    await _finish(message, query, tags=[query], mode="tag")


async def run_blackbird(message: Message, raw: str):
    """OSINT-поиск аккаунтов Blackbird (/blackbird и кнопка меню)."""
    raw = (raw or "").strip()
    if not raw:
        await message.answer(
            "Формат: /blackbird &lt;ник или email&gt;\n"
            "Пример: <code>/blackbird johndoe</code>\n\n"
            "*Нужен BLACKBIRD_DIR (путь к папке Blackbird на сервере)."
        )
        return
    status = await message.answer(f"🕊 Blackbird: ищу аккаунты «{raw}»… "
                                  "(может занять до минуты)")
    # search_blackbird — синхронный (subprocess), иначе заморозит event loop
    items = await asyncio.to_thread(search_blackbird, raw, "@" in raw, 40)
    await status.delete()
    if not items:
        await message.answer(f"🕊 <b>Blackbird: {raw}</b>\n\nНичего не найдено "
                             "(или Blackbird не настроен).")
        return
    stats = {"total": len(items), "by_platform": {}, "sentiment": {}}
    await send_findings(message, f"🕊 Blackbird: {raw}", items, {"stats": stats})


@router.message(Command("blackbird"))
async def cmd_blackbird(message: Message, command: CommandObject):
    """Blackbird: OSINT-поиск аккаунтов по нику/email (/bb ник)."""
    await run_blackbird(message, (command.args or ""))


# ---------- Диалог /monitor ----------

@router.message(Command("monitor"))
async def cmd_monitor(message: Message, state: FSMContext):
    """Начало мониторинга — сразу запрос поиска (без выбора режима)."""
    await state.set_state(MonitorState.query)
    await message.answer(QUERY_HINT, reply_markup=field_chips())


async def _ask_tags(message: Message, state: FSMContext):
    """Переход к шагу «теги»: чипы быстрых тегов + Пропустить."""
    data = await state.get_data()
    await state.update_data(
        query=data.get("query", ""),
        field=data.get("field", ""),
        mode=data.get("mode", "query"),
    )
    await state.set_state(MonitorState.tags)
    await message.answer(
        "Теги для классификации (быстрый выбор 👇 либо отправьте свои "
        "через запятую):\n<code>криптовалюта, технологии</code>",
        reply_markup=tags_chips(),
    )


@router.callback_query(F.data.startswith("qm:field:"))
async def pick_field(callback: CallbackQuery, state: FSMContext):
    """Выбор поля поиска чипом (ФИО/телефон/email/ИНН/паспорт/авто)."""
    field = (callback.data or "").split(":", 2)[-1]
    await state.update_data(field=field)
    await state.set_state(MonitorState.fieldvalue)
    await callback.answer()
    if field:
        title = FIELD_TITLES.get(field, "Поиск")
        await callback.message.answer(
            f"Отправьте значение для «{title}»:\n"
            f"<code>{field}:…</code>"
        )
    else:
        await callback.message.answer("Отправьте запрос для глобального поиска:")


@router.message(MonitorState.fieldvalue)
async def process_field_value(message: Message, state: FSMContext):
    """Значение после выбора поля чипом."""
    if await _dispatch_button_text(message, state):
        return
    value = (message.text or "").strip()
    if not value or value.startswith("/"):
        await message.answer("Запрос не распознан. Отправьте текст или /cancel.")
        return
    data = await state.get_data()
    await state.update_data(query=value, field=data.get("field", ""),
                            mode=data.get("mode", "query"))
    await _ask_tags(message, state)


@router.message(MonitorState.query)
async def process_query(message: Message, state: FSMContext):
    """Обработка поискового запроса (с префиксом поля или свободного)."""
    if await _dispatch_button_text(message, state):
        return
    query = (message.text or "").strip()
    if not query or query.startswith("/"):
        await message.answer("Запрос не распознан. Повторите.")
        return

    field, value = parse_search_field(query)
    data = await state.get_data()
    mode = data.get("mode", "query")
    await state.update_data(query=value, field=field, mode=mode)
    await _ask_tags(message, state)


@router.callback_query(F.data.startswith("qm:tag:"))
async def pick_quick_tag(callback: CallbackQuery, state: FSMContext):
    """Быстрый тег из чипов — сразу запуск мониторинга."""
    label = (callback.data or "").split(":", 2)[-1]
    data = await state.get_data()
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer()
    await _finish(callback.message, data["query"], tags=[label],
                  mode=data.get("mode", "query"), field=data.get("field", ""))


@router.message(Command("skip"))
async def cmd_skip(message: Message, state: FSMContext):
    """Пропуск тегов в диалоге мониторинга."""
    current = await state.get_state()
    if current != MonitorState.tags:
        return
    data = await state.get_data()
    await state.clear()
    await _finish(message, data["query"], tags=[data["query"]],
                  mode=data.get("mode", "query"), field=data.get("field", ""))


@router.callback_query(F.data == "mon:skiptags", MonitorState.tags)
async def skip_tags(callback: CallbackQuery, state: FSMContext):
    """Пропуск тегов — возьмём сам запрос."""
    data = await state.get_data()
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer()
    await _finish(callback.message, data["query"], tags=[data["query"]],
                  mode=data.get("mode", "query"), field=data.get("field", ""))


@router.message(MonitorState.tags)
async def process_tags(message: Message, state: FSMContext):
    """Обработка свободных тегов и запуск мониторинга."""
    if await _dispatch_button_text(message, state):
        return
    tags = [t.strip() for t in (message.text or "").replace(",", "\n").split("\n") if t.strip()]

    if not tags:
        await message.answer("Теги не распознаны. Повторите или /skip.")
        return

    data = await state.get_data()
    await state.clear()
    await _finish(message, data["query"], tags=tags,
                  mode=data.get("mode", "query"), field=data.get("field", ""))


# ---------- Навигация: назад / отмена ----------

async def _cancel_dialog(message: Message, state: FSMContext):
    """Отмена диалога — чистим FSM и открываем главное меню."""
    await state.clear()
    from .start import _send_help
    await _send_help(message)


async def _dispatch_button_text(message: Message, state: FSMContext) -> bool:
    """Если сообщение — текст reply-кнопки главного меню, не даём ему стать
    ошибочным запросом/тегом: чистим FSM и выполняем кнопку. Возвращает True."""
    from .start import RU_BUTTONS, dispatch_menu
    text = (message.text or "").strip()
    if text in RU_BUTTONS:
        await state.clear()
        await dispatch_menu(message, state, RU_BUTTONS[text])
        return True
    return False


@router.callback_query(F.data == "mon:cancel")
async def cancel_monitor(callback: CallbackQuery, state: FSMContext):
    """Отмена мониторинга — чистим состояние и возвращаем меню."""
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _cancel_dialog(callback.message, state)
    await callback.answer("Отменено.")


@router.callback_query(F.data == "mon:backfield")
async def back_to_field(callback: CallbackQuery, state: FSMContext):
    """Назад с шага «теги» на шаг «запрос»."""
    await state.set_state(MonitorState.query)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("Повторите запрос:", reply_markup=field_chips())
    await callback.answer()
