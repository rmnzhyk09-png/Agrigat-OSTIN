"""Разбор .torrent файлов: bencode → метаданные, info_hash и magnet-ссылка.

Реализуем собственный bencode-декодер (без внешних зависимостей) и берём
info_hash из оригинальных байтов info-словаря, чтобы magnet совпал с клиентами.
"""
import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .schema import extract_emails, extract_phones, looks_like_name

logger = logging.getLogger(__name__)

MAX_FILES_IN_TEXT = 200

_SECT = "Торренты"


class TorrentParseError(ValueError):
    pass


def _bdecode(data: bytes, pos: int):
    """(значение, сырые байты узла, следующая позиция)."""
    c = data[pos:pos + 1]
    if not c:
        raise TorrentParseError("неожиданный конец bencode")

    if c == b"i":
        end = data.index(b"e", pos)
        try:
            value = int(data[pos + 1:end])
        except ValueError:
            raise TorrentParseError("битая целочисленная величина") from None
        return value, data[pos:end + 1], end + 1

    if c == b"l":
        pos += 1
        items: list = []
        while data[pos:pos + 1] != b"e":
            value, _, pos = _bdecode(data, pos)
            items.append(value)
        return items, None, pos + 1

    if c == b"d":
        start = pos
        pos += 1
        items: dict = {}
        while data[pos:pos + 1] != b"e":
            key, _, pos = _bdecode(data, pos)
            if not isinstance(key, bytes):
                raise TorrentParseError("ключ словаря не строка")
            value, _, pos = _bdecode(data, pos)
            items[key] = value
        return items, data[start:pos + 1], pos + 1

    if c in b"0123456789":
        colon = data.index(b":", pos)
        length = int(data[pos:colon])
        begin = colon + 1
        return data[begin:begin + length], data[pos:begin + length], begin + length

    raise TorrentParseError(f"недопустимый байт {c!r}")


def _top_level_chunks(data: bytes):
    """Пары (ключ, исходные байты значения, значение) верхнего уровня."""
    if not data.startswith(b"d"):
        raise TorrentParseError("торрент должен быть словарём bencode")
    pos = 1
    out = []
    while data[pos:pos + 1] != b"e":
        key, _, pos = _bdecode(data, pos)
        if not isinstance(key, bytes):
            raise TorrentParseError("ключ словаря не строка")
        value, raw, pos = _bdecode(data, pos)
        out.append((key, raw, value))
    return out


def _bytes_str(value, default="") -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip()
    return default


def _fmt_size(size: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ПБ"


def _uniq(items: list) -> list:
    out: list = []
    for it in items:
        if it and it not in out:
            out.append(it)
    return out


def _path_contacts(file_path: str) -> tuple[str, list[str], list[str]]:
    """Из имени/пути файла раздачи вытаскивает ФИО, телефоны и email.

    Дампы в торрентах часто названы как «_Фамилия_Имя_телефон_.txt» —
    эти данные должны попадать в записи и профили, а не только в текст.
    """
    phones = extract_phones(file_path)
    emails = extract_emails(file_path)
    base = re.split(r"[\\/]+", file_path)[-1]
    tokens = [t for t in re.split(r"[\\/_\-.\s]+", base) if t]
    name_tokens = [t for t in tokens
                   if re.fullmatch(r"[А-ЯЁA-Z][а-яёa-z]*(?:-[А-ЯЁA-Z][а-яёa-z]*)?", t)]
    name = ""
    for n in (3, 2):
        for i in range(len(name_tokens) - n + 1):
            cand = " ".join(name_tokens[i:i + n])
            if looks_like_name(cand):
                name = cand
                break
        if name:
            break
    return name, _uniq(phones), _uniq(emails)


def parse_torrent(path: Path, filename: str = "") -> dict:
    """Метаданные .torrent: {name, total_size, files, trackers, created,
    info_hash, magnet, comment, record, file_records}."""
    data = path.read_bytes()
    chunks = _top_level_chunks(data)
    info_value = info_raw = None
    for key, raw, value in chunks:
        if key == b"info":
            info_value, info_raw = value, raw
            break
    if not isinstance(info_value, dict) or not info_raw:
        raise TorrentParseError("нет info-словаря — файл не является торрентом")

    name = _bytes_str(info_value.get(b"name"), path.stem)
    info_hash = hashlib.sha1(info_raw).hexdigest()
    magnet = ("magnet:?xt=urn:btih:" + info_hash + "&dn=" + quote(name))

    files: list[tuple[str, int]] = []
    if isinstance(info_value.get(b"files"), list):
        for entry in info_value[b"files"]:
            if not isinstance(entry, dict):
                continue
            parts = [p.decode("utf-8", "replace")
                     for p in entry.get(b"path", []) if isinstance(p, bytes)]
            length = entry.get(b"length", 0)
            if isinstance(length, int):
                files.append(("/".join(parts), length))
    else:
        length = info_value.get(b"length", 0)
        if isinstance(length, int):
            files.append((name, length))
    total_size = sum(s for _, s in files)

    root = {k: v for k, _, v in chunks}  # верхнеуровневые метаданные (кроме info)
    trackers: list[str] = []
    for key, _, value in chunks:
        if key == b"announce" and isinstance(value, bytes):
            trackers.append(value.decode("utf-8", "replace"))
        if key == b"announce-list" and isinstance(value, list):
            for row in value:
                if isinstance(row, list):
                    for u in row:
                        if isinstance(u, bytes):
                            trackers.append(u.decode("utf-8", "replace"))
    seen = set()
    trackers = [t for t in trackers if not (t in seen or seen.add(t))]

    created = root.get(b"creation date", 0)
    created_iso = ""
    if isinstance(created, int) and created > 0:
        created_iso = datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
    comment = _bytes_str(root.get(b"comment"))

    lines = [f"Торрент: <b>{name}</b>", "",
             f"Размер: {_fmt_size(total_size)} · файлов: {len(files)}",
             f"SHA1: <code>{info_hash}</code>",
             f"Magnet: {magnet}"]
    if trackers:
        lines.append("")
        lines.append("Трекеры: " + ", ".join(trackers[:8]))
    if created_iso:
        lines.append(f"Создан: {created_iso}")
    if comment:
        lines.append(f"Комментарий: {comment}")
    if files:
        lines.append("")
        lines.append("Файлы:")
        for p, s in files[:MAX_FILES_IN_TEXT]:
            lines.append(f"• {p} ({_fmt_size(s)})")
        if len(files) > MAX_FILES_IN_TEXT:
            lines.append(f"… и ещё {len(files) - MAX_FILES_IN_TEXT}")

    record = {
        "source": filename or path.name,
        "section": _SECT,
        "author": "",
        "text": "\n".join(lines),
        "url": magnet,
        "date": created_iso,
    }

    file_records = []
    for p, s in files[:100]:
        fname, phones, emails = _path_contacts(p)
        lines: list[str] = []
        if fname:
            lines.append(f"ФИО: {fname}")
        for ph in phones:
            lines.append(f"Телефон: {ph}")
        for e in emails:
            lines.append(f"Email: {e}")
        lines.append(f"Файл в раздаче «{name}»: {p} ({_fmt_size(s)})")
        file_records.append({
            "source": filename or path.name,
            "section": _SECT,
            "author": fname or "",
            "text": "\n".join(lines),
            "url": magnet,
            "date": created_iso,
            "fields": {"name": fname, "phones": phones, "emails": emails},
        })

    return {"meta": {
        "format": "torrent", "source": filename or path.name,
        "filename": filename or path.name,
        "rows_parsed": len(files), "rows_kept": len(file_records) + 1,
    }, "records": [record] + file_records}