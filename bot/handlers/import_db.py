"""Импорт файла БД: /import (по чату ≤20 МБ) и /import_url (по прямой ссылке).

Принимает документ (.db/.sqlite/.sqlite3/.csv/.json/.xlsx/.sql/.zip/.rar/.7z),
анализирует записи, раскладывает по категориям (разделам) и сохраняет в
собственную БД (Supabase). При обнаружении новой категории автоматически
создаётся новый раздел.

Крупные файлы (больше лимита Telegram на скачивание 20 МБ) лучше прислать
ссылкой через /import_url — бот заберёт файл сам, без ограничения Telegram,
и при желании сохранит копию в Supabase Storage.
"""
import logging
from pathlib import Path

import httpx
from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..config import settings
from ..dbimport.parser import SUPPORTED_EXTENSIONS
from ..dbimport.store import import_database_file

logger = logging.getLogger(__name__)
router = Router()

_ALLOWED = SUPPORTED_EXTENSIONS

# Лимит скачивания по чату (Telegram не даёт боту тянуть больше) — ~20 МБ.
CHAT_DOWNLOAD_LIMIT = 20 * 1024 * 1024

HELP_TEXT = (
    "<b>Импорт базы данных</b>\n\n"
    "Пришлите файл БД — скрипт разберёт записи, определит категорию каждого "
    "сообщения и сохранит всё в собственную БД (Supabase). Найденная новая "
    "категория автоматически создаёт новый раздел.\n"
    "Из записей с ФИО/телефоном автоматически извлекаются <b>профили людей</b> — "
    "их можно искать командой <code>/profile Иванов</code>.\n\n"
    "<b>Поддерживаются:</b> "
    "SQLite (.db/.sqlite3), SQL-дампы (.sql), CSV, JSON (включая Telegram export), "
    "Excel (.xlsx), текст (.txt)\n"
    "🖅 Архивы: ZIP, RAR и 7z — внутри разбираются БД, CSV, Excel, txt и торренты "
    "(в т.ч. вложенные архивы). Лимиты распаковки: 1000 файлов / 1 ГБ "
    "(меняются переменными MAX_ARCHIVE_FILES и MAX_ARCHIVE_TOTAL_MB)\n"
    "🧲 Торренты (.torrent): имя, файлы и размеры раздачи, трекеры, "
    "SHA1 (info_hash) и magnet-ссылка\n\n"
    "<b>Про размер файла:</b>\n"
    "Через чат Telegram бот может скачать файл <b>не больше ~20 МБ</b>. "
    "Если файл больше — положите его на прямой хостинг и пришлите ссылку "
    "командой:\n"
    "<code>/import_url https://example.com/big.zip</code>\n"
    "Так бот заберёт файл сам, без лимита Telegram, и при желании сохранит "
    "копию в Supabase Storage (бакет <code>SUPABASE_BUCKET</code>, по умолчанию "
    "<code>imports</code>).\n\n"
    "<b>Защита от дублей:</b> если запись с такими данными уже есть в базе, "
    "она пропускается; база дополняется только новыми записями.\n\n"
    "<b>Умный разбор:</b> если в таблице распознаются ФИО, телефоны или email, "
    "файл автоматически разбирается на «карточки» с полями, и эти контакты "
    "находятся обычным поиском. Раскладка (колонки-поля или «поле: значение» "
    "блоками) и типы полей определяются самим ботом.\n\n"
    "<b>Формат данных в файле:</b>\n"
    "• текст записи — колонка <code>text / message / content</code>\n"
    "• автор — <code>author / from / user</code>\n"
    "• дата — <code>date / created_at</code>\n"
    "• категория — колонка <code>категория / раздел / субъект</code>;\n"
    "    если её нет, раздел создаётся из <b>названия столбца</b>, где лежит текст\n\n"
    "После загрузки бот пришлёт отчёт: сколько записей, какие разделы найдены "
    "и какие созданы заново."
)


@router.message(F.text == "/import")
async def cmd_import(message: Message):
    """Справка по импорту БД."""
    await message.answer(HELP_TEXT, parse_mode="HTML")


@router.message(F.document)
async def on_document(message: Message, bot: Bot):
    """Получен файл БД — анализируем и сохраняем."""
    document = message.document
    filename = document.file_name or "upload.bin"
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED:
        await message.answer(
            f"Формат <code>{ext}</code> не поддерживается. Допустимые: "
            f"{', '.join(sorted(_ALLOWED))}\n\n/import — подробности.",
            parse_mode="HTML",
        )
        return

    # Telegram не даёт боту скачивать файлы больше ~20 МБ — предупредим заранее
    # вместо невнятной ошибки «file is too big».
    if document.file_size and document.file_size > CHAT_DOWNLOAD_LIMIT:
        await message.answer(
            f"Файл <b>{_esc(filename)}</b> ({document.file_size / 1024 / 1024:.1f} МБ) "
            f"больше лимита скачивания в чате (~20 МБ).\n\n"
            f"Положите его на прямой хостинг и пришлите ссылку:\n"
            f"<code>/import_url https://example.com/{_esc(filename)}</code>",
            parse_mode="HTML",
        )
        return

    uploads = Path(settings.upload_dir)
    uploads.mkdir(parents=True, exist_ok=True)
    dest = uploads / f"{message.from_user.id}_{message.message_id}{ext}"

    status = await message.answer("🕵️ Вскрываю файл и раскладываю по разделам…")
    try:
        await bot.download(document, destination=str(dest))
        if not dest.exists() or dest.stat().st_size == 0:
            raise IOError("файл не скачался")
    except Exception as ex:
        logger.warning("download error: %s", ex)
        await status.edit_text(f"Не удалось скачать файл: {ex}"
                               + ("\n\nПопробуйте /import_url со ссылкой на файл."
                                  if ex else ""))
        return

    await _run_import(status, dest, filename)


@router.message(Command("import_url"))
async def cmd_import_url(message: Message, command: CommandObject):
    """Импорт по прямой ссылке (обходит лимит Telegram в 20 МБ)."""
    url = (command.args or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        await message.answer(
            "Формат: /import_url &lt;прямая ссылка на файл&gt;\n"
            "Пример: <code>/import_url https://example.com/big.zip</code>\n\n"
            "Так бот заберёт файл сам, без лимита Telegram на 20 МБ.",
        )
        return

    # имя из ссылки + расширение
    import urllib.parse
    _fn = urllib.parse.unquote(Path(url.split("?", 1)[0]).name) or "download.bin"
    _fn = _fn.replace("/", "_")[:200]
    ext = Path(_fn).suffix.lower()
    if ext not in _ALLOWED:
        await message.answer(
            f"Формат <code>{ext}</code> не поддерживается. Допустимые: "
            f"{', '.join(sorted(_ALLOWED))}",
            parse_mode="HTML",
        )
        return

    uploads = Path(settings.upload_dir)
    uploads.mkdir(parents=True, exist_ok=True)
    dest = uploads / f"{message.from_user.id}_{message.message_id}{ext}"

    status = await message.answer("⬇️ Скачиваю файл по ссылке…")
    ok, msg = await _download_from_url(url, dest)
    if not ok:
        await status.edit_text(f"Не удалось скачать по ссылке: {msg}")
        return
    await status.edit_text(f"✅ Скачано ({dest.stat().st_size / 1024 / 1024:.1f} МБ). "
                           "Вскрываю и раскладываю по разделам…")

    await _run_import(status, dest, _fn, source_url=url)


async def _download_from_url(url: str, dest: Path) -> tuple[bool, str]:
    """Стримит файл с прямой ссылки в dest. Возвращает (ок, сообщение)."""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            async with client.stream("GET", url) as r:
                if r.status_code >= 400:
                    return False, f"HTTP {r.status_code}"
                with open(dest, "wb") as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)
        if not dest.exists() or dest.stat().st_size == 0:
            return False, "файл пустой"
    except httpx.HTTPError as ex:
        return False, str(ex)
    except OSError as ex:
        return False, str(ex)
    return True, ""


async def _run_import(status: Message, dest: Path, filename: str,
                      source_url: str = ""):
    """Общий прогон: импорт в БД + копия в Supabase Storage + отчёт.

    Временный файл удаляется в любом исходе (finally) — чтобы не копить диск.
    """
    try:
        result = await import_database_file(dest, filename)
    except Exception as ex:
        logger.exception("import error")
        await status.edit_text(f"Ошибка импорта: {ex}")
        _unlink(dest)
        return

    storage_note = ""
    try:
        if not result.get("error") and source_url and dest.exists():
            # копия загруженного файла в Supabase Storage (необязательно)
            from ..dbimport.store import SupabaseStore
            sb = SupabaseStore()
            if sb.enabled:
                import time
                remote = f"{int(time.time())}_{filename}"
                file_url = await sb.upload_file(dest, remote)
                if file_url:
                    storage_note = ("💾 Копия файла в Supabase Storage:\n"
                                    f"{file_url}")
    except Exception:
        logger.warning("storage upload skipped", exc_info=True)
    finally:
        _unlink(dest)

    if "error" in result:
        await status.edit_text(result["error"])
        return

    await _render_report(status, filename, result, storage_note)


def _unlink(path: Path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


async def _render_report(status: Message, filename: str, result: dict,
                         storage_note: str = ""):
    lines = [
        "✅ <b>База принята в боевой реестр</b>",
        "",
        f"Файл: <code>{result.get('filename', filename)}</code>",
        f"Формат: <b>{result.get('format', '?')}</b>",
        f"Записей в файле: <b>{result.get('total', 0)}</b>",
        f"🆕 Добавлено новых: <b>{result.get('added', 0)}</b>",
    ]
    contacts = result.get("contacts") or {}
    if contacts.get("names") or contacts.get("phones") or contacts.get("emails"):
        lines.append(
            "👤 ФИО: <b>{names}</b> · 📞 телефонов: <b>{phones}</b> · "
            "✉️ email: <b>{emails}</b>".format(
                names=contacts.get("names", 0),
                phones=contacts.get("phones", 0),
                emails=contacts.get("emails", 0),
            )
        )
    dupes = result.get("duplicates", 0)
    if dupes:
        lines.append(f"♻️ Дубликатов пропущено: <b>{dupes}</b>")
    elif result.get("added", 1) == 0:
        lines.append("♻️ Все записи уже были в базе — новые данные не добавлены.")
    profiles = result.get("profiles_created", 0)
    if profiles:
        lines.append(f"👤 Профилей людей извлечено: <b>{profiles}</b> (когда есть ФИО/телефон — см. /profile)")
    lines += [
        f"Разделов найдено: <b>{len(result['sections'])}</b>",
        "",
        "<b>Разделы:</b>",
    ]
    for name in result["sections"]:
        marker = "🆕" if name in result["new_sections"] else "•"
        lines.append(f"{marker} {name}")
    lines.append("")
    if storage_note:
        lines.append(storage_note + "\n")
    note = result.get("remote_note", "") or ""
    lines.append(note)
    if not result.get("supabase_configured", True):
        lines.append("\n<i>Сейчас данные живут только локально и сотрутся при "
                     "перезапуске Render. Ссылка на запчасть:</i>")
        from ..dbimport.store import _config_hint
        lines.append(_config_hint())
    text = "\n".join(lines)
    try:
        await status.edit_text(text, parse_mode="HTML")
    except Exception as ex:
        logger.warning("edit report: %s", ex)


def _esc(text) -> str:
    return (str(text or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")