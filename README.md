<div align="center">

# 🤖 PaltoFunPayBot

**Полнофункциональный Telegram-бот для автоматизации продаж на FunPay**

**Русский** | [English](README.en.md)

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://python.org)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-2ca5e0?logo=telegram&logoColor=white)](https://aiogram.dev)
[![License](https://img.shields.io/badge/license-Source%20Available-red)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.1-orange)](https://github.com/PaltoProjects/PaltoFunPayBot)
[![CI](https://github.com/PaltoProjects/PaltoFunPayBot/actions/workflows/ci.yml/badge.svg)](https://github.com/PaltoProjects/PaltoFunPayBot/actions/workflows/ci.yml)
[![FunPay parser](https://github.com/PaltoProjects/PaltoFunPayBot/actions/workflows/canary.yml/badge.svg)](https://github.com/PaltoProjects/PaltoFunPayBot/actions/workflows/canary.yml)

Управляй своим магазином на FunPay прямо из Telegram — автоподнятие, автовыдача, ИИ-ответы, аналитика и многое другое.

</div>

---

## ✨ Возможности

<table>
<tr>
<td width="50%">

### ⚙️ Основные модули
- 📈 **Автоподнятие** — по Cardinal-паттерну (точный кулдаун прямо от FunPay)
- 💬 **Автоответчик** — ключевые слова с regex-границами (нет ложных срабатываний)
- 📦 **Автовыдача** — статичный текст / файл с ключами / CSV / случайный / инструкция+ключ
- 🔢 **Мульти-выдача** — N ключей за один заказ
- 🟢 **Автовосстановление** — лот включается когда ключи снова появились
- 🔴 **Автодеактивация** — лот выключается когда ключи закончились
- 🌐 **Online Keeper** — держит аккаунт онлайн

</td>
<td width="50%">

### 🤖 Автоматизация
- 👋 **Приветствия** — новых покупателей с переменными и кулдауном
- 👍 **Ответ на подтверждение** + просьба об отзыве через N минут
- ⭐ **Ответ на отзывы** — индивидуальный текст для 1–5 звёзд (с лимитами FunPay)
- 🧠 **ИИ-ассистент** — DeepSeek / Anthropic / OpenAI (авто-ответ после N часов молчания)
- 💸 **Авто-вывод** — по расписанию или при достижении суммы
- ⚠️ **Авто-снятие демпинга** — деактивирует лот если конкурент сбил цену

</td>
</tr>
<tr>
<td width="50%">

### 📊 Аналитика
- 📈 Статистика продаж (день / неделя / месяц / год)
- 📉 Конверсия, средний чек, прогноз дохода
- 📊 PNG-графики прямо в Telegram
- 📥 Экспорт заказов в CSV
- 💰 Текущий баланс (₽ / $ / €)

</td>
<td width="50%">

### 🔧 Система
- 🔔 Тонкая настройка уведомлений по типам событий
- 🧩 **Система плагинов** — своя бизнес-логика без правки кода
- 💧 Водяной знак на исходящие сообщения
- 🌐 Прокси (с тестированием прямо из меню)
- 🔑 Смена golden_key онлайн
- 👥 Роли: Admin / Manager (одноразовые ключи)
- 🔐 2FA (TOTP)
- 📨 Быстрый ответ кнопкой прямо под уведомлением

</td>
</tr>
</table>

---

## 🚀 Быстрый старт

### Требования
- **Python 3.10+** (рекомендуется 3.13)
- Telegram-бот → [@BotFather](https://t.me/BotFather)
- Аккаунт FunPay + `golden_key`
- VPN/прокси (если запускаешь в России — Telegram заблокирован)

### Установка

```bash
git clone https://github.com/PaltoProjects/PaltoFunPayBot.git
cd PaltoFunPayBot

python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
python main.py
```

### Первый запуск

Бот запросит в консоли:
1. **Токен** Telegram-бота (от BotFather)
2. **Пароль администратора** (придумай любой или нажми Enter — сгенерируется автоматически)

Дальше всё через Telegram:
1. Открой бота → `/start`
2. Введи пароль
3. Отправь `golden_key` (32 символа из cookies funpay.com)
4. Готово — `/menu` для настройки

### Где взять `golden_key`
1. Зайди на [funpay.com](https://funpay.com) в браузере
2. `F12` → вкладка **Application** → **Cookies** → `https://funpay.com`
3. Найди строку `golden_key` → скопируй значение (32 символа)

> ⚠️ Используй **один и тот же IP** в браузере и для бота — иначе FunPay увидит смену IP.

---

## 🖥 Установка на VPS (Ubuntu / Debian)

```bash
sudo apt install -y python3 python3-venv python3-pip git
git clone https://github.com/PaltoProjects/PaltoFunPayBot.git /opt/funpaybot
cd /opt/funpaybot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python main.py    # один раз для мастера настройки, затем Ctrl+C
```

#### Автозапуск через systemd

Создай `/etc/systemd/system/funpaybot.service`:

```ini
[Unit]
Description=PaltoFunPayBot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/funpaybot
ExecStart=/opt/funpaybot/venv/bin/python /opt/funpaybot/main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now funpaybot
sudo systemctl status funpaybot
journalctl -u funpaybot -f   # следить за логами
```

> На VPS с нероссийским IP — VPN не нужен.

---

## ⌨️ Слэш-команды

| Команда | Описание |
|---|---|
| `/menu` | Главное меню бота |
| `/profile` | Статистика аккаунта FunPay |
| `/balance` | Текущий баланс |
| `/restart` | Перезапуск бота |
| `/golden_key` | Смена golden_key |
| `/ban <ник>` | Добавить в чёрный список |
| `/unban <ник>` | Убрать из ЧС |
| `/black_list` | Показать весь ЧС |
| `/upload_chat_img` | Загрузить картинку для чата |
| `/upload_offer_img` | Загрузить картинку для лота |
| `/test_lot <id>` | Тест автовыдачи для лота |
| `/gen_desc <id>` | ИИ-генерация описания лота |
| `/logs` | Скачать лог-файл |
| `/del_logs` | Удалить старые логи |
| `/sys` | Системная инфа (CPU / RAM / uptime) |
| `/about` | О боте и версии |
| `/watermark` | Сменить водяной знак |
| `/check_updates` | Проверить обновления (без установки) |
| `/update` | Обновить бота с GitHub (только изменённые файлы) |
| `/plugins` | Каталог плагинов |
| `/power_off` | Остановить бота |

---

## 📦 Автовыдача

1. `/menu` → 🤖 Автоматизация → 📦 Автовыдача
2. Включи тумблер → **➕ Добавить лот**
3. Введи ID лота (число из URL: `funpay.com/lots/offer?id=XXXXXXXX`)
4. Выбери тип контента:

| Тип | Когда использовать |
|---|---|
| 📝 Статичный текст | Одинаковый текст каждому (инструкция, реквизиты) |
| 📁 Файл с ключами | Уникальный ключ каждому — строки расходуются по очереди |
| 📊 CSV-таблица | Несколько колонок данных (логин+пароль и т.д.) |
| 🎲 Случайный | Выдаёт случайный вариант из списка (не расходуется) |
| 📦 Инструкция + ключ | Общая инструкция + уникальный ключ |

**Тест:** `/test_lot <ID>` — покажет что получит покупатель.

---

## 🧠 ИИ-ассистент

По умолчанию — **DeepSeek** (5 млн токенов бесплатно без карты):

1. Регистрация на [platform.deepseek.com](https://platform.deepseek.com)
2. API Keys → Create → скопировать
3. `/menu` → 🤖 Автоматизация → 🧠 ИИ-помощник → 🔑 Ключ
4. Включить тумблером — бот ответит покупателю если ты молчал дольше N часов

Также поддерживаются: **Anthropic Claude** и **OpenAI** (переключаются кнопкой 🤖 Провайдер).

---

## 🧩 Система плагинов

```python
# plugins_user/my_plugin.py
PLUGIN_INFO = {
    "id":          "my_plugin",
    "name":        "Мой плагин",
    "version":     "1.0.0",
    "author":      "you",
    "description": "Что делает",
}

def setup(api):
    @api.event_bus.on("new_order")
    async def on_order(order):
        api.logger.info(f"Новый заказ: {order.id}")
        # api.funpay_client.send_message(chat_id, "text")
        # api.config_manager.settings...

def teardown(api):
    pass
```

Включить: `/menu` → 🔧 Система → 🧩 Плагины → тумблер.

**Доступные события:** `new_message`, `new_order`, `order_paid`, `order_confirmed`, `order_refunded`, `new_review`, `lot_lifted`, `delivery_sent`, `delivery_out_of_stock`, `bot_started`, `bot_stopped`, `initial_chat`, `order_status_changed`.

---

## 🔤 Переменные в текстах

Доступны во всех шаблонах:

| Переменная | Значение |
|---|---|
| `$username` | Ник покупателя |
| `$chat_name` | Имя чата |
| `$chat_id` | ID чата |
| `$date` | Текущая дата |
| `$time` | Текущее время |
| `$order_id` | ID заказа |
| `$order_price` | Цена заказа |
| `$stars` | Кол-во звёзд отзыва |
| `$review_text` | Текст отзыва |
| `$message_text` | Текст входящего сообщения |
| `$sleep:N` | Пауза N секунд перед отправкой |
| `$photo:url` | Отправить фото из URL |

---

## 📁 Структура проекта

```
funpaybot/
├── main.py                        # точка входа
├── requirements.txt
├── start.bat / check.bat          # быстрый запуск на Windows
├── config/
│   └── settings.py                # все Pydantic-модели настроек
├── core/
│   ├── event_bus.py               # async event bus
│   ├── funpay_client.py           # обёртка над FunPayAPI
│   └── proxy_check.py             # валидация golden_key / прокси
├── bot/
│   ├── keyboards.py               # все inline-клавиатуры
│   ├── states.py                  # FSM-состояния
│   ├── middleware.py
│   └── handlers/
│       ├── auth.py                # /start, пароль, 2FA
│       ├── setup.py               # мастер первого запуска
│       ├── commands.py            # 20 слэш-команд
│       ├── menu.py                # всё меню (4 раздела)
│       ├── quick_reply.py         # быстрый ответ под уведомлением
│       ├── plugins.py             # UI плагинов
│       └── twofa.py               # TOTP
├── modules/
│   ├── online_keeper.py
│   ├── auto_lift.py               # автоподнятие с точным кулдауном FunPay
│   ├── auto_greeting.py
│   ├── auto_response.py           # regex word-boundary matching
│   ├── auto_delivery.py           # 5 типов контента
│   ├── auto_deactivation.py
│   ├── auto_review_reply.py       # FunPay limits 999 chars / 10 lines
│   ├── auto_withdraw.py           # вывод: schedule / amount trigger
│   ├── anti_dumping.py            # авто-деактивация при демпинге
│   ├── ai_assistant.py            # DeepSeek / Claude / OpenAI
│   ├── order_reactions.py
│   ├── lot_descriptions.py
│   ├── analytics.py
│   └── notifications.py
├── plugins/
│   ├── manager.py
│   └── api.py
├── plugins_user/
│   └── example_plugin.py
├── FunPayAPI/                     # встроенный форк с исправлениями
│   ├── account.py
│   ├── types.py
│   ├── common/
│   └── updater/
├── utils/
│   ├── logger.py
│   ├── variables.py               # подстановка $переменных
│   ├── passwords.py               # bcrypt-хэширование пароля админа
│   ├── audit_log.py
│   ├── error_reporter.py
│   ├── totp.py
│   └── proxy_keepalive.py
└── data/                          # создаётся автоматически
    ├── config.json                # настройки (в .gitignore)
    ├── analytics.db
    ├── delivery_files/            # ключи для автовыдачи
    └── logs/
```

---

## ⚠️ Важно

- Все секреты (`golden_key`, API-ключи, токен бота) хранятся **локально** в `data/config.json` — никуда не отправляются
- Пароль администратора хранится в виде **bcrypt-хэша**, а не плейнтекста
- `data/config.json` добавлен в `.gitignore` — не попадёт в git
- Антибан-задержки включены по умолчанию (случайные 0.8–2.2 сек между запросами)
- Не меняй IP во время работы бота — FunPay следит за сменой

---

## 🤝 Вклад в проект

Pull Requests и Issues приветствуются. Перед отправкой PR убедись что:
- Код соответствует стилю проекта (async/await, Pydantic v2, loguru)
- Нет секретов в коде
- `data/config.json` не включён в коммит

---

<div align="center">

Сделано с ❤️ для FunPay-продавцов

</div>
