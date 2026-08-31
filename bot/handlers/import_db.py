"""Импорт файла БД: /import.

Принимает документ (.db/.sqlite/.sqlite3/.csv/.json/.xlsx), анализирует записи,
раскладывает по категориям (разделам) и сохраняет в собственную БД (Supabase).
При обнаружении новой категории автоматически создаётся новый раздел.
"""
import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import Message

from ..config import settings
from ..dbimport.parser import SUPPORTED_EXTENSIONS
from ..dbimport.store import import_database_file

logger = logging.getLogger(__name__)
router = Router()

_ALLOWED = SUPPORTED_EXTENSIONS

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
        await status.edit_text(f"Не удалось скачать файл: {ex}")
        return

    try:
        result = await import_database_file(dest, filename)
    except Exception as ex:
        logger.exception("import error")
        await status.edit_text(f"Ошибка импорта: {ex}")
        return
    finally:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass

    if "error" in result:
        await status.edit_text(result["error"])
        return

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
    note = result.get("remote_note", "") or ""
    lines.append(note)
    if not result.get("supabase_configured", True):
        lines.append("\n<i>Сейчас данные живут только локально и сотрутся при "
                     "перезапуске Render. Ссылка на запчасть:</i>")
        from ..dbimport.store import _config_hint
        lines.append(_config_hint())
    await status.edit_text("\n".join(lines), parse_mode="HTML")