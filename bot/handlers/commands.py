"""
Все слэш-команды из FunPayBot.com (скрины 6-11):

/menu, /profile, /balance, /restart, /golden_key, /ban, /unban, /black_list,
/upload_chat_img, /upload_offer_img, /test_lot, /logs, /about, /sys, /del_logs,
/power_off, /watermark, /check_updates, /plugins
"""
from __future__ import annotations

import asyncio
import os
import platform
import sys
from datetime import datetime
from pathlib import Path

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

from bot.handlers.auth import is_admin, is_authorized
from config.settings import config_manager
from core.funpay_client import funpay_client
from utils.logger import logger

router = Router(name="commands")

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "logs"


def _ensure(msg: types.Message) -> bool:
    if not is_authorized(msg.from_user.id):
        return False
    return True


# ─── /profile ────────────────────────────────────────────────────────────────

@router.message(Command("profile"))
async def cmd_profile(msg: types.Message) -> None:
    if not _ensure(msg):
        return
    fp = config_manager.settings.funpay
    if not funpay_client.account:
        await msg.answer("❌ Бот не подключён к FunPay.")
        return
    bal = funpay_client.get_balance()
    active = funpay_client.get_active_orders_count()
    text = (
        f"👤 <b>Профиль FunPay</b>\n\n"
        f"Аккаунт: <b>{fp.username}</b>\n"
        f"ID: <code>{fp.account_id}</code>\n"
        f"Ссылка: https://funpay.com/users/{fp.account_id}/\n\n"
        f"Баланс: <code>{bal['rub']:.2f}₽ / {bal['usd']:.2f}$ / {bal['eur']:.2f}€</code>\n"
        f"Активных заказов: <b>{active}</b>"
    )
    await msg.answer(text, disable_web_page_preview=True)


# ─── /balance ────────────────────────────────────────────────────────────────

@router.message(Command("balance"))
async def cmd_balance(msg: types.Message) -> None:
    if not _ensure(msg):
        return
    bal = funpay_client.get_balance()
    await msg.answer(
        f"💰 <b>Баланс</b>\n\n"
        f"<code>{bal['rub']:.2f} ₽</code>\n"
        f"<code>{bal['usd']:.2f} $</code>\n"
        f"<code>{bal['eur']:.2f} €</code>"
    )


# ─── /restart ────────────────────────────────────────────────────────────────

@router.message(Command("restart"))
async def cmd_restart(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только для админов."); return
    await msg.answer("🔄 Перезапускаю бота...")
    logger.info(f"Перезапуск инициирован админом {msg.from_user.id}")
    # Корректный рестарт через replace процесса
    os.execv(sys.executable, [sys.executable] + sys.argv)


# ─── /golden_key ─────────────────────────────────────────────────────────────

@router.message(Command("golden_key"))
async def cmd_golden_key(msg: types.Message, state: FSMContext) -> None:
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только для админов."); return
    from bot.states import EditStates
    await msg.answer("🔑 Отправьте новый <b>golden_key</b> (32 символа):")
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="__golden_key__", back_to="menu:system")


# ─── /ban /unban /black_list ─────────────────────────────────────────────────

@router.message(Command("ban"))
async def cmd_ban(msg: types.Message) -> None:
    if not _ensure(msg):
        return
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("Использование: <code>/ban &lt;ник&gt;</code>"); return
    nick = parts[1].strip()
    if nick not in config_manager.settings.blacklist.nicknames:
        config_manager.settings.blacklist.nicknames.append(nick)
        config_manager.save()
    await msg.answer(f"🚫 Добавлен в чёрный список: <code>{nick}</code>")


@router.message(Command("unban"))
async def cmd_unban(msg: types.Message) -> None:
    if not _ensure(msg):
        return
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("Использование: <code>/unban &lt;ник&gt;</code>"); return
    nick = parts[1].strip()
    bl = config_manager.settings.blacklist.nicknames
    if nick in bl:
        bl.remove(nick)
        config_manager.save()
        await msg.answer(f"✅ Удалён из чёрного списка: <code>{nick}</code>")
    else:
        await msg.answer(f"Ник <code>{nick}</code> не найден в списке.")


@router.message(Command("black_list"))
async def cmd_black_list(msg: types.Message) -> None:
    if not _ensure(msg):
        return
    nicks = config_manager.settings.blacklist.nicknames
    if not nicks:
        await msg.answer("🚫 Чёрный список пуст."); return
    await msg.answer(
        "🚫 <b>Чёрный список</b>\n\n" + "\n".join(f"• <code>{n}</code>" for n in nicks)
    )


# ─── /upload_chat_img /upload_offer_img ──────────────────────────────────────

@router.message(Command("upload_chat_img"))
async def cmd_upload_chat_img(msg: types.Message, state: FSMContext) -> None:
    if not _ensure(msg):
        return
    await msg.answer(
        "📷 Прикрепите изображение следующим сообщением.\n"
        "Бот загрузит его на FunPay и пришлёт image_id, который можно вставлять в чат."
    )
    from bot.states import EditStates
    await state.set_state(EditStates.editing_text)
    await state.update_data(__upload_mode__="chat")


@router.message(Command("upload_offer_img"))
async def cmd_upload_offer_img(msg: types.Message, state: FSMContext) -> None:
    if not _ensure(msg):
        return
    await msg.answer(
        "📷 Прикрепите изображение следующим сообщением.\n"
        "Бот загрузит его на FunPay для использования в лотах."
    )
    from bot.states import EditStates
    await state.set_state(EditStates.editing_text)
    await state.update_data(__upload_mode__="offer")


@router.message(F.photo)
async def handle_photo_upload(msg: types.Message, state: FSMContext) -> None:
    """Если ждём фото для загрузки — обрабатываем, иначе игнорим."""
    if not _ensure(msg):
        return
    data = await state.get_data()
    mode = data.get("__upload_mode__")
    if not mode:
        return
    if not funpay_client.account:
        await msg.answer("❌ Бот не подключён к FunPay."); await state.clear(); return

    # Скачиваем фото
    largest = msg.photo[-1]
    tmp_path = Path("/tmp") / f"upload_{largest.file_id}.jpg"
    try:
        await msg.bot.download(largest, destination=tmp_path)
    except Exception as e:
        await msg.answer(f"❌ Не удалось скачать: {e}"); await state.clear(); return

    try:
        loop = asyncio.get_event_loop()
        if mode == "chat":
            image_id = await loop.run_in_executor(
                None, funpay_client.account.upload_image, str(tmp_path)
            )
        else:
            image_id = await loop.run_in_executor(
                None,
                lambda: funpay_client.account.upload_image(str(tmp_path), type="offer"),
            )
        await msg.answer(f"✅ Загружено!\nimage_id: <code>{image_id}</code>")
    except Exception as e:
        await msg.answer(f"❌ Ошибка загрузки: {e}")
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        await state.clear()


# ─── /test_lot ───────────────────────────────────────────────────────────────

@router.message(Command("test_lot"))
async def cmd_test_lot(msg: types.Message) -> None:
    if not _ensure(msg):
        return
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await msg.answer("Использование: <code>/test_lot &lt;ID лота&gt;</code>"); return
    lot_id = parts[1].strip()
    from modules.auto_delivery import test_delivery
    result = await test_delivery(lot_id)
    await msg.answer(f"🧪 <b>Тест лота {lot_id}</b>\n\n{result}")


# ─── /logs /del_logs ─────────────────────────────────────────────────────────

@router.message(Command("logs"))
async def cmd_logs(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только для админов."); return
    log_path = LOG_DIR / "bot.log"
    if not log_path.exists():
        await msg.answer("❌ Файл логов не найден."); return
    try:
        await msg.answer_document(FSInputFile(log_path), caption="📄 Текущий лог-файл")
    except Exception as e:
        await msg.answer(f"❌ Не удалось отправить: {e}")


@router.message(Command("del_logs"))
async def cmd_del_logs(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только для админов."); return
    if not LOG_DIR.exists():
        await msg.answer("❌ Папка логов не найдена."); return
    n = 0
    for f in LOG_DIR.glob("bot.log.*"):  # удаляем только архивные
        try:
            f.unlink()
            n += 1
        except Exception:
            pass
    await msg.answer(f"🗑 Удалено старых лог-файлов: <b>{n}</b>")


# ─── /about ──────────────────────────────────────────────────────────────────

@router.message(Command("about"))
async def cmd_about(msg: types.Message) -> None:
    if not _ensure(msg):
        return
    text = (
        f"🤖 <b>PaltoFunPayBot v{config_manager.settings.version}</b>\n\n"
        f"📦 Автовыдача товаров\n"
        f"📈 Автоподнятие лотов\n"
        f"💬 Умный автоответчик\n"
        f"🟢 Автовосстановление лотов\n"
        f"🔴 Автодеактивация (если товар закончился)\n"
        f"🔵 Постоянный онлайн\n"
        f"📩 Уведомления в Telegram\n"
        f"⚙️ Полный контроль через Telegram\n"
        f"🧩 Поддержка плагинов\n"
        f"🧠 ИИ-помощник (DeepSeek / Anthropic / OpenAI)"
    )
    await msg.answer(text)


@router.callback_query(F.data == "info:about")
async def cb_info_about(c: types.CallbackQuery) -> None:
    """Кнопка «🤖 PaltoFunPayBot» из приветствия неавторизованного юзера."""
    text = (
        f"🤖 <b>PaltoFunPayBot v{config_manager.settings.version}</b>\n\n"
        f"📦 Автовыдача товаров\n"
        f"📈 Автоподнятие лотов\n"
        f"💬 Умный автоответчик\n"
        f"🟢 Автовосстановление лотов\n"
        f"🔴 Автодеактивация (если товар закончился)\n"
        f"🔵 Постоянный онлайн\n"
        f"📩 Уведомления в Telegram\n"
        f"⚙️ Полный контроль через Telegram\n"
        f"🧩 Поддержка плагинов\n"
        f"🧠 ИИ-помощник (DeepSeek / Anthropic / OpenAI)"
    )
    await c.message.answer(text)
    await c.answer()


# ─── /sys ────────────────────────────────────────────────────────────────────

@router.message(Command("sys"))
async def cmd_sys(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только для админов."); return

    # Системная информация
    try:
        import psutil  # опциональная зависимость
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        ram_used = mem.used / 1024**3
        ram_total = mem.total / 1024**3
        ram_line = f"RAM: <code>{ram_used:.1f} / {ram_total:.1f} ГБ ({mem.percent}%)</code>\n"
        cpu_line = f"CPU: <code>{cpu}%</code>\n"
    except ImportError:
        ram_line = "RAM: <i>(установите psutil)</i>\n"
        cpu_line = "CPU: <i>(установите psutil)</i>\n"

    # Размер логов
    log_size = 0
    if LOG_DIR.exists():
        log_size = sum(f.stat().st_size for f in LOG_DIR.glob("**/*") if f.is_file())

    # Аптайм бота
    started = config_manager.settings.funpay.account_id
    text = (
        f"🖥 <b>Системная информация</b>\n\n"
        f"OS: <code>{platform.system()} {platform.release()}</code>\n"
        f"Python: <code>{platform.python_version()}</code>\n"
        f"{cpu_line}{ram_line}"
        f"Логи: <code>{log_size / 1024:.1f} КБ</code>\n"
        f"Время сервера: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"
        f"FunPay подключён: {'✅' if funpay_client.account else '❌'}"
    )
    await msg.answer(text)


# ─── /power_off ──────────────────────────────────────────────────────────────

@router.message(Command("power_off"))
async def cmd_power_off(msg: types.Message) -> None:
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только для админов."); return
    await msg.answer("🛑 Бот выключается. Чтобы запустить снова — выполните <code>python main.py</code> на сервере.")
    logger.warning(f"Бот выключен админом {msg.from_user.id}")
    funpay_client.stop()
    # graceful exit
    asyncio.get_event_loop().call_later(1, lambda: os._exit(0))


# ─── /watermark ──────────────────────────────────────────────────────────────

@router.message(Command("watermark"))
async def cmd_watermark(msg: types.Message, state: FSMContext) -> None:
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только для админов."); return
    cur = config_manager.settings.watermark.text
    await msg.answer(
        f"💧 <b>Водяной знак</b>\n\nТекущий:\n<code>{cur}</code>\n\nОтправьте новый текст:"
    )
    from bot.states import EditStates
    await state.set_state(EditStates.editing_text)
    await state.update_data(target="watermark.text", back_to="menu:system")


# ─── /check_updates ──────────────────────────────────────────────────────────

@router.message(Command("check_updates"))
async def cmd_check_updates(msg: types.Message) -> None:
    if not _ensure(msg):
        return
    await msg.answer(
        f"🔄 Текущая версия: <b>{config_manager.settings.version}</b>\n\n"
        f"Проверка обновлений с GitHub будет в следующих релизах."
    )


# ─── /plugins ────────────────────────────────────────────────────────────────
# (перенесён в bot/handlers/plugins.py — там полноценное меню как в FunPayBot.com)


# ─── /test_msg — тестовое уведомление ────────────────────────────────────────

@router.message(Command("test_msg"))
async def cmd_test_msg(msg: types.Message) -> None:
    """Имитирует входящее сообщение в FunPay-чате — для проверки внешнего вида."""
    if not _ensure(msg):
        return

    # Создаём фейковое сообщение
    # chat_id = -1 — сентинел для тестовых сообщений. funpay_client.send_message
    # проверяет cid <= 0 и тихо пропускает — реальный запрос к FunPay не уходит.
    class FakeMessage:
        author = "TestBuyer"
        author_id = 999999
        chat_id = -1
        chat_name = "TestBuyer"
        text = (
            "Привет! Это тестовое уведомление.\n"
            "Хотел бы заказать ваш товар — расскажите подробнее, пожалуйста?"
        )

    from core.event_bus import Event, event_bus
    await event_bus.emit(Event.NEW_MESSAGE, FakeMessage())
    await msg.answer(
        "✅ Тестовое уведомление отправлено.\n\n"
        "Если ничего не пришло — проверьте, что включены уведомления "
        "о сообщениях в /menu → 🔧 Система → 🔔 Уведомления."
    )


# ─── /gen_desc — генерация описания лота через ИИ ────────────────────────────

@router.message(Command("gen_desc"))
async def cmd_gen_desc(msg: types.Message) -> None:
    """Использование: /gen_desc <lot_id> [подсказка для ИИ]"""
    if not _ensure(msg):
        return

    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await msg.answer(
            "Использование: <code>/gen_desc &lt;lot_id&gt; [подсказка]</code>\n\n"
            "Пример: <code>/gen_desc 12345</code>\n"
            "С подсказкой: <code>/gen_desc 12345 сделай акцент на скорости выдачи</code>"
        )
        return

    from config.settings import config_manager
    if not config_manager.settings.lot_desc_generator.enabled:
        await msg.answer(
            "🔴 Генератор описаний выключен.\n"
            "Включите его: /menu → 🤖 Автоматизация → 🧠 ИИ-помощник → 📝 Описания лотов"
        )
        return
    if not config_manager.settings.ai.api_key:
        await msg.answer(
            "❌ Не задан API-ключ ИИ.\n"
            "Настройте: /menu → 🤖 Автоматизация → 🧠 ИИ-помощник → 🔑 Ключ"
        )
        return

    lot_id = int(parts[1])
    hint = parts[2] if len(parts) > 2 else ""

    status_msg = await msg.answer("⏳ Генерирую описания...")

    from modules.lot_descriptions import generate_descriptions
    variants = await generate_descriptions(lot_id, hint)

    if not variants:
        await status_msg.edit_text(
            "❌ Не удалось сгенерировать описания.\n"
            "Возможные причины: лот не найден, ИИ-API вернул ошибку, ключ недействителен."
        )
        return

    text = f"✨ <b>Сгенерированные варианты для лота {lot_id}:</b>\n\n"
    for i, v in enumerate(variants, 1):
        text += f"<b>━━━ Вариант {i} ━━━</b>\n{v}\n\n"
    text += "<i>Выберите понравившийся и вставьте в редактор лота на FunPay.</i>"

    # Telegram режет длинные сообщения на 4096 — обрезаем если надо
    if len(text) > 4000:
        text = text[:4000] + "\n\n<i>... (обрезано)</i>"
    await status_msg.edit_text(text)
