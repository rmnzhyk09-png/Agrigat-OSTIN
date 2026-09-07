"""Интеграция OSINT-инструмента Snoop (https://github.com/snooppr/snoop).

Snoop делает username presence-check по 400+ сайтам (проверяет, зарегистрирован ли
ник на сайте, и собирает прямые ссылки на профили). Работает как отдельный процесс
(внутри Snoop свои зависимости), поэтому вызывается в отдельном потоке и не
блокирует event loop бота.

Подключение: задать SNOOP_DIR = абсолютный путь к папке snoop (там лежит
snoop.py / главный модуль). Пусто = провайдер неактивен.

Snoop пишет текстовый отчёт в <SNOOP_DIR>/results/nicknames/txt/<ник>.txt
со строками «<флаг>  |  <сайт>  ... |  <url>» — парсим именно его (надёжнее,
чем цветной stdout). На всякий случай есть fallback на греп stdout.
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

# Строка отчёта Snoop: <флаг страны>  |  <имя сайта>... |  <url профиля>
_REPORT_LINE_RE = re.compile(
    r"^\s*.*?\|\s*(.*?)\s*\|\s*(https?://\S+)\s*$", re.IGNORECASE)

# Строка «найдено» в stdout Snoop (при --no-func цвет выключен):
#   [+] vk.com: https://vk.com/ivan
_FOUND_RE = re.compile(
    r"(?P<name>[A-Za-z][\w\-.]{1,60})\s*:\s*(?P<url>https?://[^\s\[\]]+)",
    re.IGNORECASE,
)

# Служебные строки отчёта, которые не являются находками.
_SKIP_LINES = ("géo", "geo ", "resource", "запрашиваемый объект", "сессия",
               "база snoop", "исключённые регионы", "выбор конкретных",
               "обновлено", "©", "email", "телефон")

# Убирает ANSI/colorama-коды из stdout.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _is_blocked(url: str) -> bool:
    u = (url or "").lower()
    return any(h in u for h in settings.blocked_hosts)


def _enabled() -> bool:
    return bool(settings.snoop_dir and (
        Path(settings.snoop_dir).is_dir()))


def _report_candidates(identifier: str) -> list[Path]:
    """Пути, куда Snoop может записать txt-отчёт в зависимости от окружения."""
    candidates = []
    if settings.snoop_dir:
        candidates.append(Path(settings.snoop_dir) / "results" / "nicknames" / "txt" / f"{identifier}.txt")
    if os.name == "nt":
        appdata = os.environ.get("LOCALAPPDATA", "")
        if appdata:
            candidates.append(Path(appdata) / "snoop" / "results" / "nicknames" / "txt" / f"{identifier}.txt")
    else:
        home = os.path.expanduser("~")
        candidates.append(Path(home) / ".snoop" / "results" / "nicknames" / "txt" / f"{identifier}.txt")
    return candidates


def _run_snoop(identifier: str) -> subprocess.CompletedProcess:
    """Запускает snoop.py <ник> и возвращает результат.

    Синтаксис этой версии Snoop: nickname передаётся позиционно
    (флаг -u в Snoop = --userlist <файл>!).
    """
    py = settings.snoop_python or sys.executable
    cmd = [py, "snoop.py", identifier]
    # --no-func отключает ANSI-цвета в stdout (проще грепать).
    # --base задаёт базу сайтов: BDdemo (по умолчанию, быстро) или BDfull.
    cmd += ["--no-func", "--base", settings.snoop_base or "BDdemo"]
    try:
        # text=False: читаем байты — Snoop пишет в cp1251 на Windows независимо
        # от PYTHONIOENCODING, и text=True в подпроцессе упал бы на декодировании.
        # PYTHONUTF8=1 обязателен: без него Snoop падает с UnicodeEncodeError
        # на cp1251-консоли прямо во время печати (не дописывая txt-отчёт).
        proc = subprocess.run(
            cmd,
            cwd=settings.snoop_dir,
            capture_output=True,
            timeout=settings.snoop_timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8",
                 "PYTHONUTF8": "1", "TERM": "dumb"},
        )
    except subprocess.TimeoutExpired:
        logger.warning("snoop: timeout для %s", identifier)
        return subprocess.CompletedProcess(cmd, -1, b"", b"timeout")
    except OSError as ex:
        logger.warning("snoop: run error: %s", ex)
        return subprocess.CompletedProcess(cmd, -2, b"", str(ex).encode("utf-8", "replace"))
    return proc


def _decode(data) -> str:
    """Декодирует байты Snoop: сначала UTF-8, при сбое — locale (cp1251)."""
    if isinstance(data, str):
        return data
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("cp1251", errors="replace")
        except (UnicodeDecodeError, LookupError):
            return data.decode("utf-8", errors="replace")


def _parse_report(identifier: str) -> list[dict]:
    """Читает текстовый отчёт Snoop и достаёт (сайт, url) находок."""
    items: list[dict] = []
    seen: set = set()
    for path in _report_candidates(identifier):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw in lines:
            stripped = raw.strip()
            low = stripped.lower()
            if not stripped or any(s in low for s in _SKIP_LINES):
                continue
            m = _REPORT_LINE_RE.match(stripped)
            if not m:
                continue
            name = (m.group(1) or "").strip()
            url = (m.group(2) or "").strip().rstrip(".).,:;")
            if not name or not url.startswith("http"):
                continue
            if _is_blocked(url):
                continue
            key = url.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "platform": PLATFORM,
                "author": identifier,
                "url": url,
                "text": f"Snoop: найден аккаунт «{name}» по нику {identifier} → {url}",
                "published_at": "",
                "score": 0.8,
            })
        break
    return items


def _parse_text(identifier: str, output: str) -> list[dict]:
    """Fallback: грепаем сырой (бесцветный) stdout Snoop на найденные сайты+URL."""
    items: list[dict] = []
    seen: set = set()
    output = _ANSI_RE.sub("", output or "")
    for m in _FOUND_RE.finditer(output):
        name = m.group("name").strip().strip(":=|")
        url = m.group("url").strip().rstrip(".).,:;")
        if not url.startswith("http"):
            continue
        if _is_blocked(url):
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "platform": PLATFORM,
            "author": identifier,
            "url": url,
            "text": f"Snoop: найден аккаунт «{name}» по нику {identifier} → {url}",
            "published_at": "",
            "score": 0.8,
        })
    return items


def _try_json(identifier: str, raw: str) -> list[dict]:
    """Snoop может отдавать JSON-отчёт. Пытаемся распарсить (запасной путь)."""
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
    # Основной источник — txt-отчёт (Snoop пишет его всегда);
    # fallback — stdout (JSON / текстовый греп).
    items = _parse_report(identifier)
    if not items:
        out = _decode(proc.stdout) + "\n" + _decode(proc.stderr)
        items = _try_json(identifier, out) or _parse_text(identifier, out)
    if items:
        logger.info("snoop: %s -> %d сайтов", identifier, len(items))
    return items[:limit]