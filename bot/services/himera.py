"""Интеграция Himera Search API (https://himera-search.one).

Платный OSINT-провайдер: поиск по ФИО, телефону, паспорту, ИНН, email,
СНИЛС, адресу, авто/VIN и юрлицам. Подключение включается заданием
HIMERA_API_KEY (+ опционально HIMERA_BASE_URL) в окружении.

Формат API v2 (https://api.himera-search.info/2.0/):
  POST <base>/<type>  body (application/x-www-form-urlencoded):
    key=<API_KEY>&<поля_типа>=...
  Типы и их поля:
    name_standart: firstname,lastname,middlename,day,mounth,year
    phone:         phone (формат 79123456789)
    passport:      passport
    inn_fl:        inn
    email:         email
    snils:         snils
    adres:         city,street,home,flat
    avto:          number (кириллица, напр. A777AA77)
    vin:           vin
    inn:           inn (юрлицо, аналогично ogrn)
    scoring:       firstname,lastname,middlename,birthday (дд.мм.гггг)
    credit:        firstname,lastname,middlename,birthday (дд.мм.гггг)
  Ответ успеха:
    {"status":"ok","data":[{...}],"date_create":"...","url":"https://himera-search.info/request/..."}
  Ошибки:
    {"status":"not_found"}
    {"error":"Not enough money"} / {"error":"Limit expired"}
    {"error":"invalid api key"} / {"error":"invalid ip adress"}
    {"error":"invalid type request"}

Внимание: сервис платный (~1₽–139₽ за запрос) и привязан к IP. При
несовпадении IP ({"error":"invalid ip adress"}) нужно авторизовать IP,
с которого ходит сервер (на Render — исходящий IP), в личном кабинете.
"""
import json
import logging
import re
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

PLATFORM = "himera"

DEFAULT_BASE_URL = "https://api.himera-search.info/2.0"

# Проверка: включено ли подключение
def _enabled() -> bool:
    return bool(getattr(settings, "himera_api_key", None))


# Пакет запросов: сколько реальных POST-запросов уже сделано в этом процессе.
# Жёсткий потолок от HIMERA_MAX_REQUESTS (по умолчанию 15) защищает пакет
# от перерасхода: после исчерпания Himera перестаёт вызываться.
_budget_used = 0


def _budget_max() -> int:
    return max(1, int(getattr(settings, "himera_max_requests", 15) or 15))


def _budget_ok() -> bool:
    return _budget_used < _budget_max()


def _budget_spend():
    global _budget_used
    _budget_used += 1
    logger.info("himera: использовано %d/%d запросов пакета",
                _budget_used, _budget_max())


def himera_budget_used() -> int:
    """Сколько запросов Himera уже потрачено в этом процессе."""
    return _budget_used


def himera_budget_left() -> int:
    """Сколько запросов Himera осталось в пакете (0 = исчерпан)."""
    return max(0, _budget_max() - _budget_used)


# Поля-псевдонимы для склеивания имени из разобранного ФИО
_NAME_FIELDS = ("name", "full_name", "fio", "firstname", "first_name",
                "surname", "lastname", "middlename")
_EXCLUDE_KEYS = ("id", "score", "_id", "@id", "__primarykey")

# Паспорт РФ: 6 цифр, либо 4 цифры + разделитель + 6 цифр (10 цифр суммарно)
RE_PASSPORT = re.compile(r"^\d{6}$|\d{4}[\s\-]\d{6}$")


def _split_fio(query: str) -> dict:
    """Пытается разобрать ФИО и дату рождения на поля Himera.

    Возвращает dict с полями firstname/lastname/middlename/day/mounth/year
    (те, что удалось извлечь). Пустой dict — разобрать не удалось.
    """
    parts = [p for p in re.split(r"[\s,]+", (query or "").strip()) if p]
    fields: dict = {}

    # Дата рождения: дд.мм.гггг или дд.мм.гг
    m = re.search(r"(\d{2})[./](\d{2})[./](\d{2,4})", (query or ""))
    if m:
        fields["day"], fields["mounth"], _y = m.group(1), m.group(2), m.group(3)
        year = _y
        if len(_y) == 2:
            year = ("19" if int(_y) >= 30 else "20") + _y
        fields["year"] = year
        parts = [p for p in parts if not re.fullmatch(r"\d{2}[./]\d{2}[./]\d{2,4}", p)]

    # Первые 1–3 слова, начинающиеся с буквы, — ФИО
    words = [p for p in parts if re.fullmatch(r"[А-ЯЁA-Z][а-яёa-z-]+", p)]
    if len(words) >= 1:
        fields["lastname"] = words[0]
    if len(words) >= 2:
        fields["firstname"] = words[1]
    if len(words) >= 3:
        fields["middlename"] = words[2]

    # Нужно минимум фамилия для name_standart
    return fields if fields.get("lastname") else {}


def _guess_type(query: str) -> str:
    """Определяет тип Himera по содержимому запроса.

    Приоритет эвристики (однозначные/дешёвые сначала):
      email -> ФИО+дата(name_standart) -> VIN -> авто -> паспорт ->
      ИНН/СНИЛС/телефон (по формату) -> ФИО(name_standart) -> default.
      Телефон и СНИЛС оба 11 цифр: различаем по началу номера (79/78/89/9*
      — мобильный, всё остальное — СНИЛС).
      Паспорт 4+6(разделитель) нельзя путать с ИНН 10 цифр — детект до сноса пробелов.
    """
    qraw = (query or "").strip()
    q = re.sub(r"[\s\-()]+", "", qraw)
    if not q:
        return "default"
    digits = "".join(c for c in q if c.isdigit())

    if "@" in q and "." in q:
        return "email"

    # ФИО с датой рождения — всегда name_standart
    if _split_fio(qraw).get("lastname") and re.search(r"\d{2}[./]\d{2}[./]\d{2,4}", qraw):
        return "name_standart"

    # VIN: 17 символов A-Z0-9 (латиница)
    if re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", q):
        return "vin"
    # Номер авто RU: буквы (кириллица/латиница-похожие) + 3-4 цифры + 2 буквы + цифры
    if re.fullmatch(r"[A-ZА-ЯЁ]{1,2}\d{3,4}[A-ZА-ЯЁ]{2,3}\d{2,3}", q):
        return "avto"

    # Паспорт: 6 цифр, либо 4 разделитель 6 (10 цифр) — по исходной строке
    if RE_PASSPORT.search(qraw):
        return "passport"

    # Мобильный телефон РФ: 79/78/89 (11 цифр) или 9* (10 цифр)
    if (len(digits) == 11 and re.fullmatch(r"(?:79|78|89)\d{9}", q)) or \
       (len(digits) == 10 and re.fullmatch(r"9\d{9}", q)) or \
       re.fullmatch(r"\+7\d{10}", q):
        return "phone"

    # ИНН 10 или 12 цифр
    if len(digits) in (10, 12) and re.fullmatch(r"\d{10,12}", q):
        return "inn" if len(digits) == 10 else "inn_fl"

    # СНИЛС 11 цифр (не распознан как телефон)
    if len(digits) == 11 and re.fullmatch(r"\d{11}", q):
        return "snils"

    # ФИО (несколько словарных слов)
    if _split_fio(qraw).get("lastname"):
        return "name_standart"

    return "default"


def _build_params(query: str, qtype: str) -> dict:
    """Строит параметры формы для запроса Himera.

    Для name_standart/scoring/credit распарсивает ФИО+дату; для остальных
    кладёт всё в соответствующее поле типа.
    """
    q = (query or "").strip()
    if qtype in ("name_standart", "scoring", "credit"):
        fields = _split_fio(q)
        if qtype == "name_standart":
            # birthday не нужен; day/mounth/year уже в fields из _split_fio
            return fields
        # scoring/credit используют единое поле birthday дд.мм.гггг
        b = re.search(r"(\d{2})[./](\d{2})[./](\d{2,4})", q)
        birthday = ""
        if b:
            _y = b.group(3)
            if len(_y) == 2:
                _y = ("19" if int(_y) >= 30 else "20") + _y
            birthday = f"{b.group(1)}.{b.group(2)}.{_y}"
        out = {k: v for k, v in fields.items()
               if k in ("firstname", "lastname", "middlename")}
        if birthday:
            out["birthday"] = birthday
        return out

    # Остальные типы: поле = тип, значение = целый запрос
    return {qtype: q}


async def _request(qtype: str, params: dict) -> dict:
    base = (getattr(settings, "himera_base_url", None) or DEFAULT_BASE_URL).strip()
    key = settings.himera_api_key
    url = f"{base.rstrip('/')}/{qtype}"
    data = dict(params)
    data["key"] = key
    headers = {"Accept": "application/json"}
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        try:
            r = await client.post(url, data=data, headers=headers)
        except httpx.HTTPError as ex:
            logger.debug("himera request error: %s", ex)
            return {}
        if r.status_code >= 400:
            logger.info("himera: HTTP %s %s", r.status_code, r.text[:200])
            return {}
        try:
            return r.json()
        except ValueError:
            return {}


def _rows(payload: dict) -> list[dict]:
    """Достаёт список записей из ответа Himera."""
    if not isinstance(payload, dict):
        return []
    if payload.get("status") == "not_found":
        logger.info("himera: not_found")
        return []
    err = payload.get("error")
    if err:
        logger.info("himera: error=%s", err)
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _row_to_item(row: dict, query: str, report_url: str) -> dict:
    """Запись Himera -> item бота."""
    lines = []
    for key, val in row.items():
        if val is None or val == "":
            continue
        if str(key).lower() in _EXCLUDE_KEYS:
            continue
        if isinstance(val, (list, dict)):
            try:
                val = json.dumps(val, ensure_ascii=False)[:300]
            except Exception:
                val = str(val)
        lines.append(f"{str(key).capitalize()}: {val}")

    name = query
    for f in _NAME_FIELDS:
        if row.get(f):
            name = str(row[f])
            break

    url = report_url or row.get("url") or ""
    return {
        "platform": PLATFORM,
        "author": name,
        "url": str(url),
        "text": "\n".join(lines)[:1500],
        "published_at": str(row.get("date_create") or ""),
        "score": 0.95,
    }


async def search_himera(query: str, limit: int = 10) -> list[dict]:
    """Платный поиск по Himera Search API.

    Запрос классифицируется по типу, отправляется POST-запросом, ответ
    разворачивается в плоский список item'ов. Работает до тех пор, пока
    задан HIMERA_API_KEY и не исчерпан пакет запросов (HIMERA_MAX_REQUESTS).
    """
    if not _enabled():
        return []
    if not _budget_ok():
        logger.info("himera: пакет запросов исчерпан, пропускаем")
        return []
    q = (query or "").strip()
    if not q:
        return []
    qtype = _guess_type(q)
    if qtype == "default":
        # Обобщённый запрос без распознанного типа Himera не берёт
        logger.info("himera: тип не распознан, пропускаем («%s»)", q[:60])
        return []
    params = _build_params(q, qtype)
    payload = await _request(qtype, params)
    _budget_spend()
    rows = _rows(payload)
    report_url = (payload or {}).get("url") or ""
    items = [_row_to_item(r, q, report_url) for r in rows]

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
        logger.info("himera(%s): %d -> %d items", qtype, len(rows), len(out))
    return out