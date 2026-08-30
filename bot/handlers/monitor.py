"""Мониторинг: глобальный поиск, поиск по тегам и по аккаунту."""
import logging

from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..tasks import run_monitoring
from .fmt_menu import send_findings

router = Router()

MODE_TITLES = {
    "query": "Пробив по фразе",
    "tag": "Пробив по тегу",
    "account": "Пробив по аккаунту",
}

MODE_HINTS = {
    "query": "Отправьте цель для пробива по фразе:\n\n<code>нейросети для бизнеса</code>",
    "tag": "Отправьте тег (с # или без):\n\n<code>#криптовалюта</code>",
    "account": "Отправьте никнейм или ссылку на цель:\n\n<code>@durov</code> или <code>t.me/durov</code>",
}


class MonitorState(StatesGroup):
    """Состояния диалога мониторинга."""
    mode = State()
    query = State()
    tags = State()


def _mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕵️ Пробив по фразе", callback_data="mon:mode:query")],
        [InlineKeyboardButton(text="#️⃣ Пробив по тегу", callback_data="mon:mode:tag")],
        [InlineKeyboardButton(text="👤 Пробив по аккаунту", callback_data="mon:mode:account")],
    ])


async def _finish(message: Message, query: str, tags: list[str], mode: str):
    """Прогнать мониторинг и показать сводку + выбор формата сохранения."""
    tags_line = ", ".join(tags) if tags else "—"
    status = await message.answer(
        f"Цель: <b>{query}</b>\nТеги: {tags_line}\nСобираю данные…"
    )

    result = await run_monitoring(query, tags, mode=mode)

    if "error" in result:
        await status.edit_text(result["error"])
        return

    await status.delete()
    title = f"{MODE_TITLES.get(mode, 'Поиск')}: {query}"
    await send_findings(message, title, result["items"], result)
    from ..db import repo
    try:
        await repo.record_search(message.from_user.id, f"[{mode}] {query}",
                                 len(result["items"]))
        await repo.record_job(message.from_user.id, f"monitor:{mode}", result["stats"])
        await repo.record_report(message.from_user.id, result["summary"], result["stats"])
    except Exception as ex:
        logging.getLogger(__name__).warning("не записано в БД: %s", ex)


# ---------- Быстрые команды ----------

@router.message(Command("find"))
async def cmd_find(message: Message, command: CommandObject):
    """Глобальный поиск по запросу: /find запрос."""
    query = (command.args or "").strip()
    if not query:
        await message.answer("Формат: /find &lt;запрос&gt;\nПример: /find нейросети для бизнеса")
        return
    await _finish(message, query, tags=[query], mode="query")


@router.message(Command("tag"))
async def cmd_tag(message: Message, command: CommandObject):
    """Поиск по хештегу: /tag тег."""
    query = (command.args or "").strip().lstrip("#")
    if not query:
        await message.answer("Формат: /tag &lt;тег&gt;\nПример: /tag криптовалюта")
        return
    await _finish(message, query, tags=[query], mode="tag")


# ---------- Диалог /monitor ----------

@router.message(Command("monitor"))
async def cmd_monitor(message: Message, state: FSMContext):
    """Начало мониторинга: выбор режима."""
    await state.set_state(MonitorState.mode)
    await message.answer("Выберите режим пробива:", reply_markup=_mode_keyboard())


@router.callback_query(F.data.startswith("mon:mode:"), MonitorState.mode)
async def process_mode(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора режима."""
    mode = callback.data.split(":")[-1]
    await state.update_data(mode=mode)
    await state.set_state(MonitorState.query)
    await callback.message.edit_text(MODE_HINTS.get(mode, MODE_HINTS["query"]))
    await callback.answer()


@router.message(MonitorState.query)
async def process_query(message: Message, state: FSMContext):
    """Обработка поискового запроса."""
    query = (message.text or "").strip()
    if not query or query.startswith("/"):
        await message.answer("Запрос не распознан. Повторите.")
        return

    await state.update_data(query=query)
    await state.set_state(MonitorState.tags)
    await message.answer(
        "Отправьте теги для классификации через запятую:\n"
        "<code>криптовалюта, технологии</code>\n\n"
        "Или /skip — использовать сам запрос.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="mon:skiptags")],
        ]),
    )


@router.message(Command("skip"))
async def cmd_skip(message: Message, state: FSMContext):
    """Пропуск тегов в диалоге мониторинга."""
    current = await state.get_state()
    if current != MonitorState.tags:
        return
    data = await state.get_data()
    await state.clear()
    await _finish(message, data["query"], tags=[data["query"]],
                  mode=data.get("mode", "query"))


@router.callback_query(F.data == "mon:skiptags", MonitorState.tags)
async def skip_tags(callback: CallbackQuery, state: FSMContext):
    """Пропуск тегов — возьмём сам запрос."""
    data = await state.get_data()
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await _finish(callback.message, data["query"], tags=[data["query"]],
                  mode=data.get("mode", "query"))


@router.message(MonitorState.tags)
async def process_tags(message: Message, state: FSMContext):
    """Обработка тегов и запуск мониторинга."""
    tags = [t.strip() for t in (message.text or "").replace(",", "\n").split("\n") if t.strip()]

    if not tags:
        await message.answer("Теги не распознаны. Повторите или /skip.")
        return

    data = await state.get_data()
    await state.clear()
    await _finish(message, data["query"], tags=tags, mode=data.get("mode", "query"))
