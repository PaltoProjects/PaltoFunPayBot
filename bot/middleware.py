"""
Middleware aiogram — журналирование действий и 2FA-проверка.

audit_middleware:
  Логирует каждое сообщение и каждый callback в data/logs/audit.log.

twofa_middleware:
  Если для юзера включена 2FA и сессия не подтверждена — блокирует все
  действия, кроме /start и /login.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from utils.audit_log import audit
from utils.logger import logger


# ──────────────────────────────────────────────────────────────────────────────
# Audit middleware
# ──────────────────────────────────────────────────────────────────────────────

class AuditMiddleware(BaseMiddleware):
    """Логирует все события от пользователей."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        try:
            if isinstance(event, Message):
                user_id = event.from_user.id if event.from_user else 0
                # Команда?
                text = event.text or event.caption or ""
                if text.startswith("/"):
                    cmd = text.split()[0]
                    audit(user_id, "COMMAND", data=cmd, text=text)
                elif event.photo:
                    audit(user_id, "PHOTO", text=event.caption or "")
                elif text:
                    audit(user_id, "TEXT", text=text)
            elif isinstance(event, CallbackQuery):
                user_id = event.from_user.id if event.from_user else 0
                audit(user_id, "BUTTON_CLICK", data=event.data or "")
        except Exception as e:
            logger.debug(f"audit middleware error: {e}")

        return await handler(event, data)


# ──────────────────────────────────────────────────────────────────────────────
# 2FA middleware
# ──────────────────────────────────────────────────────────────────────────────

# Состояния 2FA — какие юзеры успешно подтвердили 2FA в текущей сессии бота.
# Сбрасывается при перезапуске бота.
_2fa_confirmed_until: Dict[int, float] = {}

# Длительность сессии 2FA — час после подтверждения
_2FA_SESSION_SEC = 60 * 60


def is_2fa_passed(user_id: int) -> bool:
    """Прошёл ли юзер 2FA в текущей сессии."""
    until = _2fa_confirmed_until.get(user_id, 0)
    return time.time() < until


def mark_2fa_passed(user_id: int) -> None:
    """Помечает что юзер прошёл 2FA — сессия будет валидной _2FA_SESSION_SEC секунд."""
    _2fa_confirmed_until[user_id] = time.time() + _2FA_SESSION_SEC


def reset_2fa(user_id: int) -> None:
    _2fa_confirmed_until.pop(user_id, None)


class TwoFAMiddleware(BaseMiddleware):
    """Если включена 2FA — блокирует всё кроме /start, /login и ввода кода."""

    # Команды которые разрешены ДО прохождения 2FA
    ALLOWED_COMMANDS = {"/start", "/login"}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        from config.settings import config_manager
        from bot.handlers.auth import is_authorized

        cfg = config_manager.settings.telegram

        # 2FA включена?
        if not getattr(cfg, "twofa_enabled", False):
            return await handler(event, data)

        # Получаем user_id
        user_id = 0
        if isinstance(event, (Message, CallbackQuery)):
            user_id = event.from_user.id if event.from_user else 0

        # Если юзер не авторизован — обычный поток (auth.py его поймает)
        if not is_authorized(user_id):
            return await handler(event, data)

        # Уже прошёл 2FA в этой сессии?
        if is_2fa_passed(user_id):
            return await handler(event, data)

        # Не прошёл — блокируем всё кроме /start, /login и ввода кода
        if isinstance(event, Message):
            text = event.text or ""
            cmd = text.split()[0] if text else ""
            # Если ждём ввода кода (FSM состояние) — пропускаем
            from aiogram.fsm.context import FSMContext
            state: FSMContext = data.get("state")
            if state:
                cur = await state.get_state()
                if cur and "TwoFA" in str(cur):
                    return await handler(event, data)
            if cmd in self.ALLOWED_COMMANDS:
                return await handler(event, data)
            await event.answer(
                "🔐 Требуется подтверждение 2FA.\n"
                "Введите команду /login и затем код, который придёт на вашу почту."
            )
            return None
        elif isinstance(event, CallbackQuery):
            await event.answer("🔐 Требуется 2FA. Введите /login", show_alert=True)
            return None

        return await handler(event, data)


# ──────────────────────────────────────────────────────────────────────────────
# Error middleware — ловит ошибки в обработчиках и шлёт красивый отчёт в TG
# ──────────────────────────────────────────────────────────────────────────────

class ErrorMiddleware(BaseMiddleware):
    """Перехватывает все ошибки в обработчиках aiogram."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception as e:
            # Где случилось
            ctx = "TG"
            if isinstance(event, Message):
                if event.text and event.text.startswith("/"):
                    ctx = f"command:{event.text.split()[0]}"
                else:
                    ctx = "TG:message"
            elif isinstance(event, CallbackQuery):
                ctx = f"callback:{(event.data or '')[:30]}"

            # Шлём красивый отчёт
            try:
                from utils.error_reporter import report_error
                await report_error(e, context=ctx, show_traceback=True)
            except Exception:
                logger.exception(f"[{ctx}] {e}")

            # Юзеру — короткая реакция, чтобы UI не висел
            try:
                if isinstance(event, CallbackQuery):
                    await event.answer("❌ Ошибка. Подробности отправлены админу.", show_alert=True)
                elif isinstance(event, Message):
                    await event.answer("❌ Произошла ошибка. Подробности отправлены админу.")
            except Exception:
                pass
