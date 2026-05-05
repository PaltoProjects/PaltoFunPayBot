"""
2FA (двухфакторная авторизация) для входа в TG-бот.

Команды:
  /login       — пользователь вводит 6-значный код из Google Authenticator
  /2fa_setup   — админ включает 2FA: генерируется секрет, показывается строка для ручного ввода
  /2fa_off     — админ выключает 2FA

После /login сессия 2FA валидна 1 час, потом снова попросит код.
"""
from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.handlers.auth import is_admin, is_authorized
from bot.middleware import is_2fa_passed, mark_2fa_passed, reset_2fa
from config.settings import config_manager
from utils.audit_log import audit
from utils.logger import logger
from utils.totp import generate_secret, now_code, otpauth_url, verify

router = Router(name="twofa")


class TwoFAStates(StatesGroup):
    waiting_code = State()


# ─── /login — ввод кода ──────────────────────────────────────────────────────

@router.message(Command("login"))
async def cmd_login(msg: types.Message, state: FSMContext) -> None:
    user_id = msg.from_user.id
    if not is_authorized(user_id):
        await msg.answer("⛔ Сначала авторизуйтесь паролем через /start.")
        return

    cfg = config_manager.settings.telegram
    if not cfg.twofa_enabled:
        await msg.answer("🔓 2FA выключена — вход не нужен. Используйте /menu.")
        return

    if is_2fa_passed(user_id):
        await msg.answer("✅ Вы уже прошли 2FA в этой сессии. /menu — открыть меню.")
        return

    await msg.answer(
        "🔐 <b>Двухфакторная авторизация</b>\n\n"
        "Введите 6-значный код из Google Authenticator (или другого приложения):",
        parse_mode="HTML",
    )
    await state.set_state(TwoFAStates.waiting_code)


@router.message(TwoFAStates.waiting_code)
async def cmd_login_code(msg: types.Message, state: FSMContext) -> None:
    code = (msg.text or "").strip()
    user_id = msg.from_user.id
    cfg = config_manager.settings.telegram

    if not cfg.twofa_secret:
        await msg.answer("❌ 2FA не настроена. Обратитесь к администратору.")
        await state.clear()
        return

    if verify(cfg.twofa_secret, code):
        mark_2fa_passed(user_id)
        audit(user_id, "LOGIN_2FA_SUCCESS")
        await msg.answer("✅ <b>Код принят!</b>\nСессия активна 1 час.\n\n/menu — открыть меню.", parse_mode="HTML")
        logger.info(f"2FA: user {user_id} прошёл")
        await state.clear()
    else:
        audit(user_id, "LOGIN_2FA_FAIL", data=code[:6])
        await msg.answer("❌ Неверный код. Попробуйте ещё раз:")
        # Не сбрасываем state — даём ещё попытку


# ─── /2fa_setup — включение 2FA ──────────────────────────────────────────────

@router.message(Command("2fa_setup"))
async def cmd_2fa_setup(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только для админов.")
        return

    cfg = config_manager.settings.telegram

    # Если уже включена — показываем текущий секрет
    if cfg.twofa_enabled and cfg.twofa_secret:
        url = otpauth_url(cfg.twofa_secret, "PaltoFunPayBot", str(msg.from_user.id))
        await msg.answer(
            "🔐 <b>2FA уже включена</b>\n\n"
            f"Если потеряли доступ — добавьте секрет вручную в Google Authenticator:\n"
            f"<code>{cfg.twofa_secret}</code>\n\n"
            f"Или ссылка для QR:\n<code>{url}</code>\n\n"
            f"Текущий код (для проверки): <code>{now_code(cfg.twofa_secret)}</code>\n\n"
            f"Выключить 2FA: /2fa_off",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    # Генерируем новый секрет
    secret = generate_secret()
    cfg.twofa_secret = secret
    cfg.twofa_enabled = True
    config_manager.save()
    audit(msg.from_user.id, "2FA_ENABLED")

    url = otpauth_url(secret, "PaltoFunPayBot", str(msg.from_user.id))
    await msg.answer(
        "🔐 <b>2FA включена!</b>\n\n"
        "<b>Шаг 1.</b> Установите Google Authenticator (или Authy / 1Password / Microsoft Authenticator):\n"
        "  • Android: <code>play.google.com/store/apps/details?id=com.google.android.apps.authenticator2</code>\n"
        "  • iOS: <code>apps.apple.com/app/google-authenticator/id388497605</code>\n\n"
        "<b>Шаг 2.</b> Откройте приложение → «+» → «Ввести ключ настройки» и введите:\n\n"
        f"  Аккаунт: <code>PaltoFunPayBot</code>\n"
        f"  Ключ: <code>{secret}</code>\n"
        f"  Тип: <i>По времени</i>\n\n"
        f"<b>Шаг 3.</b> Проверьте — текущий код приложения должен совпадать с этим:\n"
        f"  <code>{now_code(secret)}</code>\n\n"
        f"При следующем входе используйте /login.\n"
        f"Выключить: /2fa_off",
        parse_mode="HTML",
    )


# ─── /2fa_off ────────────────────────────────────────────────────────────────

@router.message(Command("2fa_off"))
async def cmd_2fa_off(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только для админов.")
        return
    cfg = config_manager.settings.telegram
    cfg.twofa_enabled = False
    cfg.twofa_secret = ""
    config_manager.save()
    reset_2fa(msg.from_user.id)
    audit(msg.from_user.id, "2FA_DISABLED")
    await msg.answer("🔓 2FA выключена.")


# ─── /audit — просмотр последних действий ───────────────────────────────────

@router.message(Command("audit"))
async def cmd_audit(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только для админов.")
        return
    from utils.audit_log import read_recent
    text = read_recent(50)
    if len(text) > 4000:
        text = text[-4000:]
    await msg.answer(
        f"📜 <b>Последние действия (50 строк):</b>\n\n<code>{text}</code>",
        parse_mode="HTML",
    )


# ─── /export_settings — экспорт настроек (без отправки в облако) ────────────

@router.message(Command("export_settings"))
async def cmd_export(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только для админов.")
        return

    from pathlib import Path
    from aiogram.types import FSInputFile

    cfg_path = Path("data/config.json")
    if not cfg_path.exists():
        await msg.answer("❌ Конфиг не найден.")
        return

    audit(msg.from_user.id, "CONFIG_EXPORT")
    try:
        await msg.answer_document(
            FSInputFile(cfg_path),
            caption="📦 Бэкап конфигурации.\n\n⚠️ Файл содержит golden_key и API-ключи — храните в безопасном месте.",
        )
    except Exception as e:
        await msg.answer(f"❌ Не удалось отправить файл: {e}")
