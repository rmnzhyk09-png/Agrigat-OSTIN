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
    # ОГРНИП (15 цифр), ОГРН (13), ИНН (10/12) и прочие справочники не являются
    # телефон: длина настоящего номера — 7..13 цифр с учётом кода страны.
    if not (7 <= len(digits) <= 13):
        return None
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
# Строки вида "Телефон: +7..." / "Телефон: 7900..." (записи Очко бога/getcontact)
_TELEPHONE_LINE_RE = re.compile(
    r"(?:телефон|тел\.?|моб\.?|phone)[ыа-яё]*[\s]*[:=\-\s]+\s*"
    r"([+]?\d[\d\s().\-]{5,20})",
    re.IGNORECASE,
)
# Телефоны, обёрнутые в .csv-строку: "+7(...), +7(...)"
_PHONE_TOKENS_RE = re.compile(r"[+]?\d[\d\s().\-]{5,20}")
# Имя/фамилия из записей Очко бога ("first_name: X", "last_name: Y")
_LABEL_FIRST_RE = re.compile(
    r"(?:first_name|имя)[\s]*[:=\-\s]+\s*([A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё@'.\-]{0,60})",
    re.IGNORECASE,
)
_LABEL_LAST_RE = re.compile(
    r"(?:last_name|фамилия)[\s]*[:=\-\s]+\s*([A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё@'.\-]{0,60})",
    re.IGNORECASE,
)
_LABEL_MIDDLE_RE = re.compile(
    r"(?:middle_name|отчество)[\s]*[:=\-\s]+\s*([A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё@'.\-]{0,60})",
    re.IGNORECASE,
)

# ФИО из "ИП: 'Петров Пётр'", "ФИО: Петров Пётр", "Имя: ..." —
# записи Taxi_ru/Georgia/500+500, где имя лежит в тексте, а не в fields.
# Сепаратор НЕ пересекает перевод строки (иначе «ИП\nБатуми» уходит в имя).
_LABEL_FIO_RE = re.compile(
    r"(?:ФИО|имя|ип|владелец|директор|физическо[еи]\s+лиц[ао])"
    r"[^\S\r\n]*[:=]?[^\S\r\n]*[\"']?[^\S\r\n]*"
    r"([\wА-ЯЁа-яё][\wА-ЯЁа-яё' .\-]{1,80})",
    re.IGNORECASE,
)

# Город/регион из "Город: ...", "Населённый пункт: ...", "Район: ..."
_LABEL_CITY_RE = re.compile(
    r"(?:город|гор\.?|населённ\w+\s+пункт|населенный\s+пункт|регион|район)[\s]*[:=\-\s]+\s*"
    r"([А-ЯЁA-Z][А-ЯЁа-яёA-Za-z .\-]{2,60})",
    re.IGNORECASE,
)

# Знак «+» после имени ("Помазная Лидия Сергеевна +", "Светлана +" —
# признак вызванного/подтверждённого контакта в базе типа 500+500).
_LINE_NAME_PLUS_RE = re.compile(
    r"(?m)^\s*([\wА-ЯЁа-яё][\wА-ЯЁа-яё' .\-]{1,100}?)\s*\+\s*$",
)


def _is_noise_name(value: str) -> bool:
    """Отбрасывает мусорные значения (точки, одиночные буквы, пустые)."""
    v = (value or "").strip(" .-")
    return len(v) < 2


def extract_fields_from_text(text: str) -> dict[str, Any]:
    """Извлекает структурированные поля из текста записи."""
    result: dict[str, Any] = {}
    if not text:
        return result

    # Телефоны из полей/строк "Телефон: ..." и токенов "+7..."
    # (Очко бога / getcontact: сами номера лежат в тексте, а не в fields)
    tel_set: set[str] = set()
    for grp in _TELEPHONE_LINE_RE.findall(text):
        norm = _norm_phone(grp)
        if norm:
            tel_set.add(norm)
    # Дополнительный проход: любые "+7..." токены в тексте.
    for tok in _PHONE_TOKENS_RE.findall(text):
        norm = _norm_phone(tok)
        if norm:
            tel_set.add(norm)
    if tel_set:
        result["phones"] = sorted(tel_set)

    # Имя/фамилия из "first_name:/last_name:" (Очко бога)
    mf = _LABEL_FIRST_RE.search(text)
    if mf and not _is_noise_name(mf.group(1)):
        result["first_name"] = mf.group(1).strip()
    ml = _LABEL_LAST_RE.search(text)
    if ml and not _is_noise_name(ml.group(1)):
        result["last_name"] = ml.group(1).strip()
    mm = _LABEL_MIDDLE_RE.search(text)
    if mm and not _is_noise_name(mm.group(1)):
        result["patronymic"] = mm.group(1).strip()

    # ФИО/ИП-владелец: "ИП: 'Ситников Юрий Андреевич'" (Taxi_ru),
    # "ФИО: Эдвард Химшиашвили" (Georgia) — если нет явного first/last.
    if not result.get("first_name") and not result.get("last_name"):
        mfio = _LABEL_FIO_RE.search(text)
        if mfio:
            cand = _norm(mfio.group(1)).strip(" \"'")
            if not _is_noise_name(cand) and "(" not in cand:
                # Разбиваем "Фамилия Имя Отчество" → surname/first/patronymic
                parts = cand.split()
                if parts:
                    result["full_name"] = cand
                    if len(parts) >= 3:
                        result["surname"] = parts[0]
                        result["first_name"] = parts[1]
                        result["patronymic"] = parts[2]
                    elif len(parts) == 2:
                        result["surname"] = parts[0]
                        result["first_name"] = parts[1]

    # Имя в строке вида "Помазная Лидия Сергеевна +" (500+500)
    if not result.get("full_name") and not result.get("first_name"):
        mplus = _LINE_NAME_PLUS_RE.search(text)
        if mplus:
            cand = _norm(mplus.group(1)).strip()
            parts = cand.split()
            if len(parts) >= 2 and not _is_noise_name(cand):
                result["full_name"] = cand
                result["surname"] = parts[0]
                result["first_name"] = parts[1]
                if len(parts) >= 3:
                    result["patronymic"] = parts[2]

    # Город (Taxi_ru): "Город: Нефтеюганск"
    mcity = _LABEL_CITY_RE.search(text)
    if mcity and not _is_noise_name(mcity.group(1)):
        city = _norm(mcity.group(1)).strip()
        if not any(fc in city for fc in ("город:", "г.:", "село", "деревня")):
            result["city"] = city
            # если нет жёсткого адреса — подставляем город как место регистрации
            if not result.get("registration_address"):
                result["registration_address"] = f"г. {city}"

    # ИНН
    inns = _INN_RE.findall(text)
    if inns:
        phone_digits = {re.sub(r"\D", "", p) for p in result.get("phones") or []}
        cands = [i for i in inns if i not in phone_digits]
        # 12-значный контакт-телефон (код страны + номер, напр. Грузия) —
        # не считать ИНН: телефоны старше и приоритетнее
        filtered = [c for c in cands if not (len(c) == 12 and c.startswith("99"))]
        if filtered:
            result["inn"] = filtered[0] if len(filtered) == 1 else filtered[:1]
        elif cands:
            result["inn"] = cands[0]

    # СНИЛС
    snils_raw = _SNILS_RE.findall(text)
    if snils_raw:
        # Защита от ложных срабатываний: 11-значный СНИЛС, который на самом
        # деле является телефоном ("+79529637711" == СНИЛС 11 цифр), не должен
        # уезжать в snils. Грешим в сторону телефона, если номер совпадает с
        # уже найденным телефоном или имеет префикс мобильного.
        cand = snils_raw[0]
        digits = re.sub(r"\D", "", cand)
        tel_digits = (result.get("phones") or [])
        tel_digits = [re.sub(r"\D", "", p) for p in tel_digits]
        flagged = digits in tel_digits or (
            len(digits) == 11 and digits.startswith(("79", "78", "89"))
            and "-" not in cand and " " not in cand
        )
        if not flagged:
            result["snils"] = digits

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

    # Имя из ролей first_name/last_name/patronymic (записи Очко бога):
    # в них имя лежит в тексте, а не в fields["name"], поэтому собираем ФИО.
    if not result.get("full_name"):
        surname = result.get("surname") or result.get("last_name") or ""
        fname = result.get("first_name") or ""
        pname = result.get("patronymic") or ""
        if fname or surname or pname:
            result["full_name"] = " ".join(x for x in (surname, fname, pname) if x)
            if result.get("surname") or result.get("last_name"):
                result["surname"] = surname
    if result.get("full_name") and not result.get("first_name"):
        # На случай "Иванов Иван": first_name = вторая часть
        parts = result["full_name"].split()
        if len(parts) >= 2 and not result.get("first_name"):
            result["first_name"] = parts[1]
            if len(parts) >= 3 and not result.get("patronymic"):
                result["patronymic"] = parts[2]

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
