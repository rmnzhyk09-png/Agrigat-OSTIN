"""Интеграция OSINT-инструмента Blackbird (https://github.com/p1ngul1n0/blackbird).

Blackbird делает reverse username/email search по 600+ сайтам (база WhatsMyName)
и возвращает найденные аккаунты с адресами профилей.

Запускается как отдельный процесс (внутри Blackbird используется asyncio.run,
поэтому вызывать его в асинхронном цикле бота нельзя) и читает JSON-результат,
который Blackbird кладёт в папку results/. Включён только если задан
BLACKBIRD_DIR (абсолютный путь к папке с blackbird.py).
"""
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from ..config import settings

logger = logging.getLogger(__name__)

PLATFORM = "blackbird"

# Фильтр сайтов, чтобы не бегать по всем 600+ (ускоряет и снижает загрузку).
# Пустая строка = искать по всем. Пример: 'cat=social'
FILTER = os.getenv("BLACKBIRD_FILTER", "cat=social")

_FOUND_KEYS = ("name", "url")


def _enabled() -> bool:
    return bool(settings.blackbird_dir and (
        Path(settings.blackbird_dir) / "blackbird.py").is_file())


def _results_dir() -> Path:
    return Path(settings.blackbird_dir) / "results"


def _latest_json_for(identifier: str) -> Path | None:
    """Самый свежий .json под ник/email в папке results."""
    root = _results_dir()
    if not root.is_dir():
        return None
    candidates: list[tuple[float, Path]] = []
    for p in root.rglob("*.json"):
        if identifier.lower() in p.name.lower():
            try:
                candidates.append((p.stat().st_mtime, p))
            except OSError:
                continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _run_blackbird(identifier: str, as_email: bool) -> int:
    """Запускает blackbird.py и возвращает returncode (0 = ок)."""
    py = sys.executable or "python"
    cmd = [py, "blackbird.py"]
    if as_email:
        cmd += ["--email", identifier]
    else:
        cmd += ["--username", identifier]
    cmd += ["--json", "--no-update"]
    if FILTER:
        cmd += ["--filter", FILTER]
    try:
        proc = subprocess.run(
            cmd,
            cwd=settings.blackbird_dir,
            capture_output=True,
            text=True,
            timeout=settings.blackbird_timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        logger.warning("blackbird timeout for %s", identifier)
        return -1
    except OSError as ex:
        logger.warning("blackbird run error: %s", ex)
        return -2
    return proc.returncode


def _account_to_item(acc: dict, identifier: str) -> dict:
    name = acc.get("name", "")
    url = acc.get("url", "")
    category = acc.get("category", "")
    lines = [f"Blackbird: найден аккаунт «{name}» по нику/почте {identifier}"]
    if category:
        lines.append(f"Категория: {category}")
    meta = acc.get("metadata")
    if isinstance(meta, list):
        for m in meta[:6]:
            if isinstance(m, dict):
                mname = m.get("name") or m.get("key") or ""
                mval = m.get("value") or ""
                if mname and mval:
                    lines.append(f"{mname}: {mval}")
    return {
        "platform": PLATFORM,
        "author": identifier,
        "url": url,
        "text": "\n".join(lines)[:1500],
        "published_at": "",
        "score": 1.0 if url else 0.5,
    }


def _load_results(identifier: str) -> list[dict]:
    path = _latest_json_for(identifier)
    if not path:
        logger.info("blackbird: нет JSON для %s", identifier)
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as ex:
        logger.warning("blackbird: не прочитать %s: %s", path, ex)
        return []
    if not isinstance(data, list):
        return []
    items: list[dict] = []
    for acc in data:
        if not isinstance(acc, dict):
            continue
        if acc.get("status") != "FOUND":
            continue
        if not any(acc.get(k) for k in _FOUND_KEYS):
            continue
        items.append(_account_to_item(acc, identifier))
    return items


def search_blackbird(identifier: str, as_email: bool = False,
                     limit: int = 30) -> list[dict]:
    """Полный поиск: запуск Blackbird + чтение JSON-результата.

    Возвращает список найденных аккаунтов в формате item бота (пусто, если
    Blackbird не настроен или ошибся).
    """
    if not _enabled():
        logger.debug("blackbird: не настроен (BLACKBIRD_DIR пуст)")
        return []
    identifier = (identifier or "").strip()
    if not identifier:
        return []
    rc = _run_blackbird(identifier, as_email)
    if rc != 0:
        logger.info("blackbird: returncode=%s для %s", rc, identifier)
        return []
    items = _load_results(identifier)
    return items[:limit]
