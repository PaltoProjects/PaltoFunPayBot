"""
Пример плагина PaltoFunPayBot — демонстрация всех возможностей расширенного API.

Скопируйте файл, переименуйте, поменяйте PLUGIN_INFO['id'] и логику.

Включить: /plugins → выберите плагин → 🚀 Включить.
Когда плагин ВЫКЛЮЧЕН — его команды и обработчики НЕ работают.

Что доступно через api:
    api.event_bus       — события (alias для core.event_bus)
    api.funpay_client   — клиент FunPay
    api.config_manager  — глобальный конфиг
    api.logger          — логгер
    api.storage         — личное хранилище плагина (JSON, key-value)
    api.secrets         — шифрованное хранилище (для паролей и API-ключей)
    api.scheduler       — планировщик (every / daily / once_at)
    api.http            — HTTP-клиент (для внешних API)
    api.lots            — утилиты лотов: get_lot, update_lot, list_my_lots
    api.orders          — утилиты заказов: refund
    api.analytics       — SELECT-запросы к analytics.db
    api.data_dir        — личная папка данных плагина

Декораторы:
    @api.command("name")     — команда /name
    @api.callback("prefix")  — callback-кнопка plg_<plugin_id>:<prefix>:<data>
    @api.on("event_name")    — подписка на событие event_bus
    @api.tag_handler("tag")  — реакция на оплату лота с тегом в описании

Методы:
    api.notify_admins(text)        — отправить TG всем админам
    api.send_to_chat(chat_id, text) — отправить в чат FunPay
    api.audit(action, **kwargs)    — запись в audit-лог
    api.tg_bot()                   — получить aiogram Bot

Доступные события (см. core/event_bus.py → класс Event):
    new_message, new_order, order_paid, order_confirmed, order_refunded,
    new_review, lot_lifted, delivery_sent, delivery_out_of_stock,
    bot_started, bot_stopped, initial_chat, order_status_changed
"""

PLUGIN_INFO = {
    "id":          "example_plugin",
    "name":        "Example Plugin",
    "version":     "1.0.0",
    "author":      "you",
    "description": "Демонстрация всех возможностей plugin-API.",
    "command":     "/example",
    "lot_tag":     "demo:tag",      # лоты с этим тегом в описании будут обрабатываться
    "emoji":       "🧩",
    "available":   True,
}


def setup(api) -> None:
    api.logger.info(f"[{PLUGIN_INFO['name']}] загружен")

    # ─── Хранилище ──────────────────────────────────────────────────────────

    counter = api.storage.get("counter", 0)
    api.logger.info(f"[example] загружен, счётчик: {counter}")

    # ─── Секретное хранилище ────────────────────────────────────────────────

    # api.secrets.set("steam_password", "secret123")
    # password = api.secrets.get("steam_password")

    # ─── Подписка на события ────────────────────────────────────────────────

    @api.on("new_order")
    async def on_new_order(order):
        api.storage.set("counter", api.storage.get("counter", 0) + 1)
        api.logger.info(
            f"[example] заказ #{getattr(order, 'id', '?')}, всего обработано: {api.storage.get('counter')}"
        )

    # ─── Команда /example ───────────────────────────────────────────────────

    @api.command("example")
    async def cmd_example(message):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🧮 Показать счётчик",
                                 callback_data=f"plg_{PLUGIN_INFO['id']}:show:counter"),
            InlineKeyboardButton(text="🔄 Сбросить",
                                 callback_data=f"plg_{PLUGIN_INFO['id']}:reset:counter"),
        ]])
        await message.answer(
            "🧩 <b>Example Plugin</b>\n\n"
            "Демонстрация callback-кнопок плагина.",
            reply_markup=kb,
        )

    # ─── Callback-кнопки ────────────────────────────────────────────────────

    @api.callback("show")
    async def cb_show(c, data):
        # data — то что после prefix: например "counter"
        if data == "counter":
            n = api.storage.get("counter", 0)
            await c.answer(f"Счётчик: {n}", show_alert=True)

    @api.callback("reset")
    async def cb_reset(c, data):
        if data == "counter":
            api.storage.set("counter", 0)
            await c.answer("✅ Сброшено", show_alert=True)
            api.audit("RESET", counter=0)

    # ─── Tag-handler — реакция на оплату лота с конкретным тегом ────────────

    @api.tag_handler("demo:tag")
    async def handle_tagged_purchase(order, lot_description):
        chat_id = getattr(order, "chat_id", None)
        if not chat_id:
            return
        api.send_to_chat(chat_id, "🎁 Спасибо за покупку лота с тегом demo:tag!")
        await api.notify_admins(
            f"🧩 Example Plugin сработал на заказ #{getattr(order, 'id', '?')}"
        )

    # ─── Планировщик ────────────────────────────────────────────────────────

    @api.scheduler.every(hours=24)
    async def daily_report():
        n = api.storage.get("counter", 0)
        await api.notify_admins(f"📊 Example Plugin: за всё время обработано заказов: {n}")

    # ─── HTTP-запрос (раскомментировать для теста) ──────────────────────────

    # @api.scheduler.every(minutes=10)
    # async def fetch_data():
    #     resp = await api.http.get("https://api.example.com/data")
    #     if resp["status"] == 200:
    #         api.storage.set("last_data", resp["json"])

    # ─── Аналитика — пример запроса к БД ────────────────────────────────────

    # rows = api.analytics.query("SELECT COUNT(*) FROM orders WHERE status = 'paid'")
    # api.logger.info(f"Оплаченных заказов: {rows[0][0]}")


def teardown(api) -> None:
    api.logger.info(f"[{PLUGIN_INFO['name']}] выгружен (счётчик={api.storage.get('counter', 0)})")
