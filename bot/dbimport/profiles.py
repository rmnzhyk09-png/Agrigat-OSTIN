"""Извлечение структурированных данных о человеке из записей импорта.

Превращает «сырые» записи (text/fields) в профиль — словарь с полями,
которые можно сохранить в Supabase (db_profiles) или показать в карточке.
"""
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------- нормализация ----------

def _norm(text) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return " ".join(text.split())


def _norm_phone(text) -> str | None:
    raw = str(text or "").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits.startswith(("7", "8")):
        digits = "7" + digits[1:]
    elif raw.startswith("+") and 8 <= len(digits) <= 15:
        digits = "+" + digits
        return digits
    elif 8 <= len(digits) <= 15 and not raw.startswith("+"):
        digits = "+" + digits
        return digits
    else:
        return None
    return "+" + digits


def _norm_date(text) -> str | None:
    text = _norm(text)
    if not text:
        return None
    m = re.match(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})", text)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"(\d{4})$", text)
    if m:
        return m.group(1)
    return None


def _unique(items) -> list:
    out = []
    for it in items:
        if it and it not in out:
            out.append(it)
    return out


# ---------- извлечение полей ----------

_INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")
_SNILS_RE = re.compile(r"\b(\d{3}[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{2})\b")
_PASSPORT_RE = re.compile(
    r"(?:паспорт|passport)[:\s]*(\d{2}[\s\-]?\d{2})[\s,]+(\d{6})",
    re.IGNORECASE,
)
_PASSPORT_ISSUED_RE = re.compile(
    r"(?:выдан|кем выдан)[:\s]+(.{5,80})",
    re.IGNORECASE,
)
_PASSPORT_DATE_RE = re.compile(
    r"(?:выдан|дата выдачи)[:\s]+.*?(\d{1,2}[\s.\-/]\d{1,2}[\s.\-/]\d{4}|\d{4})",
    re.IGNORECASE,
)
_DRIVING_RE = re.compile(
    r"(?:водительское|водительски[а-я]+)\s+(?:удостоверени[ея]|права)[:\s]+"
    r"([A-ZА-ЯЁa-zа-яё\d\s\-]{5,30})",
    re.IGNORECASE,
)
_MILITARY_RE = re.compile(
    r"(?:военный билет|военный)[а-я]*\s*[:\s]+(.{5,60})",
    re.IGNORECASE,
)
_ADDRESS_RE = re.compile(
    r"(?:адрес|прописка|проживани[ея])[:\s]+(.{10,120})",
    re.IGNORECASE,
)
_POSTAL_CODE_RE = re.compile(r"\b(\d{6})\b")
_FAMILY_STATUS_RE = re.compile(
    r"(?:семейн|состоит|не состоит|женат|не женат|разведен|вдовец|холост|замужем|не замужем)"
    r"[а-яё]*(?:\s+на)?\s*[:\s]*(\S+)",
    re.IGNORECASE,
)
_VK_RE = re.compile(r"(?:vk\.com|вконтакте)[/\s]+([A-Za-z0-9_.]+)", re.IGNORECASE)
_INSTAGRAM_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]+)", re.IGNORECASE)
_TELEGRAM_RE = re.compile(r"(?:t\.me|telegram\.me)/([A-Za-z0-9_]+)", re.IGNORECASE)
_CAR_RE = re.compile(
    r"(?:автомобил|авто|машина)[:\s]*"
    r"([А-ЯЁA-Z][\w\s\-]{2,40})\s*"
    r"(?:г\.?н\.?|госномер|номер)[:\s]*"
    r"([А-ЯЁA-Z0-9]{6,10})",
    re.IGNORECASE,
)
_PLATE_RE = re.compile(
    r"\b([А-ЯЁA-Z]{1}\d{3}[А-ЯЁA-Z]{2}\d{2,3})\b"
)
_COURT_RE = re.compile(
    r"суд[а-яё]*\s+(?:дел[ао]?[:\s]*)?([А-ЯЁA-Z].{5,150})",
    re.IGNORECASE,
)
_DEBT_RE = re.compile(
    r"(?:должник|задолженност|долг|исполнительн|пристав)[:\s]*"
    r"(?:[:\s]*([\d\s.,]+)\s*(?:руб|₽)?)?",
    re.IGNORECASE,
)
_INN_COMPANY_RE = re.compile(
    r"(?:ИНН работодателя|ИНН организации|ИНН\s+(?:юрлица|компании))[:\s]*(\d{10}|\d{12})",
    re.IGNORECASE,
)
_OGRN_RE = re.compile(r"\b(\d{13}|\d{15})\b")
_COURT_AMOUNT_RE = re.compile(
    r"(?:сумма|взыскать|взыскани[ея]|долг)[:\s]*([\d\s.,]+)\s*(?:руб|₽)?",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def extract_fields_from_text(text: str) -> dict[str, Any]:
    """Извлекает структурированные поля из текста записи."""
    result: dict[str, Any] = {}
    if not text:
        return result

    # ИНН
    inns = _INN_RE.findall(text)
    if inns:
        result["inn"] = inns[0] if len(inns) == 1 else inns

    # СНИЛС
    snils = _SNILS_RE.findall(text)
    if snils:
        result["snils"] = snils[0].replace(" ", "").replace("-", "")

    # Паспорт
    pp = _PASSPORT_RE.search(text)
    if pp:
        result["passport_series"] = pp.group(1).replace(" ", "-")
        result["passport_number"] = pp.group(2)
    pp_issued = _PASSPORT_ISSUED_RE.search(text)
    if pp_issued:
        result["passport_issued_by"] = _norm(pp_issued.group(1))
    pp_date = _PASSPORT_DATE_RE.search(text)
    if pp_date:
        result["passport_issue_date"] = _norm_date(pp_date.group(1))

    # Водительское
    dl = _DRIVING_RE.search(text)
    if dl:
        result["driver_license"] = _norm(dl.group(1))

    # Военный билет
    mil = _MILITARY_RE.search(text)
    if mil:
        result["military_id"] = _norm(mil.group(1))

    # Адрес
    addr = _ADDRESS_RE.search(text)
    if addr:
        addr_text = _norm(addr.group(1))
        result["registration_address"] = addr_text
        postal = _POSTAL_CODE_RE.search(addr_text)
        if postal:
            result["registration_postal_code"] = postal.group(1)

    # Семейное положение
    fam = _FAMILY_STATUS_RE.search(text)
    if fam:
        result["family_status"] = _norm(fam.group(1))

    # VK
    vk = _VK_RE.search(text)
    if vk:
        result["vk_url"] = "https://vk.com/" + vk.group(1)
    inst = _INSTAGRAM_RE.search(text)
    if inst:
        result["instagram_url"] = "https://instagram.com/" + inst.group(1)
    tg = _TELEGRAM_RE.search(text)
    if tg:
        result["telegram"] = {"username": "@" + tg.group(1),
                              "url": "https://t.me/" + tg.group(1)}

    # Авто
    car = _CAR_RE.search(text)
    if car:
        result["vehicles"] = [{"make": _norm(car.group(1)),
                               "plate": car.group(2).upper()}]
    else:
        plates = _PLATE_RE.findall(text)
        if plates:
            result["vehicles"] = [{"plate": p.upper()} for p in plates]

    # Суды и долги
    court = _COURT_RE.search(text)
    if court:
        result["court_cases"] = [{"raw": _norm(court.group(1))}]
    amounts = _COURT_AMOUNT_RE.findall(text)
    if amounts:
        nums = []
        for a in amounts:
            clean = re.sub(r"[^\d.,]", "", a).replace(",", ".").strip()
            if clean:
                try:
                    nums.append(float(clean))
                except ValueError:
                    pass
        if nums:
            result["enforcement_debt_total"] = sum(nums)

    # ИНН работодателя
    emp_inn = _INN_COMPANY_RE.search(text)
    if emp_inn:
        result["employer_inn"] = emp_inn.group(1)

    return result


# ---------- сборка профиля ----------

_PROFILE_PHONE_KEYS = {"phones", "phone"}
_PROFILE_EMAIL_KEYS = {"emails", "email"}
_PROFILE_NAME_KEYS = {"name", "full_name", "author"}
_PROFILE_DATE_KEYS = {"date", "date_of_birth", "dob"}
_PROFILE_URL_KEYS = {"url", "links"}
_PROFILE_CATEGORY_KEYS = {"category", "section", "subject"}


def build_profile_from_record(record: dict, source_file: str = "") -> dict[str, Any]:
    """Строит профиль из одной записи импорта (карточка + текст)."""
    fields = record.get("fields", {})
    text = record.get("text", "")
    combined_text = text
    result: dict[str, Any] = {}

    # Базовые поля из fields
    name = fields.get("name", "")
    if name:
        result["full_name"] = _norm(name)
        parts = _norm(name).split()
        if len(parts) >= 3:
            result["surname"] = parts[0]
            result["first_name"] = parts[1]
            result["patronymic"] = parts[2]
        elif len(parts) == 2:
            result["surname"] = parts[0]
            result["first_name"] = parts[1]

    phones = fields.get("phones") or []
    if phones:
        result["phones"] = _unique([_norm_phone(p) or p for p in phones])

    emails = fields.get("emails") or []
    if emails:
        result["emails"] = _unique([_norm(e) for e in emails])

    # Дата из fields или record
    date = fields.get("date", "") or record.get("date", "")
    if date:
        result["date_of_birth"] = _norm_date(date) or _norm(date)

    # URL
    url = record.get("url", "")
    if url:
        result["vk_url"] = url if url.startswith("http") else "https://" + url

    # Извлечение полей из текста
    extracted = extract_fields_from_text(combined_text)
    for k, v in extracted.items():
        if k in result and isinstance(result[k], list) and isinstance(v, list):
            result[k] = _unique(result[k] + v)
        elif k not in result or not result[k]:
            result[k] = v

    # Мета
    if source_file:
        result["source_files"] = [source_file]

    return result


def merge_profiles(profiles: list[dict]) -> dict[str, Any]:
    """Объединяет несколько профилей одного человека в один."""
    if not profiles:
        return {}
    if len(profiles) == 1:
        return profiles[0]

    merged: dict[str, Any] = {}
    for p in profiles:
        for k, v in p.items():
            if k in ("source_files",):
                existing = merged.get(k, [])
                if isinstance(v, list):
                    existing = existing + v
                else:
                    existing.append(str(v))
                merged[k] = _unique(existing)
            elif k in ("phones", "emails", "name_variants"):
                existing = merged.get(k, [])
                if isinstance(v, list):
                    existing = existing + v
                merged[k] = _unique(existing)
            elif k in ("court_cases", "enforcement_proceedings", "vehicles",
                        "real_estate", "career_history", "businesses",
                        "media_mentions", "reviews"):
                existing = merged.get(k, [])
                if isinstance(v, list):
                    existing = existing + v
                merged[k] = existing
            elif k in ("enforcement_debt_total", "court_debt_total",
                        "tax_debt_total"):
                existing = merged.get(k, 0)
                try:
                    merged[k] = max(existing or 0, v or 0)
                except TypeError:
                    merged[k] = v
            elif not merged.get(k):
                merged[k] = v
    return merged


# ---------- оценка заполненности ----------

_MAJOR_FIELDS = [
    "full_name", "date_of_birth", "inn", "snils",
    "phones", "emails", "registration_address",
    "passport_series", "passport_number",
    "court_cases", "enforcement_proceedings",
    "vehicles", "real_estate",
    "current_employer", "businesses",
    "vk_url", "instagram_url", "telegram",
]


def completeness_score(profile: dict) -> float:
    """Оценка заполненности профиля (0.0–1.0)."""
    filled = 0
    for field in _MAJOR_FIELDS:
        val = profile.get(field)
        if isinstance(val, (list, dict)):
            if val:
                filled += 1
        elif val:
            filled += 1
    return round(filled / len(_MAJOR_FIELDS), 2)


def confidence_level(score: float) -> str:
    """Уровень достоверности по оценке заполненности."""
    if score >= 0.6:
        return "high"
    if score >= 0.3:
        return "medium"
    return "low"
