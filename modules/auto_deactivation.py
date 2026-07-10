"""
Автодеактивация лотов: когда заканчиваются ключи в файле, лот выключается.
Автовосстановление: периодически проверяем деактивированные нашим ботом лоты,
и если в файле снова появились ключи — включаем обратно.

Мультиаккаунт: лот выключается/включается на аккаунте-владельце; в файле
состояния хранится индекс аккаунта ({"lot_id": ..., "acc": ...}). Старый
формат (список строк) читается и трактуется как «активный аккаунт».
"""
from __future__ import annotations

from typing import Dict, List, Optional

import asyncio
import json
from pathlib import Path

from config.settings import config_manager
from core.funpay_client import accounts_manager
from utils.logger import logger

# Сохраняем, какие лоты МЫ деактивировали, чтобы безопасно их восстанавливать
DEACT_PATH = Path(__file__).resolve().parent.parent / "data" / "auto_deactivated.json"
DEACT_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load() -> List[Dict]:
    """Список [{"lot_id": str, "acc": int}] (легаси-строки конвертируются)."""
    if not DEACT_PATH.exists():
        return []
    try:
        raw = json.loads(DEACT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: List[Dict] = []
    for item in raw:
        if isinstance(item, dict) and "lot_id" in item:
            out.append({"lot_id": str(item["lot_id"]), "acc": int(item.get("acc", -1))})
        elif isinstance(item, (str, int)):
            # Легаси-формат: только lot_id, аккаунт неизвестен → активный
            out.append({"lot_id": str(item), "acc": -1})
    return out


def _save(data: List[Dict]) -> None:
    DEACT_PATH.write_text(json.dumps(data), encoding="utf-8")


def _client_or_active(client=None, acc: int = -1):
    if client is not None:
        return client
    return accounts_manager.get(acc) or accounts_manager.active()


async def deactivate_lot(lot_id: str, client=None) -> bool:
    """Выключает лот через FunPayAPI (на аккаунте client) и запоминает его."""
    if not config_manager.settings.auto_activation.enabled:
        return False
    client = _client_or_active(client)
    if client.account is None:
        return False

    try:
        loop = asyncio.get_event_loop()
        # Получаем текущие поля лота, ставим active=False
        lot_fields = await loop.run_in_executor(
            None, client.account.get_lot_fields, int(lot_id)
        )
        lot_fields.active = False
        await loop.run_in_executor(None, client.account.save_lot, lot_fields)
        deact = _load()
        if not any(d["lot_id"] == str(lot_id) for d in deact):
            deact.append({"lot_id": str(lot_id), "acc": client.index})
            _save(deact)
        logger.info(f"auto_deactivation: лот {lot_id} выключен [{client.alias}]")
        return True
    except Exception as e:
        logger.warning(f"deactivate_lot({lot_id}): {e}")
        return False


async def activate_lot(lot_id: str, client=None) -> bool:
    """Включает лот обратно (на аккаунте, который его выключал)."""
    deact = _load()
    entry = next((d for d in deact if d["lot_id"] == str(lot_id)), None)
    client = _client_or_active(client, entry["acc"] if entry else -1)
    if client.account is None:
        return False
    try:
        loop = asyncio.get_event_loop()
        lot_fields = await loop.run_in_executor(
            None, client.account.get_lot_fields, int(lot_id)
        )
        lot_fields.active = True
        await loop.run_in_executor(None, client.account.save_lot, lot_fields)
        if entry:
            deact.remove(entry)
            _save(deact)
        logger.info(f"auto_restore: лот {lot_id} включён обратно [{client.alias}]")
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
            if cfg.enabled and accounts_manager.connected_clients():
                for entry in _load():
                    if get_remaining_keys(entry["lot_id"]) > 0:
                        await activate_lot(entry["lot_id"])
            await asyncio.sleep(max(60, cfg.check_interval_minutes) * 60)


auto_restore = AutoRestore()
