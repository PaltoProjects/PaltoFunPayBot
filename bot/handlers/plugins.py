"""
Меню плагинов — формат FunPayBot.com.

Команда /plugins
   ├─ Список с пагинацией (по 13 плагинов на страницу)
   ├─ Закреплённые наверху с эмодзи 📌
   └─ Тап → детальное меню плагина:
         • Имя + статус (Доступен/Заблокирован) + ВКЛ/ВЫКЛ
         • Закреплён/Не закреплён
         • Описание + команда
         • Кнопки: Включить/Выключить · Закрепить/Открепить · Назад

Также — диспатчер команд плагинов:
если юзер пишет /activity, /gpt_panel и т.д., и плагин ВКЛЮЧЁН — отрабатывает.
Если плагин ВЫКЛЮЧЕН — отвечаем "Плагин отключён".
"""
from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot.handlers.auth import is_authorized
from plugins.manager import plugin_manager
from utils.logger import logger

router = Router(name="plugins")

# Сколько плагинов на странице (как в FunPayBot.com)
PAGE_SIZE = 13


# ──────────────────────────────────────────────────────────────────────────────
# Клавиатуры
# ──────────────────────────────────────────────────────────────────────────────

def _build_list_kb(plugins: list, page: int, total_pages: int):
    """Список плагинов с пагинацией. По 13 на страницу."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    chunk = plugins[start:end]

    for p in chunk:
        # Цветной индикатор включён ли плагин
        if not p["available"]:
            indicator = "⚫"
        else:
            indicator = "🟢" if p["enabled"] else "🔴"
        pin_mark = "📌 " if p["pinned"] else ""
        label = f"{indicator} {pin_mark}{p['name']}"
        b.button(text=label, callback_data=f"plg:open:{p['id']}")

    # Все плагины — в одну колонку
    b.adjust(1)

    # Пагинация: ⏪ ◀️ N/M ▶️ ⏩
    pag_row = []
    if total_pages > 1:
        # Стрелка влево
        if page > 0:
            pag_row.append(("◀️", f"plg:page:{page - 1}"))
        # Счётчик страниц
        pag_row.append((f"{page + 1}/{total_pages}", "plg:noop"))
        # Стрелка вправо
        if page < total_pages - 1:
            pag_row.append(("▶️", f"plg:page:{page + 1}"))

    # Добавляем пагинацию как отдельный ряд
    if pag_row:
        for text, cb in pag_row:
            b.button(text=text, callback_data=cb)
        # Эта строка должна быть в один ряд → вызываем adjust ещё раз с параметрами
        # b.adjust получает аргументы для каждого ряда отдельно
        rows = [1] * len(chunk) + [len(pag_row), 1]
        b.adjust(*rows)

    # Кнопка Назад
    b.button(text="⬅️ Назад", callback_data="plg:back_main")
    if not pag_row:
        b.adjust(*([1] * len(chunk) + [1]))

    return b.as_markup()


def _build_detail_kb(plugin_id: str, enabled: bool, pinned: bool):
    """Детальное меню плагина."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()

    # Тумблер ВКЛ/ВЫКЛ
    if enabled:
        b.button(text="🔴 Выключить", callback_data=f"plg:toggle:{plugin_id}")
    else:
        b.button(text="🚀 Включить", callback_data=f"plg:toggle:{plugin_id}")

    # Закрепить / Открепить
    if pinned:
        b.button(text="📌 Открепить", callback_data=f"plg:pin:{plugin_id}")
    else:
        b.button(text="📌 Закрепить вверху", callback_data=f"plg:pin:{plugin_id}")

    b.button(text="⬅️ Назад", callback_data="plg:list")
    b.adjust(1)
    return b.as_markup()


# ──────────────────────────────────────────────────────────────────────────────
# Текст для меню списка
# ──────────────────────────────────────────────────────────────────────────────

def _list_text() -> str:
    return (
        "🧩 <b>Ваши функции</b>\n\n"
        "Здесь отображаются встроенные модули и плагины.\n"
        "🟢 — активные функции, 🔴 — выключенные, ⚫ — недоступные.\n"
        "📌 — закреплённые плагины."
    )


def _detail_text(p: dict) -> str:
    if not p["available"]:
        status = "⚫ <b>ЗАБЛОКИРОВАН</b>"
    elif p["enabled"]:
        status = "🟢 <b>АКТИВЕН</b>"
    else:
        status = "🔴 <b>ОТКЛЮЧЕН</b>"
    pin_status = "📌 Закреплён" if p["pinned"] else "📍 Не закреплён"

    text = (
        f"{p['emoji']} <b>{p['name']}</b>\n"
        f"Статус: Доступно | {status}\n"
        f"{pin_status}\n\n"
        f"📝 {p['description']}"
    )
    if p.get("lot_tag"):
        text += f"\nТег в лоте: <code>{p['lot_tag']}</code>"
    if p.get("command"):
        text += f"\nКоманда: /{p['command']}"
    return text


# ──────────────────────────────────────────────────────────────────────────────
# Команда /plugins (показать список)
# ──────────────────────────────────────────────────────────────────────────────

@router.message(Command("plugins"))
async def cmd_plugins(msg: types.Message, state: FSMContext) -> None:
    if not is_authorized(msg.from_user.id):
        await msg.answer("⛔ Доступ запрещён.")
        return
    plugins = plugin_manager.list_plugins()
    total_pages = max(1, (len(plugins) + PAGE_SIZE - 1) // PAGE_SIZE)
    if not plugins:
        await msg.answer(
            "🧩 Плагинов пока нет.\n\n"
            "Положите .py-файлы в папку <code>plugins_user/</code> и используйте "
            "🔧 Система → 🧩 Плагины → Перезагрузить."
        )
        return
    await msg.answer(_list_text(), reply_markup=_build_list_kb(plugins, 0, total_pages))


@router.callback_query(F.data == "plg:list")
async def cb_back_to_list(c: types.CallbackQuery) -> None:
    plugins = plugin_manager.list_plugins()
    total_pages = max(1, (len(plugins) + PAGE_SIZE - 1) // PAGE_SIZE)
    await c.message.edit_text(_list_text(), reply_markup=_build_list_kb(plugins, 0, total_pages))
    await c.answer()


@router.callback_query(F.data.startswith("plg:page:"))
async def cb_page(c: types.CallbackQuery) -> None:
    try:
        page = int(c.data.removeprefix("plg:page:"))
    except ValueError:
        await c.answer(); return
    plugins = plugin_manager.list_plugins()
    total_pages = max(1, (len(plugins) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    try:
        await c.message.edit_text(_list_text(), reply_markup=_build_list_kb(plugins, page, total_pages))
    except Exception:
        pass  # если содержимое не поменялось
    await c.answer()


@router.callback_query(F.data == "plg:noop")
async def cb_noop(c: types.CallbackQuery) -> None:
    await c.answer()


@router.callback_query(F.data == "plg:back_main")
async def cb_back_main(c: types.CallbackQuery) -> None:
    # Возвращаем в главное меню — то же что menu:main
    from bot.handlers.menu import back_main
    await back_main(c)


# ──────────────────────────────────────────────────────────────────────────────
# Открытие плагина / тумблер / закрепление
# ──────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("plg:open:"))
async def cb_open(c: types.CallbackQuery) -> None:
    pid = c.data.removeprefix("plg:open:")
    p = plugin_manager.get(pid)
    if not p:
        await c.answer("Плагин не найден", show_alert=True); return
    await c.message.edit_text(
        _detail_text(p),
        reply_markup=_build_detail_kb(pid, p["enabled"], p["pinned"]),
    )
    await c.answer()


@router.callback_query(F.data.startswith("plg:toggle:"))
async def cb_toggle(c: types.CallbackQuery) -> None:
    pid = c.data.removeprefix("plg:toggle:")
    p = plugin_manager.get(pid)
    if not p:
        await c.answer("Плагин не найден", show_alert=True); return
    if not p["available"]:
        await c.answer("Плагин недоступен.", show_alert=True); return
    plugin_manager.toggle(pid)
    new_p = plugin_manager.get(pid)
    state = "включён ✅" if new_p["enabled"] else "выключен ❌"
    logger.info(f"Плагин {pid} {state} (юзер {c.from_user.id})")
    await c.answer(f"Плагин {state}")
    # Обновляем детальное меню
    await c.message.edit_text(
        _detail_text(new_p),
        reply_markup=_build_detail_kb(pid, new_p["enabled"], new_p["pinned"]),
    )


@router.callback_query(F.data.startswith("plg:pin:"))
async def cb_pin(c: types.CallbackQuery) -> None:
    pid = c.data.removeprefix("plg:pin:")
    p = plugin_manager.get(pid)
    if not p:
        await c.answer("Плагин не найден", show_alert=True); return
    plugin_manager.toggle_pin(pid)
    new_p = plugin_manager.get(pid)
    state = "закреплён 📌" if new_p["pinned"] else "откреплён"
    await c.answer(f"Плагин {state}")
    await c.message.edit_text(
        _detail_text(new_p),
        reply_markup=_build_detail_kb(pid, new_p["enabled"], new_p["pinned"]),
    )


@router.callback_query(F.data == "plg:reload")
async def cb_reload(c: types.CallbackQuery) -> None:
    n = plugin_manager.reload_all()
    await c.answer(f"🔄 Перезагружено плагинов: {n}", show_alert=True)
    plugins = plugin_manager.list_plugins()
    total_pages = max(1, (len(plugins) + PAGE_SIZE - 1) // PAGE_SIZE)
    try:
        await c.message.edit_text(_list_text(), reply_markup=_build_list_kb(plugins, 0, total_pages))
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Диспатчер команд плагинов
# ──────────────────────────────────────────────────────────────────────────────
# Перехватывает любые команды кроме встроенных. Если команда зарегистрирована
# плагином и плагин ВКЛЮЧЁН — выполняет её. Иначе — сообщает что плагин выключен.

# Список встроенных команд бота — их не перехватываем
BUILTIN_COMMANDS = {
    "start", "menu", "profile", "balance", "restart", "golden_key",
    "ban", "unban", "black_list", "upload_chat_img", "upload_offer_img",
    "test_lot", "logs", "about", "sys", "del_logs", "power_off",
    "watermark", "check_updates", "plugins", "test_msg", "login",
    "2fa_setup", "2fa_off", "audit", "export_settings", "gen_desc",
}


@router.message(F.text.startswith("/"))
async def plugin_command_dispatcher(msg: types.Message, state: FSMContext) -> None:
    """Перехватывает команды и направляет их в плагин (если он включён)."""
    if not is_authorized(msg.from_user.id):
        return

    cmd = (msg.text or "").split()[0].lstrip("/").split("@")[0].lower()
    if cmd in BUILTIN_COMMANDS:
        return  # это встроенная команда — её обработают другие хендлеры

    # Активный обработчик плагина
    handler = plugin_manager.get_command(cmd)
    if handler:
        try:
            await handler(msg)
        except Exception as e:
            logger.exception(f"Plugin command /{cmd} failed: {e}")
            await msg.answer(f"❌ Ошибка плагина: {e}")
        return

    # Команду объявил какой-то плагин — но он выключен?
    plugin = plugin_manager.find_plugin_by_command(cmd)
    if plugin:
        if not plugin.available:
            await msg.answer(f"⚫ Плагин <b>{plugin.name}</b> недоступен.")
        else:
            await msg.answer(
                f"🔴 Плагин <b>{plugin.name}</b> отключён.\n"
                f"Включите его в /plugins → выберите плагин → 🚀 Включить."
            )


# ─── Диспатчер callback-кнопок плагинов ──────────────────────────────────────

@router.callback_query(F.data.startswith("plg_"))
async def plugin_callback_dispatcher(c: types.CallbackQuery) -> None:
    """
    Диспатчит callback-кнопки плагинов.

    Формат: plg_<plugin_id>:<prefix>:<data>
    Например: plg_my_steam:rent:30  → handler 'rent' плагина 'my_steam' с data="30"
    """
    if not is_authorized(c.from_user.id):
        await c.answer("⛔ Только для авторизованных", show_alert=True)
        return

    result = plugin_manager.get_callback_handler(c.data or "")
    if not result:
        # Это не плагин — возможно встроенный обработчик уже обработал
        return
    handler, data = result
    try:
        await handler(c, data)
    except Exception as e:
        logger.exception(f"Plugin callback {c.data} failed: {e}")
        await c.answer(f"❌ Ошибка: {e}", show_alert=True)
