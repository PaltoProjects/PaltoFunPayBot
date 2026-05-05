"""
Авто-вывод средств с баланса FunPay.

Два режима (выбираются в настройках):
  trigger="schedule" — по расписанию (раз в день/неделю/месяц в указанный час)
  trigger="amount"   — при достижении накопленной суммы выводить сразу

Запоминает дату последнего вывода в data/auto_withdraw.json чтобы не повторять.

⚠️ Метод вывода через FunPayAPI:
  account.withdraw(method, amount, details)  — точное название может отличаться.
  Если в API нет этого метода — модуль логирует факт, но реально не выводит.
  Реализация может потребовать обновления при смене FunPayAPI.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config.settings import config_manager
from core.event_bus import event_bus
from core.funpay_client import funpay_client
from utils.audit_log import audit
from utils.logger import logger

STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "auto_withdraw.json"
STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(data: dict) -> None:
    STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _last_withdraw_dt() -> Optional[datetime]:
    state = _load_state()
    ts = state.get("last_withdraw_ts")
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts))
    except Exception:
        return None


def _set_last_withdraw_now(amount: float, method: str, details: str) -> None:
    state = _load_state()
    state["last_withdraw_ts"] = time.time()
    history = state.setdefault("history", [])
    history.append({
        "ts": time.time(),
        "amount_rub": amount,
        "method": method,
        "details_short": details[-4:] if details else "",
    })
    # Храним только последние 100 записей
    state["history"] = history[-100:]
    _save_state(state)


# ──────────────────────────────────────────────────────────────────────────────
# Проверка пора ли выводить
# ──────────────────────────────────────────────────────────────────────────────

def _should_withdraw_now(balance_rub: float) -> bool:
    cfg = config_manager.settings.auto_withdraw
    if not cfg.enabled:
        return False
    if balance_rub < cfg.min_amount_rub:
        return False

    if cfg.trigger == "amount":
        # Достигли суммы — выводим прямо сейчас
        return True

    # trigger == "schedule"
    last = _last_withdraw_dt()
    now = datetime.now()

    # Час дня для вывода
    if now.hour != cfg.schedule_hour:
        return False

    if not last:
        # Никогда ещё не выводили — выводим
        return True

    if cfg.schedule == "daily":
        # Прошёл хотя бы один день
        return last.date() < now.date()
    elif cfg.schedule == "weekly":
        # Прошла хотя бы неделя
        return (now - last) >= timedelta(days=7)
    elif cfg.schedule == "monthly":
        # Прошёл хотя бы месяц
        return (now - last) >= timedelta(days=30)

    return False


# ──────────────────────────────────────────────────────────────────────────────
# Сам вывод
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_wallet_and_currency(method_str: str):
    """
    Преобразует строковое имя метода (из настроек) в пару
    (enums.Wallet, enums.Currency) для account.withdraw().

    Поддерживаемые значения payment_method:
      card / card_rub → Wallet.CARD_RUB, Currency.RUB
      card_usd        → Wallet.CARD_USD, Currency.USD
      card_eur        → Wallet.CARD_EUR, Currency.EUR
      qiwi            → Wallet.QIWI,     Currency.RUB
      yoomoney / youmoney / fps → Wallet.YOUMONEY, Currency.RUB
      binance         → Wallet.BINANCE,  Currency.USD
      usdt / trc / usdt_trc → Wallet.TRC, Currency.USD
      webmoney        → Wallet.WEBMONEY, Currency.USD
    """
    try:
        from FunPayAPI.common.enums import Currency, Wallet
    except ImportError:
        return None, None

    m = (method_str or "").strip().lower()
    _MAP = {
        "card":       (Wallet.CARD_RUB, Currency.RUB),
        "card_rub":   (Wallet.CARD_RUB, Currency.RUB),
        "card_usd":   (Wallet.CARD_USD, Currency.USD),
        "card_eur":   (Wallet.CARD_EUR, Currency.EUR),
        "qiwi":       (Wallet.QIWI,     Currency.RUB),
        "yoomoney":   (Wallet.YOUMONEY, Currency.RUB),
        "youmoney":   (Wallet.YOUMONEY, Currency.RUB),
        "fps":        (Wallet.YOUMONEY, Currency.RUB),
        "binance":    (Wallet.BINANCE,  Currency.USD),
        "usdt":       (Wallet.TRC,      Currency.USD),
        "trc":        (Wallet.TRC,      Currency.USD),
        "usdt_trc":   (Wallet.TRC,      Currency.USD),
        "webmoney":   (Wallet.WEBMONEY, Currency.USD),
    }
    return _MAP.get(m, (None, None))


async def _do_withdraw(amount_rub: float) -> tuple:
    """
    Возвращает (success: bool, message: str).

    Правильная сигнатура FunPayAPI:
      account.withdraw(currency: Currency, wallet: Wallet, amount: float, address: str) -> float
    """
    cfg = config_manager.settings.auto_withdraw
    if not funpay_client.account:
        return False, "FunPay не подключён"

    if not cfg.payment_details:
        return False, "Не указаны реквизиты получателя в настройках"

    wallet, currency = _resolve_wallet_and_currency(cfg.payment_method)
    if wallet is None or currency is None:
        return False, (
            f"Неизвестный метод вывода: '{cfg.payment_method}'. "
            "Допустимые значения: card, card_rub, card_usd, card_eur, "
            "qiwi, yoomoney, binance, usdt, webmoney."
        )

    if not hasattr(funpay_client.account, "withdraw"):
        return False, (
            "В FunPayAPI не нашёлся метод withdraw(). "
            "Проверьте обновления библиотеки или сделайте вывод вручную."
        )

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: funpay_client.account.withdraw(currency, wallet, amount_rub, cfg.payment_details),
        )
        return True, str(result)
    except Exception as e:
        return False, str(e)


# ──────────────────────────────────────────────────────────────────────────────
# Фоновый цикл
# ──────────────────────────────────────────────────────────────────────────────

class AutoWithdraw:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("AutoWithdraw запущен.")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        # Проверяем каждые 5 минут
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.exception(f"AutoWithdraw tick: {e}")
            await asyncio.sleep(5 * 60)

    async def _tick(self) -> None:
        cfg = config_manager.settings.auto_withdraw
        if not cfg.enabled or not funpay_client.account:
            return

        bal = funpay_client.get_balance()
        balance_rub = float(bal.get("rub", 0))

        if not _should_withdraw_now(balance_rub):
            return

        # Сумма к выводу — весь рублёвый баланс (округляем вниз до целых)
        amount = float(int(balance_rub))
        if amount < cfg.min_amount_rub:
            return

        logger.info(f"AutoWithdraw: вывод {amount}₽ через {cfg.payment_method}")

        ok, message = await _do_withdraw(amount)

        # Лог в audit
        audit(
            user_id=0,  # системное действие
            action="AUTO_WITHDRAW_SUCCESS" if ok else "AUTO_WITHDRAW_FAIL",
            data=f"amount={amount} method={cfg.payment_method}",
            text=message[:200],
        )

        # Уведомление в TG
        if ok:
            _set_last_withdraw_now(amount, cfg.payment_method, cfg.payment_details)
            text = (
                f"💸 <b>Авто-вывод выполнен</b>\n\n"
                f"Сумма: <code>{amount:.2f}₽</code>\n"
                f"Способ: <code>{cfg.payment_method}</code>\n"
                f"Реквизиты: <code>...{cfg.payment_details[-4:]}</code>"
            )
        else:
            text = (
                f"⚠️ <b>Авто-вывод не выполнен</b>\n\n"
                f"Причина: <code>{message[:300]}</code>\n"
                f"Сумма попытки: <code>{amount:.2f}₽</code>\n\n"
                f"Проверьте настройки в /menu → 🤖 Автоматизация → Авто-вывод."
            )
        await event_bus.emit("auto_withdraw_event", text)


@event_bus.on("auto_withdraw_event")
async def _on_auto_withdraw(text: str) -> None:
    """Прокидываем сообщение в TG-уведомления."""
    from modules.notifications import _send_to_all
    await _send_to_all(text)


auto_withdraw = AutoWithdraw()


# ──────────────────────────────────────────────────────────────────────────────
# Вспомогательная функция для меню — ручной вывод сейчас
# ──────────────────────────────────────────────────────────────────────────────

async def withdraw_now() -> tuple:
    """Вызвать вывод вручную из меню. Возвращает (success, message)."""
    if not funpay_client.account:
        return False, "FunPay не подключён"
    bal = funpay_client.get_balance()
    amount = float(int(bal.get("rub", 0)))
    cfg = config_manager.settings.auto_withdraw
    if amount < cfg.min_amount_rub:
        return False, f"Баланс {amount}₽ меньше минимума ({cfg.min_amount_rub}₽)"
    ok, message = await _do_withdraw(amount)
    if ok:
        _set_last_withdraw_now(amount, cfg.payment_method, cfg.payment_details)
    audit(0, "MANUAL_WITHDRAW", data=f"amount={amount} ok={ok}", text=message[:200])
    return ok, message
