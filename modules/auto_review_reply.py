"""
Автоответ на отзывы по звёздам.

Контекстные переменные: $order_id, $stars, $review_text (доп. к базовым)
"""
from __future__ import annotations

from config.settings import config_manager
from core.event_bus import Event, event_bus
from core.funpay_client import accounts_manager
from utils.logger import logger
from utils.variables import send_with_vars


def _truncate_review_text(text: str, max_chars: int = 999, max_lines: int = 10) -> str:
    """
    Обрезает текст до лимитов FunPay для ответов на отзывы:
      - не более 999 символов
      - не более 10 строк

    Обрезает по границе предложения (. ! ?) или строки, как делает Cardinal.
    """
    # Обрезаем по количеству строк
    lines = text.splitlines()
    if len(lines) > max_lines:
        text = "\n".join(lines[:max_lines])

    # Обрезаем по количеству символов
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]
    # Ищем последнюю границу предложения
    for sep in (".", "!", "?", "\n"):
        pos = truncated.rfind(sep)
        if pos > max_chars // 2:  # отрезаем не больше половины
            return truncated[:pos + 1].strip()

    # Крайний случай — просто режем по символу
    return truncated.strip()


@event_bus.on(Event.NEW_REVIEW)
async def handle_review(review) -> None:
    cfg = config_manager.settings.auto_review_reply
    if not cfg.enabled:
        return

    stars    = int(getattr(review, "stars", 0) or 0)
    order_id = str(getattr(review, "order_id", None) or getattr(review, "id", "") or "")
    if not (1 <= stars <= 5) or not order_id:
        return

    text = {1: cfg.reply_1, 2: cfg.reply_2, 3: cfg.reply_3,
            4: cfg.reply_4, 5: cfg.reply_5}[stars]
    if not text:
        return

    reviewer    = getattr(review, "author", "") or getattr(review, "buyer_username", "") or ""
    review_text = getattr(review, "text", "") or ""

    # Отвечаем через аккаунт, на который пришёл отзыв (мультиаккаунт)
    client = accounts_manager.client_for(review)
    if client.account is None:
        return

    # Подстановка переменных (send_review — не send_message, делаем sub вручную)
    from utils.variables import substitute
    text = substitute(
        text,
        username=reviewer,
        order_id=order_id,
        stars=str(stars),
        review_text=review_text,
    )

    # Ограничения FunPay: 999 символов, 10 строк. Превышение → ошибка API.
    text = _truncate_review_text(text)

    try:
        client.account.send_review(order_id, text, stars=stars)
        logger.info(f"review_reply → заказ {order_id} ({stars}★)")
    except Exception as e:
        try:
            client.account.send_review_reply(order_id, text)
            logger.info(f"review_reply (legacy) → {order_id}")
        except Exception as e2:
            logger.warning(f"review_reply: {e} / {e2}")
