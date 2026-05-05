"""
Авторизация в TG-боте (как на скрине).

Поток:
1. /start → если юзер не авторизован → просим ввести пароль или ключ менеджера
2. Получаем сообщение → проверяем → выдаём роль
3. Если уже авторизован → сразу /start с приветствием

Команда /menu доступна только авторизованным.
"""
from __future__ import annotations

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot.states import AuthStates, SetupStates
from bot.keyboards import kb_unauthorized, kb_main_menu
from config.settings import config_manager
from utils.logger import logger

router = Router(name="auth")


def is_authorized(user_id: int) -> bool:
    s = config_manager.settings.telegram
    return user_id in s.admin_ids or user_id in s.manager_ids


def is_admin(user_id: int) -> bool:
    return user_id in config_manager.settings.telegram.admin_ids


# ──────────────────────────────────────────────────────────────────────────────
# /start
# ──────────────────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(msg: types.Message, state: FSMContext) -> None:
    user_id = msg.from_user.id
    name = msg.from_user.first_name or "пользователь"

    if is_authorized(user_id):
        # Уже авторизован — короткое приветствие + подсказка про /menu
        await msg.answer(
            f"👋 С возвращением, <b>{name}</b>!\n\n"
            f"⚙️ Чтобы открыть меню, введите команду /menu"
        )
        await state.clear()
        return

    # Не авторизован — просим пароль
    text = (
        f"👋 Привет, <i>{name}</i>!\n\n"
        f"⛔ <b>Доступ запрещён</b>\n\n"
        f"Для авторизации введите <b>пароль администратора</b> "
        f"или <b>ключ регистрации менеджера</b>, который вы указали в настройках бота."
    )
    await msg.answer(text, reply_markup=kb_unauthorized())
    await state.set_state(AuthStates.waiting_password)


# ──────────────────────────────────────────────────────────────────────────────
# Ввод пароля
# ──────────────────────────────────────────────────────────────────────────────

@router.message(AuthStates.waiting_password)
async def password_received(msg: types.Message, state: FSMContext) -> None:
    candidate = (msg.text or "").strip()
    user_id = msg.from_user.id
    s = config_manager.settings.telegram

    # Импорт audit здесь (а не наверху) чтобы избежать циклических импортов
    from utils.audit_log import audit

    if candidate and candidate == s.admin_password:
        if user_id not in s.admin_ids:
            s.admin_ids.append(user_id)
            config_manager.save()
        logger.info(f"Пользователь {user_id} авторизовался как АДМИНИСТРАТОР")
        audit(user_id, "LOGIN_SUCCESS", data="role=admin")
        await _grant_access(msg, state, role="admin")
        return

    if candidate and candidate in s.manager_keys:
        if user_id not in s.manager_ids:
            s.manager_ids.append(user_id)
            # Одноразовый ключ — удаляем после использования
            s.manager_keys.remove(candidate)
            config_manager.save()
        logger.info(f"Пользователь {user_id} авторизовался как МЕНЕДЖЕР")
        audit(user_id, "LOGIN_SUCCESS", data="role=manager")
        await _grant_access(msg, state, role="manager")
        return

    audit(user_id, "LOGIN_FAIL", data=f"attempted={candidate[:8]}***")
    await msg.answer("❌ Неверный пароль. Попробуйте ещё раз:")


async def _grant_access(msg: types.Message, state: FSMContext, role: str) -> None:
    role_label = "Администратор" if role == "admin" else "Менеджер"
    await msg.answer(
        f"✅ <b>Доступ предоставлен!</b>\n"
        f"Роль: <b>{role_label}</b>\n\n"
        f"Теперь вы можете использовать все функции бота.\n"
        f"⚙️ Чтобы открыть меню, введите команду /menu"
    )

    # Если ещё не настроен FunPay — переходим в setup wizard
    fp = config_manager.settings.funpay
    if not fp.golden_key:
        await msg.answer(
            "🚀 Перед началом работы нужно подключить FunPay-аккаунт.\n\n"
            "🔑 Отправьте ваш <b>golden_key</b> (32 символа букв и цифр).\n\n"
            "<b>Как получить:</b>\n"
            "1. Откройте funpay.com в браузере, войдите в свой аккаунт\n"
            "2. Нажмите F12 → вкладка Application (или Storage)\n"
            "3. Cookies → https://funpay.com → найдите <code>golden_key</code>\n"
            "4. Скопируйте значение (32 символа) и отправьте сюда"
        )
        await state.set_state(SetupStates.waiting_golden_key)
    else:
        await state.clear()


# ──────────────────────────────────────────────────────────────────────────────
# Защита всего остального — блокируем неавторизованных
# ──────────────────────────────────────────────────────────────────────────────

@router.message(F.text)
async def block_unauthorized(msg: types.Message, state: FSMContext) -> None:
    """Catch-all для неавторизованных. Авторизованных не трогаем —
    их сообщения уходят в commands_router / menu_router (они выше в include_router)."""
    if not is_authorized(msg.from_user.id):
        await cmd_start(msg, state)
