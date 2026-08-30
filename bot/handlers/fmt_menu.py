"""Меню выбора формата сохранения результатов поиска.

Вместо спама всеми файлами бот показывает сводку «что найдено» и даёт
кнопки: JSON / CSV / Markdown / PDF / График PNG. Файлы отправляются
только после выбора формата.
"""
import logging
import time

from aiogram import Router, F
from aiogram.types import (
    InlineKeyboardMarkup,
    CallbackQuery,
    Message,
    BufferedInputFile,
)

from ..reporting import generate_json, generate_csv, generate_markdown
from ..reporting.charts import generate_chart_png
from ..reporting.pdf import generate_pdf
from ..reporting.summary import PLATFORM_NAMES

logger = logging.getLogger(__name__)
router = Router()

_TTL = 30 * 60       # результаты живут полчаса
_MAX = 300           # максимум закэшированных пользователей
_PAGE = 15           # находок на одной странице списка

_FORMAT_ROWS = [
    [{"text": "📄 JSON", "callback_data": "fmt:json"},
     {"text": "📊 CSV", "callback_data": "fmt:csv"}],
    [{"text": "📑 Markdown", "callback_data": "fmt:md"},
     {"text": "📕 PDF", "callback_data": "fmt:pdf"}],
    [{"text": "📈 График PNG", "callback_data": "fmt:png"}],
]

_FMT_CHOICES = {"fmt:json", "fmt:csv", "fmt:md", "fmt:markdown",
                "fmt:pdf", "fmt:png", "fmt:close"}

# Результаты, ожидающие выбора формата: user_id -> (ts, title, items, stats)
_LAST: dict[int, tuple[float, str, list, dict]] = {}


def _store(user_id: int, title: str, items: list[dict], stats: dict):
    if len(_LAST) > _MAX:
        now = time.time()
        for uid in [k for k, (ts, *_) in _LAST.items() if now - ts > _TTL]:
            _LAST.pop(uid, None)
    _LAST[user_id] = (time.time(), title, items, stats)


def _load(user_id: int):
    entry = _LAST.get(user_id)
    if not entry:
        return None
    ts, title, items, stats = entry
    if time.time() - ts > _TTL:
        _LAST.pop(user_id, None)
        return None
    return title, items, stats


def _esc(text) -> str:
    return (str(text or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _result_kb(has_items: bool) -> InlineKeyboardMarkup:
    rows = [list(r) for r in _FORMAT_ROWS]
    if has_items:
        rows.append([{"text": "📋 Показать все находки",
                      "callback_data": "fmt:list"}])
    rows.append([{"text": "🚫 Не сохранять", "callback_data": "fmt:close"}])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_findings(message: Message, title: str,
                        items: list[dict], result: dict):
    """Сводка найденного + клавиатура выбора формата сохранения."""
    stats = result.get("stats", {})
    _store(message.from_user.id, title, items, stats)

    if not items:
        await message.answer(f"🕵️ <b>{_esc(title)}</b>\n\nНайдено: <b>0</b> совпадений")
        return

    lines = [f"🕵️ <b>{_esc(title)}</b>", "",
             f"Найдено: <b>{len(items)}</b> совпадений"]

    by_platform = stats.get("by_platform") or {}
    if by_platform:
        parts = [f"{PLATFORM_NAMES.get(p, p)}: {c}"
                 for p, c in sorted(by_platform.items(), key=lambda x: -x[1])[:6]]
        lines.append("Платформы: " + ", ".join(parts))

    sentiment = stats.get("sentiment") or {}
    if sentiment:
        lines.append(
            f"Тональность: 📈 {sentiment.get('positive', 0)} · "
            f"😐 {sentiment.get('neutral', 0)} · "
            f"📉 {sentiment.get('negative', 0)}")

    lines += ["", "<b>Топ находок:</b>"]
    for i, it in enumerate(items[:5], 1):
        url = it.get("url") or ""
        text = _esc((it.get("text") or "").replace("\n", " ")[:80])
        platform = _esc(it.get("platform") or "")
        head = f"{i}. [{platform}] "
        if url.startswith(("http", "magnet:")):
            lines.append(head + f"<a href=\"{url}\">{text}</a>")
        else:
            lines.append(head + text)

    if len(items) > 5:
        lines.append(f"… и ещё <b>{len(items) - 5}</b> — «📋 Показать все находки»")

    lines += ["", "Сохранить результат в формате 👇"]
    await message.answer("\n".join(lines), parse_mode="HTML",
                         disable_web_page_preview=True, reply_markup=_result_kb(True))


def _item_line(i: int, it: dict) -> str:
    url = it.get("url") or ""
    text = _esc((it.get("text") or "").replace("\n", " ")[:90])
    platform = _esc(it.get("platform") or "")
    line = f"{i}. [{platform}] "
    if url.startswith(("http", "magnet:")):
        line += f"<a href=\"{url}\">{text}</a>"
    else:
        line += text
    author = (it.get("author") or "").strip()
    if author:
        line += f"\n    👤 {_esc(author)[:60]}"
    return line


async def _send_chunk(message: Message, title: str, items: list[dict],
                      offset: int):
    chunk = items[offset:offset + _PAGE]
    lines = [f"📋 <b>{_esc(title)}</b>", ""]
    for i, it in enumerate(chunk, offset + 1):
        lines.append(_item_line(i, it))
    lines.append(f"\nПоказано <b>{min(offset + _PAGE, len(items))}</b> из <b>{len(items)}</b>")

    rows = []
    if offset + _PAGE < len(items):
        rows.append([{"text": f"Показать ещё ({len(items) - offset - _PAGE})",
                      "callback_data": f"fmt:more:{offset + _PAGE}"}])
    rows.append([{"text": "🚫 Закрыть", "callback_data": "fmt:closelist"}])
    kb = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

    await message.answer("\n".join(lines), parse_mode="HTML",
                         disable_web_page_preview=True, reply_markup=kb)


@router.callback_query(F.data == "fmt:closelist")
async def close_list(callback: CallbackQuery):
    """Закрыть список находок (кэш для меню форматов остаётся живым)."""
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "fmt:list")
async def show_list(callback: CallbackQuery):
    """Показать все находки постранично."""
    entry = _load(callback.from_user.id)
    if not entry:
        await callback.answer("Результат устарел — повторите поиск.", show_alert=True)
        return
    title, items, stats = entry
    if not items:
        await callback.answer("Ничего не найдено.")
        return
    await _send_chunk(callback.message, title, items, 0)
    await callback.answer()


@router.callback_query(F.data.startswith("fmt:more:"))
async def show_more(callback: CallbackQuery):
    """Следующая страница списка находок."""
    entry = _load(callback.from_user.id)
    if not entry:
        await callback.answer("Результат устарел — повторите поиск.", show_alert=True)
        return
    title, items, stats = entry
    try:
        offset = int((callback.data or "").split(":", 2)[-1])
    except (ValueError, IndexError):
        offset = 0
    await _send_chunk(callback.message, title, items, offset)
    await callback.answer()


@router.callback_query(F.data.in_(_FMT_CHOICES))
async def process_format(callback: CallbackQuery):
    choice = (callback.data or "").split(":", 1)[-1]
    entry = _load(callback.from_user.id)
    if not entry:
        await callback.answer("Результат устарел — повторите поиск.",
                              show_alert=True)
        return
    title, items, stats = entry

    if choice == "close":
        _LAST.pop(callback.from_user.id, None)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.answer("Ок, файлы не нужны.")
        return

    try:
        if choice == "json":
            await callback.message.answer_document(
                BufferedInputFile(generate_json(items).encode("utf-8"),
                                  filename="result.json"),
                caption=f"📄 JSON «{_esc(title)}»")
        elif choice == "csv":
            await callback.message.answer_document(
                BufferedInputFile(generate_csv(items).encode("utf-8"),
                                  filename="result.csv"),
                caption=f"📊 CSV «{_esc(title)}»")
        elif choice in ("md", "markdown"):
            await callback.message.answer_document(
                BufferedInputFile(generate_markdown(items).encode("utf-8"),
                                  filename="result.md"),
                caption=f"📑 Markdown «{_esc(title)}»")
        elif choice == "pdf":
            blob = generate_pdf(items, stats, title)
            if not blob:
                await callback.answer("PDF не собрался — попробуйте другой формат.",
                                      show_alert=True)
                return
            await callback.message.answer_document(
                BufferedInputFile(blob, filename="result.pdf"),
                caption=f"📕 PDF «{_esc(title)}»")
        elif choice == "png":
            blob = generate_chart_png(items, stats)
            if not blob:
                await callback.answer("График не построился.", show_alert=True)
                return
            await callback.message.answer_photo(
                BufferedInputFile(blob, filename="chart.png"),
                caption=f"📈 График «{_esc(title)}»")
        await callback.answer("Файл отправлен")
    except Exception as ex:
        logger.warning("не удалось отправить %s: %s", choice, ex)
        await callback.answer("Ошибка при отправке файла.", show_alert=True)