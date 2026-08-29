"""Разбор файла БД на записи: SQLite, CSV, JSON (в т.ч. Telegram export), XLSX."""
import csv
import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# Расширения, которые бот принимает в /import
SUPPORTED_EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".csv", ".json", ".xlsx"}

# Колонки, которые ищем в данных (основные русские и английские варианты)
_TEXT_KEYS = ("text", "message", "content", "title", "description", "body",
              "comment", "tresc", "post")
_AUTHOR_KEYS = ("author", "from", "sender", "user", "username", "channel",
                "channel_name", "nickname", "nick", "автор")
_DATE_KEYS = ("date", "datetime", "created_at", "published_at", "time", "ts",
              "date_time", "дата")
_URL_KEYS = ("url", "link", "source_url", "permalink", "href")

MAX_TEXT = 20000


def parse_file(path: str | Path, filename: str = "") -> tuple[dict, list[dict]]:
    """Парсит файл и возвращает (метаинформация, записи).

    Каждая запись — dict: {source, author, text, url, date}.
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".db", ".sqlite", ".sqlite3"):
        rows, source = _iter_sqlite(path)
        fmt = "sqlite"
    elif ext == ".csv":
        rows, source = _iter_csv(path)
        fmt = "csv"
    elif ext == ".json":
        rows, source = _iter_json(path)
        fmt = "json"
    elif ext == ".xlsx":
        rows, source = _iter_xlsx(path)
        fmt = "xlsx"
    else:
        raise ValueError(f"Неподдерживаемый формат: {ext}. Поддерживаются: "
                         f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}")

    records: list[dict] = []
    for row in rows:
        rec = _normalize(row, source)
        if not rec.get("text"):
            continue
        rec["source"] = rec["source"] or (filename or path.name)
        records.append(rec)

    meta = {"format": fmt, "source": source, "filename": filename or path.name,
            "rows_parsed": len(rows), "rows_kept": len(records)}
    logger.info("parse %s: %s -> %s записей", filename, fmt, len(records))
    return meta, records


def _value(row: dict, keys) -> object:
    for key in keys:
        if key in row:
            value = row[key]
            if value is not None and str(value).strip():
                return value
    return None


def _clean(text) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return " ".join(text.split())


def _parse_date(value) -> str:
    """Нормализуем дату в строку ISO (без жёсткого парсинга)."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    return s[:50]


def _normalize(row: dict, default_source: str) -> dict:
    text = _value(row, _TEXT_KEYS) or ""
    if isinstance(text, list):  # Telegram export: text бывает списком
        parts = []
        for part in text:
            if isinstance(part, dict):
                parts.append(str(part.get("text", part.get("message", "")) or ""))
            else:
                parts.append(str(part))
        text = "\n".join(p for p in parts if p and p.strip())
    text = _clean(text)
    if not text:
        return {}

    author = _value(row, _AUTHOR_KEYS)
    if isinstance(author, dict):  # Telegram export: from = {"id":..., "name":...}
        author = author.get("name") or author.get("username") or ""
    url = _value(row, _URL_KEYS)
    if isinstance(url, list):
        url = ", ".join(str(u) for u in url if u)

    return {
        "source": str(default_source or ""),
        "author": _clean(str(author or "")),
        "text": text[:MAX_TEXT],
        "url": _clean(str(url or "")),
        "date": _parse_date(_value(row, _DATE_KEYS)),
    }


def _iter_sqlite(path: Path) -> tuple[list[dict], str]:
    """Читаем все таблицы SQLite, отдаём строки, где есть текст."""
    rows: list[dict] = []
    source = ""
    def _q(name: str) -> str:
        return '"' + str(name).replace('"', '""') + '"'

    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        tables = [row[0] for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            try:
                cols = [c[1] for c in cur.execute(
                    f'PRAGMA table_info({_q(table)})')]
            except sqlite3.Error:
                continue
            text_cols = [c for c in cols if c.lower() in _TEXT_KEYS]
            select = ", ".join(_q(c) for c in cols)
            for row in cur.execute(f'SELECT {select} FROM {_q(table)}'):
                d = dict(zip(cols, row))
                if text_cols and any(d.get(c) for c in text_cols):
                    rows.append(d)
                    if not source:
                        source = table
            # Если таблица пустая — просто не попадает в записи
        cur.close()
        conn.close()
    except Exception as ex:
        logger.warning("sqlite parse error: %s", ex)
    if not source:
        source = "sqlite"
    return rows, source


def _iter_csv(path: Path) -> tuple[list[dict], str]:
    rows: list[dict] = []
    source = "csv"
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        for row in csv.DictReader(f, dialect=dialect):
            if row and any((v or "").strip() for v in row.values()):
                rows.append(row)
    return rows, source


def _iter_json(path: Path) -> tuple[list[dict], str]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)

    items: list = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Telegram export → ключ "messages"
        for key in ("messages", "items", "posts", "rows", "data", "result"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
        if not items:
            items = [data]
    source = "json"
    if isinstance(data, dict) and data.get("chat"):
        source = _clean(str(data["chat"].get("title", "") or "")).strip() or "json"

    rows = [item for item in items if isinstance(item, dict)]
    return rows, source


def _iter_xlsx(path: Path) -> tuple[list[dict], str]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        logger.warning("openpyxl не установлен, .xlsx не поддержан")
        return [], "xlsx"

    rows: list[dict] = []
    wb = load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        wb.close()
        return [], "xlsx"
    header: list[str] = []
    for idx, row in enumerate(ws.iter_rows(values_only=True)):
        if idx == 0:
            header = [str(cell).strip() if cell is not None else f"col{n}"
                      for n, cell in enumerate(row, 1)]
            continue
        d = {header[n]: row[n] for n in range(min(len(header), len(row)))}
        if any((v or "") for v in d.values()):
            rows.append(d)
    wb.close()
    return rows, "xlsx"