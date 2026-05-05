"""
Ответ на подтверждение заказа и просьба об отзыве.

Контекстные переменные: $order_id, $order_price, $order_desc (доп. к базовым)
"""
from __future__ import annotations

import asyncio

from config.settings import config_manager
from core.event_bus import Event, event_bus
from core.funpay_client import funpay_client
from utils.logger import logger
from utils.variables import send_with_vars


@event_bus.on(Event.ORDER_CONFIRMED)
async def handle_order_confirmed(order) -> None:
    chat_id = getattr(order, "chat_id", None) or getattr(order, "buyer_chat_id", None)
    if not chat_id:
        return

    cfg_confirm = config_manager.settings.order_confirm_reply
    cfg_review  = config_manager.settings.ask_review

    buyer      = getattr(order, "buyer_username", "") or ""
    order_id   = str(getattr(order, "id", "") or "")
    order_price = str(getattr(order, "price", "") or "")
    order_desc  = str(getattr(order, "description", "") or "")
    chat_name   = buyer

    sub_kw = dict(
        username=buyer,
        chat_name=chat_name,
        order_id=order_id,
        order_price=order_price,
        order_desc=order_desc,
        account=funpay_client.account,
    )

    # 1) Сразу отвечаем
    if cfg_confirm.enabled and cfg_confirm.text:
        await send_with_vars(cfg_confirm.text, funpay_client.send_message, chat_id, **sub_kw)
        logger.info(f"order_confirm_reply → заказ {order_id}, покупатель {buyer}")

    # 2) Через delay просим отзыв
    if cfg_review.enabled and cfg_review.text:
        delay = max(0, cfg_review.delay_minutes) * 60
        asyncio.create_task(_ask_review_later(
            chat_id, cfg_review.text, delay, order_id=order_id, sub_kw=sub_kw
        ))


async def _ask_review_later(chat_id: int, text: str, delay: int, order_id: str, sub_kw: dict) -> None:
    await asyncio.sleep(delay)
    await send_with_vars(text, funpay_client.send_message, chat_id, **sub_kw)
    logger.info(f"ask_review → заказ {order_id} (delay {delay}s)")
