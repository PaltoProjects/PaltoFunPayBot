"""
Клиент FunPay — обёртка над FunPayAPI (форк из FunPayCardinal).
Локальная копия лежит в /FunPayAPI/ и должна загружаться вместо pypi-версии.
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import OrderedDict
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
    """
    Высокоуровневая обёртка над FunPayAPI — один клиент на один FunPay-аккаунт.

    acc_cfg — настройки конкретного аккаунта (FunPayAccountSettings);
    None = legacy-режим, читаем глобальные settings.funpay (активный аккаунт).
    """

    def __init__(self, acc_cfg: Optional[Any] = None) -> None:
        self.acc_cfg = acc_cfg
        self.index: int = 0  # позиция в списке аккаунтов (ставит менеджер)
        self.account: Optional[Any] = None
        self.runner:  Optional[Any] = None
        self._running: bool = False
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        # Недавние ИСХОДЯЩИЕ сообщения: chat_id → [(text, timestamp), ...]
        # Используется для подавления echo-уведомлений когда FunPay
        # присылает наше же сообщение через NEW_MESSAGE.
        self._recent_outgoing: Dict[int, List[Tuple[str, float]]] = {}
        # ID заказов, по которым уже эмитили ORDER_PAID — FunPay присылает
        # оплату дважды (NEW_ORDER со статусом PAID + ORDER_STATUS_CHANGED).
        # Без дедупа аналитика, уведомления и автовыдача срабатывали бы дважды.
        self._emitted_paid: "OrderedDict[str, bool]" = OrderedDict()
        # Fallback на old mode: если NEW_MESSAGE перестал приходить, а
        # активность в чатах есть (LAST_CHAT_MESSAGE_CHANGED) — читаем
        # сообщения из ChatShortcut, чтобы бот не ослеп.
        self._last_new_message_ts: float = 0.0
        self._activity_without_new_message: int = 0
        self._old_mode_fallback: bool = False
        self._fallback_last_emitted: Dict[int, str] = {}  # chat_id → последний эмитнутый текст

    # ─── Настройки этого аккаунта ─────────────────────────────────────────────

    def _cfg(self) -> Any:
        """Настройки этого аккаунта (или legacy-глобальные, если acc_cfg нет)."""
        return self.acc_cfg if self.acc_cfg is not None else config_manager.settings.funpay

    @property
    def alias(self) -> str:
        if self.acc_cfg is not None:
            return self.acc_cfg.display_name()
        return config_manager.settings.funpay.username or f"#{self.index + 1}"

    @property
    def own_id(self) -> Optional[int]:
        """ID аккаунта FunPay, которым управляет этот клиент."""
        return self._cfg().account_id

    def _sync_if_active(self) -> None:
        """
        Если этот клиент управляет АКТИВНЫМ аккаунтом — обновляем legacy-вид
        (settings.funpay.golden_key и т.д.) перед save(), иначе save()
        скопирует устаревший legacy обратно поверх наших свежих данных.
        """
        fp = config_manager.settings.funpay
        if self.acc_cfg is not None and fp.active_account() is self.acc_cfg:
            fp.sync_legacy_from_active()

    # ─── Подключение ──────────────────────────────────────────────────────────

    def connect(self) -> Tuple[bool, str]:
        if not FUNPAY_AVAILABLE or Account is None:
            return False, (
                f"FunPayAPI не загрузилась: {FUNPAY_IMPORT_ERROR}. "
                "Установите: pip install FunPayAPI"
            )
        cfg = self._cfg()
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
            self._sync_if_active()
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
            self._sync_if_active()
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

        # Точка отсчёта для old-mode fallback — стартовые INITIAL-события
        # не должны триггерить переключение.
        import time as _t
        self._last_new_message_ts = _t.time()

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
        # Штампуем объекты события индексом аккаунта-источника: обработчики
        # (автоответ, выдача и т.д.) отвечают через тот же аккаунт
        # (accounts_manager.client_for(obj)).
        for attr in ("message", "order", "chat"):
            obj = getattr(ev, attr, None)
            if obj is not None:
                try:
                    setattr(obj, "_palto_acc", self.index)
                except Exception:
                    pass
        try:
            await self._dispatch_inner(ev)
        except Exception as e:
            logger.exception(f"❌ _dispatch упал: {type(e).__name__}: {e}")

    def _should_emit_paid(self, order) -> bool:
        """True только для ПЕРВОГО события об оплате конкретного заказа."""
        oid = str(getattr(order, "id", "") or "")
        if not oid:
            return True  # без id дедупить нечем — пропускаем как есть
        if oid in self._emitted_paid:
            return False
        self._emitted_paid[oid] = True
        # Ограничиваем память — храним последние 2000 заказов
        while len(self._emitted_paid) > 2000:
            self._emitted_paid.popitem(last=False)
        return True

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
                self._note_new_message_alive(msg)
                await self._process_new_message(msg)
            return

        # LAST_CHAT_MESSAGE_CHANGED — в норме игнорируем (NEW_MESSAGE приходит
        # отдельно с теми же данными, иначе были бы дубли). Но если NEW_MESSAGE
        # перестал приходить при живой активности в чатах — включаем fallback
        # и читаем сообщения отсюда (аналог old mode Cardinal'а).
        if EventTypes and ev_type == EventTypes.LAST_CHAT_MESSAGE_CHANGED:
            await self._handle_last_chat_message_changed(ev)
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
                    if status == OrderStatuses.PAID and self._should_emit_paid(order):
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
                elif status == OrderStatuses.PAID and self._should_emit_paid(order):
                    await event_bus.emit(Event.ORDER_PAID, order)
            return

        # Прочие в DEBUG
        logger.debug(f"FunPay event ignored: {type_name}")

    # ─── Old-mode fallback ────────────────────────────────────────────────────
    #
    # Симптом: на некоторых аккаунтах FunPay перестаёт отдавать NEW_MESSAGE,
    # но LAST_CHAT_MESSAGE_CHANGED продолжает приходить. Без fallback бот
    # молча слепнет: нет автоответа, приветствий, уведомлений.

    # Включаем fallback, если NEW_MESSAGE нет дольше этого времени…
    FALLBACK_AFTER_SEC = 300
    # …и за это время накопилось столько входящих LAST_CHAT_MESSAGE_CHANGED.
    FALLBACK_AFTER_EVENTS = 3

    def _note_new_message_alive(self, msg) -> None:
        """Вызывается на каждом NEW_MESSAGE: сбрасывает счётчики fallback."""
        import time as _t
        self._last_new_message_ts = _t.time()
        self._activity_without_new_message = 0
        if self._old_mode_fallback:
            self._old_mode_fallback = False
            logger.info("NEW_MESSAGE снова приходит — выключаю old-mode fallback")
        # Защита от дубля на границе переключения: помечаем текст как уже
        # обработанный, чтобы fallback не эмитнул его повторно.
        cid = getattr(msg, "chat_id", None)
        text = (getattr(msg, "text", "") or "").strip()
        if cid and text:
            self._fallback_last_emitted[cid] = text

    async def _handle_last_chat_message_changed(self, ev) -> None:
        """
        Решает, надо ли эмитить сообщение из LAST_CHAT_MESSAGE_CHANGED.
        В норме — нет (дубль NEW_MESSAGE). При молчании NEW_MESSAGE — да.
        """
        chat = getattr(ev, "chat", None)
        if chat is None:
            return
        chat_id = getattr(chat, "id", None)
        text = (getattr(chat, "last_message_text", "") or "").strip()
        if not chat_id or not text:
            return

        # Интересуют только входящие непрочитанные сообщения не от нас
        if getattr(chat, "last_by_bot", False) or not getattr(chat, "unread", False):
            return
        if self.is_recent_outgoing(chat_id, text):
            return

        import time as _t
        now = _t.time()

        if not self._old_mode_fallback:
            if now - self._last_new_message_ts < self.FALLBACK_AFTER_SEC:
                return
            self._activity_without_new_message += 1
            if self._activity_without_new_message < self.FALLBACK_AFTER_EVENTS:
                return
            self._old_mode_fallback = True
            logger.warning(
                f"⚠️ NEW_MESSAGE не приходит уже {int(now - self._last_new_message_ts)}с "
                f"при живой активности в чатах — включаю old-mode fallback: "
                f"сообщения будут читаться из LAST_CHAT_MESSAGE_CHANGED"
            )

        # Дедуп: одно и то же последнее сообщение чата не эмитим дважды
        if self._fallback_last_emitted.get(chat_id) == text:
            return
        self._fallback_last_emitted[chat_id] = text
        await self._emit_message_from_chat(chat)

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
        msg._palto_acc = self.index  # маршрутизация ответа в этот аккаунт

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

    # FunPay не принимает сообщения длиннее 20 строк — режем как Cardinal.
    _MAX_LINES_PER_MESSAGE = 20

    @classmethod
    def _split_message(cls, text: str) -> List[str]:
        """Разбивает текст на куски по 20 строк (лимит FunPay)."""
        lines = str(text).split("\n")
        chunks = []
        while lines:
            chunk = "\n".join(lines[:cls._MAX_LINES_PER_MESSAGE]).rstrip()
            del lines[:cls._MAX_LINES_PER_MESSAGE]
            if chunk:
                chunks.append(chunk)
        return chunks

    def send_message(self, chat_id, text: str, attempts: int = 3) -> bool:
        """
        Отправляет сообщение в FunPay-чат.

        Длинный текст режется на куски по 20 строк (лимит FunPay), каждый
        кусок отправляется с ретраями (attempts попыток). True — только если
        доставлены ВСЕ куски.
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

        for chunk in self._split_message(text):
            if not self._send_chunk(cid, chunk, attempts):
                return False
        return True

    def _send_chunk(self, cid: int, text: str, attempts: int = 3) -> bool:
        """
        Отправляет один кусок (<= 20 строк) с ретраями.

        AttributeError на парсинге ответа FunPay считается успехом —
        сообщение в этом случае ушло, а упал только парсер ответа
        в самом FunPayAPI (ретраить нельзя — будет дубль).
        """
        import time as _t
        last_err = ""
        for attempt in range(attempts):
            if attempt:
                _t.sleep(1)
            try:
                # update_last_saved_message=False — иначе FunPayAPI попытается
                # обновить кэш и упадёт с "'NoneType' object has no attribute 'text'"
                self.account.send_message(cid, text, update_last_saved_message=False)
                self._register_outgoing(cid, text)
                return True

            except AttributeError as e:
                if "'text'" in str(e):
                    self._register_outgoing(cid, text)
                    logger.debug(f"send_message({cid}): отправлено (FunPayAPI парсер упал на ответе)")
                    return True
                last_err = f"AttributeError: {e}"
                logger.debug(f"send_message({cid}): {e} (попытка {attempt + 1}/{attempts})")

            except Exception as e:
                err_str = str(e)
                err_type = type(e).__name__

                # Тихие коды — обычное состояние, не пишем WARNING
                if any(k in err_str for k in (
                    "не удалось получить истории чатов",
                    "Не удалось получить истории чатов",
                    "MessageNotDelivered",
                )):
                    last_err = err_str[:120]
                elif "RequestFailed" in err_type:
                    last_err = f"HTTP ошибка ({err_type})"
                else:
                    last_err = f"{err_type}: {err_str[:200]}"
                logger.debug(f"send_message({cid}): {last_err} (попытка {attempt + 1}/{attempts})")

        logger.warning(f"send_message({cid}): не доставлено после {attempts} попыток: {last_err}")
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


# ──────────────────────────────────────────────────────────────────────────────
# Мультиаккаунт: менеджер клиентов
# ──────────────────────────────────────────────────────────────────────────────

class FunPayAccountsManager:
    """
    Держит по FunPayClient на каждый аккаунт из settings.funpay.accounts.
    Все включённые аккаунты поллятся одновременно; «активный» — тот, с которым
    работают команды (/profile, /balance) и legacy-код через funpay_client.
    """

    def __init__(self) -> None:
        self.clients: List[FunPayClient] = []
        self._default: Optional[FunPayClient] = None  # legacy-режим без accounts

    # ─── Жизненный цикл ─────────────────────────────────────────────────────

    def load_from_config(self) -> None:
        """Пересоздаёт клиентов по списку аккаунтов из конфига."""
        self.stop_all()
        self.clients = []
        for i, acc in enumerate(config_manager.settings.funpay.accounts):
            cl = FunPayClient(acc)
            cl.index = i
            self.clients.append(cl)

    async def start_all(self) -> List[Tuple["FunPayClient", bool, str]]:
        """Подключает и запускает поллинг всех включённых аккаунтов."""
        loop = asyncio.get_event_loop()
        results: List[Tuple[FunPayClient, bool, str]] = []
        for cl in self.enabled_clients():
            if not cl._cfg().golden_key:
                continue
            ok, message = await loop.run_in_executor(None, cl.connect)
            if ok:
                await cl.start_polling()
                logger.info(f"[{cl.alias}] аккаунт подключён и поллится")
            else:
                logger.error(f"[{cl.alias}] FunPay не подключился: {message}")
            results.append((cl, ok, message))
        return results

    def stop_all(self) -> None:
        for cl in self.all():
            cl.stop()

    # ─── Доступ к клиентам ──────────────────────────────────────────────────

    def all(self) -> List[FunPayClient]:
        if self.clients:
            return list(self.clients)
        if self._default is not None:
            return [self._default]
        return []

    def enabled_clients(self) -> List[FunPayClient]:
        return [c for c in self.all() if c.acc_cfg is None or c.acc_cfg.enabled]

    def connected_clients(self) -> List[FunPayClient]:
        return [c for c in self.all() if c.account is not None]

    def active(self) -> FunPayClient:
        """Клиент активного аккаунта. Всегда возвращает объект."""
        if self.clients:
            idx = config_manager.settings.funpay.active_index
            if not (0 <= idx < len(self.clients)):
                idx = 0
            return self.clients[idx]
        if self._default is None:
            self._default = FunPayClient()
        return self._default

    def get(self, index: Any) -> Optional[FunPayClient]:
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return None
        clients = self.all()
        if 0 <= idx < len(clients):
            return clients[idx]
        return None

    def client_for(self, obj: Any) -> FunPayClient:
        """
        Клиент аккаунта, породившего событие (по штампу _palto_acc).
        Fallback — активный аккаунт.
        """
        cl = self.get(getattr(obj, "_palto_acc", None))
        return cl if cl is not None else self.active()

    def tag(self, obj: Any = None, index: Any = None) -> str:
        """Префикс '[алиас] ' для уведомлений — только когда аккаунтов > 1."""
        if len(self.all()) <= 1:
            return ""
        if obj is not None:
            cl = self.client_for(obj)
        else:
            cl = self.get(index) or self.active()
        return f"[{cl.alias}] "

    # ─── Управление списком аккаунтов ───────────────────────────────────────

    def add_account(self, golden_key: str, proxy: str = "",
                    user_agent: str = "") -> Tuple[int, FunPayClient]:
        from config.settings import DEFAULT_USER_AGENT, FunPayAccountSettings
        fp = config_manager.settings.funpay
        acc = FunPayAccountSettings(
            golden_key=golden_key.strip(),
            proxy=proxy,
            user_agent=user_agent or DEFAULT_USER_AGENT,
        )
        fp.accounts.append(acc)
        cl = FunPayClient(acc)
        cl.index = len(fp.accounts) - 1
        self.clients.append(cl)
        if len(fp.accounts) == 1:
            fp.active_index = 0
            fp.sync_legacy_from_active()
        config_manager.save()
        return cl.index, cl

    def remove_account(self, index: int) -> bool:
        fp = config_manager.settings.funpay
        if not (0 <= index < len(fp.accounts)):
            return False
        if index < len(self.clients):
            self.clients[index].stop()
            self.clients.pop(index)
        fp.accounts.pop(index)
        for i, cl in enumerate(self.clients):
            cl.index = i
        if fp.active_index >= len(fp.accounts):
            fp.active_index = max(0, len(fp.accounts) - 1)
        if fp.accounts:
            fp.sync_legacy_from_active()
        else:
            fp.golden_key = ""
            fp.account_id = None
            fp.username = None
        config_manager.save()
        return True

    def set_active(self, index: int) -> bool:
        fp = config_manager.settings.funpay
        if not (0 <= index < len(fp.accounts)):
            return False
        fp.active_index = index
        fp.sync_legacy_from_active()
        config_manager.save()
        return True


accounts_manager = FunPayAccountsManager()
accounts_manager.load_from_config()


class _ActiveClientProxy:
    """
    Обратная совместимость: модуль-синглтон funpay_client теперь указывает
    на клиента АКТИВНОГО аккаунта. Весь legacy-код (`funpay_client.account`,
    `.send_message`, `.get_balance`, ...) продолжает работать без правок.
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(accounts_manager.active(), name)


funpay_client = _ActiveClientProxy()
