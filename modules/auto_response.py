"""
Автоответчик по ключевым словам.

Контекстные переменные: $message_text (доп. к базовым)
"""
from __future__ import annotations

import re

from config.settings import config_manager
from core.event_bus import Event, event_bus
from core.funpay_client import funpay_client
from utils.logger import logger
from utils.variables import send_with_vars


@event_bus.on(Event.NEW_MESSAGE)
async def handle_new_message(message) -> None:
    cfg = config_manager.settings.auto_response

    if getattr(message, "author_id", None) == config_manager.settings.funpay.account_id:
        return

    author    = getattr(message, "author", "") or ""
    text_in   = (getattr(message, "text", "") or "").lower()
    chat_id   = getattr(message, "chat_id", None)
    chat_name = getattr(message, "chat_name", "") or author

    if not chat_id or not text_in:
        return

    bl = config_manager.settings.blacklist
    if bl.enabled and author in bl.nicknames:
        logger.info(f"autoresponse: пропуск {author} (чёрный список)")
        return

    if not cfg.enabled or not cfg.templates:
        return

    for keyword, response in cfg.templates.items():
        # Используем границы слов для русского+английского, чтобы «да» не срабатывало
        # на «ладан», «давно» и т.п. (простой `in` давал ложные срабатывания).
        _kw = re.escape(keyword.lower())
        _pattern = r"(?<![а-яёa-zA-Z0-9])" + _kw + r"(?![а-яёa-zA-Z0-9])"
        if not re.search(_pattern, text_in):
            continue
        await send_with_vars(
            response,
            funpay_client.send_message,
            chat_id,
            account=funpay_client.account,
            username=author,
            chat_name=chat_name,
            message_text=getattr(message, "text", "") or "",
        )
        logger.info(f"autoresponse → {author}: keyword={keyword!r}")
        break
