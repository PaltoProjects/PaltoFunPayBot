"""
Событийная шина: позволяет модулям и плагинам подписываться
на события (новый заказ, новое сообщение, отзыв и т.д.) без жёсткой связи.

Usage:
    from core.event_bus import event_bus, Event

    @event_bus.on("new_order")
    async def handle(order):
        ...

    await event_bus.emit("new_order", order)
"""
from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, List

from utils.logger import logger

Handler = Callable[..., Awaitable[Any] | Any]


class Event:
    """Имена основных событий — чтобы не плодить magic-strings."""
    NEW_MESSAGE = "new_message"
    NEW_ORDER = "new_order"
    ORDER_PAID = "order_paid"
    ORDER_CONFIRMED = "order_confirmed"
    ORDER_REFUNDED = "order_refunded"
    NEW_REVIEW = "new_review"
    LOT_LIFTED = "lot_lifted"
    BOT_STARTED = "bot_started"
    BOT_STOPPED = "bot_stopped"


class EventBus:
    def __init__(self) -> None:
        self._handlers: Dict[str, List[Handler]] = defaultdict(list)

    def on(self, event_name: str) -> Callable[[Handler], Handler]:
        """Декоратор регистрации обработчика."""
        def decorator(fn: Handler) -> Handler:
            self.subscribe(event_name, fn)
            return fn
        return decorator

    def subscribe(self, event_name: str, handler: Handler) -> None:
        self._handlers[event_name].append(handler)
        logger.debug(f"Подписан обработчик {handler.__name__} на событие '{event_name}'")

    def unsubscribe(self, event_name: str, handler: Handler) -> None:
        if handler in self._handlers.get(event_name, []):
            self._handlers[event_name].remove(handler)

    async def emit(self, event_name: str, *args: Any, **kwargs: Any) -> None:
        """Запускает всех подписчиков события параллельно. Падения логируются, не ломают шину."""
        handlers = list(self._handlers.get(event_name, []))
        if not handlers:
            return

        tasks: List[Awaitable[Any]] = []
        for h in handlers:
            try:
                result = h(*args, **kwargs)
                if inspect.isawaitable(result):
                    tasks.append(self._safe(h.__name__, result))
            except Exception as e:
                logger.exception(f"Обработчик {h.__name__} упал на событии '{event_name}': {e}")

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _safe(name: str, coro: Awaitable[Any]) -> None:
        try:
            await coro
        except Exception as e:
            # Лог + (если важная) уведомление в TG
            try:
                from utils.error_reporter import report_error
                await report_error(e, context=f"event_bus:{name}")
            except Exception:
                # Если даже отчёт не удался — просто пишем в логи
                logger.exception(f"Async-обработчик {name} упал: {e}")


event_bus = EventBus()
