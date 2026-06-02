"""
Меню (/menu) и роутинг по 4 разделам.

Структура повторяет FunPayBot.com (см. скрины):
- ⚙️  Основные модули — глобальные тумблеры
- 🤖 Автоматизация — детальная настройка модулей
- 📊 Управление и Аналитика — статистика, чёрный список, юзеры
- 🔧 Система и Конфиги — уведомления, плагины, golden_key, watermark
"""
from __future__ import annotations

import html
import secrets

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot import keyboards as kb
from bot.handlers.auth import is_admin, is_authorized
from bot.states import EditStates
from config.settings import config_manager
from core.funpay_client import funpay_client
from utils.logger import logger

router = Router(name="menu")


def s():
    return config_manager.settings

# Подсказки по переменным для полей ввода
from utils.variables import describe_vars as _dv

# Подсказки переменных для меню (импортируем из utils.variables)
_VARS_BASE     = _dv("base")      # базовые: дата/время/username/chat_id/sleep/photo
_VARS_BUYER    = _dv("base")      # для приветствий и подтверждений (те же базовые)
_VARS_ORDER    = _dv("order")     # + $order_id/$order_price/$order_desc
_VARS_RESPONSE = _dv("message")   # + $message_text
_VARS_REVIEW   = _dv("review")    # + $order_id/$stars/$review_text
_VARS_DELIVERY = _dv("delivery")  # + $order_id/$order_price/$lot_id



# ─── Утилита: установка вложенного поля по строке "a.b.c" ────────────────────

def _set_nested(target: str, value) -> None:
    parts = target.split(".")
    obj = s()
    for p in parts[:-1]:
        obj = getattr(obj, p)
    setattr(obj, parts[-1], value)
    config_manager.save()


# ─── /menu ────────────────────────────────────────────────────────────────────



@router.callback_query(F.data.startswith("edit:cancel:"))
async def edit_cancel(c: types.CallbackQuery, state: FSMContext) -> None:
    """Отменяет любое редактирование и возвращает в нужное меню."""
    back_cb = c.data.removeprefix("edit:cancel:")
    await state.clear()
    try:
        await c.message.delete()
    except Exception:
        pass
    # Отправляем главное меню (или конкретный раздел)
    text = "❌ Редактирование отменено."
    await c.message.answer(text)
    await c.answer()

@router.message(Command("menu"))
async def cmd_menu(msg: types.Message, state: FSMContext) -> None:
    if not is_authorized(msg.from_user.id):
        await msg.answer("⛔ Доступ запрещён. Введите /start для авторизации.")
        return
    await state.clear()
    await msg.answer(
        f"👋 Привет! Это меню PaltoFunPayBot v{s().version}. Выберите, что хотите настроить.",
        reply_markup=kb.kb_main_menu(),
    )


@router.callback_query(F.data == "menu:main")
async def back_main(c: types.CallbackQuery) -> None:
    await c.message.edit_text(
        f"👋 Привет! Это меню PaltoFunPayBot v{s().version}. Выберите, что хотите настроить.",
        reply_markup=kb.kb_main_menu(),
    )
    await c.answer()


@router.callback_query(F.data == "menu:exit")
async def menu_exit(c: types.CallbackQuery) -> None:
    await c.message.edit_text("👋 Меню закрыто. /menu — открыть снова.")
    await c.answer()


# ═════════════════════════════════════════════════════════════════════════════
# 1) ОСНОВНЫЕ МОДУЛИ — тумблеры
# ═════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu:core")
async def menu_core(c: types.CallbackQuery) -> None:
    await c.message.edit_text(
        "⚙️ <b>Основные модули</b>\n\nЗдесь вы можете включать и выключать основные модули бота.",
        reply_markup=kb.kb_core_modules(s()),
    )
    await c.answer()


@router.callback_query(F.data.startswith("core:t:"))
async def core_toggle(c: types.CallbackQuery) -> None:
    field = c.data.removeprefix("core:t:")
    obj = getattr(s(), field, None)
    if obj is None or not hasattr(obj, "enabled"):
        await c.answer("Не нашёл модуль", show_alert=True)
        return
    obj.enabled = not obj.enabled
    config_manager.save()
    await menu_core(c)


@router.callback_query(F.data == "core:info")
async def core_info(c: types.CallbackQuery) -> None:
    txt = (
        "ℹ️ <b>Что делают модули</b>\n\n"
        "<b>Автоподнятие</b> — раз в N минут поднимает все ваши лоты в выдаче FunPay.\n\n"
        "<b>Автоответчик</b> — отвечает по ключевым словам из шаблонов.\n\n"
        "<b>Автовыдача</b> — после оплаты заказа сразу шлёт покупателю заранее настроенный текст или ключ из файла.\n\n"
        "<b>Мульти-выдача</b> — если включена, при покупке N штук шлёт N ключей.\n\n"
        "<b>Автодеактивация</b> — выключает лот, когда в файле кончаются ключи.\n\n"
        "<b>Автовосстановление</b> — включает обратно те лоты, которые мы выключили, как только в файле появятся ключи.\n\n"
        "<b>Устаревший режим сообщений</b> — для совместимости с FunPay (если у бота возникают проблемы с чтением сообщений).\n\n"
        "<b>Не отмечать прочитанным</b> — при отправке наших ответов чат остаётся «непрочитанным» в FunPay (вы заметите его).\n\n"
        "⚠️ Чаты автоматически помечаются как прочитанные."
    )
    await c.message.edit_text(txt, reply_markup=kb.kb_back("menu:core"))
    await c.answer()


@router.callback_query(F.data == "core:help")
async def core_help(c: types.CallbackQuery) -> None:
    await c.answer("Тапни на любой пункт, чтобы переключить его.", show_alert=True)


# ═════════════════════════════════════════════════════════════════════════════
# 2) АВТОМАТИЗАЦИЯ
# ═════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu:automation")
async def menu_automation(c: types.CallbackQuery) -> None:
    await c.message.edit_text("🤖 <b>Автоматизация</b>", reply_markup=kb.kb_automation())
    await c.answer()


# ─── Автоответчик ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "auto:response")
async def auto_response(c: types.CallbackQuery) -> None:
    await c.message.edit_text(
        f"💬 <b>Автоответчик</b>\n\nШаблонов: <b>{len(s().auto_response.templates)}</b>",
        reply_markup=kb.kb_auto_response(s()),
    )
    await c.answer()


@router.callback_query(F.data == "resp:toggle")
async def resp_toggle(c: types.CallbackQuery) -> None:
    s().auto_response.enabled = not s().auto_response.enabled
    config_manager.save()
    await auto_response(c)


@router.callback_query(F.data == "resp:list")
async def resp_list(c: types.CallbackQuery) -> None:
    t = s().auto_response.templates
    if not t:
        await c.answer("Шаблонов нет", show_alert=True); return
    text = "📝 <b>Шаблоны автоответчика</b>\n\n" + "\n".join(f"• <code>{k}</code> → {v[:60]}" for k, v in t.items())
    await c.message.edit_text(text, reply_markup=kb.kb_templates_list(t, "resp", "auto:response"))
    await c.answer()


@router.callback_query(F.data == "resp:add")
async def resp_add(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "📝 Отправьте <b>ключевое слово</b> (триггер):\n"
        "<i>Например: гарантия, цена, привет</i>",
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("auto:response")
    )
    await state.set_state(EditStates.editing_template_keyword)
    await c.answer()


@router.callback_query(F.data.startswith("resp:del:"))
async def resp_del(c: types.CallbackQuery) -> None:
    kw = c.data.removeprefix("resp:del:")
    s().auto_response.templates.pop(kw, None)
    config_manager.save()
    await c.answer(f"Удалено: {kw}")
    await resp_list(c)


# ─── Автовыдача ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "auto:delivery")
async def auto_delivery(c: types.CallbackQuery) -> None:
    cfg = s().auto_delivery
    await c.message.edit_text(
        f"📦 <b>Автовыдача</b>\n\n"
        f"Настраивается <b>вручную для каждого лота</b>: после оплаты бот отправит "
        f"покупателю заранее заданный текст или строку из файла с ключами.\n\n"
        f"Лотов настроено: <b>{len(cfg.lots)}</b>",
        reply_markup=kb.kb_auto_delivery(s()),
    )
    await c.answer()


@router.callback_query(F.data == "dlv:toggle")
async def dlv_toggle(c: types.CallbackQuery) -> None:
    s().auto_delivery.enabled = not s().auto_delivery.enabled
    config_manager.save()
    await auto_delivery(c)


@router.callback_query(F.data == "dlv:list")
async def dlv_list(c: types.CallbackQuery) -> None:
    lots = s().auto_delivery.lots
    if not lots:
        await c.answer("Лотов пока нет", show_alert=True); return
    from modules.auto_delivery import get_remaining_keys
    lines = []
    for lid, info in lots.items():
        t = info.get("type")
        if t == "static":
            lines.append(f"📝 <b>{lid}</b> — статичный текст")
        else:
            n = get_remaining_keys(lid)
            lines.append(f"📁 <b>{lid}</b> — файл, осталось {n} шт.")
    await c.message.edit_text(
        "📦 <b>Лоты с автовыдачей</b>\n\n" + "\n".join(lines),
        reply_markup=kb.kb_delivery_list(lots),
    )
    await c.answer()


@router.callback_query(F.data == "dlv:add")
async def dlv_add(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "📦 Отправьте <b>ID лота</b>\n"
        "<i>Число из URL вашего лота: funpay.com/lots/offer?id=<b>12345678</b></i>",
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("auto:delivery")
    )
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="__dlv_lot_id__", back_to="auto:delivery")
    await c.answer()


@router.callback_query(F.data == "dlv:oos")
async def dlv_oos(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        f"✏️ Текст «товар закончился»\n"
        f"Покупатель увидит его если ключи по лоту закончились.\n\n"
        f"Текущий: <code>{s().auto_delivery.out_of_stock_message}</code>"
        + _VARS_BASE("\n\n", "\n"),
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("auto:delivery")
    )
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="auto_delivery.out_of_stock_message", back_to="auto:delivery")
    await c.answer()


@router.callback_query(F.data.startswith("dlv:edit:"))
async def dlv_edit(c: types.CallbackQuery) -> None:
    lot_id = c.data.removeprefix("dlv:edit:")
    info = s().auto_delivery.lots.get(lot_id)
    if not info:
        await c.answer("Лот не найден", show_alert=True); return
    if info["type"] == "static":
        body = f"📝 Тип: <b>статика</b>\nТекст:\n<code>{info['content']}</code>"
    else:
        from modules.auto_delivery import get_remaining_keys
        body = f"📁 Тип: <b>файл</b>\nОсталось ключей: <b>{get_remaining_keys(lot_id)}</b>"
    await c.message.edit_text(
        f"📦 <b>Лот {lot_id}</b>\n\n{body}",
        reply_markup=kb.kb_delivery_edit(lot_id),
    )
    await c.answer()


@router.callback_query(F.data.startswith("dlv:rm:"))
async def dlv_rm(c: types.CallbackQuery) -> None:
    from modules.auto_delivery import remove_lot
    lot_id = c.data.removeprefix("dlv:rm:")
    remove_lot(lot_id)
    await c.answer(f"Лот {lot_id} удалён", show_alert=True)
    await dlv_list(c)


@router.callback_query(F.data.startswith("dlv:set:"))
async def dlv_set(c: types.CallbackQuery, state: FSMContext) -> None:
    lot_id = c.data.removeprefix("dlv:set:")
    await c.message.edit_text(
        f"📦 Лот <b>{lot_id}</b>\nКакой тип контента?",
        reply_markup=kb.kb_delivery_type_choice(),
    )
    await state.update_data(__dlv_set_lot_id__=lot_id)
    await c.answer()


@router.callback_query(F.data.startswith("dlv:type:"))
async def dlv_type(c: types.CallbackQuery, state: FSMContext) -> None:
    t = c.data.removeprefix("dlv:type:")
    data = await state.get_data()
    lot_id = data.get("__dlv_set_lot_id__") or data.get("__dlv_lot_id__")
    if not lot_id:
        await c.answer("Сначала укажите ID лота", show_alert=True); return

    if t == "static":
        await c.message.answer(
            f"📝 Лот <b>{lot_id}</b>: отправьте <b>один</b> текст выдачи.\n"
            f"Этот текст получит каждый покупатель сразу после оплаты.\n\n" + _VARS_BASE,
            parse_mode="HTML",
            reply_markup=kb.kb_cancel_edit("auto:delivery"),
        )
        await state.set_state(EditStates.editing_text)
        await state.update_data(target=f"__dlv_static__:{lot_id}", back_to="auto:delivery")
    elif t == "file":
        await c.message.answer(
            f"📁 Лот <b>{lot_id}</b>: список ключей для выдачи.\n"
            f"Отправьте <b>одну строку = один ключ</b>. "
            f"Бот выдаст по очереди, использованную строку удалит.",
            parse_mode="HTML",
            reply_markup=kb.kb_cancel_edit("auto:delivery"),
        )
        await state.set_state(EditStates.editing_text)
        await state.update_data(target=f"__dlv_file__:{lot_id}", back_to="auto:delivery")
    elif t == "csv":
        await c.message.answer(
            f"📊 Лот <b>{lot_id}</b>: <b>CSV-таблица</b> с данными.\n\n"
            f"Первая строка — заголовки колонок. Остальные — данные (по одной строке на покупателя).\n\n"
            f"<b>Пример:</b>\n"
            f"<code>login,password,note\n"
            f"user1@mail.com,Pass123,VIP-аккаунт\n"
            f"user2@mail.com,Qwerty,обычный</code>\n\n"
            f"Дальше попрошу шаблон выдачи (как форматировать строку).",
            parse_mode="HTML",
            reply_markup=kb.kb_cancel_edit("auto:delivery"),
        )
        await state.set_state(EditStates.editing_text)
        await state.update_data(target=f"__dlv_csv__:{lot_id}", back_to="auto:delivery")
    elif t == "random":
        await c.message.answer(
            f"🎲 Лот <b>{lot_id}</b>: <b>случайный выбор</b> из списка.\n"
            f"Отправьте варианты, по одному на строку — бот при каждой выдаче выберет случайный.\n"
            f"<i>Варианты <b>не расходуются</b> — могут повторяться.</i>",
            parse_mode="HTML",
            reply_markup=kb.kb_cancel_edit("auto:delivery"),
        )
        await state.set_state(EditStates.editing_text)
        await state.update_data(target=f"__dlv_random__:{lot_id}", back_to="auto:delivery")
    elif t == "combined":
        await c.message.answer(
            f"📦 Лот <b>{lot_id}</b>: <b>инструкция + уникальный ключ</b>.\n\n"
            f"Сначала отправьте <b>общую инструкцию</b> (какую увидит каждый покупатель), "
            f"потом попрошу список ключей.",
            parse_mode="HTML",
            reply_markup=kb.kb_cancel_edit("auto:delivery"),
        )
        await state.set_state(EditStates.editing_text)
        await state.update_data(target=f"__dlv_combined_instr__:{lot_id}", back_to="auto:delivery")
    await c.answer()


# ─── Тумблер мульти-выдачи ──────────────────────────────────────────────────

@router.callback_query(F.data == "dlv:multi")
async def dlv_multi(c: types.CallbackQuery) -> None:
    s().multi_delivery.enabled = not s().multi_delivery.enabled
    config_manager.save()
    await auto_delivery(c)


# ─── Задержка выдачи ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("dlv:delay:"))
async def dlv_delay(c: types.CallbackQuery, state: FSMContext) -> None:
    lot_id = c.data.removeprefix("dlv:delay:")
    info = s().auto_delivery.lots.get(lot_id)
    if not info:
        await c.answer("Лот не найден", show_alert=True); return
    cur = int(info.get("delay_sec", 0))
    await c.message.answer(
        f"⏱ Задержка перед выдачей лота <b>{lot_id}</b>.\n"
        f"Сейчас: <b>{cur}</b> сек.\n\n"
        f"Полезно если хочешь имитировать ручную работу.\n"
        f"Введите 0-3600 секунд:",
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit(f"dlv:edit:{lot_id}"),
    )
    await state.set_state(EditStates.editing_int)
    await state.update_data(
        target=f"__dlv_delay__:{lot_id}",
        min_value=0, max_value=3600,
        back_to=f"dlv:edit:{lot_id}",
    )
    await c.answer()


# ─── Приветствия ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "auto:greeting")
async def auto_greeting(c: types.CallbackQuery) -> None:
    cur = s().auto_greeting.text
    await c.message.edit_text(
        f"👋 <b>Приветствия</b>\n\n"
        f"Текущий текст:\n<code>{cur}</code>\n\n"
        + _VARS_BASE,
        reply_markup=kb.kb_greeting(s()),
    )
    await c.answer()


@router.callback_query(F.data == "grt:toggle")
async def grt_toggle(c: types.CallbackQuery) -> None:
    s().auto_greeting.enabled = not s().auto_greeting.enabled
    config_manager.save(); await auto_greeting(c)


@router.callback_query(F.data == "grt:t_sys")
async def grt_t_sys(c: types.CallbackQuery) -> None:
    s().auto_greeting.ignore_system_messages = not s().auto_greeting.ignore_system_messages
    config_manager.save(); await auto_greeting(c)


@router.callback_query(F.data == "grt:text")
async def grt_text(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "✏️ Отправьте новый текст приветствия:"
        + _VARS_BUYER,
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("auto:greeting")
    )
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="auto_greeting.text", back_to="auto:greeting")
    await c.answer()


@router.callback_query(F.data == "grt:cd")
async def grt_cd(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "⏱ Введите кулдаун в днях (0–365):",
        reply_markup=kb.kb_cancel_edit("auto:greeting")
    )
    await state.set_state(EditStates.editing_int)
    await state.update_data(target="auto_greeting.cooldown_days", min_value=0, max_value=365, back_to="auto:greeting")
    await c.answer()


# ─── Ответ на подтверждение + просьба об отзыве ──────────────────────────────

@router.callback_query(F.data == "auto:confirm")
async def auto_confirm(c: types.CallbackQuery) -> None:
    cf = s().order_confirm_reply
    ar = s().ask_review
    await c.message.edit_text(
        f"👍 <b>Ответ на подтверждение заказа</b>\n\n"
        f"Текст ответа:\n<code>{cf.text}</code>\n\n"
        f"Просьба об отзыве через <b>{ar.delay_minutes}</b> мин:\n<code>{ar.text}</code>",
        reply_markup=kb.kb_order_confirm(s()),
    )
    await c.answer()


@router.callback_query(F.data == "cfm:toggle")
async def cfm_toggle(c: types.CallbackQuery) -> None:
    s().order_confirm_reply.enabled = not s().order_confirm_reply.enabled
    config_manager.save(); await auto_confirm(c)


@router.callback_query(F.data == "cfm:askr")
async def cfm_askr(c: types.CallbackQuery) -> None:
    s().ask_review.enabled = not s().ask_review.enabled
    config_manager.save(); await auto_confirm(c)


@router.callback_query(F.data == "cfm:text")
async def cfm_text(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "✏️ Новый текст ответа на подтверждение заказа:\n\n"
        + _VARS_ORDER,
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("auto:confirm")
    )
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="order_confirm_reply.text", back_to="auto:confirm")
    await c.answer()


@router.callback_query(F.data == "cfm:askr_text")
async def cfm_askr_text(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "✏️ Новый текст просьбы об отзыве:"
        + _VARS_ORDER,
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("auto:confirm")
    )
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="ask_review.text", back_to="auto:confirm")
    await c.answer()


# ─── Ответ на отзывы ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "auto:review")
async def auto_review(c: types.CallbackQuery) -> None:
    r = s().auto_review_reply
    txt = (
        "⭐ <b>Автоответ на отзывы</b>\n\n"
        f"⭐⭐⭐⭐⭐: <code>{r.reply_5}</code>\n"
        f"⭐⭐⭐⭐: <code>{r.reply_4}</code>\n"
        f"⭐⭐⭐: <code>{r.reply_3}</code>\n"
        f"⭐⭐: <code>{r.reply_2}</code>\n"
        f"⭐: <code>{r.reply_1}</code>"
    )
    await c.message.edit_text(txt, reply_markup=kb.kb_review_reply(s()))
    await c.answer()


@router.callback_query(F.data == "rvw:toggle")
async def rvw_toggle(c: types.CallbackQuery) -> None:
    s().auto_review_reply.enabled = not s().auto_review_reply.enabled
    config_manager.save(); await auto_review(c)


@router.callback_query(F.data.startswith("rvw:edit:"))
async def rvw_edit(c: types.CallbackQuery, state: FSMContext) -> None:
    stars = c.data.removeprefix("rvw:edit:")
    await c.message.answer(
        f"✏️ Текст ответа на отзыв с {stars} ★:"
        + _VARS_REVIEW,
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("auto:review_reply")
    )
    await state.set_state(EditStates.editing_text)
    await state.update_data(target=f"auto_review_reply.reply_{stars}", back_to="auto:review")
    await c.answer()


# ─── Автоподнятие ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "auto:lift")
async def auto_lift(c: types.CallbackQuery) -> None:
    enabled = s().auto_lift.enabled
    status = "🟢 ВКЛЮЧЕНО" if enabled else "🔴 ВЫКЛЮЧЕНО"
    text = (
        "📈 <b>Автоподнятие лотов</b>\n\n"
        f"Статус: <b>{status}</b>\n\n"
        "<i>Бот сам поднимает все ваши лоты, когда это возможно. "
        "Если FunPay говорит «нельзя, попробуйте через X часов» — "
        "бот ждёт ровно столько и автоматически поднимает позже.\n\n"
        "После каждого прохода — отчёт в TG: что подняли, "
        "что в кулдауне.</i>"
    )
    await c.message.edit_text(text, reply_markup=kb.kb_auto_lift(s()))
    await c.answer()


@router.callback_query(F.data == "lft:toggle")
async def lft_toggle(c: types.CallbackQuery) -> None:
    s().auto_lift.enabled = not s().auto_lift.enabled
    config_manager.save()
    await auto_lift(c)


# ─── Авто-вывод ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "auto:withdraw")
async def auto_withdraw_menu(c: types.CallbackQuery) -> None:
    w = s().auto_withdraw
    text = (
        "💸 <b>Авто-вывод средств</b>\n\n"
        "Бот периодически проверяет баланс и автоматически выводит средства "
        "по выбранному правилу.\n\n"
    )
    if w.trigger == "schedule":
        sched_label = {"daily": "ежедневно", "weekly": "еженедельно", "monthly": "ежемесячно"}.get(
            w.schedule, w.schedule
        )
        text += f"Сейчас: <b>{sched_label}</b> в {w.schedule_hour}:00, минимум <b>{w.min_amount_rub}₽</b>"
    else:
        text += f"Сейчас: при достижении <b>{w.min_amount_rub}₽</b> на балансе"
    if not w.payment_details:
        text += "\n\n⚠️ <b>Не указаны реквизиты</b> — бот не сможет вывести средства."
    await c.message.edit_text(text, reply_markup=kb.kb_auto_withdraw(s()))
    await c.answer()


@router.callback_query(F.data == "wd:toggle")
async def wd_toggle(c: types.CallbackQuery) -> None:
    s().auto_withdraw.enabled = not s().auto_withdraw.enabled
    config_manager.save()
    await auto_withdraw_menu(c)


@router.callback_query(F.data == "wd:trigger")
async def wd_trigger(c: types.CallbackQuery) -> None:
    cur = s().auto_withdraw.trigger
    s().auto_withdraw.trigger = "amount" if cur == "schedule" else "schedule"
    config_manager.save()
    await auto_withdraw_menu(c)


@router.callback_query(F.data == "wd:schedule")
async def wd_schedule(c: types.CallbackQuery) -> None:
    """Циклическое переключение: daily → weekly → monthly → daily."""
    cur = s().auto_withdraw.schedule
    nxt = {"daily": "weekly", "weekly": "monthly", "monthly": "daily"}.get(cur, "weekly")
    s().auto_withdraw.schedule = nxt
    config_manager.save()
    await auto_withdraw_menu(c)


@router.callback_query(F.data == "wd:amount")
async def wd_amount(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "💵 Введите минимальную сумму вывода в рублях (от 100 до 1 000 000):",
        reply_markup=kb.kb_cancel_edit("auto:withdraw"),
    )
    await state.set_state(EditStates.editing_int)
    await state.update_data(
        target="auto_withdraw.min_amount_rub",
        min_value=100, max_value=1_000_000,
        back_to="auto:withdraw",
    )
    await c.answer()


@router.callback_query(F.data == "wd:method")
async def wd_method(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "💳 Введите способ вывода (например: <code>card</code>, <code>qiwi</code>, "
        "<code>yoomoney</code>, <code>usdt</code>, <code>btc</code>):",
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("auto:withdraw"),
    )
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="auto_withdraw.payment_method", back_to="auto:withdraw")
    await c.answer()


@router.callback_query(F.data == "wd:details")
async def wd_details(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "📝 Введите реквизиты получателя (номер карты / кошелька / адрес):\n\n"
        "<i>⚠️ Эти данные хранятся в config.json — храните файл в безопасности.</i>",
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("auto:withdraw"),
    )
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="auto_withdraw.payment_details", back_to="auto:withdraw")
    await c.answer()


@router.callback_query(F.data == "wd:now")
async def wd_now(c: types.CallbackQuery) -> None:
    from modules.auto_withdraw import withdraw_now
    await c.answer("💸 Запрашиваю вывод...")
    ok, message = await withdraw_now()
    if ok:
        await c.message.answer(f"✅ Вывод выполнен.\n<code>{message[:300]}</code>")
    else:
        await c.message.answer(f"❌ Не удалось вывести: <code>{message[:300]}</code>")


# ─── ИИ ───────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "auto:ai")
async def auto_ai(c: types.CallbackQuery) -> None:
    await c.message.edit_text(
        "🧠 <b>ИИ-помощник</b>\n\n"
        "💰 <b>Где взять API-ключ:</b>\n"
        "• <a href='https://platform.deepseek.com'>DeepSeek</a> — 5М токенов бесплатно ⭐\n"
        "• <a href='https://console.anthropic.com'>Anthropic</a>\n"
        "• <a href='https://platform.openai.com'>OpenAI</a>",
        reply_markup=kb.kb_ai(s()),
        disable_web_page_preview=True,
    )
    await c.answer()


@router.callback_query(F.data == "ai:toggle")
async def ai_toggle(c): s().ai.enabled = not s().ai.enabled; config_manager.save(); await auto_ai(c)


@router.callback_query(F.data == "ai:rewrite")
async def ai_rewrite(c): s().ai.rewrite_drafts = not s().ai.rewrite_drafts; config_manager.save(); await auto_ai(c)


# ─── ИИ → Описания лотов ─────────────────────────────────────────────────────

@router.callback_query(F.data == "ai:descs")
async def ai_descs(c: types.CallbackQuery) -> None:
    cfg = s().lot_desc_generator
    text = (
        "📝 <b>Генератор описаний лотов</b>\n\n"
        "ИИ создаёт 2-3 варианта описания для вашего лота.\n"
        "Использование: <code>/gen_desc &lt;lot_id&gt; [подсказка]</code>\n\n"
        f"<b>Тон:</b> {cfg.tone}\n"
        f"<b>Макс. длина:</b> {cfg.max_length} символов\n"
        f"<b>Эмодзи:</b> {'да' if cfg.include_emoji else 'нет'}"
    )
    await c.message.edit_text(text, reply_markup=kb.kb_lot_desc(s()))
    await c.answer()


@router.callback_query(F.data == "ldg:toggle")
async def ldg_toggle(c): s().lot_desc_generator.enabled = not s().lot_desc_generator.enabled; config_manager.save(); await ai_descs(c)


@router.callback_query(F.data == "ldg:emoji")
async def ldg_emoji(c): s().lot_desc_generator.include_emoji = not s().lot_desc_generator.include_emoji; config_manager.save(); await ai_descs(c)


@router.callback_query(F.data == "ldg:tone")
async def ldg_tone(c: types.CallbackQuery) -> None:
    """Циклическое переключение тона."""
    tones = ["профессиональный", "дружелюбный", "краткий", "продающий"]
    cur = s().lot_desc_generator.tone
    nxt = tones[(tones.index(cur) + 1) % len(tones)] if cur in tones else "профессиональный"
    s().lot_desc_generator.tone = nxt
    config_manager.save()
    await ai_descs(c)


@router.callback_query(F.data == "ldg:length")
async def ldg_length(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "📏 Введите максимальную длину описания в символах (100-2000):",
        reply_markup=kb.kb_cancel_edit("ai:descs"),
    )
    await state.set_state(EditStates.editing_int)
    await state.update_data(
        target="lot_desc_generator.max_length",
        min_value=100, max_value=2000,
        back_to="ai:descs",
    )
    await c.answer()


@router.callback_query(F.data == "ldg:prompt")
async def ldg_prompt(c: types.CallbackQuery, state: FSMContext) -> None:
    cur = s().lot_desc_generator.custom_prompt
    text = (
        f"📋 <b>Кастомный системный промпт</b>\n\n"
        f"Если задан — заменит автоматически генерируемый промпт.\n"
        f"Полезно если хотите конкретный стиль/формат.\n\n"
        f"Текущий: <code>{cur or '(не задан)'}</code>\n\n"
        f"Отправьте новый текст или <code>-</code> чтобы сбросить:"
    )
    await c.message.answer(text, reply_markup=kb.kb_cancel_edit("ai:descs"))
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="lot_desc_generator.custom_prompt", back_to="ai:descs")
    await c.answer()


# ─── ИИ → Авто-снятие демпинга ───────────────────────────────────────────────

@router.callback_query(F.data == "ai:dump")
async def ai_dump(c: types.CallbackQuery) -> None:
    cfg = s().anti_dumping
    text = (
        "⚠️ <b>Авто-снятие демпинга</b>\n\n"
        "Бот проверяет минимальные цены конкурентов в категориях ваших лотов.\n"
        "Если конкурент дешевле более чем на N% — ваш лот деактивируется автоматически, "
        "чтобы не продавать в убыток.\n\n"
        f"<b>Проверка:</b> каждые {cfg.interval_minutes} мин\n"
        f"<b>Порог:</b> {cfg.threshold_percent:.0f}% (если конкурент дешевле — снимаем)\n"
        f"<b>Уведомления:</b> {'да' if cfg.notify else 'нет'}"
    )
    await c.message.edit_text(text, reply_markup=kb.kb_anti_dumping(s()))
    await c.answer()


@router.callback_query(F.data == "adp:toggle")
async def adp_toggle(c): s().anti_dumping.enabled = not s().anti_dumping.enabled; config_manager.save(); await ai_dump(c)


@router.callback_query(F.data == "adp:notify")
async def adp_notify(c): s().anti_dumping.notify = not s().anti_dumping.notify; config_manager.save(); await ai_dump(c)


@router.callback_query(F.data == "adp:interval")
async def adp_interval(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "⏱ Введите интервал проверки в минутах (5-240):",
        reply_markup=kb.kb_cancel_edit("ai:dump"),
    )
    await state.set_state(EditStates.editing_int)
    await state.update_data(
        target="anti_dumping.interval_minutes",
        min_value=5, max_value=240,
        back_to="ai:dump",
    )
    await c.answer()


@router.callback_query(F.data == "adp:threshold")
async def adp_threshold(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "📉 Порог демпинга в процентах (5-50):\n"
        "Например, 15 означает: если конкурент дешевле нас на 15%+, лот снимается.",
        reply_markup=kb.kb_cancel_edit("ai:dump"),
    )
    await state.set_state(EditStates.editing_int)
    await state.update_data(
        target="anti_dumping.threshold_percent",
        min_value=5, max_value=50,
        back_to="ai:dump",
    )
    await c.answer()


@router.callback_query(F.data == "ai:provider")
async def ai_provider(c: types.CallbackQuery) -> None:
    order = ["deepseek", "anthropic", "openai"]
    cur = s().ai.provider
    nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "deepseek"
    s().ai.provider = nxt
    s().ai.model = {"deepseek": "deepseek-v4-flash", "anthropic": "claude-haiku-4-5-20251001", "openai": "gpt-5-nano"}[nxt]
    config_manager.save()
    await auto_ai(c)


@router.callback_query(F.data == "ai:model")
async def ai_model(c: types.CallbackQuery, state: FSMContext) -> None:
    hints = {
        "deepseek": "<code>deepseek-v4-flash</code> или <code>deepseek-v4-pro</code>",
        "anthropic": "<code>claude-haiku-4-5-20251001</code>",
        "openai": "<code>gpt-5-nano</code>",
    }
    await c.message.answer(
        f"📝 Провайдер: <b>{s().ai.provider}</b>\n{hints.get(s().ai.provider, '')}\n\nОтправьте имя модели:",
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("auto:ai")
    )
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="ai.model", back_to="auto:ai")
    await c.answer()


@router.callback_query(F.data == "ai:hours")
async def ai_hours(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer("⏰ Через сколько часов молчания ИИ должен отвечать? (1-72):",
        reply_markup=kb.kb_cancel_edit("auto:ai")
    )
    await state.set_state(EditStates.editing_int)
    await state.update_data(target="ai.auto_reply_after_hours", min_value=1, max_value=72, back_to="auto:ai")
    await c.answer()


@router.callback_query(F.data == "ai:instruction")
async def ai_instruction(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(f"📋 Текущая:\n<code>{s().ai.auto_reply_instruction}</code>\n\n"
        "Новая инструкция для ИИ-ассистента:\n"
        "<i>Переменные ($username и др.) в инструкции не работают — ИИ получает её как системный промпт.</i>",
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("auto:ai")
    )
    await state.set_state(EditStates.editing_ai_instruction)  # инструкция ИИ
    await c.answer()


@router.callback_query(F.data == "ai:key")
async def ai_key(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "🔑 Отправьте API-ключ (сообщение будет удалено после сохранения):",
        reply_markup=kb.kb_cancel_edit("auto:ai")
    )
    await state.set_state(EditStates.editing_ai_key)
    await c.answer()


# ═════════════════════════════════════════════════════════════════════════════
# 3) УПРАВЛЕНИЕ И АНАЛИТИКА
# ═════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu:analytics")
async def menu_analytics(c: types.CallbackQuery) -> None:
    await c.message.edit_text("📊 <b>Управление и Аналитика</b>", reply_markup=kb.kb_analytics())
    await c.answer()


@router.callback_query(F.data == "ana:stats")
async def ana_stats(c: types.CallbackQuery) -> None:
    """Открывает меню выбора периода для аналитики."""
    await c.message.edit_text(
        "📈 <b>Статистика продаж</b>\n\n"
        "Выберите период для просмотра отчётов и графиков:",
        reply_markup=kb.kb_stats_periods(),
    )
    await c.answer()


@router.callback_query(F.data.startswith("ana:p:"))
async def ana_pick_period(c: types.CallbackQuery) -> None:
    """После выбора периода — показываем сводный отчёт + кнопки на детальные."""
    period = c.data.removeprefix("ana:p:")
    try:
        from modules.analytics import full_text_report
        text = full_text_report(period)
    except Exception as e:
        text = f"❌ Ошибка построения отчёта: {e}"
    await c.message.edit_text(text, reply_markup=kb.kb_stats_actions(period))
    await c.answer()


@router.callback_query(F.data.startswith("ana:r:"))
async def ana_text_report(c: types.CallbackQuery) -> None:
    """Текстовый отчёт по конкретной метрике."""
    _, _, kind, period = c.data.split(":", 3)
    try:
        if kind == "conv":
            from modules.analytics import conversion_rate
            r = conversion_rate(period)
            text = (
                f"🎯 <b>Конверсия за {period}</b>\n\n"
                f"Уникальных чатов: <b>{r['chats']}</b>\n"
                f"Купили: <b>{r['buyers']}</b>\n"
                f"Конверсия: <b>{r['rate']:.1f}%</b>"
            )
        elif kind == "avg":
            from modules.analytics import average_check
            r = average_check(period)
            text = (
                f"💵 <b>Средний чек за {period}</b>\n\n"
                f"Средний чек: <b>{r['avg']:.2f}₽</b>\n"
                f"Заказов: <b>{r['count']}</b>\n"
                f"Общая выручка: <b>{r['total']:.2f}₽</b>"
            )
        elif kind == "forecast":
            from modules.analytics import forecast
            r = forecast(period)
            text = (
                f"🔮 <b>Прогноз дохода</b>\n\n"
                f"На основе {r['based_on_days']} дней с продажами:\n"
                f"Средний доход в день: <b>{r['avg_per_day']:.0f}₽</b>\n"
                f"Прогноз на {period}: <b>~{r['forecast']:.0f}₽</b>"
            )
        elif kind == "cmp":
            from modules.analytics import compare_periods
            r = compare_periods(period)
            cur, prev = r["current"], r["previous"]
            sign_t = "📈" if r["diff_total_pct"] >= 0 else "📉"
            sign_c = "📈" if r["diff_count_pct"] >= 0 else "📉"
            text = (
                f"📈 <b>Сравнение периодов ({period})</b>\n\n"
                f"<b>Сейчас:</b> {cur['total']:.0f}₽ ({cur['count']} заказов)\n"
                f"<b>Прошлый:</b> {prev['total']:.0f}₽ ({prev['count']} заказов)\n\n"
                f"{sign_t} Выручка: <b>{r['diff_total_pct']:+.1f}%</b>\n"
                f"{sign_c} Заказы: <b>{r['diff_count_pct']:+.1f}%</b>"
            )
        else:
            text = "Неизвестный отчёт."
    except Exception as e:
        text = f"❌ Ошибка: {e}"
    await c.message.answer(text)
    await c.answer()


@router.callback_query(F.data.startswith("ana:c:"))
async def ana_chart(c: types.CallbackQuery) -> None:
    """Отправка графика как PNG-изображения."""
    _, _, kind, period = c.data.split(":", 3)
    try:
        from modules.analytics import (
            chart_revenue, chart_top_products, chart_peak_hours,
        )
        if kind == "revenue":
            png = chart_revenue(period)
            caption = f"📊 Доходы за {period}"
        elif kind == "top":
            png = chart_top_products(10, period)
            caption = f"🏆 Топ-10 товаров за {period}"
        elif kind == "peak":
            png = chart_peak_hours()
            caption = "⏰ Часы пик продаж"
        else:
            png = None
            caption = ""

        if png is None:
            await c.answer(
                "Нет данных для графика, либо matplotlib не установлен.\n"
                "pip install matplotlib",
                show_alert=True,
            )
            return

        from aiogram.types import BufferedInputFile
        await c.message.answer_photo(
            photo=BufferedInputFile(png, filename="chart.png"),
            caption=caption,
        )
        await c.answer()
    except Exception as e:
        await c.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data == "ana:csv")
async def ana_csv(c: types.CallbackQuery) -> None:
    """Экспорт всех заказов в CSV."""
    try:
        from modules.analytics import export_csv
        path = export_csv()
        from aiogram.types import FSInputFile
        await c.message.answer_document(
            FSInputFile(path),
            caption="📥 Экспорт всех заказов в CSV",
        )
        await c.answer()
    except Exception as e:
        await c.answer(f"Ошибка экспорта: {e}", show_alert=True)


@router.callback_query(F.data == "ana:balance")
async def ana_balance(c: types.CallbackQuery) -> None:
    bal = funpay_client.get_balance()
    text = (
        "💰 <b>Баланс</b>\n\n"
        f"<code>{bal['rub']:.2f} ₽</code>\n"
        f"<code>{bal['usd']:.2f} $</code>\n"
        f"<code>{bal['eur']:.2f} €</code>"
    )
    await c.message.edit_text(text, reply_markup=kb.kb_back("menu:analytics"))
    await c.answer()


@router.callback_query(F.data == "ana:templates")
async def ana_templates(c: types.CallbackQuery) -> None:
    t = s().response_templates.templates
    await c.message.edit_text(
        f"📝 <b>Шаблоны ответов</b>\n\nВсего: <b>{len(t)}</b>\n\n"
        + "\n".join(f"• <code>{k}</code> → {v[:50]}" for k, v in list(t.items())[:20]),
        reply_markup=kb.kb_response_templates(t),
    )
    await c.answer()


@router.callback_query(F.data == "ana:templates_add")
async def ana_templates_add(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer("📝 Название шаблона:",
        reply_markup=kb.kb_cancel_edit("auto:ai")
    )
    await state.set_state(EditStates.editing_template_keyword)
    await state.update_data(__rt_mode__=True)
    await c.answer()


@router.callback_query(F.data == "ana:templates_list")
async def ana_templates_list(c: types.CallbackQuery) -> None:
    t = s().response_templates.templates
    if not t:
        await c.answer("Пусто", show_alert=True); return
    await c.message.edit_text(
        "📝 <b>Шаблоны ответов</b>\n\n" + "\n".join(f"• <code>{k}</code> → {v}" for k, v in t.items()),
        reply_markup=kb.kb_templates_list(t, "rt", "ana:templates"),
    )
    await c.answer()


@router.callback_query(F.data.startswith("rt:del:"))
async def rt_del(c: types.CallbackQuery) -> None:
    kw = c.data.removeprefix("rt:del:")
    s().response_templates.templates.pop(kw, None)
    config_manager.save()
    await c.answer(f"Удалено: {kw}")
    await ana_templates_list(c)


@router.callback_query(F.data == "ana:blacklist")
async def ana_blacklist(c: types.CallbackQuery) -> None:
    await c.message.edit_text(
        f"🚫 <b>Чёрный список</b>\n\nВ списке: <b>{len(s().blacklist.nicknames)}</b>",
        reply_markup=kb.kb_blacklist(s()),
    )
    await c.answer()


@router.callback_query(F.data == "bl:toggle")
async def bl_toggle(c): s().blacklist.enabled = not s().blacklist.enabled; config_manager.save(); await ana_blacklist(c)


@router.callback_query(F.data == "bl:list")
async def bl_list(c: types.CallbackQuery) -> None:
    nicks = s().blacklist.nicknames
    if not nicks:
        await c.answer("Список пуст", show_alert=True); return
    await c.message.edit_text(
        "🚫 <b>Чёрный список</b>\n\n" + "\n".join(f"• <code>{n}</code>" for n in nicks),
        reply_markup=kb.kb_blacklist_list(nicks),
    )
    await c.answer()


@router.callback_query(F.data == "bl:add")
async def bl_add(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer("🚫 Ник для добавления:",
        reply_markup=kb.kb_cancel_edit("auto:ai")
    )
    await state.set_state(EditStates.editing_blacklist_nick)  # ник
    await c.answer()


@router.callback_query(F.data.startswith("bl:rm:"))
async def bl_rm(c: types.CallbackQuery) -> None:
    n = c.data.removeprefix("bl:rm:")
    if n in s().blacklist.nicknames:
        s().blacklist.nicknames.remove(n)
        config_manager.save()
    await c.answer(f"Удалён: {n}")
    await bl_list(c)


@router.callback_query(F.data == "ana:users")
async def ana_users(c: types.CallbackQuery) -> None:
    tg = s().telegram
    await c.message.edit_text(
        f"👥 <b>Пользователи</b>\n\nАдминов: <b>{len(tg.admin_ids)}</b>\nМенеджеров: <b>{len(tg.manager_ids)}</b>\nАктивных ключей: <b>{len(tg.manager_keys)}</b>",
        reply_markup=kb.kb_users(len(tg.admin_ids), len(tg.manager_ids)),
    )
    await c.answer()


@router.callback_query(F.data == "usr:list_adm")
async def usr_list_adm(c: types.CallbackQuery) -> None:
    ids = s().telegram.admin_ids
    await c.message.edit_text(
        "👑 <b>Администраторы</b>\n\n" + ("\n".join(f"• <code>{i}</code>" for i in ids) or "—"),
        reply_markup=kb.kb_back("ana:users"),
    )
    await c.answer()


@router.callback_query(F.data == "usr:list_mgr")
async def usr_list_mgr(c: types.CallbackQuery) -> None:
    ids = s().telegram.manager_ids
    await c.message.edit_text(
        "🛠 <b>Менеджеры</b>\n\n" + ("\n".join(f"• <code>{i}</code>" for i in ids) or "—"),
        reply_markup=kb.kb_back("ana:users"),
    )
    await c.answer()


@router.callback_query(F.data == "usr:new_key")
async def usr_new_key(c: types.CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        await c.answer("⛔ Только для админов.", show_alert=True); return
    key = secrets.token_urlsafe(12)
    s().telegram.manager_keys.append(key)
    config_manager.save()
    await c.message.answer(
        f"🔑 <b>Ключ менеджера создан</b>\n\n<code>{key}</code>\n\n"
        f"Передайте его новому менеджеру — пусть введёт его на /start. "
        f"Ключ одноразовый, после использования удалится."
    )
    await c.answer()


# ═════════════════════════════════════════════════════════════════════════════
# 4) СИСТЕМА И КОНФИГИ
# ═════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu:system")
async def menu_system(c: types.CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        await c.answer("⛔ Только для админов.", show_alert=True); return
    await c.message.edit_text("🔧 <b>Система и Конфиги</b>", reply_markup=kb.kb_system())
    await c.answer()


@router.callback_query(F.data == "sys:notifs")
async def sys_notifs(c: types.CallbackQuery) -> None:
    await c.message.edit_text(
        "🔔 <b>Уведомления</b>\n\nВыберите, о чём вы хотите получать уведомления в Telegram:",
        reply_markup=kb.kb_notifications(s()),
    )
    await c.answer()


@router.callback_query(F.data.startswith("ntf:t:"))
async def ntf_t(c: types.CallbackQuery) -> None:
    field = c.data.removeprefix("ntf:t:")
    setattr(s().notifications, field, not getattr(s().notifications, field))
    config_manager.save()
    await sys_notifs(c)


@router.callback_query(F.data == "sys:notifs_style")
async def sys_notifs_style(c: types.CallbackQuery) -> None:
    await c.message.edit_text("💬 <b>Вид уведомлений</b>", reply_markup=kb.kb_notification_style(s()))
    await c.answer()


@router.callback_query(F.data.startswith("nst:t:"))
async def nst_t(c: types.CallbackQuery) -> None:
    field = c.data.removeprefix("nst:t:")
    setattr(s().notification_style, field, not getattr(s().notification_style, field))
    config_manager.save()
    await sys_notifs_style(c)


@router.callback_query(F.data == "sys:plugins")
async def sys_plugins(c: types.CallbackQuery) -> None:
    """Открывает новый интерфейс плагинов из bot/handlers/plugins.py."""
    from bot.handlers.plugins import _list_text, _build_list_kb, PAGE_SIZE
    from plugins.manager import plugin_manager
    plugins = plugin_manager.list_plugins()
    total_pages = max(1, (len(plugins) + PAGE_SIZE - 1) // PAGE_SIZE)
    if not plugins:
        await c.message.edit_text(
            "🧩 Плагинов пока нет.\n\n"
            f"Положите .py-файлы в <code>{s().plugins.plugins_dir}/</code> "
            "и нажмите «🔄 Перезагрузить».",
            reply_markup=kb.kb_back("menu:system"),
        )
    else:
        await c.message.edit_text(_list_text(), reply_markup=_build_list_kb(plugins, 0, total_pages))
    await c.answer()


# Старые plg:reload / plg:t: обработчики удалены — теперь это всё
# в bot/handlers/plugins.py (plg:open, plg:toggle, plg:pin, plg:reload, plg:page)


@router.callback_query(F.data == "sys:gk")
async def sys_gk(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "🔑 Отправьте новый <b>golden_key</b> (32 символа)\n"
        "После сохранения бот автоматически переподключится к FunPay.\n\n"
        "Как получить: FunPay → F12 → Application → Cookies → <code>golden_key</code>",
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("menu:system")
    )
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="__golden_key__", back_to="menu:system")
    await c.answer()


# ─── User-Agent ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "sys:ua")
async def sys_ua(c: types.CallbackQuery, state: FSMContext) -> None:
    cur = s().funpay.user_agent or "не задан"
    short = cur if len(cur) < 80 else cur[:80] + "..."
    await c.message.answer(
        f"🪪 <b>User-Agent для FunPay</b>\n\n"
        f"⚠️ <i>КРИТИЧЕСКИ ВАЖНО! Без браузерного User-Agent FunPay не отдаёт "
        f"чаты и сообщения.</i>\n\n"
        f"<b>Текущий:</b>\n<code>{short}</code>\n\n"
        f"Чтобы получить ваш РЕАЛЬНЫЙ User-Agent:\n"
        f"1. Откройте <code>https://www.whatsmyua.info/</code>\n"
        f"2. Скопируйте 'Your User Agent String'\n"
        f"3. Отправьте сюда\n\n"
        f"Или отправьте <code>default</code> — поставится стандартный (Chrome 109).",
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("menu:system"),
    )
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="__user_agent__", back_to="menu:system")
    await c.answer()


# ─── Прокси ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "sys:proxy")
async def sys_proxy(c: types.CallbackQuery) -> None:
    proxy = s().funpay.proxy
    short = proxy.split("@")[-1] if proxy else "не задан"
    has_auth = "@" in proxy if proxy else False
    text = (
        "🌐 <b>Прокси</b>\n\n"
        f"Текущий: <code>{short}</code>\n"
        f"Авторизация: {'✅ да' if has_auth else '❌ нет'}\n\n"
        "<i>Один и тот же прокси используется для FunPay и Telegram.\n"
        "После смены прокси нужен перезапуск бота — /restart</i>"
    )
    await c.message.edit_text(text, reply_markup=kb.kb_proxy(s()))
    await c.answer()


@router.callback_query(F.data == "prx:show")
async def prx_show(c: types.CallbackQuery) -> None:
    px = s().funpay.proxy
    await c.message.answer(f"📡 Полная строка: <code>{px or 'не задан'}</code>")
    await c.answer()


@router.callback_query(F.data == "prx:edit")
async def prx_edit(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "✏️ <b>Введите новый прокси</b>\n\n"
        "Поддерживаемые форматы:\n"
        "• <code>login:pass@ip:port</code> — с авторизацией\n"
        "• <code>ip:port</code> — без авторизации\n"
        "• <code>login:pass:ip:port</code> — альтернативный\n\n"
        "<i>После сохранения нужен /restart, чтобы прокси применился.</i>",
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("sys:proxy"),
    )
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="__proxy__", back_to="sys:proxy")
    await c.answer()


@router.callback_query(F.data == "prx:remove")
async def prx_remove(c: types.CallbackQuery) -> None:
    s().funpay.proxy = ""
    config_manager.save()
    await c.answer("🗑 Прокси удалён. Перезапустите бота: /restart", show_alert=True)
    await sys_proxy(c)


@router.callback_query(F.data == "prx:test")
async def prx_test(c: types.CallbackQuery) -> None:
    """Проверяет прокси через requests с тем же форматом что и FunPay-клиент."""
    import asyncio as _aio
    px = s().funpay.proxy
    if not px:
        await c.answer("❌ Прокси не задан", show_alert=True)
        return
    await c.message.answer("🧪 Проверяю прокси...")

    def _check():
        try:
            import requests
            from core.funpay_client import funpay_client
            proxies = funpay_client._build_proxy_dict(px)
            r = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=15)
            if r.status_code == 200:
                ip = r.json().get("origin", "?")
                return True, f"Видимый IP: {ip}"
            return False, f"Код ответа: {r.status_code}"
        except Exception as e:
            return False, str(e)

    loop = _aio.get_event_loop()
    ok, msg = await loop.run_in_executor(None, _check)
    icon = "✅" if ok else "❌"
    await c.message.answer(f"{icon} {msg}")
    await c.answer()


@router.callback_query(F.data == "sys:watermark")
async def sys_watermark(c: types.CallbackQuery) -> None:
    wm = s().watermark
    await c.message.edit_text(
        f"💧 <b>Водяной знак</b>\n\nДобавляется ко всем исходящим сообщениям бота.\n\n"
        f"Текущий текст:\n<code>{wm.text}</code>",
        reply_markup=kb.kb_watermark(s()),
    )
    await c.answer()


@router.callback_query(F.data == "wm:toggle")
async def wm_toggle(c): s().watermark.enabled = not s().watermark.enabled; config_manager.save(); await sys_watermark(c)


@router.callback_query(F.data == "wm:text")
async def wm_text(c: types.CallbackQuery, state: FSMContext) -> None:
    await c.message.answer(
        "✏️ Новый текст водяного знака:\n"
        f"<i>Текущий: <code>{s().watermark.text}</code></i>"
        + _VARS_BASE,
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("sys:watermark")
    )
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="watermark.text", back_to="sys:watermark")
    await c.answer()


@router.callback_query(F.data == "sys:status")
async def sys_status(c: types.CallbackQuery) -> None:
    fp = s().funpay
    bal = funpay_client.get_balance() if fp.golden_key else {"rub": 0, "usd": 0, "eur": 0}
    text = (
        f"📊 <b>Статус</b>\n\n"
        f"Версия: <code>{s().version}</code>\n"
        f"Аккаунт: <b>{fp.username or '—'}</b> (ID: <code>{fp.account_id or '—'}</code>)\n"
        f"Баланс: <code>{bal['rub']:.2f}₽ / {bal['usd']:.2f}$ / {bal['eur']:.2f}€</code>\n\n"
        f"<b>Модули:</b>\n"
        f"{'🟢' if s().auto_lift.enabled else '🔴'} Автоподнятие\n"
        f"{'🟢' if s().auto_response.enabled else '🔴'} Автоответчик\n"
        f"{'🟢' if s().auto_delivery.enabled else '🔴'} Автовыдача (лотов: {len(s().auto_delivery.lots)})\n"
        f"{'🟢' if s().auto_greeting.enabled else '🔴'} Приветствия\n"
        f"{'🟢' if s().order_confirm_reply.enabled else '🔴'} Ответ на подтверждение\n"
        f"{'🟢' if s().auto_review_reply.enabled else '🔴'} Ответ на отзывы\n"
        f"{'🟢' if s().ai.enabled else '🔴'} ИИ-помощник ({s().ai.provider})\n"
        f"{'🟢' if s().watermark.enabled else '🔴'} Водяной знак"
    )
    await c.message.edit_text(text, reply_markup=kb.kb_back("menu:system"))
    await c.answer()


@router.callback_query(F.data == "sys:updates")
async def sys_updates(c: types.CallbackQuery) -> None:
    await c.answer(f"Текущая версия: {s().version}\nПроверка обновлений будет в следующих релизах.", show_alert=True)


# ═════════════════════════════════════════════════════════════════════════════
# FSM-обработчики ввода
# ═════════════════════════════════════════════════════════════════════════════

@router.message(EditStates.editing_text)
async def edit_text(msg: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    target = data.get("target")
    text = msg.text or ""

    # Спецтаргеты для автовыдачи и системы
    if target == "__dlv_lot_id__":
        lot_id = text.strip()
        if not lot_id:
            await msg.answer("❌ Пусто. Введите ID лота (число из URL) или его название."); return
        hint = ""
        # Если ввели число и есть подключение — сразу распознаём название лота,
        # чтобы продавец видел, что сопоставление при оплате сработает.
        if lot_id.isdigit():
            from modules.auto_delivery import resolve_match_text
            title = resolve_match_text(lot_id)
            if title:
                hint = f"\n🔎 Распознан лот: «<i>{title}</i>»"
            else:
                hint = (
                    "\n⚠️ Не удалось получить название лота с FunPay "
                    "(бот не подключён или лот не ваш). Если автовыдача не сработает — "
                    "добавьте лот заново, указав <b>название</b> лота вместо ID."
                )
        await msg.answer(
            f"📦 Лот <b>{lot_id}</b>: какой тип контента?{hint}",
            reply_markup=kb.kb_delivery_type_choice(),
        )
        await state.update_data(__dlv_set_lot_id__=lot_id)
        return

    if target and target.startswith("__dlv_static__:"):
        lot_id = target.removeprefix("__dlv_static__:")
        from modules.auto_delivery import add_lot_static
        add_lot_static(lot_id, text)
        await msg.answer(f"✅ Лот <b>{lot_id}</b>: статичный текст сохранён. /menu для возврата.")
        await state.clear(); return

    if target and target.startswith("__dlv_file__:"):
        lot_id = target.removeprefix("__dlv_file__:")
        from modules.auto_delivery import add_lot_file
        lines = [ln for ln in text.splitlines() if ln.strip()]
        add_lot_file(lot_id, lines)
        await msg.answer(f"✅ Лот <b>{lot_id}</b>: сохранено {len(lines)} ключей. /menu для возврата.")
        await state.clear(); return

    if target and target.startswith("__dlv_csv__:"):
        # Шаг 1: получили CSV-данные. Шаг 2: попросим шаблон форматирования.
        lot_id = target.removeprefix("__dlv_csv__:")
        await state.update_data(__dlv_csv_data__=text, __dlv_csv_lot_id__=lot_id)
        await msg.answer(
            f"📊 CSV сохранён.\n\n"
            f"Теперь отправьте <b>шаблон выдачи</b> — как форматировать одну строку.\n\n"
            f"Используйте <code>{{имя_колонки}}</code> или <code>{{0}}</code>, <code>{{1}}</code>...\n\n"
            f"Пример: <code>Логин: {{login}}\nПароль: {{password}}\nЗаметка: {{note}}</code>\n\n"
            f"Или отправьте <code>-</code> чтобы использовать дефолтный формат «Колонка: значение»."
        )
        await state.update_data(target=f"__dlv_csv_tpl__:{lot_id}")
        return

    if target and target.startswith("__dlv_csv_tpl__:"):
        lot_id = target.removeprefix("__dlv_csv_tpl__:")
        data = await state.get_data()
        csv_text = data.get("__dlv_csv_data__", "")
        template = "" if text.strip() == "-" else text
        from modules.auto_delivery import add_lot_csv
        count = add_lot_csv(lot_id, csv_text, template=template)
        await msg.answer(f"✅ Лот <b>{lot_id}</b>: CSV сохранён ({count} строк данных). /menu")
        await state.clear(); return

    if target and target.startswith("__dlv_random__:"):
        lot_id = target.removeprefix("__dlv_random__:")
        from modules.auto_delivery import add_lot_random
        options = [ln for ln in text.splitlines() if ln.strip()]
        if not options:
            await msg.answer("❌ Нужно хотя бы одно значение."); return
        add_lot_random(lot_id, options)
        await msg.answer(f"✅ Лот <b>{lot_id}</b>: сохранено {len(options)} случайных вариантов. /menu")
        await state.clear(); return

    if target and target.startswith("__dlv_combined_instr__:"):
        # Шаг 1: получили инструкцию. Шаг 2: попросим ключи.
        lot_id = target.removeprefix("__dlv_combined_instr__:")
        await state.update_data(__dlv_combined_instr_text__=text, __dlv_combined_lot_id__=lot_id)
        await msg.answer(
            f"📦 Инструкция сохранена.\n\n"
            f"Теперь отправьте <b>список уникальных ключей</b>: одна строка = один ключ. "
            f"Бот будет выдавать инструкцию + следующий ключ из списка."
        )
        await state.update_data(target=f"__dlv_combined_keys__:{lot_id}")
        return

    if target and target.startswith("__dlv_combined_keys__:"):
        lot_id = target.removeprefix("__dlv_combined_keys__:")
        data = await state.get_data()
        instr = data.get("__dlv_combined_instr_text__", "")
        keys = [ln for ln in text.splitlines() if ln.strip()]
        if not keys:
            await msg.answer("❌ Нужен хотя бы один ключ."); return
        from modules.auto_delivery import add_lot_combined
        add_lot_combined(lot_id, instr, keys)
        await msg.answer(f"✅ Лот <b>{lot_id}</b>: инструкция + {len(keys)} ключей сохранены. /menu")
        await state.clear(); return

    if target == "__golden_key__":
        from core.proxy_check import validate_golden_key
        ok, err = validate_golden_key(text)
        if not ok:
            await msg.answer(f"❌ {err}"); return
        s().funpay.golden_key = text.strip()
        config_manager.save()
        # Переподключаемся
        import asyncio as _aio
        loop = _aio.get_event_loop()
        success, m = await loop.run_in_executor(None, funpay_client.connect)
        await msg.answer(f"{'✅' if success else '❌'} {m}\n/menu")
        await state.clear(); return

    if target == "__proxy__":
        # Принимаем любой формат: ip:port / login:pass@ip:port / login:pass:ip:port
        proxy = text.strip()
        if proxy.lower() in ("-", "нет", "none", "off"):
            proxy = ""
        else:
            # Минимальная валидация: должно быть ip:port
            import re
            if not re.search(r"\d+\.\d+\.\d+\.\d+:\d+|@[^:]+:\d+", proxy):
                await msg.answer(
                    "❌ Неверный формат прокси.\n"
                    "Пример: <code>login:pass@1.2.3.4:8080</code> или <code>1.2.3.4:8080</code>"
                ); return
        s().funpay.proxy = proxy
        config_manager.save()
        if proxy:
            await msg.answer(
                f"✅ Прокси сохранён: <code>{proxy.split('@')[-1]}</code>\n\n"
                f"⚠️ Перезапустите бота, чтобы прокси применился к Telegram-боту: /restart"
            )
        else:
            await msg.answer("🗑 Прокси удалён. Перезапустите: /restart")
        await state.clear(); return

    if target == "__user_agent__":
        DEFAULT_UA = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
        )
        ua = text.strip()
        if ua.lower() in ("default", "по умолчанию", "-"):
            ua = DEFAULT_UA
        if "python-requests" in ua:
            await msg.answer(
                "❌ Этот User-Agent (python-requests) — именно тот что не работает!\n"
                "Введите браузерный User-Agent или <code>default</code>."
            ); return
        if len(ua) < 30:
            await msg.answer(
                "❌ User-Agent слишком короткий. Получите его на whatsmyua.info"
            ); return
        s().funpay.user_agent = ua
        config_manager.save()
        await msg.answer(
            f"✅ User-Agent сохранён.\n"
            f"⚠️ Чтобы применилось к FunPay — перезапустите бота: /restart"
        )
        await state.clear(); return

    # Обычный таргет — точечная установка поля по dotted-path
    if not target:
        await state.clear(); return
    _set_nested(target, text)
    await msg.answer("✅ Сохранено. /menu")
    await state.clear()


@router.message(EditStates.editing_int)
async def edit_int(msg: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    target = data.get("target")
    mn, mx = data.get("min_value", 0), data.get("max_value", 10**9)
    try:
        v = int((msg.text or "").strip())
        assert mn <= v <= mx
    except Exception:
        await msg.answer(f"❌ Введите число от {mn} до {mx}:"); return
    _set_nested(target, v)
    await msg.answer("✅ Сохранено. /menu")
    await state.clear()


@router.message(EditStates.editing_template_keyword)
async def edit_tpl_kw(msg: types.Message, state: FSMContext) -> None:
    kw = (msg.text or "").strip()
    if len(kw) < 2:
        await msg.answer("❌ Минимум 2 символа."); return
    await state.update_data(template_keyword=kw)
    await state.set_state(EditStates.editing_template_response)
    await msg.answer(
        f"📝 Триггер: <code>{kw}</code>\nТеперь отправьте текст ответа:"
        + _VARS_RESPONSE,
        parse_mode="HTML",
        reply_markup=kb.kb_cancel_edit("auto:response")
    )


@router.message(EditStates.editing_template_response)
async def edit_tpl_resp(msg: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    kw = data.get("template_keyword")
    if not kw:
        await state.clear(); return
    if data.get("__rt_mode__"):
        s().response_templates.templates[kw] = msg.text or ""
    else:
        s().auto_response.templates[kw.lower()] = msg.text or ""
    config_manager.save()
    await msg.answer(f"✅ Шаблон сохранён: <code>{kw}</code>. /menu")
    await state.clear()


@router.message(EditStates.editing_blacklist_nick)
async def edit_bl(msg: types.Message, state: FSMContext) -> None:
    nick = (msg.text or "").strip()
    if not nick:
        await msg.answer("❌ Пусто."); return
    if nick not in s().blacklist.nicknames:
        s().blacklist.nicknames.append(nick)
        config_manager.save()
    await msg.answer(f"✅ Добавлено: <code>{nick}</code>. /menu")
    await state.clear()


@router.message(EditStates.editing_ai_instruction)
async def edit_ai_instr(msg: types.Message, state: FSMContext) -> None:
    s().ai.auto_reply_instruction = msg.text or ""
    config_manager.save()
    await msg.answer("✅ Инструкция обновлена. /menu")
    await state.clear()


@router.message(EditStates.editing_ai_key)
async def edit_ai_key(msg: types.Message, state: FSMContext) -> None:
    s().ai.api_key = (msg.text or "").strip()
    config_manager.save()
    try:
        await msg.delete()
    except Exception:
        pass
    await msg.answer("✅ Ключ сохранён (исходное сообщение удалено). /menu")
    await state.clear()
