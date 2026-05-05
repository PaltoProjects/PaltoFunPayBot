"""
Proxy keep-alive — раз в N минут дёргаем FunPay чтобы прокси не закрывал
idle-соединение. Большинство платных прокси режут TCP-коннект который
молчит 5-15 минут.

Это сильно снижает количество ConnectionResetError / WinError 1236.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from core.funpay_client import funpay_client
from utils.logger import logger


class ProxyKeepAlive:
    """Раз в 4 минуты дёргает FunPay чтобы прокси не закрывал соединение."""

    INTERVAL_SEC = 240  # 4 минуты — комфортный интервал для большинства прокси

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_success_at: float = 0.0
        self._fails_in_row = 0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._last_success_at = time.time()
        self._task = asyncio.create_task(self._loop())
        logger.info("ProxyKeepAlive запущен.")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        # Первый ping через 30 сек после старта (даём боту время подключиться)
        await asyncio.sleep(30)

        while self._running:
            try:
                await self._ping()
            except Exception as e:
                logger.debug(f"ProxyKeepAlive: {e}")
            await asyncio.sleep(self.INTERVAL_SEC)

    async def _ping(self) -> None:
        """Лёгкий запрос на FunPay чтобы оживить соединение."""
        if not funpay_client.account:
            return

        loop = asyncio.get_event_loop()
        try:
            # Самый дешёвый запрос — get на главную FunPay
            # (FunPayAPI имеет метод method который шлёт запрос)
            await loop.run_in_executor(
                None,
                self._do_ping,
            )
            self._fails_in_row = 0
            self._last_success_at = time.time()
            logger.debug("ProxyKeepAlive: ping OK")

        except Exception as e:
            self._fails_in_row += 1
            outage = time.time() - self._last_success_at

            # Если разрыв >5 минут — уведомляем юзера
            if outage > 300 and self._fails_in_row >= 2:
                from modules.notifications import _send_to_all
                try:
                    await _send_to_all(
                        f"⚠️ <b>Долгая потеря связи с FunPay</b>\n\n"
                        f"Связь не восстанавливается уже <b>{int(outage // 60)} мин</b>.\n"
                        f"Последняя ошибка: <code>{type(e).__name__}: {str(e)[:200]}</code>\n\n"
                        f"💡 Возможные причины:\n"
                        f"• Прокси перегружен или упал\n"
                        f"• Интернет-соединение нестабильно\n"
                        f"• FunPay временно недоступен\n\n"
                        f"Бот сам пытается восстановиться."
                    )
                except Exception:
                    pass
                # Сбрасываем счётчик чтобы не спамить
                self._last_success_at = time.time()
                self._fails_in_row = 0

    def _do_ping(self) -> None:
        """Синхронная часть ping — вызов FunPay."""
        if not funpay_client.account:
            return
        try:
            # Запрашиваем CSRF/баланс — это лёгкий запрос
            funpay_client.account.get_balance()
        except AttributeError:
            # Если метод изменился — пробуем get_chats
            funpay_client.account.get_chats(update=True)


proxy_keepalive = ProxyKeepAlive()
