"""FSM-состояния для онбординга и редактирования настроек."""
from aiogram.fsm.state import State, StatesGroup


class AuthStates(StatesGroup):
    """Авторизация в TG-боте."""
    waiting_password = State()


class SetupStates(StatesGroup):
    """Первоначальная настройка FunPay-подключения после авторизации."""
    waiting_golden_key = State()


class EditStates(StatesGroup):
    """Редактирование настраиваемых полей через меню."""
    editing_text = State()
    editing_int = State()
    editing_template_keyword = State()
    editing_template_response = State()
    editing_blacklist_nick = State()
    editing_ai_instruction = State()
    editing_ai_key = State()
