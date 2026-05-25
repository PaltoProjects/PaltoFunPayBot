"""
Уведомления в Telegram о событиях FunPay.

Формат — как в FunPayBot.com:
  🆘 FunPay (поддержка): текст...
  [📨 Ответить]  [📝 Шаблоны]
  [🔍 Детали чата]  [💬 FunPay: Closedoorpls ↗]
"""
from __future__ import annotations

import time
from html import escape
from typing import Any, Dict, List, Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config.settings import config_manager
from core.event_bus import Event, event_bus
from core.funpay_client import funpay_client
from utils.logger import logger

_bot_ref: Optional[Bot] = None
PHOTO_CAPTION_LIMIT = 900


def set_bot(bot: Bot) -> None:
    global _bot_ref
    _bot_ref = bot


def _all_recipients() -> List[int]:
    s = config_manager.settings.telegram
    return list(set(s.admin_ids + s.manager_ids))


def _e(default: str) -> str:
    return default if config_manager.settings.notification_style.use_emoji else ""


def _buyer_link(username: str, uid: Optional[int]) -> str:
    style = config_manager.settings.notification_style
    if style.show_buyer_link and uid:
        return f'<a href="https://funpay.com/users/{uid}/">{escape(username or "?")}</a>'
    return escape(username or "?")


def _chat_link(chat_id: Any) -> str:
    if chat_id:
        return f"https://funpay.com/chat/?node={chat_id}"
    return "https://funpay.com/chat/"


def _clip_html_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 1)].rstrip() + "…"


# ─── Определение роли отправителя ────────────────────────────────────────────

def _detect_sender_role(message) -> tuple:
    """Возвращает (эмодзи, название_роли)."""
    author = (getattr(message, "author", "") or "").strip()
    author_id = getattr(message, "author_id", None)
    own_id = config_manager.settings.funpay.account_id

    if own_id and author_id == own_id:
        return ("🏷", f"Вы (PaltoBot)")

    if author_id == 0:
        return ("🤖", "FunPay (системное)")

    author_lower = author.lower()

    support_kw = ["поддержка", "support", "funpay", "фанпей", "fp_support"]
    if any(k in author_lower for k in support_kw):
        return ("🆘", "FunPay (поддержка)")

    arb_kw = ["арбитраж", "arbitrage", "арбитр", "mediator"]
    if any(k in author_lower for k in arb_kw):
        return ("⚖️", "Арбитраж")

    mod_kw = ["модератор", "moderator", "admin", "администратор"]
    if any(k in author_lower for k in mod_kw):
        return ("👮", "Модератор")

    return ("🛒", author)


# ─── Клавиатура с 4 кнопками ─────────────────────────────────────────────────

def kb_message_actions(chat_id: Any, chat_name: str) -> InlineKeyboardMarkup:
    """
    Клавиатура под уведомлением о сообщении.

    Содержит:
      ряд 1: 📨 Ответить · 📝 Шаблоны
      ряд 2: 🔍 Детали · 💬 FunPay: имя_чата
    """
    cid = str(chat_id) if chat_id else "0"
    chat_link = _chat_link(cid)
    safe_name = (chat_name or "Чат")[:20]
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📨 Ответить",  callback_data=f"qr:reply:{cid}"),
            InlineKeyboardButton(text="📝 Шаблоны",   callback_data=f"qr:tpl:{cid}"),
        ],
        [
            InlineKeyboardButton(text="🔍 Детали чата",          callback_data=f"qr:info:{cid}"),
            InlineKeyboardButton(text=f"💬 FunPay: {safe_name} ↗", url=chat_link),
        ],
    ])


# ─── Заглушение чатов ────────────────────────────────────────────────────────

def _is_muted(chat_id: Any) -> bool:
    style = config_manager.settings.notification_style
    cid = str(chat_id)
    until = style.muted_chats.get(cid, 0)
    if until and time.time() < until:
        return True
    if until:
        style.muted_chats.pop(cid, None)
        config_manager.save()
    return False


def mute_chat(chat_id: Any, hours: float = 1.0) -> None:
    style = config_manager.settings.notification_style
    style.muted_chats[str(chat_id)] = time.time() + hours * 3600
    config_manager.save()


def unmute_chat(chat_id: Any) -> None:
    style = config_manager.settings.notification_style
    style.muted_chats.pop(str(chat_id), None)
    config_manager.save()


# ─── Отправка ────────────────────────────────────────────────────────────────

async def _send_with_photo(uid: int, text: str, photo_url: Optional[str],
                            markup: Optional[InlineKeyboardMarkup] = None) -> None:
    if not _bot_ref:
        return
    try:
        if photo_url:
            await _bot_ref.send_photo(uid, photo=photo_url, caption=text,
                                       parse_mode="HTML", reply_markup=markup)
        else:
            await _bot_ref.send_message(uid, text, parse_mode="HTML",
                                         disable_web_page_preview=True, reply_markup=markup)
    except Exception as e:
        logger.debug(f"notify photo {uid}: {e}")
        try:
            await _bot_ref.send_message(uid, text, parse_mode="HTML",
                                         disable_web_page_preview=True, reply_markup=markup)
        except Exception as e2:
            logger.debug(f"notify fallback {uid}: {e2}")


async def _send_to_all(text: str, markup: Optional[InlineKeyboardMarkup] = None,
                        photo_url: Optional[str] = None) -> None:
    if not _bot_ref:
        return
    for uid in _all_recipients():
        await _send_with_photo(uid, text, photo_url, markup)


# ──────────────────────────────────────────────────────────────────────────────
# Подписки на события
# ──────────────────────────────────────────────────────────────────────────────

@event_bus.on(Event.NEW_MESSAGE)
async def notify_message(message) -> None:
    """Отправляет уведомление о новом сообщении в чате FunPay в Telegram админам."""
    try:
        if not config_manager.settings.notifications.new_messages:
            logger.debug("notify_message: уведомления о сообщениях выключены")
            return

        style = config_manager.settings.notification_style
        author_id = getattr(message, "author_id", None)
        author_name = getattr(message, "author", None) or ""
        own_id = config_manager.settings.funpay.account_id
        own_name = config_manager.settings.funpay.username or ""
        by_bot = bool(getattr(message, "by_bot", False))
        msg_text = (getattr(message, "text", "") or "")
        msg_chat_id = getattr(message, "chat_id", None) or getattr(message, "node_id", None)

        # is_own — наши собственные сообщения (отправленные через бот или с сайта)
        # Признаки: author_id == own_id, author == own_name, by_bot=True
        is_own = bool(
            (own_id and author_id and author_id == own_id) or
            (own_name and author_name and author_name == own_name)
        )

        # Подавляем echo: если этот же текст мы только что отправили в этот же чат
        # через нашего бота — это echo от FunPay, дубль не нужен.
        try:
            from core.funpay_client import funpay_client
            if msg_chat_id and funpay_client.is_recent_outgoing(msg_chat_id, msg_text):
                logger.info(f"📨 пропуск echo своего сообщения (chat={msg_chat_id})")
                return
        except Exception:
            pass

        # Сообщения наши/нашего бота — уведомлять только если включено
        if (is_own or by_bot) and not style.show_own_messages:
            logger.info(
                f"📨 пропуск своего сообщения "
                f"(is_own={is_own} by_bot={by_bot} author={author_name!r})"
            )
            return

        chat_id   = getattr(message, "chat_id", None) or getattr(message, "node_id", None)
        chat_name = getattr(message, "chat_name", None) or getattr(message, "node_name", None) or "Чат"
        body      = (getattr(message, "text", "") or "")[:1000]

        # FunPayAPI хранит одну картинку в image_link, не в images
        image_link = getattr(message, "image_link", None)
        images = []
        if image_link:
            images = [image_link]

        logger.info(f"📨 notify_message: chat={chat_id}({chat_name}) author={author_name!r} text={body[:60]!r}")

        if chat_id and _is_muted(chat_id) and not is_own:
            logger.debug(f"notify_message: чат {chat_id} замьючен")
            return

        # Получатели (админы/менеджеры в TG)
        recipients = _all_recipients()
        if not recipients:
            logger.warning("notify_message: НЕТ ПОЛУЧАТЕЛЕЙ! Войдите в TG-бот через /start.")
            return

        role_emoji, role_name = _detect_sender_role(message)
        safe_role_name = escape(role_name or "?")
        safe_body = escape(body or "(без текста)")

        if style.compact_mode:
            header = f"{role_emoji} <b>{safe_role_name}:</b> {escape((body or '(без текста)')[:120])}"
        else:
            header = f"{role_emoji} <b>{safe_role_name}:</b> {safe_body}"

        # Имена картинок ссылками (как Example.png на референсе)
        if images:
            prefix = f"{role_emoji} <b>{safe_role_name}:</b> "
            header = prefix + _clip_html_text(safe_body, PHOTO_CAPTION_LIMIT - len(prefix))
            links = []
            for i, img_url in enumerate(images[:5]):
                fname = img_url.split("/")[-1].split("?")[0] or f"image_{i+1}.png"
                links.append(f'<a href="{escape(img_url, quote=True)}">{escape(fname)}</a>')
            link_line = " ".join(links)
            if len(link_line) + 1 < PHOTO_CAPTION_LIMIT:
                header = _clip_html_text(header, PHOTO_CAPTION_LIMIT - len(link_line) - 1)
                header += "\n" + link_line
            else:
                header = _clip_html_text(header, PHOTO_CAPTION_LIMIT)

        markup = kb_message_actions(chat_id, chat_name)
        first_photo = images[0] if images else None

        for uid in recipients:
            await _send_with_photo(uid, header, first_photo, markup)
            for extra_url in images[1:5]:
                try:
                    await _bot_ref.send_photo(uid, photo=extra_url)
                except Exception as e:
                    logger.debug(f"extra photo {uid}: {e}")

        logger.debug(f"notify_message: отправлено {len(recipients)} получателям")

    except Exception as e:
        logger.exception(f"notify_message FAILED: {e}")


@event_bus.on(Event.NEW_ORDER)
async def notify_new_order(order) -> None:
    if not config_manager.settings.notifications.new_orders:
        return
    style = config_manager.settings.notification_style
    title    = getattr(order, "description", None) or getattr(order, "title", "?")
    price    = getattr(order, "price", "?")
    buyer    = getattr(order, "buyer_username", None) or getattr(order, "buyer", "?")
    buyer_id = getattr(order, "buyer_id", None)
    order_id = getattr(order, "id", "?")

    if style.compact_mode:
        text = f"{_e('🆕 ')}Новый заказ #{order_id}: {title} — {price}"
    else:
        text = (
            f"{_e('🆕 ')}<b>Новый заказ</b>\n"
            f"#{order_id}\n"
            f"📦 {title}\n"
            f"💰 {price}\n"
            f"👤 {_buyer_link(buyer, buyer_id)}"
        )
    await _send_to_all(text)


@event_bus.on(Event.ORDER_PAID)
async def notify_order_paid(order) -> None:
    if not config_manager.settings.notifications.order_paid:
        return
    style    = config_manager.settings.notification_style
    order_id = getattr(order, "id", "?")
    price    = getattr(order, "price", "?")
    text     = f"{_e('💵 ')}<b>Заказ оплачен</b>\n#{order_id}\nСумма: {price}"
    if style.show_balance_after_order and funpay_client.account:
        bal   = funpay_client.get_balance()
        text += f"\n\n💰 Баланс: {bal['rub']:.2f}₽"
    await _send_to_all(text)


@event_bus.on(Event.ORDER_CONFIRMED)
async def notify_order_confirmed(order) -> None:
    if not config_manager.settings.notifications.order_confirmed:
        return
    text = f"{_e('👍 ')}<b>Заказ подтверждён</b>\n#{getattr(order, 'id', '?')}"
    await _send_to_all(text)


@event_bus.on(Event.ORDER_REFUNDED)
async def notify_order_refunded(order) -> None:
    if not config_manager.settings.notifications.order_refunded:
        return
    text = f"{_e('↩️ ')}<b>Возврат средств</b>\n#{getattr(order, 'id', '?')}"
    await _send_to_all(text)


@event_bus.on(Event.NEW_REVIEW)
async def notify_review(review) -> None:
    if not config_manager.settings.notifications.new_reviews:
        return
    stars    = int(getattr(review, "stars", 0) or 0)
    rev_text = (getattr(review, "text", "") or "")[:200]
    order_id = getattr(review, "order_id", "?")
    text     = f"{_e('⭐ ')}<b>Новый отзыв</b>\n{'⭐' * stars}\n#{order_id}\n\n{rev_text}"
    await _send_to_all(text)


@event_bus.on("delivery_sent")
async def notify_delivery(order, content: str) -> None:
    if not config_manager.settings.notifications.delivery_sent:
        return
    snippet = content[:80] + ("..." if len(content) > 80 else "")
    text = (
        f"{_e('📦 ')}<b>Товар выдан</b>\n"
        f"Заказ #{getattr(order, 'id', '?')}\n"
        f"Содержимое: <code>{snippet}</code>"
    )
    await _send_to_all(text)


@event_bus.on(Event.LOT_LIFTED)
async def notify_lift(report: str) -> None:
    if not config_manager.settings.notifications.lot_lifted:
        return
    # Если report уже содержит свой заголовок — отправляем как есть.
    # Если это просто короткая строка (без переносов) — добавляем префикс.
    if "\n" in report or report.startswith("📈"):
        await _send_to_all(report)
    else:
        await _send_to_all(f"{_e('📈 ')}Поднятие: {report}")


@event_bus.on(Event.BOT_STARTED)
async def notify_started(account=None) -> None:
    """
    При запуске шлёт два сообщения:
      1) 🚀 Бот включился
      2) Вы используете аккаунт <name>  (только если FunPay подключён)
    """
    if not config_manager.settings.notifications.bot_started_stopped:
        return

    # Сообщение #1 — факт включения
    await _send_to_all(f"{_e('🚀 ')}<b>PaltoFunPayBot запущен</b>")

    # Сообщение #2 — если есть подключённый аккаунт FunPay
    name = None
    if account is not None:
        name = getattr(account, "username", None)
    if not name:
        # Подстраховка — может быть в funpay_client.account
        try:
            from core.funpay_client import funpay_client
            if funpay_client.account:
                name = getattr(funpay_client.account, "username", None)
        except Exception:
            pass
    if not name:
        # И ещё — может быть сохранено в конфиге с прошлой сессии
        name = config_manager.settings.funpay.username or None

    if name:
        await _send_to_all(f"Вы используете аккаунт <b>{name}</b>")


@event_bus.on(Event.BOT_STOPPED)
async def notify_stopped() -> None:
    if not config_manager.settings.notifications.bot_started_stopped:
        return
    await _send_to_all(f"{_e('🛑 ')}<b>PaltoFunPayBot остановлен</b>")
