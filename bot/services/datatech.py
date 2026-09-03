"""Интеграция DataTech OSINT API (https://datatech.work/public-api/data/search).

Параллельный поиск по внешним базам. Подключение включается заданием
DATATECH_API_KEY (+ опционально DATATECH_BASE_URL) в окружении.

Формат API (по docs.infotrackpeople.org/endpoints/search/):
  POST <base>  body (application/json):
    {"searchOptions":[{"type": "<тип>", "query": "<запрос>"}, ...]}
  Типы: full_text, phone, name, address, email, plate_number, vin,
        passport, snils, inn, username, password, telegram_id, tg_msg.
  Ответ:
    {"data": {"<имя_базы>": {"data": [ {запись}, ... ]}},
     "records": N, "searchId": ...}
"""
import json
import logging
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

PLATFORM = "datatech"

DEFAULT_BASE_URL = "https://datatech.work/public-api/data/search"

# Проверка: включено ли подключение
def _enabled() -> bool:
    return bool(getattr(settings, "datatech_api_key", None))


_CELLS = ("data_provider", "db_name", "name", "first_name", "surname",
          "patronymic", "phone", "email", "address", "inn", "snils",
          "passport", "plate", "vin", "telegram", "username", "birth_date",
          "date_of_birth", "date_prootocol_end", "doc_number", "osp",
          "claimant", "ie_number", "debt_total", "debt_balance", "business_type")


def _guess_type(query: str) -> str:
    """Определяет тип поиска по содержимому запроса.

    Приоритет эвристики:
      email -> phone (+701 / 79X… / разделители) -> plate ->
      vin -> inn(12) -> snils(11) -> passport(6 или 4+6 с пробелом) ->
      name(2+ слова) -> full_text.
    Неоднозначные чистые 10-значные (не похожи на телефон) отправляем как
    full_text — DataTech сам парсит «любой текст».
    """
    import re
    q = (query or "").strip()
    if not q:
        return "full_text"
    digits = "".join(c for c in q if c.isdigit())

    if "@" in q and "." in q:
        return "email"

    # Телефон с явным префиксом '+' в начале — полноформатный международный.
    if q.startswith("+") and re.fullmatch(r"\+\d[\d\s\-()].*", q):
        return "phone"

    # Номер авто RU: 1-2 буквы + 3-4 цифры + 2 буквы + 2-3 цифры
    if re.fullmatch(r"[A-ZА-Я]{1,2}\d{3,4}[A-ZА-Я]{2,3}\d{2,3}", q.split(" ")[0]):
        return "plate_number"
    # VIN: 17 символов A-Z0-9 (латиница)
    if len(digits) + sum(1 for c in q if c.isalpha()) == 17 and \
       re.fullmatch(r"[A-Z0-9]{17}", q):
        return "vin"

    # ИНН 12 цифр
    if len(digits) == 12 and re.fullmatch(r"\d{12}", q.strip()):
        return "inn"
    # СНИЛС 11 цифр
    if len(digits) == 11 and re.fullmatch(r"\d{11}", q.strip()):
        return "snils"
    # Паспорт: 6 цифр, либо 4 цифры + пробел/дефис + 6 цифр (10 цифр с разделителем)
    if len(digits) == 6 and re.fullmatch(r"\d{6}", q.strip()):
        return "passport"
    if len(digits) == 10 and re.fullmatch(r"\d{4}[\s\-]\d{6}", q.strip()):
        return "passport"

    # Телефон: разделители (скобки/дефис/пробел между цифрами) либо РФ мобильный.
    if re.search(r"\d[\s\-()]", q) and 7 <= len(digits) <= 15:
        return "phone"
    if re.fullmatch(r"[789]\d{9,10}", q.strip()):
        return "phone"

    # Имя-фамилия (2+ слова, каждое с буквы)
    words = q.split()
    if len(words) >= 2 and all(w and w[0].isalpha() and not w[0].isdigit()
                              for w in words):
        return "name"
    return "full_text"


def _build_search_options(query: str) -> list[dict]:
    """Строит searchOptions. Один основной, при разборе ФИО можно комбинировать."""
    return [{"type": _guess_type(query), "query": (query or "").strip()}]


async def _request(search_options: list[dict]) -> dict:
    base = (getattr(settings, "datatech_base_url", None) or DEFAULT_BASE_URL).strip()
    key = settings.datatech_api_key
    headers = {
        "Authorization": f"Bearer {key}",
        "X-API-Key": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"searchOptions": search_options}
    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
        try:
            r = await client.post(base, json=payload, headers=headers)
        except httpx.HTTPError as ex:
            logger.debug("datatech request error: %s", ex)
            return {}
        if r.status_code >= 400:
            logger.info("datatech: HTTP %s %s", r.status_code, r.text[:200])
            return {}
        try:
            return r.json()
        except ValueError:
            return {}


def _flatten(payload: dict) -> list[dict]:
    """Разворачивает ответ {data: {<база>: {data:[…]}}} в плоский список записей."""
    data = payload.get("data") or payload
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for db_name, group in data.items():
        if not isinstance(group, dict):
            continue
        rows = group.get("data") or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                rec = dict(row)
                rec.setdefault("db_name", db_name)
                out.append(rec)
    return out


def _row_to_item(row: dict, query: str) -> dict:
    """Запись DataTech -> item бота."""
    lines = []
    for key, val in row.items():
        if val is None or val == "":
            continue
        if isinstance(val, (list, dict)):
            try:
                val = json.dumps(val, ensure_ascii=False)[:300]
            except Exception:
                val = str(val)
        if str(key).lower() in ("id", "score", "_id", "@id"):
            continue
        lines.append(f"{str(key).capitalize()}: {val}")

    name = (row.get("name") or row.get("full_name") or row.get("fio")
            or row.get("first_name") or row.get("surname") or query)
    url = row.get("url") or row.get("profile_url") or ""
    return {
        "platform": PLATFORM,
        "author": str(name),
        "url": str(url),
        "text": "\n".join(lines)[:1500],
        "published_at": "",
        "score": 0.9,
    }


async def search_datatech(query: str, limit: int = 10) -> list[dict]:
    """Параллельный поиск по внешним базам DataTech.

    Запрос классифицируется по типу (телефон/ИНН/ФИО/email/…), отправляется
    как один searchOption, ответ разворачивается в плоский список item'ов.
    """
    if not _enabled():
        return []
    q = (query or "").strip()
    if not q:
        return []
    search_options = _build_search_options(q)
    payload = await _request(search_options)
    rows = _flatten(payload)
    items = [_row_to_item(r, q) for r in rows]
    seen: set = set()
    out: list[dict] = []
    for it in items:
        key = f"{it.get('url')}|{it.get('text')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= limit:
            break
    if out:
        logger.info("datatech(%s): %d -> %d items", search_options[0]["type"],
                    len(rows), len(out))
    return out
