"""
Клиент FunPay — обёртка над FunPayAPI (форк из FunPayCardinal).
Локальная копия лежит в /FunPayAPI/ и должна загружаться вместо pypi-версии.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import config_manager
from core.event_bus import Event, event_bus
from utils.logger import logger

# ── Импорты FunPayAPI с правильными путями ────────────────────────────────────
Account = None
Runner  = None
FUNPAY_AVAILABLE = False
FUNPAY_IMPORT_ERROR = None

# КРИТИЧЕСКИ ВАЖНО: принудительно ставим корень проекта В НАЧАЛО sys.path,
# чтобы локальная FunPayAPI (Cardinal-форк) загружалась вместо pypi-версии.
# Если локальная папка не первая, Python может взять старую pypi-версию,
# в которой не работает get_chats_histories → нет NEW_MESSAGE.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_local_funpay_dir = _PROJECT_ROOT / "FunPayAPI"

if _local_funpay_dir.exists():
    project_root_str = str(_PROJECT_ROOT)
    # Ставим в самое начало
    if project_root_str in sys.path:
        sys.path.remove(project_root_str)
    sys.path.insert(0, project_root_str)
    # Если pypi-версия уже была импортирована — выгружаем её
    for mod_name in list(sys.modules.keys()):
        if mod_name == "FunPayAPI" or mod_name.startswith("FunPayAPI."):
            del sys.modules[mod_name]

try:
    import FunPayAPI as _funpay_module
    from FunPayAPI import Account  # type: ignore
    from FunPayAPI.updater.runner import Runner  # type: ignore

    FUNPAY_AVAILABLE = True
    _funpay_path = getattr(_funpay_module, "__file__", "?")
    is_local = str(_local_funpay_dir) in _funpay_path
    if is_local:
        logger.info(f"✅ FunPayAPI загружена из ЛОКАЛЬНОЙ копии (Cardinal-форк): {_funpay_path}")
    else:
        logger.warning(f"⚠️ FunPayAPI загружена НЕ из локальной папки! Путь: {_funpay_path}")
        logger.warning(f"⚠️ Ожидался путь начинающийся с: {_local_funpay_dir}")
        logger.warning(f"⚠️ Это может означать что используется pypi-версия с багами!")
        logger.warning(f"⚠️ Удалите её: pip uninstall FunPayAPI")

except ImportError as _e:
    FUNPAY_IMPORT_ERROR = str(_e)
    # Пробуем запасной путь
    try:
        from FunPayAPI import Account  # type: ignore
        from FunPayAPI.runner import Runner  # type: ignore  # старые версии
        FUNPAY_AVAILABLE = True
        logger.debug("FunPayAPI загружена (запасной путь FunPayAPI.runner)")
    except (ImportError, Exception) as _e2:
        FUNPAY_IMPORT_ERROR = str(_e2)

except (TypeError, SyntaxError) as _e:
    FUNPAY_IMPORT_ERROR = str(_e)


class FunPayClient:
    """Высокоуровневая обёртка над FunPayAPI."""

    def __init__(self) -> None:
        self.account: Optional[Any] = None
        self.runner:  Optional[Any] = None
        self._running: bool = False
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        # Недавние ИСХОДЯЩИЕ сообщения: chat_id → [(text, timestamp), ...]
        # Используется для подавления echo-уведомлений когда FunPay
        # присылает наше же сообщение через NEW_MESSAGE.
        self._recent_outgoing: Dict[int, List[Tuple[str, float]]] = {}

    # ─── Подключение ──────────────────────────────────────────────────────────

    def connect(self) -> Tuple[bool, str]:
        if not FUNPAY_AVAILABLE or Account is None:
            return False, (
                f"FunPayAPI не загрузилась: {FUNPAY_IMPORT_ERROR}. "
                "Установите: pip install FunPayAPI"
            )
        cfg = config_manager.settings.funpay
        if not cfg.golden_key:
            return False, "Не задан golden_key."

        # КРИТИЧЕСКАЯ проверка User-Agent — без него FunPay не отдаёт чаты
        if not cfg.user_agent or "python-requests" in cfg.user_agent:
            DEFAULT_UA = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
            )
            logger.warning("⚠️ User-Agent НЕ задан или python-requests — FunPay не будет отдавать чаты!")
            logger.warning(f"⚠️ Принудительно использую браузерный UA: Chrome 109")
            logger.warning(f"⚠️ Чтобы поменять — запустите: python main.py --setup-ua")
            cfg.user_agent = DEFAULT_UA
            config_manager.save()

        try:
            # Если задан прокси — собираем dict для requests
            proxy_dict = self._build_proxy_dict(cfg.proxy) if cfg.proxy else None
            if proxy_dict:
                logger.info(f"FunPay через прокси: {cfg.proxy.split('@')[-1]}")

            logger.info(f"User-Agent: {cfg.user_agent[:60]}...")

            self.account = Account(
                cfg.golden_key,
                user_agent=cfg.user_agent,
                proxy=proxy_dict,
            ).get()
            cfg.account_id = self.account.id
            cfg.username   = self.account.username
            config_manager.save()

            # Cardinal-стиль приветствие
            self._print_welcome_banner()

            return True, f"Подключение: {self.account.username} (ID {self.account.id})"
        except Exception as e:
            err_str = str(e)
            err_type = type(e).__name__
            # Распознаём типичные проблемы и даём понятное сообщение
            if "UnauthorizedError" in err_type or "Unauthorized" in err_str:
                hint = (
                    "❌ FunPay не принимает golden_key.\n"
                    "Возможные причины:\n"
                    "  1. golden_key получен с другого IP (бот ходит через прокси, "
                    "а ключ взят с обычного IP) → откройте funpay.com В БРАУЗЕРЕ "
                    "ЧЕРЕЗ ТОТ ЖЕ ПРОКСИ, авторизуйтесь, скопируйте golden_key из cookies.\n"
                    "  2. golden_key устарел (вы разлогинились где-то) → возьмите "
                    "новый из cookies funpay.com.\n"
                    "  3. golden_key введён неправильно (32 символа букв и цифр)."
                )
                logger.error(hint)
                return False, hint
            logger.exception(f"Ошибка подключения к FunPay: {e}")
            return False, f"Ошибка: {e}"

    @staticmethod
    def _build_proxy_dict(proxy: str) -> Dict[str, str]:
        """Преобразует строку прокси в dict для requests."""
        if "@" not in proxy and proxy.count(":") == 3:
            login, password, ip, port = proxy.split(":")
            proxy = f"{login}:{password}@{ip}:{port}"
        url = f"http://{proxy}"
        return {"http": url, "https": url}

    def _print_welcome_banner(self) -> None:
        """Cardinal-стиль приветствие после подключения к FunPay."""
        if not self.account:
            return

        from datetime import datetime
        h = datetime.now().hour
        if 5 <= h < 12:
            greet = "Доброе утро"
        elif 12 <= h < 18:
            greet = "Добрый день"
        elif 18 <= h < 23:
            greet = "Добрый вечер"
        else:
            greet = "Доброй ночи"

        # Баланс
        try:
            bal = self.account.get_balance()
            rub = getattr(bal, "total_rub", 0.0) or 0.0
            usd = getattr(bal, "total_usd", 0.0) or 0.0
            eur = getattr(bal, "total_eur", 0.0) or 0.0
            balance_line = f"Ваш текущий баланс: {rub} RUB | {usd} USD | {eur} EUR"
        except Exception:
            balance_line = "Ваш текущий баланс: получить не удалось"

        # Незакрытые сделки (опционально)
        try:
            self.account.get_sales()
            sales = self.account.active_sales
            sales_line = f"Текущие незавершенные сделки: {sales}."
        except Exception:
            sales_line = "Текущие незавершенные сделки: —"

        sep = "─" * 60
        logger.info(sep)
        logger.info(f"{greet}, {self.account.username}.")
        logger.info(f"Ваш ID: {self.account.id}.")
        logger.info(balance_line)
        logger.info(sales_line)
        logger.info("Удачной торговли!")
        logger.info(sep)

    # ─── Поллинг ──────────────────────────────────────────────────────────────

    async def start_polling(self) -> None:
        """
        Запускает поллинг FunPay в двух потоках (точно как FunPayCardinal):
          - Поток 1: runner.loop() — обработчик ОЧЕРЕДИ запросов к FunPay
          - Поток 2: runner.listen() — поллер событий (NEW_MESSAGE и т.д.)

        Без потока loop() — listen() возвращает 0 событий!
        """
        if not FUNPAY_AVAILABLE or not self.account:
            logger.warning("Поллинг не запущен (нет аккаунта или FunPayAPI).")
            return

        self._running = True

        if Runner is None:
            logger.warning("Runner недоступен")
            return

        try:
            self.runner = Runner(self.account)
            logger.info("FunPay Runner инициализирован")
        except Exception as e:
            logger.error(f"Ошибка инициализации Runner: {e}")
            self.runner = None
            return

        # Сохраняем главный asyncio-цикл — из него мы будем планировать
        # обработку событий, которые ловит listen() в фоновом потоке
        self._main_loop = asyncio.get_event_loop()

        # Поток 1: runner.loop() — обрабатывает очередь payload-запросов к FunPay.
        # БЕЗ ЭТОГО ПОТОКА runner.listen() возвращает 0 событий!
        # Cardinal делает то же самое: Thread(target=self.runner.loop, daemon=True).start()
        import threading
        loop_thread = threading.Thread(
            target=self._runner_loop_thread,
            name="FunPayRunnerLoop",
            daemon=True,
        )
        loop_thread.start()
        logger.info("FunPay runner.loop() запущен (обработчик очереди)")

        # Поток 2: runner.listen() — поллит события и yield-ит их
        listen_thread = threading.Thread(
            target=self._listen_loop_thread,
            name="FunPayListen",
            daemon=True,
        )
        listen_thread.start()
        logger.info("FunPay listen-поток запущен")

    def _runner_loop_thread(self) -> None:
        """
        Запускает runner.loop() — обработчик очереди payload-запросов.
        Это ОТДЕЛЬНЫЙ поток от listen(). Cardinal делает так же.
        """
        if not self.runner:
            return
        try:
            self.runner.loop()
        except Exception as e:
            logger.exception(f"runner.loop упал: {e}")

    def _listen_loop_thread(self) -> None:
        """
        Синхронный цикл listen() в отдельном потоке.

        runner.listen() — это generator, который сам делает HTTP-запросы
        и yield'ит события. Бесконечный цикл.
        """
        if not self.runner:
            return

        logger.info("⏳ Listen-цикл стартовал, ожидание событий FunPay...")

        try:
            for ev in self.runner.listen(requests_delay=4, ignore_exceptions=True):
                if not self._running:
                    logger.info("Listen-цикл остановлен")
                    break

                # Логируем каждое событие — это поможет понять что приходит
                ev_type_name = "?"
                try:
                    et = getattr(ev, "type", None)
                    ev_type_name = getattr(et, "name", None) or type(ev).__name__
                except Exception:
                    ev_type_name = type(ev).__name__
                logger.info(f"📥 FunPay event: {ev_type_name}")

                # Планируем обработку в главном asyncio-цикле
                if self._main_loop and self._main_loop.is_running():
                    try:
                        future = asyncio.run_coroutine_threadsafe(
                            self._dispatch(ev),
                            self._main_loop,
                        )
                        # Не ждём future — но логируем если оно сразу упало
                    except Exception as e:
                        logger.error(f"Не удалось запланировать _dispatch: {e}")
                else:
                    logger.warning(f"⚠️ Главный asyncio-цикл не работает! event {ev_type_name} потерян")
        except Exception as e:
            logger.exception(f"Listen-цикл упал: {e}")

    async def _dispatch(self, ev: Any) -> None:
        """
        Маппинг событий FunPayAPI → event_bus.
        """
        try:
            await self._dispatch_inner(ev)
        except Exception as e:
            logger.exception(f"❌ _dispatch упал: {type(e).__name__}: {e}")

    async def _dispatch_inner(self, ev: Any) -> None:
        """
        Используем event.type (enum EventTypes) как Cardinal — это надёжнее
        чем сравнение по type(ev).__name__.
        """
        try:
            from FunPayAPI.common.enums import EventTypes, OrderStatuses, MessageTypes
        except ImportError:
            EventTypes = None
            OrderStatuses = None
            MessageTypes = None

        ev_type = getattr(ev, "type", None)
        type_name = getattr(ev_type, "name", None) or type(ev).__name__

        # NEW_MESSAGE — официальное от FunPayAPI (новый режим, может не приходить)
        if EventTypes and ev_type == EventTypes.NEW_MESSAGE:
            msg = getattr(ev, "message", None)
            if msg:
                await self._process_new_message(msg)
            return

        # LAST_CHAT_MESSAGE_CHANGED — игнорируем, т.к. NEW_MESSAGE приходит отдельно
        # (с теми же данными). Иначе сообщение будет дублироваться.
        if EventTypes and ev_type == EventTypes.LAST_CHAT_MESSAGE_CHANGED:
            return

        # CHATS_LIST_CHANGED — служебное, игнорируем
        if EventTypes and ev_type == EventTypes.CHATS_LIST_CHANGED:
            return

        # INITIAL_CHAT — первое появление чата (auto_greeting)
        if EventTypes and ev_type == EventTypes.INITIAL_CHAT:
            chat = getattr(ev, "chat", None)
            if chat:
                await event_bus.emit("initial_chat", chat)
            return

        # NEW_ORDER
        if EventTypes and ev_type == EventTypes.NEW_ORDER:
            order = getattr(ev, "order", None)
            if order:
                logger.info(f"💰 Новый заказ #{getattr(order, 'id', '?')}: {getattr(order, 'description', '')[:60]}")
                await event_bus.emit(Event.NEW_ORDER, order)
                if OrderStatuses:
                    status = getattr(order, "status", None)
                    if status == OrderStatuses.PAID:
                        await event_bus.emit(Event.ORDER_PAID, order)
            return

        # ORDER_STATUS_CHANGED — статус заказа поменялся
        if EventTypes and ev_type == EventTypes.ORDER_STATUS_CHANGED:
            order = getattr(ev, "order", None)
            if not order:
                return
            status = getattr(order, "status", None)
            if OrderStatuses:
                if status == OrderStatuses.CLOSED:
                    logger.info(f"✅ Заказ #{getattr(order, 'id', '?')} подтверждён")
                    await event_bus.emit(Event.ORDER_CONFIRMED, order)
                elif status == OrderStatuses.REFUNDED:
                    logger.info(f"↩️ Заказ #{getattr(order, 'id', '?')} возвращён")
                    await event_bus.emit(Event.ORDER_REFUNDED, order)
                elif status == OrderStatuses.PAID:
                    await event_bus.emit(Event.ORDER_PAID, order)
            return

        # Прочие в DEBUG
        logger.debug(f"FunPay event ignored: {type_name}")

    async def _process_new_message(self, msg) -> None:
        """Эмитит NEW_MESSAGE/NEW_REVIEW в шину событий."""
        try:
            from FunPayAPI.common.enums import MessageTypes
        except ImportError:
            MessageTypes = None

        author = getattr(msg, "author", "?")
        text = (getattr(msg, "text", "") or "")[:60]
        cid = getattr(msg, "chat_id", "?")
        logger.info(f"📨 Новое сообщение в чате {cid} от {author}: {text}")

        # Если это отзыв (NEW_FEEDBACK) — эмитим NEW_REVIEW
        if MessageTypes:
            mtype = getattr(msg, "type", None)
            if mtype == MessageTypes.NEW_FEEDBACK:
                await event_bus.emit(Event.NEW_REVIEW, msg)
                return

        await event_bus.emit(Event.NEW_MESSAGE, msg)

    async def _emit_message_from_chat(self, chat) -> None:
        """
        Создаёт фейковое Message из ChatShortcut и эмитит NEW_MESSAGE.

        Используется в old-mode (когда NewMessageEvent не приходит).
        ChatShortcut содержит: id, name, last_message_text, last_by_bot, unread.
        """
        chat_id = getattr(chat, "id", None)
        chat_name = getattr(chat, "name", None) or "?"
        text = getattr(chat, "last_message_text", "") or str(chat) or ""

        if not chat_id:
            return

        # Создаём объект-обёртку с теми же атрибутами что и FunPayAPI.Message
        class _ChatMessage:
            pass

        msg = _ChatMessage()
        msg.id = 0
        msg.chat_id = chat_id
        msg.chat_name = chat_name
        msg.text = text
        msg.author = chat_name
        msg.author_id = chat_id  # используем chat_id как fallback
        msg.image_link = None
        msg.by_bot = bool(getattr(chat, "last_by_bot", False))
        msg.type = None  # MessageTypes.NON_SYSTEM по умолчанию

        await event_bus.emit(Event.NEW_MESSAGE, msg)

    def stop(self) -> None:
        self._running = False

    # ─── Tracking своих исходящих сообщений ─────────────────────────────────

    def _register_outgoing(self, chat_id: int, text: str) -> None:
        """
        Запоминаем что только что отправили это сообщение в этот чат.
        Используется в notify_message чтобы пропустить echo от FunPay.
        """
        import time as _t
        text_norm = (text or "").strip()
        if not text_norm:
            return
        bucket = self._recent_outgoing.setdefault(chat_id, [])
        bucket.append((text_norm, _t.time()))
        # Чистим хвост старше 60 сек
        cutoff = _t.time() - 60
        self._recent_outgoing[chat_id] = [(t, ts) for t, ts in bucket if ts > cutoff]

    def is_recent_outgoing(self, chat_id, text: str) -> bool:
        """
        True если этот текст был только что отправлен нами в этот чат
        (в течение последних 30 секунд).
        """
        import time as _t
        try:
            cid = int(chat_id)
        except (TypeError, ValueError):
            return False
        text_norm = (text or "").strip()
        if not text_norm:
            return False
        bucket = self._recent_outgoing.get(cid, [])
        cutoff = _t.time() - 30
        for t, ts in bucket:
            if ts > cutoff and t == text_norm:
                return True
        return False

    # ─── Утилиты ──────────────────────────────────────────────────────────────

    def get_balance(self) -> Dict[str, float]:
        if not self.account:
            return {"rub": 0.0, "usd": 0.0, "eur": 0.0}
        try:
            b = self.account.get_balance()
            return {
                "rub": float(getattr(b, "total_rub", 0) or 0),
                "usd": float(getattr(b, "total_usd", 0) or 0),
                "eur": float(getattr(b, "total_eur", 0) or 0),
            }
        except Exception as e:
            logger.warning(f"get_balance: {e}")
            return {"rub": 0.0, "usd": 0.0, "eur": 0.0}

    def get_active_orders_count(self) -> int:
        if not self.account:
            return 0
        try:
            _, orders = self.account.get_sells(
                include_paid=True, include_closed=False, include_refunded=False
            )
            return len(orders)
        except Exception:
            return 0

    def send_message(self, chat_id, text: str) -> bool:
        """
        Отправляет сообщение в FunPay-чат.

        Возвращает True если отправка прошла. AttributeError на парсинге
        ответа FunPay считается успехом — сообщение в этом случае ушло,
        а упал только парсер ответа в самом FunPayAPI.
        """
        if not self.account:
            return False

        if not text or not str(text).strip():
            return False

        # Нормализация chat_id: строка → int
        try:
            cid = int(chat_id)
        except (TypeError, ValueError):
            logger.debug(f"send_message: невалидный chat_id={chat_id!r}")
            return False

        # Невалидные chat_id (ноль и отрицательные — сентинелы для тестов).
        # Порог 99999 был слишком агрессивным: реальные FunPay-чаты со старыми
        # аккаунтами имеют низкие ID и блокировались. Теперь пропускаем только <= 0.
        if cid <= 0:
            logger.debug(f"send_message({cid}): невалидный/тестовый chat_id, пропуск")
            return False

        # Водяной знак
        try:
            wm = config_manager.settings.watermark
            if wm.enabled and wm.text and not text.endswith(wm.text):
                text = f"{text}{wm.text}"
        except Exception:
            pass

        try:
            # update_last_saved_message=False — иначе FunPayAPI попытается
            # обновить кэш и упадёт с "'NoneType' object has no attribute 'text'"
            self.account.send_message(cid, text, update_last_saved_message=False)
            self._register_outgoing(cid, text)
            return True

        except AttributeError as e:
            # 'NoneType' object has no attribute 'text' — сообщение УШЛО,
            # но FunPayAPI не смог распарсить ответ. Считаем успешной отправкой.
            if "'text'" in str(e):
                self._register_outgoing(cid, text)
                logger.debug(f"send_message({cid}): отправлено (FunPayAPI парсер упал на ответе)")
                return True
            logger.debug(f"send_message({cid}): {e}")
            return False

        except Exception as e:
            err_str = str(e)
            err_type = type(e).__name__

            # Тихие коды — обычное состояние, не пишем WARNING
            if any(k in err_str for k in (
                "не удалось получить истории чатов",
                "Не удалось получить истории чатов",
                "MessageNotDelivered",
            )):
                logger.debug(f"send_message({cid}): {err_str[:120]}")
                return False

            if "RequestFailed" in err_type:
                logger.debug(f"send_message({cid}): HTTP ошибка")
                return False

            logger.debug(f"send_message({cid}): {err_type}: {err_str[:200]}")
            return False

    def get_chat_name(self, chat_id) -> Optional[str]:
        """Возвращает имя чата из кэша аккаунта."""
        if not self.account:
            return None
        try:
            cid = int(chat_id)
        except (TypeError, ValueError):
            return None
        try:
            acc_chats = getattr(self.account, "_Account__chats", {}) or {}
            chat = acc_chats.get(cid)
            if chat:
                return getattr(chat, "name", None)
        except Exception:
            pass
        return None


funpay_client = FunPayClient()
