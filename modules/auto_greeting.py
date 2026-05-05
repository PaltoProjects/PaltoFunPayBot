"""
Авто-приветствие новых диалогов.

Базовые переменные + $chat_id, $chat_name, $username.
"""
from __future__ import annotations

from typing import Dict
import json
import time
from pathlib import Path

from config.settings import config_manager
from core.event_bus import event_bus
from core.funpay_client import funpay_client
from utils.logger import logger
from utils.variables import send_with_vars

GREETED_PATH = Path(__file__).resolve().parent.parent / "data" / "greeted.json"
GREETED_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load_greeted() -> Dict[str, float]:
    if not GREETED_PATH.exists():
        return {}
    try:
        return json.loads(GREETED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_greeted(data: Dict[str, float]) -> None:
    GREETED_PATH.write_text(json.dumps(data), encoding="utf-8")


@event_bus.on("initial_chat")
async def handle_new_chat(chat) -> None:
    cfg = config_manager.settings.auto_greeting
    if not cfg.enabled:
        return

    bl = config_manager.settings.blacklist
    chat_name = getattr(chat, "name", "") or ""
    if bl.enabled and chat_name in bl.nicknames:
        logger.info(f"greeting: пропуск {chat_name} (чёрный список)")
        return

    if cfg.ignore_system_messages:
        if getattr(chat, "last_by_bot", None):
            return

    # Пропускаем системные рассылки FunPay (DEAR_VENDORS — «Уважаемые продавцы…»).
    # ChatShortcut.last_message_type устанавливается FunPayAPI автоматически
    # на основе регулярных выражений. Отвечать на такие «чаты» бессмысленно.
    try:
        from FunPayAPI.common.enums import MessageTypes
        last_type = getattr(chat, "last_message_type", None)
        if last_type == MessageTypes.DEAR_VENDORS:
            logger.debug(f"greeting: пропуск DEAR_VENDORS (системная рассылка FunPay)")
            return
    except (ImportError, Exception):
        pass

    chat_id = getattr(chat, "id", None)
    if not chat_id:
        return

    greeted = _load_greeted()
    last_ts = greeted.get(str(chat_id), 0)
    cooldown_sec = cfg.cooldown_days * 86400
    if time.time() - last_ts < cooldown_sec:
        return

    ok = await send_with_vars(
        cfg.text,
        funpay_client.send_message,
        chat_id,
        account=funpay_client.account,
        username=chat_name,
        chat_name=chat_name,
    )
    if ok:
        greeted[str(chat_id)] = time.time()
        _save_greeted(greeted)
        logger.info(f"greeting → {chat_name} ({chat_id})")
