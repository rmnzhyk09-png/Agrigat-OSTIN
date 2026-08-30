"""Reply-клавиатура на русском: кнопки-действия вместо /команд.

Роутер подключается ПОСЛЕДНИМ: пока активен какой-то диалог (FSM),
текст кнопки обработает обработчик этого диалога; в спокойном состоянии
кнопки меню выполняют то же действие, что и соответствующие команды.
"""
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .start import RU_BUTTONS, dispatch_menu

router = Router()


@router.message(F.text.in_(RU_BUTTONS))
async def menu_button(message: Message, state: FSMContext):
    """Кнопка главного меню → действие."""
    action = RU_BUTTONS[message.text]
    await dispatch_menu(message, state, action)