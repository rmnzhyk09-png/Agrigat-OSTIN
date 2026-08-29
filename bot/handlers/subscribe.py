"""Подписка на ежедневный дайджест по тегам."""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..db import repo

router = Router()


class SubscribeState(StatesGroup):
    tags = State()


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, state: FSMContext):
    """Подписка на дайджест: спрашиваем теги."""
    await state.set_state(SubscribeState.tags)
    await message.answer(
        "Дайджест: ежедневная сводка упоминаний по тегам, в 9:00.\n\n"
        "Отправьте теги через запятую (до 3):\n"
        "<code>криптовалюта, технологии</code>"
    )


@router.message(SubscribeState.tags)
async def process_tags(message: Message, state: FSMContext):
    """Сохраняем подписку."""
    tags = [t.strip().lstrip("#") for t in (message.text or "").replace(",", "\n").split("\n")
            if t.strip() and t.strip() != "#"]
    tags = tags[:3]

    if not tags:
        await message.answer("Теги не распознаны. Пример: крипта, технологии")
        return

    await state.clear()
    try:
        await repo.set_subscription(message.from_user.id, message.from_user, tags, active=True)
        await message.answer(
            f"Подписка оформлена. Теги: {', '.join(tags)}.\n"
            "Дайджест ежедневно в 9:00. Отписка: /unsubscribe"
        )
    except Exception as ex:
        await message.answer(f"Не удалось сохранить подписку: {ex}")


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message):
    """Отписка."""
    try:
        await repo.set_subscription(message.from_user.id, message.from_user, [], active=False)
        await message.answer("Подписка отключена.")
    except Exception as ex:
        await message.answer(f"Ошибка: {ex}")
