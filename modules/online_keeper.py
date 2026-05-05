"""
Постоянный онлайн-статус.

FunPayAPI поддерживает онлайн автоматически, пока работает Runner.get_updates()
(он шлёт хартбит). Этот модуль просто следит, чтобы поллинг был активен,
и логирует статус.
"""
from __future__ import annotations

from typing import Optional

import asyncio

from config.settings import config_manager
from core.event_bus import Event, event_bus
from core.funpay_client import funpay_client
from utils.logger import logger


class OnlineKeeper:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("OnlineKeeper запущен.")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while self._running:
            cfg = config_manager.settings.online_keeper
            if cfg.enabled and funpay_client.account is not None:
                logger.debug("OnlineKeeper: ✅ онлайн")
            await asyncio.sleep(cfg.poll_interval_seconds)


online_keeper = OnlineKeeper()
