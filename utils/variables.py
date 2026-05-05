"""
Подстановка переменных в текстовых шаблонах.

БАЗОВЫЕ (доступны везде):
    $date           — дата (26.04.2025)
    $date_text      — дата (26 апреля)
    $full_date_text — дата (26 апреля 2025 года)
    $time           — время (14:35)
    $full_time      — время (14:35:07)
    $username       — никнейм собеседника
    $chat_id        — ID чата
    $chat_name      — название чата

СПЕЦИАЛЬНЫЕ УПРАВЛЯЮЩИЕ (обрабатываются при отправке):
    $sleep=N        — задержка N секунд перед отправкой части сообщения
    $photo=[ID]     — вставка изображения по image_id

КОНТЕКСТНЫЕ (только там где есть данные):
    $message_text   — текст входящего сообщения (в автоответчике)
    $order_id       — ID заказа (в подтверждении, отзывах, автовыдаче)
    $order_price    — цена заказа (в подтверждении, автовыдаче)
    $order_desc     — описание лота (в подтверждении, автовыдаче)
    $stars          — количество звёзд (в ответе на отзыв)
    $review_text    — текст отзыва покупателя (в ответе на отзыв)
    $balance_rub    — баланс в рублях (в уведомлениях)
    $lot_id         — ID лота (в автовыдаче)
"""
from __future__ import annotations

import asyncio
import datetime
from typing import Any, Dict, Optional

from utils.logger import logger


# ─── Названия месяцев ──────────────────────────────────────────────────────────
_MONTHS = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
]


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def _base_vars() -> Dict[str, str]:
    """Базовые переменные, доступные везде."""
    now = _now()
    return {
        "date":           now.strftime("%d.%m.%Y"),
        "date_text":      f"{now.day} {_MONTHS[now.month]}",
        "full_date_text": f"{now.day} {_MONTHS[now.month]} {now.year} года",
        "time":           now.strftime("%H:%M"),
        "full_time":      now.strftime("%H:%M:%S"),
    }


def substitute(
    text: str,
    *,
    # базовые контекстные
    username: str = "",
    chat_id: Any = "",
    chat_name: str = "",
    # сообщение
    message_text: str = "",
    # заказ
    order_id: str = "",
    order_price: str = "",
    order_desc: str = "",
    # отзыв
    stars: str = "",
    review_text: str = "",
    # баланс
    balance_rub: str = "",
    # лот
    lot_id: str = "",
    # доп произвольные
    extra: Optional[Dict[str, str]] = None,
) -> str:
    """
    Подставляет все переменные кроме управляющих ($sleep, $photo).
    Управляющие обрабатывает send_with_vars().
    """
    vars_: Dict[str, str] = _base_vars()
    vars_.update({
        "username":       username,
        "chat_id":        str(chat_id),
        "chat_name":      chat_name or username,
        "message_text":   message_text,
        "order_id":       order_id,
        "order_price":    order_price,
        "order_desc":     order_desc,
        "stars":          stars,
        "review_text":    review_text,
        "balance_rub":    balance_rub,
        "lot_id":         lot_id,
    })
    if extra:
        vars_.update(extra)

    for key, value in vars_.items():
        text = text.replace(f"${key}", value)

    return text


async def send_with_vars(
    text: str,
    send_fn,            # funpay_client.send_message(chat_id, text) или account.send_message
    chat_id: Any,
    *,
    account=None,       # для $photo — нужен account.send_message с image_id
    **sub_kwargs,
) -> bool:
    """
    Выполняет подстановку переменных, обрабатывает $sleep и $photo,
    затем отправляет сообщение.

    $sleep=N     — разбивает текст на части и делает asyncio.sleep(N) между ними.
    $photo=[ID]  — вставляет изображение (image_id).

    Возвращает True если хоть одна часть отправлена успешно.

    NOTE: если в sub_kwargs передан chat_id — он молча игнорируется,
    чтобы не было конфликта с позиционным аргументом chat_id.
    """
    # Защита от дубликата: если вызывающий код по привычке передал chat_id в kwargs —
    # игнорируем его (используется уже принятый позиционно).
    sub_kwargs.pop("chat_id", None)

    text = substitute(text, chat_id=chat_id, **sub_kwargs)

    # Разбиваем по $sleep=N
    import re
    parts = re.split(r"\$sleep=(\d+(?:\.\d+)?)", text)
    # parts = ["текст до", "5", "текст после", ...]

    success = False
    i = 0
    while i < len(parts):
        chunk = parts[i].strip()
        i += 1

        # Если следующий элемент — задержка
        sleep_sec = 0.0
        if i < len(parts):
            try:
                sleep_sec = float(parts[i])
                i += 1
            except (ValueError, IndexError):
                pass

        # Обрабатываем $photo=[ID] внутри chunk
        photo_ids = re.findall(r"\$photo=\[([^\]]+)\]", chunk)
        clean_text = re.sub(r"\$photo=\[[^\]]+\]", "", chunk).strip()

        # Отправляем текст
        if clean_text:
            try:
                ok = send_fn(int(chat_id), clean_text)
                if ok:
                    success = True
            except Exception as e:
                logger.warning(f"send_with_vars: {e}")

        # Отправляем изображения
        for photo_id in photo_ids:
            if account:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None,
                        lambda pid=photo_id: account.send_message(
                            int(chat_id), "", image_id=int(pid)
                        )
                    )
                    success = True
                except Exception as e:
                    logger.warning(f"send_with_vars photo {photo_id}: {e}")

        # Ждём перед следующей частью
        if sleep_sec > 0 and i < len(parts):
            await asyncio.sleep(min(sleep_sec, 300))  # макс 5 минут

    return success


def describe_vars(context: str = "base") -> str:
    """
    Возвращает строку-подсказку для меню.
    context: 'base' | 'message' | 'order' | 'review' | 'delivery'
    """
    base = (
        "\n\n<b>Доступные переменные:</b>\n"
        "<code>$username</code> — ник собеседника\n"
        "<code>$chat_id</code> — ID чата\n"
        "<code>$chat_name</code> — название чата\n"
        "<code>$date</code> — дата (26.04.2025)\n"
        "<code>$date_text</code> — дата (26 апреля)\n"
        "<code>$full_date_text</code> — дата (26 апреля 2025 года)\n"
        "<code>$time</code> — время (14:35)\n"
        "<code>$full_time</code> — время (14:35:07)\n"
        "<code>$photo=[ID]</code> — изображение\n"
        "<code>$sleep=N</code> — пауза N секунд"
    )

    extra = {
        "message": (
            "\n<code>$message_text</code> — текст сообщения собеседника"
        ),
        "order": (
            "\n<code>$order_id</code> — ID заказа\n"
            "<code>$order_price</code> — цена заказа\n"
            "<code>$order_desc</code> — описание лота"
        ),
        "review": (
            "\n<code>$order_id</code> — ID заказа\n"
            "<code>$stars</code> — кол-во звёзд\n"
            "<code>$review_text</code> — текст отзыва"
        ),
        "delivery": (
            "\n<code>$order_id</code> — ID заказа\n"
            "<code>$order_price</code> — цена заказа\n"
            "<code>$lot_id</code> — ID лота"
        ),
    }

    return base + extra.get(context, "")
