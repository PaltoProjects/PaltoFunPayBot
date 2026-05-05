"""
Мастер первичной настройки FunPay-подключения.

УПРОЩЁННАЯ ВЕРСИЯ: только ввод golden_key.
Прокси и прогрев убраны — пользователь использует системный VPN или прокси
на уровне Windows, поэтому бот ходит на FunPay напрямую.
"""
from __future__ import annotations

import asyncio

from aiogram import Router, types
from aiogram.fsm.context import FSMContext

from bot.states import SetupStates
from config.settings import config_manager
from core.funpay_client import funpay_client
from core.proxy_check import validate_golden_key
from utils.logger import logger

router = Router(name="setup")


# ──────────────────────────────────────────────────────────────────────────────
# Ввод golden_key
# ──────────────────────────────────────────────────────────────────────────────

@router.message(SetupStates.waiting_golden_key)
async def golden_key_received(msg: types.Message, state: FSMContext) -> None:
    key = (msg.text or "").strip()
    ok, err = validate_golden_key(key)
    if not ok:
        await msg.answer(f"❌ {err}\nПопробуйте снова:")
        return

    config_manager.settings.funpay.golden_key = key
    config_manager.save()

    status_msg = await msg.answer("✅ <b>Токен принят!</b>\n🚀 Подключаюсь к FunPay...")

    # Подключаемся к FunPay
    loop = asyncio.get_event_loop()
    success, message = await loop.run_in_executor(None, funpay_client.connect)

    if not success:
        await status_msg.edit_text(
            f"❌ Не удалось подключиться к FunPay.\n\n<code>{message}</code>\n\n"
            f"Проверьте golden_key и отправьте снова:"
        )
        return

    fp = config_manager.settings.funpay
    config_manager.settings.setup_completed = True
    config_manager.save()

    balance = funpay_client.get_balance()
    active = funpay_client.get_active_orders_count()

    await msg.answer(
        f"🚀 <b>PaltoFunPayBot успешно запущен!</b>\n\n"
        f"Версия: <code>{config_manager.settings.version}</code>\n"
        f"Аккаунт: <b>{fp.username}</b> (ID: <code>{fp.account_id}</code>)\n"
        f"Баланс: <code>{balance['rub']:.2f}₽</code>, "
        f"<code>{balance['usd']:.2f}$</code>, <code>{balance['eur']:.2f}€</code>\n"
        f"Активных заказов: <b>{active}</b>\n\n"
        f"⚙️ Откройте меню: /menu"
    )
    await state.clear()

    # Запускаем поллинг событий FunPay в фоне
    asyncio.create_task(funpay_client.start_polling())
    logger.info("Поллинг FunPay запущен.")
