"""Inline-клавиатуры. Структура меню повторяет FunPayBot.com (со скринов)."""
from __future__ import annotations

from typing import Dict, List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _e(b: bool) -> str:
    return "🟢" if b else "🔴"


def kb_unauthorized() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Канал @paltofunpaybot", url="https://t.me/paltofunpaybot")],
        [InlineKeyboardButton(text="🌐 GitHub PaltoFunPayBot", url="https://github.com/PaltoProjects/PaltoFunPayBot")],
    ])


def kb_main_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⚙️ Основные модули", callback_data="menu:core")
    b.button(text="🤖 Автоматизация", callback_data="menu:automation")
    b.button(text="📊 Управление и Аналитика", callback_data="menu:analytics")
    b.button(text="🔧 Система и Конфиги", callback_data="menu:system")
    b.button(text="🚪 Выйти", callback_data="menu:exit")
    b.adjust(1)
    return b.as_markup()


# ─── 1) ОСНОВНЫЕ МОДУЛИ ─────────────────────────────────────────────────────

def kb_core_modules(s) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"{_e(s.auto_lift.enabled)} Автоподнятие лотов", callback_data="core:t:auto_lift")
    b.button(text=f"{_e(s.auto_response.enabled)} Автоответчик", callback_data="core:t:auto_response")
    b.button(text=f"{_e(s.auto_delivery.enabled)} Автовыдача", callback_data="core:t:auto_delivery")
    b.button(text=f"{_e(s.multi_delivery.enabled)} Мульти-выдача", callback_data="core:t:multi_delivery")
    b.button(text=f"{_e(s.auto_restore.enabled)} Автовосстановление", callback_data="core:t:auto_restore")
    b.button(text=f"{_e(s.auto_activation.enabled)} Автодеактивация", callback_data="core:t:auto_activation")
    b.button(text=f"{_e(s.legacy_message_mode.enabled)} Устаревший режим сообщений", callback_data="core:t:legacy_message_mode")
    b.button(text="ℹ️ Инфо", callback_data="core:info")
    b.button(text=f"{_e(s.read_receipt.enabled)} Не отмечать прочитанным", callback_data="core:t:read_receipt")
    b.button(text="⬅️ Назад", callback_data="menu:main")
    b.adjust(2, 2, 2, 2, 1, 1)
    return b.as_markup()


# ─── 2) АВТОМАТИЗАЦИЯ ───────────────────────────────────────────────────────

def kb_automation() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💬 Автоответчик", callback_data="auto:response")
    b.button(text="📦 Автовыдача", callback_data="auto:delivery")
    b.button(text="👋 Приветствия", callback_data="auto:greeting")
    b.button(text="👍 Ответ на подтверждение", callback_data="auto:confirm")
    b.button(text="⭐ Ответ на отзывы", callback_data="auto:review")
    b.button(text="🧠 ИИ-помощник", callback_data="auto:ai")
    b.button(text="💸 Авто-вывод", callback_data="auto:withdraw")
    b.button(text="⬅️ Назад", callback_data="menu:main")
    b.adjust(2, 2, 2, 1, 1)
    return b.as_markup()


def kb_auto_withdraw(s) -> InlineKeyboardMarkup:
    """Меню авто-вывода средств."""
    w = s.auto_withdraw
    b = InlineKeyboardBuilder()
    b.button(text=f"{_e(w.enabled)} Включён", callback_data="wd:toggle")
    # Триггер: schedule | amount
    trigger_label = "📅 По расписанию" if w.trigger == "schedule" else "💰 По сумме"
    b.button(text=f"Триггер: {trigger_label}", callback_data="wd:trigger")
    if w.trigger == "schedule":
        sched_label = {"daily": "ежедневно", "weekly": "еженедельно", "monthly": "ежемесячно"}.get(w.schedule, w.schedule)
        b.button(text=f"⏰ {sched_label} в {w.schedule_hour}:00", callback_data="wd:schedule")
    b.button(text=f"💵 Минимум: {w.min_amount_rub}₽", callback_data="wd:amount")
    b.button(text=f"💳 Способ: {w.payment_method}", callback_data="wd:method")
    details_short = f"...{w.payment_details[-4:]}" if w.payment_details else "не задано"
    b.button(text=f"📝 Реквизиты: {details_short}", callback_data="wd:details")
    b.button(text="🚀 Вывести сейчас", callback_data="wd:now")
    b.button(text="⬅️ Назад", callback_data="menu:automation")
    b.adjust(1)
    return b.as_markup()


def kb_auto_response(s) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"{_e(s.auto_response.enabled)} Включён", callback_data="resp:toggle")
    b.button(text=f"📝 Шаблоны ({len(s.auto_response.templates)})", callback_data="resp:list")
    b.button(text="➕ Добавить", callback_data="resp:add")
    b.button(text="⬅️ Назад", callback_data="menu:automation")
    b.adjust(1, 2, 1)
    return b.as_markup()


def kb_templates_list(templates: Dict[str, str], prefix: str, back_cb: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for kw in list(templates.keys())[:30]:
        b.button(text=f"🗑 {kw[:30]}", callback_data=f"{prefix}:del:{kw}")
    b.button(text="⬅️ Назад", callback_data=back_cb)
    b.adjust(1)
    return b.as_markup()


def kb_auto_delivery(s) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"{_e(s.auto_delivery.enabled)} Включена", callback_data="dlv:toggle")
    b.button(text=f"{_e(s.multi_delivery.enabled)} Мульти-выдача (N штук)", callback_data="dlv:multi")
    b.button(text=f"📦 Лотов: {len(s.auto_delivery.lots)}", callback_data="dlv:list")
    b.button(text="➕ Добавить лот", callback_data="dlv:add")
    b.button(text="✏️ Текст 'товар закончился'", callback_data="dlv:oos")
    b.button(text="⬅️ Назад", callback_data="menu:automation")
    b.adjust(1, 1, 2, 1, 1)
    return b.as_markup()


def kb_delivery_list(lots: dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    emoji_map = {
        "static":   "📝",
        "file":     "📁",
        "csv":      "📊",
        "random":   "🎲",
        "combined": "📦",
    }
    for lot_id, info in list(lots.items())[:30]:
        t = info.get("type", "?")
        emoji = emoji_map.get(t, "❓")
        b.button(text=f"{emoji} Лот {lot_id} ({t})", callback_data=f"dlv:edit:{lot_id}")
    b.button(text="⬅️ Назад", callback_data="auto:delivery")
    b.adjust(1)
    return b.as_markup()


def kb_delivery_edit(lot_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📝 Изменить контент", callback_data=f"dlv:set:{lot_id}")
    b.button(text="⏱ Задержка выдачи", callback_data=f"dlv:delay:{lot_id}")
    b.button(text="🗑 Удалить лот", callback_data=f"dlv:rm:{lot_id}")
    b.button(text="⬅️ Назад", callback_data="dlv:list")
    b.adjust(1)
    return b.as_markup()


def kb_delivery_type_choice() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📝 Статичный текст",       callback_data="dlv:type:static")
    b.button(text="📁 Файл с ключами",         callback_data="dlv:type:file")
    b.button(text="📊 CSV-таблица",            callback_data="dlv:type:csv")
    b.button(text="🎲 Случайный из списка",    callback_data="dlv:type:random")
    b.button(text="📦 Инструкция + ключ",      callback_data="dlv:type:combined")
    b.button(text="❌ Отмена",                 callback_data="auto:delivery")
    b.adjust(1)
    return b.as_markup()


def kb_greeting(s) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"{_e(s.auto_greeting.enabled)} Включено", callback_data="grt:toggle")
    b.button(text=f"{_e(s.auto_greeting.ignore_system_messages)} Игнор системных", callback_data="grt:t_sys")
    b.button(text="✏️ Изменить текст", callback_data="grt:text")
    b.button(text=f"⏱ Кулдаун: {s.auto_greeting.cooldown_days} дн.", callback_data="grt:cd")
    b.button(text="⬅️ Назад", callback_data="menu:automation")
    b.adjust(1, 1, 2, 1)
    return b.as_markup()


def kb_order_confirm(s) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"{_e(s.order_confirm_reply.enabled)} Включено", callback_data="cfm:toggle")
    b.button(text="✏️ Изменить текст", callback_data="cfm:text")
    b.button(text=f"{_e(s.ask_review.enabled)} Просить отзыв ({s.ask_review.delay_minutes} мин)", callback_data="cfm:askr")
    b.button(text="✏️ Текст просьбы об отзыве", callback_data="cfm:askr_text")
    b.button(text="⬅️ Назад", callback_data="menu:automation")
    b.adjust(1)
    return b.as_markup()


def kb_review_reply(s) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"{_e(s.auto_review_reply.enabled)} Включено", callback_data="rvw:toggle")
    for stars in range(5, 0, -1):
        b.button(text=f"✏️ Ответ на {'⭐' * stars}", callback_data=f"rvw:edit:{stars}")
    b.button(text="⬅️ Назад", callback_data="menu:automation")
    b.adjust(1, 1, 1, 1, 1, 1, 1)
    return b.as_markup()


def kb_auto_lift(s) -> InlineKeyboardMarkup:
    """
    Меню автоподнятия — простое, всего одна кнопка-тумблер.
    Логика умная — бот сам разбирается с кулдаунами FunPay.
    """
    b = InlineKeyboardBuilder()
    b.button(text=f"{_e(s.auto_lift.enabled)} Включено", callback_data="lft:toggle")
    b.button(text="⬅️ Назад", callback_data="menu:automation")
    b.adjust(1)
    return b.as_markup()


def kb_ai(s) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"{_e(s.ai.enabled)} Включено", callback_data="ai:toggle")
    b.button(text=f"🔑 Ключ: {'есть' if s.ai.api_key else 'нет'}", callback_data="ai:key")
    b.button(text=f"🤖 {s.ai.provider}", callback_data="ai:provider")
    b.button(text=f"📝 {s.ai.model[:20]}", callback_data="ai:model")
    b.button(text=f"⏰ Через {s.ai.auto_reply_after_hours} ч.", callback_data="ai:hours")
    b.button(text="📋 Инструкция", callback_data="ai:instruction")
    b.button(text=f"{_e(s.ai.rewrite_drafts)} Переписывать черновики", callback_data="ai:rewrite")
    b.button(text="📝 Описания лотов", callback_data="ai:descs")
    b.button(text="⚠️ Авто-снятие демпинга", callback_data="ai:dump")
    b.button(text="⬅️ Назад", callback_data="menu:automation")
    b.adjust(1, 2, 2, 1, 1, 2, 1)
    return b.as_markup()


def kb_lot_desc(s) -> InlineKeyboardMarkup:
    """Меню генератора описаний лотов."""
    cfg = s.lot_desc_generator
    b = InlineKeyboardBuilder()
    b.button(text=f"{_e(cfg.enabled)} Включено", callback_data="ldg:toggle")
    b.button(text=f"🎨 Тон: {cfg.tone}", callback_data="ldg:tone")
    b.button(text=f"📏 Макс. длина: {cfg.max_length}", callback_data="ldg:length")
    b.button(text=f"{_e(cfg.include_emoji)} Эмодзи в описаниях", callback_data="ldg:emoji")
    b.button(text="📋 Кастомный промпт", callback_data="ldg:prompt")
    b.button(text="⬅️ Назад", callback_data="auto:ai")
    b.adjust(1)
    return b.as_markup()


def kb_anti_dumping(s) -> InlineKeyboardMarkup:
    """Меню авто-снятия демпинга."""
    cfg = s.anti_dumping
    b = InlineKeyboardBuilder()
    b.button(text=f"{_e(cfg.enabled)} Включено", callback_data="adp:toggle")
    b.button(text=f"⏱ Проверка: каждые {cfg.interval_minutes} мин", callback_data="adp:interval")
    b.button(text=f"📉 Порог: {cfg.threshold_percent:.0f}%", callback_data="adp:threshold")
    b.button(text=f"{_e(cfg.notify)} Уведомлять в TG", callback_data="adp:notify")
    b.button(text="⬅️ Назад", callback_data="auto:ai")
    b.adjust(1)
    return b.as_markup()


# ─── 3) УПРАВЛЕНИЕ И АНАЛИТИКА ──────────────────────────────────────────────

def kb_analytics() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📈 Статистика", callback_data="ana:stats")
    b.button(text="💰 Баланс", callback_data="ana:balance")
    b.button(text="📝 Шаблоны ответов", callback_data="ana:templates")
    b.button(text="🚫 Чёрный список", callback_data="ana:blacklist")
    b.button(text="👥 Пользователи", callback_data="ana:users")
    b.button(text="⬅️ Назад", callback_data="menu:main")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


def kb_stats_periods() -> InlineKeyboardMarkup:
    """Главное меню аналитики — выбор периода и тип отчёта."""
    b = InlineKeyboardBuilder()
    b.button(text="📅 Сегодня",   callback_data="ana:p:today")
    b.button(text="📅 Неделя",    callback_data="ana:p:week")
    b.button(text="📅 Месяц",     callback_data="ana:p:month")
    b.button(text="📅 Год",       callback_data="ana:p:year")
    b.button(text="📅 Всё время", callback_data="ana:p:all")
    b.button(text="⬅️ Назад", callback_data="menu:analytics")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


def kb_stats_actions(period: str) -> InlineKeyboardMarkup:
    """После выбора периода — список конкретных отчётов."""
    b = InlineKeyboardBuilder()
    b.button(text="📊 График доходов",   callback_data=f"ana:c:revenue:{period}")
    b.button(text="🏆 Топ-10 товаров",   callback_data=f"ana:c:top:{period}")
    b.button(text="🎯 Конверсия",        callback_data=f"ana:r:conv:{period}")
    b.button(text="💵 Средний чек",      callback_data=f"ana:r:avg:{period}")
    b.button(text="🔮 Прогноз",          callback_data=f"ana:r:forecast:{period}")
    b.button(text="📈 vs прошлый период", callback_data=f"ana:r:cmp:{period}")
    b.button(text="⏰ Часы пик",         callback_data="ana:c:peak:0")
    b.button(text="📥 Экспорт CSV",      callback_data="ana:csv")
    b.button(text="⬅️ К периодам",       callback_data="ana:stats")
    b.adjust(2, 2, 2, 2, 1)
    return b.as_markup()


def kb_response_templates(templates: dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"📝 Шаблонов: {len(templates)}", callback_data="ana:templates_list")
    b.button(text="➕ Добавить", callback_data="ana:templates_add")
    b.button(text="⬅️ Назад", callback_data="menu:analytics")
    b.adjust(2, 1)
    return b.as_markup()


def kb_blacklist(s) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"{_e(s.blacklist.enabled)} Включён", callback_data="bl:toggle")
    b.button(text=f"📋 Список ({len(s.blacklist.nicknames)})", callback_data="bl:list")
    b.button(text="➕ Добавить", callback_data="bl:add")
    b.button(text="⬅️ Назад", callback_data="menu:analytics")
    b.adjust(1, 2, 1)
    return b.as_markup()


def kb_blacklist_list(nicks: List[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for n in nicks[:50]:
        b.button(text=f"🗑 {n}", callback_data=f"bl:rm:{n}")
    b.button(text="⬅️ Назад", callback_data="ana:blacklist")
    b.adjust(1)
    return b.as_markup()


def kb_users(admins: int, managers: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"👑 Администраторов: {admins}", callback_data="usr:list_adm")
    b.button(text=f"🛠 Менеджеров: {managers}", callback_data="usr:list_mgr")
    b.button(text="🔑 Создать ключ менеджера", callback_data="usr:new_key")
    b.button(text="⬅️ Назад", callback_data="menu:analytics")
    b.adjust(1)
    return b.as_markup()


# ─── 4) СИСТЕМА И КОНФИГИ ───────────────────────────────────────────────────

def kb_system() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔔 Уведомления", callback_data="sys:notifs")
    b.button(text="💬 Вид уведомлений", callback_data="sys:notifs_style")
    b.button(text="🧩 Плагины", callback_data="sys:plugins")
    b.button(text="🌐 Прокси", callback_data="sys:proxy")
    b.button(text="🔑 golden_key", callback_data="sys:gk")
    b.button(text="🪪 User-Agent", callback_data="sys:ua")
    b.button(text="💧 Водяной знак", callback_data="sys:watermark")
    b.button(text="📊 Статус (/sys)", callback_data="sys:status")
    b.button(text="🔄 Проверить обновления", callback_data="sys:updates")
    b.button(text="⬅️ Назад", callback_data="menu:main")
    b.adjust(2, 2, 2, 2, 1, 1)
    return b.as_markup()


def kb_proxy(s) -> InlineKeyboardMarkup:
    """Меню управления прокси."""
    b = InlineKeyboardBuilder()
    short = s.funpay.proxy.split("@")[-1] if s.funpay.proxy else "не задан"
    b.button(text=f"📡 Текущий: {short}", callback_data="prx:show")
    b.button(text="✏️ Изменить", callback_data="prx:edit")
    b.button(text="🗑 Удалить", callback_data="prx:remove")
    b.button(text="🧪 Проверить", callback_data="prx:test")
    b.button(text="⬅️ Назад", callback_data="menu:system")
    b.adjust(1)
    return b.as_markup()


def kb_notifications(s) -> InlineKeyboardMarkup:
    n = s.notifications
    b = InlineKeyboardBuilder()
    items = [
        ("new_orders", "🆕 Новые заказы"),
        ("order_paid", "💵 Оплата заказа"),
        ("order_confirmed", "👍 Подтверждение"),
        ("order_refunded", "↩️ Возврат"),
        ("new_messages", "💬 Новые сообщения"),
        ("new_reviews", "⭐ Новые отзывы"),
        ("delivery_sent", "📦 Выдача товара"),
        ("lot_lifted", "📈 Поднятие лотов"),
        ("bot_started_stopped", "🚀 Старт/стоп бота"),
    ]
    for field, label in items:
        b.button(text=f"{_e(getattr(n, field))} {label}", callback_data=f"ntf:t:{field}")
    b.button(text="⬅️ Назад", callback_data="menu:system")
    b.adjust(1)
    return b.as_markup()


def kb_notification_style(s) -> InlineKeyboardMarkup:
    ns = s.notification_style
    b = InlineKeyboardBuilder()
    b.button(text=f"{_e(ns.use_emoji)} Эмодзи", callback_data="nst:t:use_emoji")
    b.button(text=f"{_e(ns.show_buyer_link)} Ссылка на покупателя", callback_data="nst:t:show_buyer_link")
    b.button(text=f"{_e(ns.show_lot_link)} Ссылка на лот", callback_data="nst:t:show_lot_link")
    b.button(text=f"{_e(ns.show_balance_after_order)} Баланс после заказа", callback_data="nst:t:show_balance_after_order")
    b.button(text=f"{_e(ns.compact_mode)} Компактный режим", callback_data="nst:t:compact_mode")
    b.button(text=f"{_e(ns.show_own_messages)} Показывать свои сообщения", callback_data="nst:t:show_own_messages")
    b.button(text="⬅️ Назад", callback_data="menu:system")
    b.adjust(1)
    return b.as_markup()


def kb_watermark(s) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"{_e(s.watermark.enabled)} Включён", callback_data="wm:toggle")
    b.button(text="✏️ Изменить текст", callback_data="wm:text")
    b.button(text="⬅️ Назад", callback_data="menu:system")
    b.adjust(1)
    return b.as_markup()


# Старая kb_plugins удалена — UI плагинов теперь в bot/handlers/plugins.py


# ─── Утилиты ─────────────────────────────────────────────────────────────────

def kb_back(callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=callback)]
    ])


def kb_confirm(yes_callback: str, no_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да", callback_data=yes_callback),
        InlineKeyboardButton(text="❌ Нет", callback_data=no_callback),
    ]])

def kb_cancel_edit(back_cb: str) -> InlineKeyboardMarkup:
    """Кнопка отмены редактирования."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"edit:cancel:{back_cb}")
    ]])

