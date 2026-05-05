"""
Быстрый ответ из Telegram в FunPay-чат.
Обрабатывает кнопки под уведомлениями о сообщениях:
  qr:reply:<chat_id>   — режим ввода ответа
  qr:tpl:<chat_id>     — показать шаблоны ответов
  qr:info:<chat_id>    — детали чата
  qr:mute:<chat_id>    — заглушить на 1ч
  qr:unmute:<chat_id>  — разглушить
  qr:send:<chat_id>    — отправить шаблон как ответ
"""
from __future__ import annotations

import html
from typing import Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from config.settings import config_manager
from core.funpay_client import funpay_client
from modules.notifications import mute_chat, unmute_chat
from utils.logger import logger
from utils.variables import send_with_vars, substitute

router = Router(name="quick_reply")


class QRStates(StatesGroup):
    waiting_reply_text = State()  # ждём текст ответа
    waiting_mute_hours = State()  # ждём кол-во часов


# ─── Вспомогательные клавиатуры ──────────────────────────────────────────────

def _kb_cancel_qr(chat_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"qr:cancel:{chat_id}")
    ]])


def _kb_templates_list(chat_id: str) -> InlineKeyboardMarkup:
    tpls = config_manager.settings.response_templates.templates
    rows = []
    for name in list(tpls.keys())[:15]:
        safe = name[:30]
        rows.append([InlineKeyboardButton(
            text=safe,
            callback_data=f"qr:send:{chat_id}:{safe}"
        )])
    rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data=f"qr:cancel:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_mute_options(chat_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1 час",  callback_data=f"qr:mute:{chat_id}:1"),
            InlineKeyboardButton(text="3 часа", callback_data=f"qr:mute:{chat_id}:3"),
            InlineKeyboardButton(text="24 часа",callback_data=f"qr:mute:{chat_id}:24"),
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"qr:cancel:{chat_id}")]
    ])


# ─── Обработчики кнопок ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("qr:reply:"))
async def qr_reply(c: CallbackQuery, state: FSMContext) -> None:
    chat_id = c.data.split(":", 2)[2]
    await state.set_state(QRStates.waiting_reply_text)
    await state.update_data(qr_chat_id=chat_id)
    await c.message.answer(
        "📨 <b>Ответ покупателю в FunPay</b>\n\n"
        "Просто отправьте сюда:\n"
        "• текст — улетит как сообщение\n"
        "• фото (можно с подписью) — улетит сначала фото, потом текст\n\n"
        "<i>В тексте можно использовать переменные: $username, $date, $time...</i>",
        parse_mode="HTML",
        reply_markup=_kb_cancel_qr(chat_id)
    )
    await c.answer()


@router.callback_query(F.data.startswith("qr:tpl:"))
async def qr_templates(c: CallbackQuery) -> None:
    chat_id = c.data.split(":", 2)[2]
    tpls = config_manager.settings.response_templates.templates
    if not tpls:
        await c.answer("Шаблоны не настроены. Добавьте в меню → Шаблоны ответов.", show_alert=True)
        return
    await c.message.answer(
        "📝 <b>Выберите шаблон для отправки:</b>",
        parse_mode="HTML",
        reply_markup=_kb_templates_list(chat_id)
    )
    await c.answer()


@router.callback_query(F.data.startswith("qr:info:"))
async def qr_info(c: CallbackQuery) -> None:
    chat_id = c.data.split(":", 2)[2]
    # Пытаемся получить информацию о чате через FunPayAPI
    info_lines = [f"🔍 <b>Детали чата</b>", f"", f"💬 ID чата: <code>{chat_id}</code>"]

    if funpay_client.account:
        try:
            # Пробуем получить историю чата через API
            chat = funpay_client.account.get_chat(int(chat_id))
            if chat:
                buyer_name = getattr(chat, "name", None) or getattr(chat, "username", "?")
                buyer_id   = getattr(chat, "id", None)
                info_lines += [
                    f"👤 Пользователь: <b>{html.escape(str(buyer_name))}</b>",
                    f"🔗 <a href=\"https://funpay.com/users/{buyer_id}/\">Профиль на FunPay</a>" if buyer_id else "",
                    f"",
                    f"🛒 Лот: {getattr(chat, 'node_name', '?')}",
                ]
        except Exception as e:
            logger.debug(f"get_chat {chat_id}: {e}")
            info_lines.append("⚠️ Не удалось загрузить детали (FunPay не подключён)")
    else:
        info_lines.append("⚠️ FunPay не подключён")

    info_lines += [
        "",
        f"🔗 <a href=\"https://funpay.com/chat/?node={chat_id}\">Открыть чат на FunPay ↗</a>",
    ]

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔇 Заглушить чат", callback_data=f"qr:mutemenu:{chat_id}"),
        InlineKeyboardButton(text="❌ Закрыть",       callback_data=f"qr:cancel:{chat_id}"),
    ]])

    await c.message.answer(
        "\n".join(l for l in info_lines if l is not None),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=kb
    )
    await c.answer()


@router.callback_query(F.data.startswith("qr:mutemenu:"))
async def qr_mute_menu(c: CallbackQuery) -> None:
    chat_id = c.data.split(":", 2)[2]
    await c.message.answer(
        "🔇 На сколько заглушить уведомления из этого чата?",
        reply_markup=_kb_mute_options(chat_id)
    )
    await c.answer()


@router.callback_query(F.data.startswith("qr:mute:"))
async def qr_mute(c: CallbackQuery) -> None:
    parts = c.data.split(":")
    # qr:mute:<chat_id>:<hours>
    chat_id = parts[2]
    hours   = float(parts[3]) if len(parts) > 3 else 1.0
    mute_chat(chat_id, hours)
    label = f"{int(hours)} ч" if hours < 24 else "24 ч"
    await c.answer(f"🔇 Чат заглушён на {label}", show_alert=False)
    try:
        await c.message.delete()
    except Exception:
        pass


@router.callback_query(F.data.startswith("qr:unmute:"))
async def qr_unmute(c: CallbackQuery) -> None:
    chat_id = c.data.split(":", 2)[2]
    unmute_chat(chat_id)
    await c.answer("🔔 Уведомления включены", show_alert=False)
    try:
        await c.message.delete()
    except Exception:
        pass


@router.callback_query(F.data.startswith("qr:send:"))
async def qr_send_template(c: CallbackQuery) -> None:
    # qr:send:<chat_id>:<template_name>
    parts   = c.data.split(":", 3)
    chat_id = parts[2] if len(parts) > 2 else "0"
    tpl_key = parts[3] if len(parts) > 3 else ""

    tpls = config_manager.settings.response_templates.templates
    text = tpls.get(tpl_key, "")
    if not text:
        await c.answer("Шаблон не найден.", show_alert=True)
        return

    await _do_send(c, chat_id, text)


@router.callback_query(F.data.startswith("qr:cancel:"))
async def qr_cancel(c: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await c.message.delete()
    except Exception:
        pass
    await c.answer("Отменено")


# ─── FSM: получаем текст ответа (или фото с подписью) ───────────────────────

@router.message(QRStates.waiting_reply_text)
async def qr_receive_text(message: Message, state: FSMContext) -> None:
    """
    Принимает либо обычный текст, либо фото (опционально с подписью).

    Если пришло фото — сначала загружаем его в FunPay, отправляем,
    затем — текст из подписи (если есть).
    """
    data    = await state.get_data()
    chat_id = data.get("qr_chat_id", "0")
    await state.clear()

    # ── Случай 1: фото (со/без подписи) ──
    if message.photo:
        await _do_send_photo_then_text(message, chat_id)
        return

    # ── Случай 2: только текст ──
    text = message.text or ""
    if not text.strip():
        await message.answer("❌ Пустое сообщение.")
        return
    await _do_send_msg(message, chat_id, text)


async def _do_send_photo_then_text(message: Message, chat_id: str) -> None:
    """
    Отправляет в FunPay-чат сначала фото, потом текст из подписи.

    Алгоритм:
      1) Скачиваем фото из Telegram
      2) Загружаем его на FunPay (account.upload_image)
      3) Отправляем картинку в чат FunPay
      4) Если есть подпись — отправляем её следующим сообщением
    """
    import asyncio
    from pathlib import Path

    if not funpay_client.account:
        await message.answer("❌ FunPay не подключён — не могу отправить фото.")
        return

    # Самое крупное фото (последнее в списке)
    photo = message.photo[-1]
    caption = message.caption or ""

    # 1. Скачиваем
    tmp_path = Path("/tmp") / f"qr_photo_{photo.file_id}.jpg"
    try:
        await message.bot.download(photo, destination=tmp_path)
    except Exception as e:
        await message.answer(f"❌ Не удалось скачать фото: {e}")
        return

    # 2. Загружаем на FunPay (это блокирующий вызов — выносим в executor)
    loop = asyncio.get_event_loop()
    try:
        image_id = await loop.run_in_executor(
            None, funpay_client.account.upload_image, str(tmp_path)
        )
    except Exception as e:
        await message.answer(f"❌ Не удалось загрузить фото в FunPay: {e}")
        try: tmp_path.unlink(missing_ok=True)
        except Exception: pass
        return
    finally:
        try: tmp_path.unlink(missing_ok=True)
        except Exception: pass

    # 3. Отправляем фото в чат
    try:
        await loop.run_in_executor(
            None,
            lambda: funpay_client.account.send_message(int(chat_id), "", image_id=int(image_id)),
        )
        logger.info(f"QuickReply photo → chat {chat_id} (image_id={image_id})")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить фото: {e}")
        return

    # 4. Если есть подпись — отправляем её следующим сообщением
    caption_sent = True
    if caption.strip():
        caption_sent = await _send_to_funpay(chat_id, caption)

    # Удаляем промпт «Ответ покупателю»
    await _try_delete_prompt(message)

    # Показываем красивое подтверждение
    if caption.strip() and caption_sent:
        await _show_sent_confirmation(message, chat_id, f"[📷 фото]\n{caption}")
    elif caption.strip() and not caption_sent:
        await message.answer(
            "⚠️ <b>Фото отправлено, а текст — нет.</b>\n"
            "Возможно, потеряна связь с FunPay. Попробуйте ещё раз через 📨 Ответить."
        )
    else:
        await _show_sent_confirmation(message, chat_id, "[📷 фото]")


# ─── Отправка в FunPay ───────────────────────────────────────────────────────

async def _do_send(c: CallbackQuery, chat_id: str, text: str) -> None:
    """Отправка через нажатие кнопки шаблона (быстрая выдача)."""
    success = await _send_to_funpay(chat_id, text)
    if success:
        await c.answer("✅ Отправлено!", show_alert=False)
        try:
            await c.message.delete()
        except Exception:
            pass
        await _show_sent_confirmation(c.message, chat_id, text)
    else:
        await c.answer("❌ Не удалось отправить. FunPay не подключён?", show_alert=True)


async def _do_send_msg(message: Message, chat_id: str, text: str) -> None:
    """Отправка текста, который юзер сам ввёл."""
    success = await _send_to_funpay(chat_id, text)
    # Удаляем промпт «Ответ покупателю в FunPay» — он юзеру больше не нужен
    await _try_delete_prompt(message)
    if success:
        await _show_sent_confirmation(message, chat_id, text)
    else:
        await message.answer(
            "❌ <b>Сообщение не отправлено</b>\n\n"
            "Возможные причины:\n"
            "• FunPay не подключён (проверьте /menu → 🔧 Система)\n"
            "• Ошибка сети или прокси\n"
            "• Чат закрыт"
        )


async def _try_delete_prompt(message: Message) -> None:
    """Пытается удалить предыдущее сообщение с промптом 'Ответ покупателю'."""
    # message — это сообщение которое прислал юзер с текстом ответа.
    # Бот отвечал на предыдущее сообщение «📨 Ответ покупателю в FunPay» — id = message.message_id - 1
    try:
        await message.bot.delete_message(message.chat.id, message.message_id - 1)
    except Exception:
        pass


async def _show_sent_confirmation(message_or_cb_message, chat_id: str, text: str) -> None:
    """
    Показывает красивое уведомление об отправленном сообщении в формате:
        📨 Я (название_аккаунта): <текст>
    с теми же 4 кнопками что и обычное уведомление.
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from config.settings import config_manager
    from modules.notifications import _chat_link

    own_name = config_manager.settings.funpay.username or "Вы"
    snippet = text[:1500]
    if len(text) > 1500:
        snippet += "\n<i>(обрезано)</i>"

    head = f"📨 <b>Я ({own_name}):</b>"
    body = f"{head}\n{snippet}" if (len(snippet) > 100 or "\n" in snippet) else f"{head} {snippet}"

    chat_link = _chat_link(chat_id)
    safe_name = (chat_id or "Чат")[:20]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📨 Ответить",  callback_data=f"qr:reply:{chat_id}"),
            InlineKeyboardButton(text="📝 Шаблоны",   callback_data=f"qr:tpl:{chat_id}"),
        ],
        [
            InlineKeyboardButton(text="🔍 Детали чата",          callback_data=f"qr:info:{chat_id}"),
            InlineKeyboardButton(text=f"💬 FunPay: {safe_name} ↗", url=chat_link),
        ],
    ])
    await message_or_cb_message.answer(body, reply_markup=kb)


async def _send_to_funpay(chat_id: str, text: str,
                          username: str = "", chat_name: str = "") -> bool:
    """Отправляет текст в FunPay-чат с подстановкой всех переменных."""
    if not funpay_client.account:
        return False
    ok = await send_with_vars(
        text,
        funpay_client.send_message,
        chat_id,
        account=funpay_client.account,
        username=username,
        chat_name=chat_name or username,
    )
    if ok:
        logger.info(f"QuickReply → chat {chat_id}: {text[:50]}")
    return ok
