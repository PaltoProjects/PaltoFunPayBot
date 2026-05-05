"""
Автодеактивация лотов: когда заканчиваются ключи в файле, лот выключается.
Автовосстановление: периодически проверяем деактивированные нашим ботом лоты,
и если в файле снова появились ключи — включаем обратно.
"""
from __future__ import annotations

from typing import List, Optional

import asyncio
import json
from pathlib import Path

from config.settings import config_manager
from core.funpay_client import funpay_client
from utils.logger import logger

# Сохраняем, какие лоты МЫ деактивировали, чтобы безопасно их восстанавливать
DEACT_PATH = Path(__file__).resolve().parent.parent / "data" / "auto_deactivated.json"
DEACT_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load() -> List[str]:
    if not DEACT_PATH.exists():
        return []
    try:
        return json.loads(DEACT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(data: List[str]) -> None:
    DEACT_PATH.write_text(json.dumps(data), encoding="utf-8")


async def deactivate_lot(lot_id: str) -> bool:
    """Выключает лот через FunPayAPI и запоминает его."""
    if not config_manager.settings.auto_activation.enabled:
        return False
    if funpay_client.account is None:
        return False

    try:
        loop = asyncio.get_event_loop()
        # Получаем текущие поля лота, ставим active=False
        lot_fields = await loop.run_in_executor(
            None, funpay_client.account.get_lot_fields, int(lot_id)
        )
        lot_fields.active = False
        await loop.run_in_executor(None, funpay_client.account.save_lot, lot_fields)
        deact = _load()
        if lot_id not in deact:
            deact.append(lot_id)
            _save(deact)
        logger.info(f"auto_deactivation: лот {lot_id} выключен")
        return True
    except Exception as e:
        logger.warning(f"deactivate_lot({lot_id}): {e}")
        return False


async def activate_lot(lot_id: str) -> bool:
    """Включает лот обратно."""
    if funpay_client.account is None:
        return False
    try:
        loop = asyncio.get_event_loop()
        lot_fields = await loop.run_in_executor(
            None, funpay_client.account.get_lot_fields, int(lot_id)
        )
        lot_fields.active = True
        await loop.run_in_executor(None, funpay_client.account.save_lot, lot_fields)
        deact = _load()
        if lot_id in deact:
            deact.remove(lot_id)
            _save(deact)
        logger.info(f"auto_restore: лот {lot_id} включён обратно")
        return True
    except Exception as e:
        logger.warning(f"activate_lot({lot_id}): {e}")
        return False


class AutoRestore:
    """Фоновый цикл автовосстановления."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("AutoRestore запущен.")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        from modules.auto_delivery import get_remaining_keys

        while self._running:
            cfg = config_manager.settings.auto_restore
            if cfg.enabled and funpay_client.account:
                deact = _load()
                for lot_id in list(deact):
                    if get_remaining_keys(lot_id) > 0:
                        await activate_lot(lot_id)
            await asyncio.sleep(max(60, cfg.check_interval_minutes) * 60)


auto_restore = AutoRestore()
