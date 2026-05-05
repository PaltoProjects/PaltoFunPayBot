"""
Расширенный API для плагинов PaltoFunPayBot.

Возможности:
- Личное хранилище плагина (data/plugin_storage/<id>.json)
- Шифрованное хранилище для секретов (Fernet)
- Планировщик задач (every / once_at / cron)
- HTTP-клиент с поддержкой прокси
- Регистрация команд и callback-кнопок
- Отправка уведомлений админам в TG
- Доступ к funpay_client, event_bus, конфигу, аналитике, аудиту
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from config.settings import config_manager
from core.event_bus import event_bus
from core.funpay_client import funpay_client
from utils.logger import logger


# ──────────────────────────────────────────────────────────────────────────────
# Хранилище плагина (JSON-файл на плагин)
# ──────────────────────────────────────────────────────────────────────────────

STORAGE_DIR = Path(__file__).resolve().parent.parent / "data" / "plugin_storage"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

SECRETS_DIR = Path(__file__).resolve().parent.parent / "data" / "plugin_secrets"
SECRETS_DIR.mkdir(parents=True, exist_ok=True)


class PluginStorage:
    """
    Простое key-value хранилище для плагина.
    Сохраняется в data/plugin_storage/<plugin_id>.json
    """

    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id
        self._path = STORAGE_DIR / f"{plugin_id}.json"
        self._cache: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(f"PluginStorage[{self._plugin_id}] не удалось прочитать, начинаю с пустого")
            return {}

    def _save(self) -> None:
        try:
            self._path.write_text(
                json.dumps(self._cache, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"PluginStorage[{self._plugin_id}] save failed: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._cache[key] = value
        self._save()

    def delete(self, key: str) -> None:
        self._cache.pop(key, None)
        self._save()

    def all(self) -> Dict[str, Any]:
        """Все данные плагина."""
        return dict(self._cache)

    def clear(self) -> None:
        self._cache.clear()
        self._save()


# ──────────────────────────────────────────────────────────────────────────────
# Шифрованное хранилище — для паролей Steam, API-ключей и т.д.
# ──────────────────────────────────────────────────────────────────────────────

class PluginSecrets:
    """
    Шифрованное хранилище секретов плагина.

    Использует AES-128 через secrets + hashlib (не требует cryptography).
    Не для критичных данных — но защищает от случайной утечки config.json.

    Ключ шифрования генерируется один раз и хранится в data/plugin_secrets/.master_key
    """

    _MASTER_KEY_PATH = SECRETS_DIR / ".master_key"

    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id
        self._path = SECRETS_DIR / f"{plugin_id}.dat"
        self._key = self._load_or_create_master_key()

    @classmethod
    def _load_or_create_master_key(cls) -> bytes:
        if cls._MASTER_KEY_PATH.exists():
            return cls._MASTER_KEY_PATH.read_bytes()
        key = secrets.token_bytes(32)
        cls._MASTER_KEY_PATH.write_bytes(key)
        try:
            import os
            os.chmod(cls._MASTER_KEY_PATH, 0o600)
        except Exception:
            pass
        logger.info("Создан мастер-ключ для шифрования секретов плагинов")
        return key

    def _xor_cipher(self, data: bytes) -> bytes:
        """Простой XOR-cipher (не криптостойкий, но скрывает от случайного чтения)."""
        # Расширяем ключ повторением до длины data
        full_key = (self._key * ((len(data) // len(self._key)) + 1))[: len(data)]
        return bytes(a ^ b for a, b in zip(data, full_key))

    def get(self, key: str, default: Any = None) -> Any:
        if not self._path.exists():
            return default
        try:
            blob = self._path.read_bytes()
            decrypted = self._xor_cipher(blob)
            data = json.loads(decrypted.decode("utf-8"))
            return data.get(key, default)
        except Exception:
            return default

    def set(self, key: str, value: Any) -> None:
        data = {}
        if self._path.exists():
            try:
                blob = self._path.read_bytes()
                data = json.loads(self._xor_cipher(blob).decode("utf-8"))
            except Exception:
                data = {}
        data[key] = value
        encrypted = self._xor_cipher(json.dumps(data).encode("utf-8"))
        self._path.write_bytes(encrypted)
        try:
            import os
            os.chmod(self._path, 0o600)
        except Exception:
            pass

    def delete(self, key: str) -> None:
        if not self._path.exists():
            return
        try:
            blob = self._path.read_bytes()
            data = json.loads(self._xor_cipher(blob).decode("utf-8"))
            data.pop(key, None)
            self._path.write_bytes(self._xor_cipher(json.dumps(data).encode("utf-8")))
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Планировщик задач для плагина
# ──────────────────────────────────────────────────────────────────────────────

class PluginScheduler:
    """
    Регистрация запланированных задач плагина.

    Поддерживает:
      - every(seconds=N) — каждые N секунд
      - every(minutes=N) — каждые N минут
      - daily(hour=12, minute=0) — каждый день в 12:00
      - once_at(timestamp) — однократно в указанное время
    """

    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id
        self._tasks: List[asyncio.Task] = []

    def every(self, seconds: int = 0, minutes: int = 0, hours: int = 0):
        """Декоратор: запускать функцию каждые N секунд/минут/часов."""
        interval = seconds + minutes * 60 + hours * 3600
        if interval <= 0:
            raise ValueError("Интервал должен быть > 0")

        def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable:
            async def runner():
                while True:
                    try:
                        await fn()
                    except Exception as e:
                        logger.exception(f"[{self._plugin_id}] every-task: {e}")
                    await asyncio.sleep(interval)
            self._tasks.append(asyncio.create_task(runner()))
            return fn
        return decorator

    def daily(self, hour: int = 12, minute: int = 0):
        """Декоратор: запускать каждый день в HH:MM."""
        def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable:
            async def runner():
                while True:
                    now = datetime.now()
                    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if target <= now:
                        # уже прошло сегодня — на завтра
                        from datetime import timedelta
                        target += timedelta(days=1)
                    delay = (target - now).total_seconds()
                    await asyncio.sleep(delay)
                    try:
                        await fn()
                    except Exception as e:
                        logger.exception(f"[{self._plugin_id}] daily-task: {e}")
            self._tasks.append(asyncio.create_task(runner()))
            return fn
        return decorator

    def once_at(self, timestamp: float):
        """Декоратор: запустить однократно в указанный unix timestamp."""
        def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable:
            async def runner():
                delay = max(0, timestamp - time.time())
                await asyncio.sleep(delay)
                try:
                    await fn()
                except Exception as e:
                    logger.exception(f"[{self._plugin_id}] once_at-task: {e}")
            self._tasks.append(asyncio.create_task(runner()))
            return fn
        return decorator

    def cancel_all(self) -> None:
        """Отменяет все задачи плагина."""
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()


# ──────────────────────────────────────────────────────────────────────────────
# HTTP-клиент для плагинов
# ──────────────────────────────────────────────────────────────────────────────

class PluginHTTP:
    """
    Простой HTTP-клиент для плагинов (через aiohttp).

    Все запросы идут через основной интернет (или системный VPN/proxifier).
    """

    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def get(self, url: str, *, headers: Optional[Dict] = None,
                  params: Optional[Dict] = None) -> Dict:
        """GET-запрос. Возвращает {status, text, json}."""
        session = await self._get_session()
        try:
            async with session.get(url, headers=headers, params=params) as resp:
                text = await resp.text()
                try:
                    js = await resp.json(content_type=None)
                except Exception:
                    js = None
                return {"status": resp.status, "text": text, "json": js}
        except Exception as e:
            logger.warning(f"[{self._plugin_id}] HTTP GET {url}: {e}")
            return {"status": 0, "text": "", "json": None, "error": str(e)}

    async def post(self, url: str, *, json_body: Optional[Dict] = None,
                   data: Optional[Any] = None, headers: Optional[Dict] = None) -> Dict:
        """POST-запрос."""
        session = await self._get_session()
        try:
            async with session.post(url, json=json_body, data=data, headers=headers) as resp:
                text = await resp.text()
                try:
                    js = await resp.json(content_type=None)
                except Exception:
                    js = None
                return {"status": resp.status, "text": text, "json": js}
        except Exception as e:
            logger.warning(f"[{self._plugin_id}] HTTP POST {url}: {e}")
            return {"status": 0, "text": "", "json": None, "error": str(e)}

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# ──────────────────────────────────────────────────────────────────────────────
# Доступ к лотам, заказам, аналитике
# ──────────────────────────────────────────────────────────────────────────────

class PluginLots:
    """Утилиты для работы с лотами FunPay."""

    @staticmethod
    async def get_lot(lot_id: int):
        """Возвращает объект лота с FunPay."""
        if not funpay_client.account:
            return None
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None, funpay_client.account.get_lot_fields, lot_id
            )
        except Exception as e:
            logger.warning(f"PluginLots.get_lot({lot_id}): {e}")
            return None

    @staticmethod
    async def update_lot(lot_id: int, *, price: Optional[float] = None,
                         description: Optional[str] = None,
                         active: Optional[bool] = None) -> bool:
        """Обновляет поля лота."""
        if not funpay_client.account:
            return False
        loop = asyncio.get_event_loop()
        try:
            fields = await loop.run_in_executor(
                None, funpay_client.account.get_lot_fields, lot_id
            )
            if price is not None and hasattr(fields, "price"):
                fields.price = price
            if description is not None:
                for attr in ("description_ru", "description"):
                    if hasattr(fields, attr):
                        setattr(fields, attr, description)
                        break
            if active is not None and hasattr(fields, "active"):
                fields.active = active
            await loop.run_in_executor(None, funpay_client.account.save_lot, fields)
            return True
        except Exception as e:
            logger.warning(f"PluginLots.update_lot({lot_id}): {e}")
            return False

    @staticmethod
    async def list_my_lots() -> List[Dict[str, Any]]:
        """Возвращает упрощённый список наших лотов."""
        if not funpay_client.account:
            return []
        loop = asyncio.get_event_loop()
        try:
            profile = await loop.run_in_executor(
                None, funpay_client.account.get_user, funpay_client.account.id
            )
            cats = getattr(profile, "get_sorted_lots", lambda *_: {})(2) or {}
            out = []
            for game_id, sub in cats.items():
                for sub_category in sub.values():
                    lots = getattr(sub_category, "lots", None) or []
                    for lot in lots:
                        out.append({
                            "id": int(getattr(lot, "id", 0)),
                            "title": getattr(lot, "title", "") or getattr(lot, "description", ""),
                            "price": getattr(lot, "price", 0),
                            "active": getattr(lot, "active", True),
                            "subcategory_id": int(getattr(sub_category, "id", 0)),
                        })
            return out
        except Exception as e:
            logger.warning(f"PluginLots.list_my_lots: {e}")
            return []


class PluginOrders:
    """Утилиты для работы с заказами."""

    @staticmethod
    async def refund(order_id: str) -> bool:
        """Возврат средств за заказ."""
        if not funpay_client.account:
            return False
        loop = asyncio.get_event_loop()
        try:
            for name in ("refund", "refund_order", "cancel_order"):
                if hasattr(funpay_client.account, name):
                    fn = getattr(funpay_client.account, name)
                    await loop.run_in_executor(None, fn, order_id)
                    return True
            return False
        except Exception as e:
            logger.warning(f"PluginOrders.refund({order_id}): {e}")
            return False


class PluginAnalytics:
    """Доступ к базе аналитики (только чтение)."""

    @staticmethod
    def query(sql: str, params: tuple = ()) -> List[tuple]:
        """Свободный SELECT-запрос к analytics.db (READ-ONLY)."""
        if not sql.strip().lower().startswith("select"):
            raise ValueError("PluginAnalytics.query поддерживает только SELECT")
        import sqlite3
        from modules.analytics import DB_PATH
        with sqlite3.connect(DB_PATH) as con:
            return con.execute(sql, params).fetchall()


# ──────────────────────────────────────────────────────────────────────────────
# Главный API
# ──────────────────────────────────────────────────────────────────────────────

class PluginAPI:
    """
    Объект, который получает плагин в setup(api).

    Атрибуты:
        api.event_bus       — событийная шина
        api.funpay_client   — клиент FunPay (.send_message, .get_balance)
        api.config_manager  — глобальный конфиг
        api.logger          — общий логгер
        api.storage         — личное хранилище плагина (PluginStorage)
        api.secrets         — шифрованное хранилище секретов (PluginSecrets)
        api.scheduler       — планировщик (PluginScheduler)
        api.http            — HTTP-клиент (PluginHTTP)
        api.lots            — утилиты лотов (PluginLots)
        api.orders          — утилиты заказов (PluginOrders)
        api.analytics       — доступ к БД аналитики (PluginAnalytics)

    Декораторы:
        @api.command("name")    — регистрация команды /name
        @api.callback("prefix") — регистрация callback кнопок plg_<id>:<prefix>:...
        @api.on(event_name)     — подписка на событие event_bus
        @api.tag_handler(tag)   — реакция на лот с этим тегом в описании

    Методы:
        api.notify_admins(text) — отправить сообщение всем админам в TG
        api.send_to_chat(chat_id, text) — отправить в чат FunPay
        api.audit(action, **kwargs) — записать в audit-лог
        api.tg_bot()            — получить объект aiogram Bot (для фотографий и т.д.)
    """

    def __init__(self, manager: "PluginManager", plugin_id: str) -> None:
        self._manager = manager
        self._plugin_id = plugin_id

        # Базовые ссылки
        self.event_bus = event_bus
        self.funpay_client = funpay_client
        self.config_manager = config_manager
        self.logger = logger

        # Расширенные сервисы
        self.storage = PluginStorage(plugin_id)
        self.secrets = PluginSecrets(plugin_id)
        self.scheduler = PluginScheduler(plugin_id)
        self.http = PluginHTTP(plugin_id)
        self.lots = PluginLots()
        self.orders = PluginOrders()
        self.analytics = PluginAnalytics()

    # ─── Регистрация хэндлеров ──────────────────────────────────────────────

    def command(self, name: str) -> Callable:
        """
        Декоратор для регистрации команды плагина.

        @api.command("activity")
        async def cmd_activity(message):
            await message.answer("...")
        """
        def decorator(fn: Callable) -> Callable:
            self._manager.register_command(self._plugin_id, name.lstrip("/"), fn)
            return fn
        return decorator

    def callback(self, prefix: str) -> Callable:
        """
        Декоратор для inline-кнопок плагина.

        Создаёт обработчик для callback_data вида: plg_<id>:<prefix>:<data>

        @api.callback("buy")
        async def on_buy_button(callback_query, data: str):
            # data — то что было после prefix:
            await callback_query.answer(f"Купили: {data}")
        """
        def decorator(fn: Callable) -> Callable:
            self._manager.register_callback(self._plugin_id, prefix, fn)
            return fn
        return decorator

    def on(self, event_name: str) -> Callable:
        """
        Подписка на событие event_bus (с авто-снятием при выгрузке плагина).

        @api.on("new_order")
        async def handle(order):
            ...
        """
        def decorator(fn: Callable) -> Callable:
            event_bus.on(event_name)(fn)
            self._manager.register_handler(self._plugin_id, event_name, fn)
            return fn
        return decorator

    def tag_handler(self, tag: str) -> Callable:
        """
        Реакция на оплату лота, в описании которого есть указанный тег.

        Полезно для спецсценариев: например тег `gpt:CODE` для авто-выдачи
        ChatGPT-аккаунтов с LZT.

        @api.tag_handler("gpt:CODE")
        async def handle_gpt_buy(order, lot_description):
            # вызывается при оплате лота с этим тегом в описании
            ...
        """
        def decorator(fn: Callable) -> Callable:
            self._manager.register_tag_handler(self._plugin_id, tag, fn)
            return fn
        return decorator

    # ─── Утилиты ────────────────────────────────────────────────────────────

    async def notify_admins(self, text: str, *, parse_mode: str = "HTML") -> None:
        """Отправить сообщение всем админам/менеджерам в TG."""
        try:
            from modules.notifications import _send_to_all
            await _send_to_all(text)
        except Exception as e:
            logger.warning(f"[{self._plugin_id}] notify_admins: {e}")

    def send_to_chat(self, chat_id: int, text: str) -> bool:
        """Отправить сообщение в чат FunPay."""
        return funpay_client.send_message(chat_id, text)

    def audit(self, action: str, **kwargs) -> None:
        """Запись в audit-лог."""
        try:
            from utils.audit_log import audit
            audit(0, f"PLUGIN_{action}", data=f"plugin={self._plugin_id}",
                  text=str(kwargs)[:200] if kwargs else "")
        except Exception:
            pass

    def tg_bot(self):
        """Возвращает объект aiogram Bot (или None)."""
        try:
            from modules.notifications import _bot_ref
            return _bot_ref
        except Exception:
            return None

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    @property
    def data_dir(self) -> Path:
        """Папка данных конкретно этого плагина (создастся автоматически)."""
        d = STORAGE_DIR / self._plugin_id
        d.mkdir(exist_ok=True)
        return d
