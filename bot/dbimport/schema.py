"""Распознавание структуры таблиц и типов полей.

Что умеет:
  * определять раскладку: горизонтальные таблицы (колонки = поля, строки =
    записи) и вертикальные (столбец «поле: значение», записи идут блоками
    одна под другой);
  * распознавать типы полей: ФИО, телефон, email, дата, ссылка, мессенджер;
  * нормализовать контакты (телефоны → +7XXXXXXXXXX, email → нижний регистр);
  * собирать «карточку» человека в текст записи, чтобы бот мог искать по
    номеру телефона/ФИО обычным поиском по тексту.
"""
import logging
import re

logger = logging.getLogger(__name__)

# ---------- регулярные выражения ----------

# Телефонный «токен»: непрерывный кусок из цифр, пробелов, '-', '.' и скобок.
# Единый проход вместо двух регексов: фрагмент RU-номера не съедает
# международный номер («+7(912)… +995 514 03 33 99» больше не слипается).
_PHONE_TOKEN_RE = re.compile(r"(?<![\d+.])[+\d][\d\s\-().]{6,19}(?![\d])")
# Связки цифр с точками/слешами — почти наверняка даты, а не номера.
_DATE_LIKE_RE = re.compile(r"(?:\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# ФИО: 2–3 слова с заглавной буквы; допустимы инициалы «И.И.»
_NAME_RE = re.compile(
    r"^[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
    r"(?:\s+(?:[А-ЯЁ][а-яё]+|[А-ЯЁ]\.)){1,2}$"
)
_NAME_ENG_RE = re.compile(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}$")

# «Поле: значение» (TXT-вертикаль)
_LABEL_VALUE_RE = re.compile(r"^([^:{}]{1,30})[:\t]+\s*(.+)$")

# Слова, которые не стоит принимать за начало ФИО
_NAME_EXCLUDE = {"город", "страна", "адрес", "улица", "область", "регион",
                 "район", "номер", "телефон", "email", "почта", "сайт",
                 "компания", "организация"}

# Поля-«контакты», участвующие в разбивке на записи
_CONTACT_TYPES = ("name", "phone", "email", "date", "url", "social")

# Красивое имя типа для вывода в карточке
_SET_KEYS = {
    "name": "ФИО", "phone": "Телефон", "email": "Email", "date": "Дата",
    "url": "Ссылка", "social": "Связь",
}


# ---------- нормализация ----------

def _clean(text) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return " ".join(text.split())


def _norm_label(label) -> str:
    return (label or "").strip()


# ---------- типы полей ----------

_FIELD_RULES: list[tuple[str, tuple]] = [
    ("email", ("email", "e-mail", "e mail", "почт", "почтов", "mail", "майл")),
    ("phone", ("телефон", "тел. ", "тел№", "моб", "сотов", "сотовый",
               "phone", "mobile", "cell", "контактн", "связь")),
    ("date", ("дата", "время", "date", "datetime", "time", "created",
              "published", "рожден", "день рождения")),
    ("url", ("url", "link", "ссылк", "сайт", "профил", "profile", "страниц",
             "page", "http", "instagram", "инстаграм", "вк ",
             "twitter", "твиттер")),
    ("social", ("telegram", "телеграм", "telega", "тг", "whatsapp", "вотсап",
                "вацап", "viber", "вайбер", "watsapp", "ватсап", "skype")),
    ("category", ("категори", "раздел", "субъект", "рубрик", "отрасл",
                  "направлен", "тип", "группа", "category", "section",
                  "subject", "group", "topic")),
    ("author", ("автор", "логин", "ник", "никнейм", "nickname", "nick",
                "author", "sender", "channel", "username", "пользовател",
                "user", "имена для")),
    ("id", ("порядков", "п/п", "идентификат", "record id", "код")),
    ("name", ("фио", "ф.и.о", "фам", "отчеств", "полное имя", "полн.",
              "full name", "fullname", "fio", "имя ", "имя,", "name,")),
]

_ID_EXACT = {"id", "no", "n", "num", "number", "код", "номер", "индекс"}


def infer_field(label: str):
    """Определяет тип поля по заголовку колонки/имени поля.

    Возвращает одно из: name, phone, email, date, url, social, category,
    author, id или None, если поле не распознано.
    """
    s = _norm_label(label).lower()
    if not s:
        return None
    if s in _ID_EXACT:
        return "id"
    if re.fullmatch(r"[№#]?\d{1,6}", s) or re.fullmatch(r"col\d+", s):
        return "id"
    for ftype, needles in _FIELD_RULES:
        for needle in needles:
            if needle in s:
                return ftype
    return None


# ---------- контакты: телефон / email ----------

def extract_phones(value) -> list[str]:
    """Все номера в тексте, нормализованные в +7XXXXXXXXXX / +CCXXXXXXXXX."""
    text = _clean(value)
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _PHONE_TOKEN_RE.finditer(text):
        tok = m.group(0).strip()
        digits = re.sub(r"\D", "", tok)
        if len(digits) < 8 or len(digits) > 15:
            continue
        if _DATE_LIKE_RE.search(tok):
            continue
        if tok.startswith("+"):
            p = "+" + digits
        elif len(digits) == 10:
            p = "+7" + digits
        elif len(digits) == 11 and digits[0] in ("7", "8"):
            p = "+7" + digits[1:]
        else:
            continue
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def extract_emails(value) -> list[str]:
    """Все email в тексте (уникальные, нижний регистр)."""
    text = _clean(value)
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _EMAIL_RE.findall(text):
        e = m.lower()
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


# ---------- ФИО ----------

def looks_like_name(value) -> bool:
    """Похоже ли значение на ФИО (2+ слова с заглавной буквы)."""
    s = _clean(value)
    if not s or len(s) > 60 or "@" in s or "\n" in s:
        return False
    if not re.search(r"[А-ЯЁA-Z]", s):
        return False
    if not (_NAME_RE.match(s) or _NAME_ENG_RE.match(s)):
        return False
    first = s.split()[0].lower()
    return first not in _NAME_EXCLUDE


# ---------- раскладка таблиц ----------

def has_contact_columns(headers: list) -> bool:
    """Есть ли среди заголовков «контактные» поля (ФИО/телефон/email)."""
    return any(infer_field(str(h).strip()) in ("name", "phone", "email")
               for h in headers if (h or "").strip())


def _matrix_columns(rows: list[dict]) -> tuple[list[str], list[str]]:
    """Первые две ячейки каждой строки в порядке колонок."""
    labels: list[str] = []
    values: list[str] = []
    for row in rows[:300]:
        cells = [row[k] for k in row.keys()]
        cells = [(c or "").strip() for c in cells]
        if not any(cells):
            continue
        labels.append(cells[0] if cells else "")
        values.append(cells[1] if len(cells) > 1 else "")
    return labels, values


def detect_vertical_matrix(rows: list[dict]) -> bool:
    """Вертикальная матрица: первая колонка = имена полей, вторая = значения."""
    if not rows:
        return False
    keys = list(rows[0].keys())
    if len(keys) < 2:
        return False
    labels, values = _matrix_columns(rows)
    total = len(labels)
    if total < 4:
        return False
    known = sum(1 for lb in labels if infer_field(lb))
    if known / total < 0.5:
        return False
    types = {infer_field(lb) for lb in labels if infer_field(lb)}
    personal = sum(1 for v in values if extract_phones(v) or extract_emails(v)
                   or looks_like_name(v))
    if len(types) >= 2:
        return True
    return personal / total >= 0.3


# ---------- сборщики записей ----------

def _unique(items: list) -> list:
    out: list = []
    for it in items:
        if it not in out:
            out.append(it)
    return out


def horizontal_records(rows: list[dict], source: str) -> list[dict]:
    """Горизонтальная таблица с контактными полями: строка = запись.

    Колонки типа ФИО/Телефон/Email складываются в структурированную карточку,
    а текст записи собирается так, чтобы по нему работал поиск.
    """
    if not rows:
        return []
    headers = list(rows[0].keys())
    fmap = {h: infer_field(str(h).strip()) for h in headers}

    records: list[dict] = []
    for row in rows:
        name_parts: list[str] = []
        phones: list[str] = []
        emails: list[str] = []
        date = url = author = section = ""
        others: list[tuple[str, str]] = []
        for h in headers:
            raw = row.get(h)
            v = _clean(str(raw) if raw is not None else "")
            if not v:
                continue
            t = fmap.get(h)
            if t == "name":
                for part in re.split(r"[\s,/]+", v):
                    if part and part not in name_parts:
                        name_parts.append(part)
            elif t == "phone":
                phones += extract_phones(v)
            elif t == "email":
                emails += extract_emails(v)
            elif t == "date" and not date:
                date = v
            elif t == "url" and not url:
                url = v
            elif t == "author" and not author:
                author = v
            elif t == "category" and not section:
                section = v
            elif t == "id":
                continue
            else:
                others.append((_norm_label(str(h).strip()) or str(h).strip(), v))

        name = " ".join(name_parts).strip()
        phones = _unique(phones)
        emails = _unique(emails)
        if not (name or phones or emails):
            continue

        lines: list[str] = []
        if name:
            lines.append(f"ФИО: {name}")
        for p in phones:
            lines.append(f"Телефон: {p}")
        for e in emails:
            lines.append(f"Email: {e}")
        for label, val in others:
            lines.append(f"{label}: {val}")

        records.append({
            "source": str(source or ""),
            "section": section,
            "author": author or name,
            "text": "\n".join(lines),
            "url": url,
            "date": date,
            "fields": {"name": name, "phones": phones, "emails": emails},
        })
    return records


def group_pairs(pairs: list[tuple[str, str]], source: str = "") -> list[dict]:
    """Группирует пары (поле, значение) в записи-карточки.

    Новая запись начинается, когда снова встречается «ФИО» (начало следующей
    карточки). Повторяющиеся телефоны/email остаются в той же записи.
    """
    records: list[dict] = []
    cur: list[tuple[str, str, str]] = []
    has_name = False
    for label, value in pairs:
        ftype = infer_field(label)
        if ftype in _CONTACT_TYPES:
            key = ftype
        else:
            key = "text:" + _norm_label(label).lower()
        if key == "name" and has_name:
            if cur:
                records.append(cur)
            cur = []
            has_name = False
        if ftype == "name":
            has_name = True
        cur.append((key, label, value))
    if cur:
        records.append(cur)
    return [compose_pair_record(g) for g in records]


def compose_pair_record(pairs: list[tuple[str, str, str]]) -> dict:
    """Собирает запись-карточку из групп пар (key, label, value)."""
    grouped: dict[str, list[tuple[str, str]]] = {}
    for key, label, value in pairs:
        grouped.setdefault(key, []).append((label, value))

    lines: list[str] = []
    name = ""
    phones: list[str] = []
    emails: list[str] = []
    url = ""
    for key in ("name", "phone", "email", "date", "url", "social"):
        for label, value in grouped.get(key, []):
            pretty = _SET_KEYS.get(key, label) or label
            if key == "email":
                found = extract_emails(value)
                emails += found or [value]
                for e in (found or [value]):
                    lines.append(f"{pretty}: {e}")
                continue
            if key == "phone":
                found = extract_phones(value)
                phones += found or [value]
                for p in (found or [value]):
                    lines.append(f"{pretty}: {p}")
                continue
            if key == "name" and not name:
                name = value
            if key == "url" and not url:
                url = value
            lines.append(f"{pretty}: {value}")
    for key, items in grouped.items():
        if key in _SET_KEYS:
            continue
        for label, value in items:
            lines.append(f"{label}: {value}")

    return {
        "source": "",
        "section": "",
        "author": name,
        "text": "\n".join(lines),
        "url": url,
        "date": "",
        "fields": {"name": name, "phones": _unique(phones),
                   "emails": _unique(emails)},
    }


def vertical_matrix_records(rows: list[dict], source: str) -> list[dict]:
    """Вертикальная матрица «поле | значение» -> записи-карточки.

    DictReader мог съесть первую строку как заголовок, поэтому значения берём
    по позициям колонок, а не по именам ключей.
    """
    pairs: list[tuple[str, str]] = []
    for row in rows:
        cells = [row[k] for k in row.keys()]
        cells = [(c or "").strip() for c in cells]
        if len(cells) < 2 or not any(cells):
            continue
        label, value = cells[0], cells[1]
        if label and value:
            pairs.append((label, value))
    return group_pairs(pairs, str(source or ""))


# ---------- текстовые файлы ----------

def _parse_label_value(line: str):
    m = _LABEL_VALUE_RE.match(line.strip())
    if not m:
        return None
    label, value = m.group(1).strip(), m.group(2).strip()
    if not label or not value:
        return None
    if label.lower().startswith(("http", "www.")):
        return None
    if re.search(r"\d", label) and infer_field(label) is None:
        return None
    return label, value


def compose_loose_block(lines: list[str]) -> dict:
    """Блок без меток: первая строка-ФИО, дальше телефоны/email/прочее."""
    texts = [ln.strip() for ln in lines if ln.strip()]
    if not texts:
        return {}
    name = ""
    phones: list[str] = []
    emails: list[str] = []
    rest: list[str] = []
    for ln in texts:
        if not name and looks_like_name(ln):
            name = ln
            continue
        p = extract_phones(ln)
        e = extract_emails(ln)
        if p or e:
            phones += p
            emails += e
        else:
            rest.append(ln)

    lines_out: list[str] = []
    if name:
        lines_out.append(f"ФИО: {name}")
    for p in _unique(phones):
        lines_out.append(f"Телефон: {p}")
    for e in _unique(emails):
        lines_out.append(f"Email: {e}")
    lines_out += rest
    return {
        "source": "",
        "section": "",
        "author": name,
        "text": "\n".join(lines_out),
        "url": "",
        "date": "",
        "fields": {"name": name, "phones": _unique(phones),
                   "emails": _unique(emails)},
    }


def split_txt_records(lines: list[str]) -> list[dict] | None:
    """Разбивает текст на записи-карточки.

    Возвращает None, если структуру не распознали (обычный построчный txt).
    """
    cleaned = [ln.strip() for ln in lines]
    non_empty = [ln for ln in cleaned if ln]
    if not non_empty:
        return []
    pair_count = sum(1 for ln in non_empty if _parse_label_value(ln))
    name_count = sum(1 for ln in non_empty if looks_like_name(ln))
    if pair_count == 0 and name_count == 0:
        return None

    # пустые строки = границы блоков
    blocks: list[list[str]] = [[]]
    for ln in cleaned:
        if ln:
            blocks[-1].append(ln)
        elif blocks[-1]:
            blocks.append([])
    blocks = [b for b in blocks if b]

    records: list[dict] = []
    for block in blocks:
        pairs: list[tuple[str, str]] = []
        loose: list[str] = []
        for ln in block:
            pv = _parse_label_value(ln)
            if pv:
                pairs.append(pv)
            else:
                loose.append(ln)

        if pairs and len(pairs) >= len(loose):
            records.extend(group_pairs(pairs))
            if loose:
                rec = compose_loose_block(loose)
                if rec:
                    records.append(rec)
        else:
            cur: list[str] = []
            for ln in block:
                if looks_like_name(ln) and any(looks_like_name(x) for x in cur):
                    records.append(compose_loose_block(cur))
                    cur = []
                cur.append(ln)
            if cur:
                records.append(compose_loose_block(cur))
    return [r for r in records if r]


# ---------- сводка по распознаванию ----------

def summarize_contacts(records: list[dict]) -> dict:
    """Считает, сколько карточек/контактов распознано."""
    names = sum(1 for r in records if r.get("fields", {}).get("name"))
    phones = sum(len(r.get("fields", {}).get("phones") or []) for r in records)
    emails = sum(len(r.get("fields", {}).get("emails") or []) for r in records)
    return {"names": names, "phones": phones, "emails": emails}