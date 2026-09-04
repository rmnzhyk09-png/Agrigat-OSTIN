"""Поиск и показ профилей: /profile Иванов.

После импорта файла бот автоматически извлекает структурированные данные
(ФИО, телефоны, email, документы, связи) и сохраняет в таблицу db_profiles.
Команда /profile позволяет искать профиль по ФИО или телефону и показать
карточку с полным набором полей.
"""
import json
import logging

from aiogram import F, Router
from aiogram.types import Message

from ..db.database import SyncSessionLocal
from ..db.models import DbProfile

logger = logging.getLogger(__name__)
router = Router()


def _esc_name(pf: dict) -> str:
    name = pf.get("full_name") or "Без имени"
    return name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_supabase(pf: dict) -> str:
    """Форматирует профиль из Supabase (dict) в читаемое сообщение."""
    lines = []
    if pf.get("date_of_birth"):
        lines.append(f"Дата рождения: {pf['date_of_birth']}")
    phones = pf.get("phones") or []
    if phones:
        lines.append("Телефоны: " + ", ".join(phones))
    emails = pf.get("emails") or []
    if emails:
        lines.append("Email: " + ", ".join(emails))
    if pf.get("registration_address"):
        lines.append(f"Адрес: {pf['registration_address']}")
    if pf.get("inn"):
        lines.append(f"ИНН: {pf['inn']}")
    if pf.get("snils"):
        lines.append(f"СНИЛС: {pf['snils']}")
    if pf.get("passport_series") or pf.get("passport_number"):
        lines.append(f"Паспорт: {(pf.get('passport_series') or '')} {(pf.get('passport_number') or '')}".strip())
    vehicles = pf.get("vehicles") or []
    for v in vehicles[:3]:
        if isinstance(v, dict):
            lines.append(f"Авто: {v.get('make', '')} {v.get('plate', '')}".strip())
    if pf.get("court_cases_count"):
        lines.append(f"Судебных дел: {pf['court_cases_count']}")
    if pf.get("enforcement_debt_total"):
        lines.append(f"Долги приставам: {pf['enforcement_debt_total']} руб.")
    if pf.get("criminal_record"):
        lines.append("Судимость: есть")
    if pf.get("bankruptcy_status"):
        lines.append(f"Банкротство: {pf['bankruptcy_status']}")
    if pf.get("current_employer"):
        lines.append(f"Работа: {pf['current_employer']}")
    confidence = pf.get("confidence") or ""
    if confidence:
        emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(confidence, "⚪")
        lines.append(f"\nДостоверность: {emoji} {confidence}")
    return "\n".join(lines)


def _fmt(profile: DbProfile) -> str:
    """Форматирует профиль в читаемое сообщение."""
    lines = []
    name = profile.full_name or "Без имени"
    lines.append(f"<b>{name}</b>")
    lines.append("")

    # Личные данные
    personal = []
    if profile.date_of_birth:
        personal.append(f"Дата рождения: {profile.date_of_birth}")
    if profile.age:
        personal.append(f"Возраст: {profile.age}")
    if profile.gender:
        personal.append(f"Пол: {profile.gender}")
    if profile.citizenship:
        personal.append(f"Гражданство: {profile.citizenship}")
    if profile.place_of_birth:
        personal.append(f"Место рождения: {profile.place_of_birth}")
    if personal:
        lines.append("<b>Личные данные:</b>")
        lines.extend(personal)
        lines.append("")

    # Документы
    docs = []
    if profile.passport_series and profile.passport_number:
        docs.append(f"Паспорт: {profile.passport_series} {profile.passport_number}")
    if profile.passport_issued_by:
        docs.append(f"Выдан: {profile.passport_issued_by}")
    if profile.passport_issue_date:
        docs.append(f"Дата выдачи: {profile.passport_issue_date}")
    if profile.inn:
        docs.append(f"ИНН: {profile.inn}")
    if profile.snils:
        docs.append(f"СНИЛС: {profile.snils}")
    if profile.driver_license:
        docs.append(f"Вод. удостоверение: {profile.driver_license}")
    if docs:
        lines.append("<b>Документы:</b>")
        lines.extend(docs)
        lines.append("")

    # Адреса
    addrs = []
    if profile.registration_address:
        addrs.append(f"Прописка: {profile.registration_address}")
    if profile.actual_address:
        addrs.append(f"Проживание: {profile.actual_address}")
    if addrs:
        lines.append("<b>Адреса:</b>")
        lines.extend(addrs)
        lines.append("")

    # Контакты
    contacts = []
    if profile.phones:
        phones = profile.phones if isinstance(profile.phones, list) else []
        contacts.append("Телефоны: " + ", ".join(phones))
    if profile.emails:
        emails = profile.emails if isinstance(profile.emails, list) else []
        contacts.append("Email: " + ", ".join(emails))
    if profile.telegram:
        tg = profile.telegram
        if isinstance(tg, dict):
            contacts.append(f"Telegram: {tg.get('username', '')} ({tg.get('url', '')})")
    if profile.social_handles:
        sh = profile.social_handles
        if isinstance(sh, dict):
            for platform, handle in sh.items():
                contacts.append(f"{platform}: {handle}")
    if contacts:
        lines.append("<b>Контакты:</b>")
        lines.extend(contacts)
        lines.append("")

    # Связи
    if profile.family_status:
        lines.append(f"<b>Семейное положение:</b> {profile.family_status}")
        lines.append("")
    if profile.relatives:
        rels = profile.relatives if isinstance(profile.relatives, list) else []
        if rels:
            lines.append("<b>Родственники:</b>")
            for r in rels:
                if isinstance(r, dict):
                    lines.append(f"  • {r.get('name', '?')} ({r.get('relation', '')})")
            lines.append("")

    # Недвижимость
    if profile.real_estate:
        re_list = profile.real_estate if isinstance(profile.real_estate, list) else []
        if re_list:
            lines.append("<b>Недвижимость:</b>")
            for r in re_list:
                if isinstance(r, dict):
                    addr = r.get("address", "")
                    area = r.get("area_m2", "")
                    lines.append(f"  • {addr}" + (f", {area} м²" if area else ""))
            lines.append("")

    # Транспорт
    if profile.vehicles:
        v_list = profile.vehicles if isinstance(profile.vehicles, list) else []
        if v_list:
            lines.append("<b>Транспорт:</b>")
            for v in v_list:
                if isinstance(v, dict):
                    make = v.get("make", "")
                    model = v.get("model", "")
                    plate = v.get("plate", "")
                    year = v.get("year", "")
                    car_str = f"  • {make} {model}".strip()
                    if year:
                        car_str += f" ({year})"
                    if plate:
                        car_str += f" [{plate}]"
                    lines.append(car_str)
            lines.append("")

    # Суды и долги
    courts = []
    if profile.court_cases_count:
        courts.append(f"Судебных дел: {profile.court_cases_count}")
    if profile.court_debt_total:
        courts.append(f"Судебные долги: {profile.court_debt_total} руб.")
    if profile.enforcement_debt_total:
        courts.append(f"Долги приставам: {profile.enforcement_debt_total} руб.")
    if profile.criminal_record:
        courts.append("Судимость: ЕСТЬ")
    if profile.bankruptcy_status:
        courts.append(f"Банкротство: {profile.bankruptcy_status}")
    if courts:
        lines.append("<b>Суды и долги:</b>")
        lines.extend(courts)
        lines.append("")

    # Работа и бизнес
    work = []
    if profile.current_employer:
        work.append(f"Место работы: {profile.current_employer}")
    if profile.position:
        work.append(f"Должность: {profile.position}")
    if profile.businesses:
        biz = profile.businesses if isinstance(profile.businesses, list) else []
        for b in biz:
            if isinstance(b, dict):
                work.append(f"Бизнес: {b.get('name', '')} ({b.get('role', '')})")
    if work:
        lines.append("<b>Работа и бизнес:</b>")
        lines.extend(work)
        lines.append("")

    # Реестры
    registries = []
    if profile.exit_ban:
        registries.append("Ограничение на выезд: ДА")
    if profile.disqualified:
        registries.append("Дисквалификация: ДА")
    if profile.efrsb_status:
        registries.append(f"ЕФРСБ: {profile.efrsb_status}")
    if registries:
        lines.append("<b>Реестры и ограничения:</b>")
        lines.extend(registries)
        lines.append("")

    # Мета
    meta = []
    if profile.overall_confidence:
        conf = profile.overall_confidence
        emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(conf, "⚪")
        meta.append(f"Достоверность: {emoji} {conf}")
    if profile.completeness_score:
        meta.append(f"Заполненность: {profile.completeness_score}")
    if profile.source_files:
        files = profile.source_files if isinstance(profile.source_files, list) else []
        if files:
            meta.append(f"Источники: {', '.join(files)}")
    if meta:
        lines.append("<i>" + " · ".join(meta) + "</i>")

    return "\n".join(lines)


@router.message(F.text.startswith("/profile"))
async def cmd_profile(message: Message):
    """Поиск профиля по ФИО или телефону."""
    text = (message.text or "").strip()
    await run_profile(message, text.replace("/profile", "").strip())


async def run_profile(message: Message, query: str):
    """Поиск профиля (используется /profile и «живой» кнопкой меню)."""
    from ..dbimport.query import search_profiles, parse_search_field
    from ..dbimport.store import SupabaseStore

    query = (query or "").strip()
    if not query:
        await message.answer(
            "Поиск профиля: <code>/profile Иванов</code>\n\n"
            "Можно искать по ФИО (частично) или по номеру телефона.\n"
            "Примеры:\n"
            "• <code>/profile Иванов</code>\n"
            "• <code>/profile Иванов Иван</code>\n"
            "• <code>/profile +79001234567</code>",
            parse_mode="HTML",
        )
        return

    field, value = parse_search_field(query)
    items = []
    try:
        items = await search_profiles(value or query, limit=5, field=field)
    except Exception as ex:
        logger.exception("profile search error")
        await message.answer(f"Ошибка поиска: {ex}")
        return

    if not items:
        await message.answer(
            f"По «{query}» в базе профилей ничего нет.\n\n"
            "Убедитесь, что данные были импортированы через /import.",
            parse_mode="HTML",
        )
        return

    if len(items) == 1:
        pf = items[0].get("profile", {})
        text = items[0].get("text", "")
        await message.answer(f"<b>{_esc_name(pf)}</b>\n\n{_fmt_supabase(pf)}",
                             parse_mode="HTML", disable_web_page_preview=True)
    else:
        parts = [f"Найдено <b>{len(items)}</b> профилей:\n"]
        for i, it in enumerate(items, 1):
            pf = it.get("profile", {})
            name = pf.get("full_name") or "Без имени"
            phones = pf.get("phones") or []
            phone_str = phones[0] if phones else "—"
            parts.append(f"{i}. <b>{name}</b> · {phone_str}")
        parts.append("\nДля подробностей: <code>/profile ФИО</code>")
        await message.answer("\n".join(parts), parse_mode="HTML")
