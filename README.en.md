<div align="center">

# 🤖 PaltoFunPayBot

**Full-featured Telegram bot for FunPay sales automation**

[Русский](README.md) | **English**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://python.org)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-2ca5e0?logo=telegram&logoColor=white)](https://aiogram.dev)
[![License](https://img.shields.io/badge/license-Source%20Available-red)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.1-orange)](https://github.com/PaltoProjects/PaltoFunPayBot)

Manage your FunPay store right from Telegram — auto-bump, auto-delivery, AI replies, analytics and much more.

</div>

---

## ✨ Features

<table>
<tr>
<td width="50%">

### ⚙️ Core modules
- 📈 **Auto-bump** — Cardinal-style pattern (exact cooldown straight from FunPay)
- 💬 **Auto-responder** — keywords with regex word boundaries (no false triggers)
- 📦 **Auto-delivery** — static text / key file / CSV / random / instruction+key
- 🔢 **Multi-delivery** — N keys per single order
- 🟢 **Auto-restore** — lot re-enables once keys are back in stock
- 🔴 **Auto-deactivation** — lot disables when keys run out
- 🌐 **Online Keeper** — keeps your account online

</td>
<td width="50%">

### 🤖 Automation
- 👋 **Greetings** — for new buyers, with variables and cooldown
- 👍 **Reply on confirmation** + review request after N minutes
- ⭐ **Review replies** — individual text for 1–5 stars (respects FunPay limits)
- 🧠 **AI assistant** — DeepSeek / Anthropic / OpenAI (auto-reply after N hours of silence)
- 💸 **Auto-withdrawal** — on schedule or when a threshold amount is reached
- ⚠️ **Anti-dumping** — deactivates a lot if a competitor undercuts your price

</td>
</tr>
<tr>
<td width="50%">

### 📊 Analytics
- 📈 Sales statistics (day / week / month / year)
- 📉 Conversion rate, average order value, revenue forecast
- 📊 PNG charts right in Telegram
- 📥 Order export to CSV
- 💰 Current balance (₽ / $ / €)

</td>
<td width="50%">

### 🔧 System
- 🔔 Fine-grained notification settings per event type
- 🧩 **Plugin system** — your own business logic without touching the core
- 💧 Watermark on outgoing messages
- 🌐 Proxy support (with testing right from the menu)
- 🔑 Change golden_key on the fly
- 👥 Roles: Admin / Manager (one-time keys)
- 🔐 2FA (TOTP)
- 📨 Quick reply button right under the notification

</td>
</tr>
</table>

---

## 🚀 Quick start

### Requirements
- **Python 3.10+** (3.13 recommended)
- A Telegram bot → [@BotFather](https://t.me/BotFather)
- A FunPay account + `golden_key`
- VPN/proxy (if you run it in Russia — Telegram is blocked there)

### Installation

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

### First run

The bot will ask in the console for:
1. Your Telegram bot **token** (from BotFather)
2. An **admin password** (make one up or press Enter to generate a random one)

Everything else is done via Telegram:
1. Open the bot → `/start`
2. Enter the password
3. Send your `golden_key` (32 characters from funpay.com cookies)
4. Done — `/menu` to configure

### Where to get `golden_key`
1. Open [funpay.com](https://funpay.com) in your browser
2. `F12` → **Application** tab → **Cookies** → `https://funpay.com`
3. Find the `golden_key` row → copy the value (32 characters)

> ⚠️ Use **the same IP** in your browser and for the bot — otherwise FunPay will notice the IP change.

---

## 🖥 VPS installation (Ubuntu / Debian)

```bash
sudo apt install -y python3 python3-venv python3-pip git
git clone https://github.com/PaltoProjects/PaltoFunPayBot.git /opt/funpaybot
cd /opt/funpaybot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python main.py    # run once for the setup wizard, then Ctrl+C
```

#### Autostart via systemd

Create `/etc/systemd/system/funpaybot.service`:

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
journalctl -u funpaybot -f   # follow the logs
```

> On a VPS with a non-Russian IP no VPN is needed.

---

## ⌨️ Slash commands

| Command | Description |
|---|---|
| `/menu` | Main bot menu |
| `/profile` | FunPay account statistics |
| `/balance` | Current balance |
| `/restart` | Restart the bot |
| `/golden_key` | Change golden_key |
| `/ban <nickname>` | Add to blacklist |
| `/unban <nickname>` | Remove from blacklist |
| `/black_list` | Show the whole blacklist |
| `/upload_chat_img` | Upload an image for chats |
| `/upload_offer_img` | Upload an image for a lot |
| `/test_lot <id>` | Test auto-delivery for a lot |
| `/gen_desc <id>` | AI-generated lot description |
| `/logs` | Download the log file |
| `/del_logs` | Delete old logs |
| `/sys` | System info (CPU / RAM / uptime) |
| `/about` | About the bot and version |
| `/watermark` | Change the watermark |
| `/check_updates` | Check for updates (without installing) |
| `/update` | Update the bot from GitHub (changed files only) |
| `/plugins` | Plugin catalog |
| `/power_off` | Stop the bot |

---

## 📦 Auto-delivery

1. `/menu` → 🤖 Automation → 📦 Auto-delivery
2. Turn the toggle on → **➕ Add lot**
3. Enter the lot ID (the number from the URL: `funpay.com/lots/offer?id=XXXXXXXX`)
4. Pick a content type:

| Type | When to use |
|---|---|
| 📝 Static text | Same text for everyone (instructions, payment details) |
| 📁 Key file | Unique key per buyer — lines are consumed one by one |
| 📊 CSV table | Several data columns (login+password etc.) |
| 🎲 Random | Sends a random option from a list (not consumed) |
| 📦 Instruction + key | Shared instruction + a unique key |

**Test:** `/test_lot <ID>` — shows what the buyer will receive.

---

## 🧠 AI assistant

Default — **DeepSeek** (5M free tokens, no card required):

1. Sign up at [platform.deepseek.com](https://platform.deepseek.com)
2. API Keys → Create → copy
3. `/menu` → 🤖 Automation → 🧠 AI assistant → 🔑 Key
4. Turn the toggle on — the bot replies to a buyer if you stayed silent longer than N hours

Also supported: **Anthropic Claude** and **OpenAI** (switch with the 🤖 Provider button).

---

## 🧩 Plugin system

```python
# plugins_user/my_plugin.py
PLUGIN_INFO = {
    "id":          "my_plugin",
    "name":        "My plugin",
    "version":     "1.0.0",
    "author":      "you",
    "description": "What it does",
}

def setup(api):
    @api.event_bus.on("new_order")
    async def on_order(order):
        api.logger.info(f"New order: {order.id}")
        # api.funpay_client.send_message(chat_id, "text")
        # api.config_manager.settings...

def teardown(api):
    pass
```

Enable: `/menu` → 🔧 System → 🧩 Plugins → toggle.

**Available events:** `new_message`, `new_order`, `order_paid`, `order_confirmed`, `order_refunded`, `new_review`, `lot_lifted`, `delivery_sent`, `delivery_out_of_stock`, `bot_started`, `bot_stopped`, `initial_chat`, `order_status_changed`.

---

## 🔤 Variables in texts

Available in all templates:

| Variable | Value |
|---|---|
| `$username` | Buyer's nickname |
| `$chat_name` | Chat name |
| `$chat_id` | Chat ID |
| `$date` | Current date |
| `$time` | Current time |
| `$order_id` | Order ID |
| `$order_price` | Order price |
| `$stars` | Review star count |
| `$review_text` | Review text |
| `$message_text` | Incoming message text |
| `$sleep:N` | Pause N seconds before sending |
| `$photo:url` | Send a photo from a URL |

---

## 📁 Project structure

```
funpaybot/
├── main.py                        # entry point
├── requirements.txt
├── start.bat / check.bat          # quick start on Windows
├── config/
│   └── settings.py                # all Pydantic settings models
├── core/
│   ├── event_bus.py               # async event bus
│   ├── funpay_client.py           # wrapper around FunPayAPI
│   └── proxy_check.py             # golden_key / proxy validation
├── bot/
│   ├── keyboards.py               # all inline keyboards
│   ├── states.py                  # FSM states
│   ├── middleware.py
│   └── handlers/
│       ├── auth.py                # /start, password, 2FA
│       ├── setup.py               # first-run wizard
│       ├── commands.py            # 20 slash commands
│       ├── menu.py                # the whole menu (4 sections)
│       ├── quick_reply.py         # quick reply under a notification
│       ├── plugins.py             # plugin UI
│       └── twofa.py               # TOTP
├── modules/
│   ├── online_keeper.py
│   ├── auto_lift.py               # auto-bump with exact FunPay cooldown
│   ├── auto_greeting.py
│   ├── auto_response.py           # regex word-boundary matching
│   ├── auto_delivery.py           # 5 content types
│   ├── auto_deactivation.py
│   ├── auto_review_reply.py       # FunPay limits: 999 chars / 10 lines
│   ├── auto_withdraw.py           # withdrawal: schedule / amount trigger
│   ├── anti_dumping.py            # auto-deactivation on price dumping
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
├── FunPayAPI/                     # bundled fork with fixes
│   ├── account.py
│   ├── types.py
│   ├── common/
│   └── updater/
├── utils/
│   ├── logger.py
│   ├── variables.py               # $variable substitution
│   ├── passwords.py               # bcrypt hashing of the admin password
│   ├── audit_log.py
│   ├── error_reporter.py
│   ├── totp.py
│   └── proxy_keepalive.py
└── data/                          # created automatically
    ├── config.json                # settings (gitignored)
    ├── analytics.db
    ├── delivery_files/            # keys for auto-delivery
    └── logs/
```

---

## ⚠️ Important

- All secrets (`golden_key`, API keys, bot token) are stored **locally** in `data/config.json` — nothing is sent anywhere
- The admin password is stored as a **bcrypt hash**, not plaintext
- `data/config.json` is in `.gitignore` — it will not end up in git
- Anti-ban delays are enabled by default (random 0.8–2.2 s between requests)
- Do not change your IP while the bot is running — FunPay tracks IP changes

---

## 🤝 Contributing

Pull Requests and Issues are welcome. Before submitting a PR make sure that:
- The code matches the project style (async/await, Pydantic v2, loguru)
- There are no secrets in the code
- `data/config.json` is not included in the commit

---

<div align="center">

Made with ❤️ for FunPay sellers

</div>
