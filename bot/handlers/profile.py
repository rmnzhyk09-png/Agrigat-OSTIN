"""Поиск и показ профилей: /profile Иванов.

После импорта файла бот автоматически извлекает структурированные данные
(ФИО, телефоны, email, документы, связи) и сохраняет в таблицу db_profiles.
Команда /profile позволяет искать профиль по ФИО или телефону и показать
карточку с полным набором полей.
"""
import logging

from aiogram import F, Router
from aiogram.types import Message

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


@router.message(F.text.startswith("/profile"))
async def cmd_profile(message: Message):
    """Поиск профиля по ФИО или телефону."""
    text = (message.text or "").strip()
    await run_profile(message, text.replace("/profile", "").strip())


async def run_profile(message: Message, query: str):
    """Поиск профиля (используется /profile и «живой» кнопкой меню)."""
    from ..dbimport.query import search_profiles, parse_search_field

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
