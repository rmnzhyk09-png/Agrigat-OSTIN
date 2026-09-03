"""Интеграция OSINT-инструмента Snoop (https://github.com/snooppr/snoop).

Snoop делает username presence-check по 400+ сайтам (проверяет, зарегистрирован ли
ник на сайте, и собирает прямые ссылки на профили). Работает как отдельный процесс
(внутри Snoop свои зависимости), поэтому вызывается в отдельном потоке и не
блокирует event loop бота.

Подключение: задать SNOOP_DIR = абсолютный путь к папке snoop (там лежит
snoop.py / главный модуль). Пусто = провайдер неактивен.
"""
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from ..config import settings

logger = logging.getLogger(__name__)

PLATFORM = "snoop"

# Части вывода Snoop, которые мы парсим. У Snoop есть флаг 5 эмодзи-блоков
# (структура отчёта), но безопаснее грепать сырой вывод на найденные сайты.
_FOUND_RE = re.compile(
    r"(?P<name>[A-Za-z][\w\-.]{1,60})\s*[:\-]\s*(?:https?[://]+)?(?P<url>[a-z0-9.\-]{4,}\.[a-z]{2,6})",
    re.IGNORECASE,
)

# Домены, недоступные из среды развёртывания (нац. блокировка / DPI / TLS-cut):
# Instagram, YouTube, files.fm закрыты. Такие результаты заведомо бесполезны —
# отбрасываем и не пытаемся по ним искать. Список берём из settings.blocked_hosts.
def _is_blocked(url: str) -> bool:
    u = (url or "").lower()
    return any(h in u for h in settings.blocked_hosts)


def _enabled() -> bool:
    return bool(settings.snoop_dir and (
        Path(settings.snoop_dir).is_dir()))


def _run_snoop(identifier: str) -> subprocess.CompletedProcess:
    """Запускает snoop.py -u <username> и возвращает результат.

    Возврат returncode != 0 или пустой вывод = ничего не нашли / ошибка.
    """
    py = settings.snoop_python or sys.executable
    cmd = [py, "snoop.py", "-u", identifier]
    if os.name != "nt":
        cmd += ["--output", "json"]   # свежие версии умеют JSON-репорт
    try:
        proc = subprocess.run(
            cmd,
            cwd=settings.snoop_dir,
            capture_output=True,
            text=True,
            timeout=settings.snoop_timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "TERM": "dumb"},
        )
    except subprocess.TimeoutExpired:
        logger.warning("snoop: timeout для %s", identifier)
        return subprocess.CompletedProcess(cmd, -1, "", "timeout")
    except OSError as ex:
        logger.warning("snoop: run error: %s", ex)
        return subprocess.CompletedProcess(cmd, -2, "", str(ex))
    return proc


def _parse_text(identifier: str, output: str) -> list[dict]:
    """Грепаем сырой вывод Snoop на найденные сайты+URL."""
    items: list[dict] = []
    seen: set = set()
    output = output or ""
    for m in _FOUND_RE.finditer(output):
        name = m.group("name").strip().strip(":=|")
        url = m.group("url").strip()
        if not url or url.endswith((".png", ".jpg")):
            continue
        if _is_blocked(url):          # не показываем заблокированные сети
            continue
        # Snoop печатает и "не найдено" сайты — берём строки, где есть ссылка-профиль.
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "platform": PLATFORM,
            "author": identifier,
            "url": f"https://{url}",
            "text": f"Snoop: найден аккаунт «{name}» по нику {identifier} → {url}",
            "published_at": "",
            "score": 0.8,
        })
    return items


def _try_json(identifier: str, raw: str) -> list[dict]:
    """Snoop (линукс) может отдавать JSON-отчёт. Пытаемся распарсить."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    items: list[dict] = []
    if isinstance(data, dict):
        found = data.get("found") or data.get("0") or data.get("found_accounts") or []
        if isinstance(found, dict):
            found = list(found.values())
        if isinstance(found, list):
            for entry in found:
                if isinstance(entry, str):
                    if _is_blocked(entry):
                        continue
                    items.append({
                        "platform": PLATFORM, "author": identifier,
                        "url": entry, "text": f"Snoop: {entry}", "score": 0.8,
                    })
    return items


def search_snoop(identifier: str, limit: int = 25) -> list[dict]:
    """Username presence-check через Snoop."""
    if not _enabled():
        return []
    identifier = (identifier or "").strip().lstrip("@")
    if not identifier:
        return []
    proc = _run_snoop(identifier)
    if proc.returncode not in (0, 1):     # 1 = найдено/вывод; 0 = нет
        logger.info("snoop: returncode=%s для %s", proc.returncode, identifier)
        return []
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    items = _try_json(identifier, out) or _parse_text(identifier, out)
    if items:
        logger.info("snoop: %s -> %d сайтов", identifier, len(items))
    return items[:limit]