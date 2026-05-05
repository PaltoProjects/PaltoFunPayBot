"""
Автоподнятие лотов — в стиле FunPayCardinal.

Логика (как просил пользователь):
1. Бот собирает все КАТЕГОРИИ профиля где есть лоты (пустые пропускает).
2. Для каждой категории пытается поднять.
3. Если FunPay говорит «Подождите N часов M минут» — бот парсит это время
   (FunPayAPI.RaiseError делает это сам) и ставит next_attempt_at = now + N часов.
4. Реактивный цикл (каждые 30 сек) — поднимает категории у которых пришло время.
5. Логи Cardinal-стиля:
     I: Поднимаю лоты категории "GTA 5 Online"...
     I: ✓ Подняты лоты категории "GTA 5 Online"
     W: Не удалось поднять лоты категории "Roblox". FunPay говорит: "Подождите 3 часа.". Следующая попытка через 3ч 0мин.
"""
from __future__ import annotations

import asyncio
import random
import re
import time
from typing import Dict, List, Optional, Tuple

from config.settings import config_manager
from core.event_bus import Event, event_bus
from core.funpay_client import funpay_client
from utils.logger import logger


# ──────────────────────────────────────────────────────────────────────────────
# Структуры
# ──────────────────────────────────────────────────────────────────────────────

class CategoryState:
    """Состояние одной категории — когда её можно поднимать."""
    __slots__ = ("game_id", "category_name", "next_attempt_at", "last_msg")

    def __init__(self, game_id: int, category_name: str):
        self.game_id = game_id
        self.category_name = category_name
        self.next_attempt_at: float = 0.0   # 0 = можно прямо сейчас
        self.last_msg: str = ""

    def can_lift_now(self) -> bool:
        return time.time() >= self.next_attempt_at

    def schedule_after(self, seconds: int, reason: str = "") -> None:
        self.next_attempt_at = time.time() + seconds
        self.last_msg = reason

    def time_until(self) -> int:
        return max(0, int(self.next_attempt_at - time.time()))


def _fmt_time(seconds: int) -> str:
    """Cardinal-стиль: '3ч 25мин' / '45мин' / '30сек'"""
    if seconds < 60:
        return f"{seconds}сек"
    if seconds < 3600:
        return f"{seconds // 60}мин"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if m == 0:
        return f"{h}ч"
    return f"{h}ч {m}мин"


def _fmt_time_ago(seconds: int) -> str:
    """
    Формат как на скрине: '4ч 2сек' / '2мин 30сек' / '15сек'.
    Показываем 2 единицы максимум.
    """
    if seconds < 60:
        return f"{seconds}сек"
    if seconds < 3600:
        m = seconds // 60
        s = seconds % 60
        if s == 0:
            return f"{m}мин"
        return f"{m}мин {s}сек"
    h = seconds // 3600
    rem = seconds % 3600
    # Если есть минуты — показываем минуты, иначе секунды
    m = rem // 60
    s = rem % 60
    if m > 0:
        return f"{h}ч {m}мин"
    if s > 0:
        return f"{h}ч {s}сек"
    return f"{h}ч"


# ──────────────────────────────────────────────────────────────────────────────
# Получение категорий с лотами
# ──────────────────────────────────────────────────────────────────────────────

async def _fetch_categories_with_lots() -> List[Tuple[int, str]]:
    """
    Возвращает [(category_id (game), name)] — категории игр у которых есть лоты.

    Работает как в FunPayCardinal:
        for subcat in profile.get_sorted_lots(2).keys():
            ... subcat.category.id, subcat.category.name ...

    get_sorted_lots(2) возвращает {SubCategory: {lot_id: LotShortcut}} —
    то есть в ключах уже только подкатегории где есть лоты.
    """
    if not funpay_client.account:
        return []
    loop = asyncio.get_event_loop()
    try:
        profile = await loop.run_in_executor(
            None, funpay_client.account.get_user, funpay_client.account.id
        )
    except Exception as e:
        logger.warning(f"Не удалось получить профиль: {e}")
        return []

    try:
        # ВАЖНО: get_sorted_lots(2) → {SubCategory: {lot_id: LotShortcut}}
        # Ключи — объекты SubCategory, не int. Cardinal делает: .keys()
        sorted_lots = profile.get_sorted_lots(2)
    except Exception as e:
        logger.warning(f"Не удалось получить лоты профиля: {e}")
        return []

    # Собираем уникальные категории (subcat.category) только обычного типа
    try:
        from FunPayAPI.common.enums import SubCategoryTypes
    except ImportError:
        SubCategoryTypes = None

    seen_category_ids = set()
    result: List[Tuple[int, str]] = []
    for subcat in sorted_lots.keys():
        # Пропускаем валюту (нельзя поднимать)
        if SubCategoryTypes and getattr(subcat, "type", None) == SubCategoryTypes.CURRENCY:
            continue

        category = getattr(subcat, "category", None)
        if not category:
            continue

        cat_id = getattr(category, "id", None)
        cat_name = getattr(category, "name", None)
        if not cat_id or cat_id in seen_category_ids:
            continue
        seen_category_ids.add(cat_id)

        try:
            cid = int(cat_id)
        except (TypeError, ValueError):
            continue

        result.append((cid, cat_name or f"Категория #{cid}"))

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Менеджер автоподнятия
# ──────────────────────────────────────────────────────────────────────────────

class AutoLift:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        # game_id → CategoryState
        self._states: Dict[int, CategoryState] = {}
        self._categories_loaded_at: float = 0.0
        self._first_run_logged = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Цикл автоподнятия лотов запущен (это не значит, что автоподнятие лотов включено).")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    # ─── Обновление списка категорий ────────────────────────────────────────

    async def _refresh_categories(self) -> None:
        """Раз в час обновляем список категорий с лотами."""
        cats = await _fetch_categories_with_lots()
        seen_ids = set()
        for gid, name in cats:
            seen_ids.add(gid)
            if gid not in self._states:
                self._states[gid] = CategoryState(gid, name)
            else:
                self._states[gid].category_name = name

        # Удаляем категории которых больше нет
        for gid in list(self._states.keys()):
            if gid not in seen_ids:
                del self._states[gid]

        self._categories_loaded_at = time.time()

        if not self._first_run_logged:
            self._first_run_logged = True
            logger.info(f"Найдено категорий с лотами для поднятия: {len(self._states)}.")

    # ─── Поднятие одной категории ───────────────────────────────────────────

    async def _try_lift_one(self, st: CategoryState) -> bool:
        """
        Пытается поднять одну категорию. Возвращает True при успехе.

        Логика Cardinal (двойной raise):
          1. Первый raise_lots → реально поднимает лоты.
          2. Второй raise_lots → сразу получает RaiseError с точным wait_time от FunPay.
             Это позволяет узнать ТОЧНЫЙ кулдаун вместо фиксированных "4 часа".
        """
        loop = asyncio.get_event_loop()
        raise_ok = False

        try:
            # ─ Первый вызов: реальное поднятие ──────────────────────────────
            await loop.run_in_executor(
                None, funpay_client.account.raise_lots, st.game_id,
            )
            raise_ok = True

            # ─ Второй вызов: получаем точный wait_time от FunPay ────────────
            # FunPay сразу вернёт RaiseError с полем wait_time — это штатно.
            # Без этого вызова мы не знаем точный кулдаун и вынуждены угадывать.
            # Cardinal делает именно так (см. cardinal.py raise_lots()).
            await asyncio.sleep(1)
            await loop.run_in_executor(
                None, funpay_client.account.raise_lots, st.game_id,
            )
            # Если второй вызов почему-то тоже прошёл — ставим 4ч по умолчанию
            logger.info(f'✓ Подняты лоты категории "{st.category_name}"')
            st.schedule_after(4 * 3600, reason="успех")

        except Exception as e:
            err_type = type(e).__name__

            # RaiseError — штатный ответ FunPay с точным временем кулдауна
            if err_type == "RaiseError" or "RaiseError" in err_type:
                wait = getattr(e, "wait_time", None)
                msg = getattr(e, "error_message", None) or str(e)

                if wait and wait > 0:
                    st.schedule_after(wait, reason=msg)
                    if raise_ok:
                        # Первый поднял успешно, второй дал точное время — это норма
                        logger.info(
                            f'✓ Подняты лоты категории "{st.category_name}". '
                            f'Следующая попытка через {_fmt_time(wait)}.'
                        )
                    else:
                        logger.warning(
                            f'Не удалось поднять лоты категории "{st.category_name}". '
                            f'FunPay говорит: "{msg}". '
                            f'Следующая попытка через {_fmt_time(wait)}.'
                        )
                else:
                    # wait_time не распарсился — ставим час
                    st.schedule_after(3600, reason=msg)
                    if not raise_ok:
                        logger.warning(
                            f'Не удалось поднять лоты категории "{st.category_name}": {msg}. '
                            f'Повтор через 1ч.'
                        )
                return raise_ok

            # HTTP 503/403/429 — временная ошибка FunPay, короткий ретрай
            err_str = str(e)
            status_code = getattr(e, "status_code", None)
            if status_code in (503, 403, 429) or "RequestFailed" in err_type:
                retry_sec = 60 if status_code in (503, 403, 429) else 30 * 60
                st.schedule_after(retry_sec, reason=err_str[:100])
                logger.warning(
                    f'HTTP {status_code or "?"} при поднятии "{st.category_name}". '
                    f'Повтор через {_fmt_time(retry_sec)}.'
                )
                return False

            # Любая другая ошибка — 30 минут
            st.schedule_after(30 * 60, reason=err_str[:200])
            logger.warning(f'Ошибка при поднятии "{st.category_name}": {err_type}: {err_str[:120]}')
            return False

        return raise_ok

    # ─── Главный цикл ───────────────────────────────────────────────────────

    async def _loop(self) -> None:
        # Первая загрузка категорий через 10 сек после старта
        await asyncio.sleep(10)

        while self._running:
            try:
                cfg = config_manager.settings.auto_lift
                if cfg.enabled and funpay_client.account:
                    await self._tick()
            except Exception as e:
                logger.exception(f"AutoLift loop: {e}")

            # Тик каждые 30 секунд — реактивно
            await asyncio.sleep(30)

    async def _tick(self) -> None:
        # Раз в час обновляем список категорий
        if not self._states or (time.time() - self._categories_loaded_at) > 3600:
            await self._refresh_categories()

        if not self._states:
            return

        # Кого поднимать сейчас?
        ready = [st for st in self._states.values() if st.can_lift_now()]
        if not ready:
            return

        antiban = config_manager.settings.antiban
        raised_now: List[Tuple[str, float]] = []  # (имя, timestamp поднятия)

        for st in ready:
            ok = await self._try_lift_one(st)
            if ok:
                raised_now.append((st.category_name, time.time()))

            # Антибан-задержка между запросами
            if antiban.human_like_delays:
                delay = random.uniform(
                    antiban.min_delay_ms / 1000,
                    antiban.max_delay_ms / 1000,
                )
                await asyncio.sleep(delay)

        # Если за этот тик что-то подняли — сразу шлём отчёт в TG
        if raised_now:
            await self._send_report(raised_now)

    # ─── Отчёт в TG ─────────────────────────────────────────────────────────

    async def _send_report(self, raised_now: List[Tuple[str, float]]) -> None:
        """
        Шлёт отчёт в стиле:
            ✅ Успешно поднято категорий: 13

            Подробнее:
            1. Alan Wake 2 (Last raised: 4ч 2сек ago.)
            2. Albion Online (Last raised: 4ч 2сек ago.)
            ...
        """
        lines = [f"✅ <b>Успешно поднято категорий:</b> {len(raised_now)}\n"]
        lines.append("<b>Подробнее:</b>")
        for i, (name, ts) in enumerate(raised_now[:30], 1):
            ago = max(0, int(time.time() - ts))
            lines.append(f"{i}. {name} (Last raised: {_fmt_time_ago(ago)} ago.)")
        if len(raised_now) > 30:
            lines.append(f"... и ещё {len(raised_now) - 30}")

        await event_bus.emit(Event.LOT_LIFTED, "\n".join(lines))


# ──────────────────────────────────────────────────────────────────────────────
# Однократное поднятие — для команды «🚀 Поднять сейчас» (если есть в меню)
# ──────────────────────────────────────────────────────────────────────────────

async def lift_all_lots() -> str:
    """Однократно пытается поднять всё что готово. Возвращает текст-отчёт."""
    if not funpay_client.account:
        return "Клиент FunPay не подключён."

    cats = await _fetch_categories_with_lots()
    if not cats:
        return "Категорий с лотами не найдено."

    raised = 0
    skipped = 0
    errors = 0
    antiban = config_manager.settings.antiban
    loop = asyncio.get_event_loop()

    for gid, name in cats:
        try:
            await loop.run_in_executor(
                None, funpay_client.account.raise_lots, gid
            )
            raised += 1
            logger.info(f'✓ Подняты лоты категории "{name}"')
        except Exception as e:
            err_type = type(e).__name__
            if "RaiseError" in err_type:
                skipped += 1
            else:
                errors += 1
                logger.warning(f'Ошибка для "{name}": {e}')

        if antiban.human_like_delays:
            delay = random.uniform(antiban.min_delay_ms / 1000, antiban.max_delay_ms / 1000)
            await asyncio.sleep(delay)

    return f"Поднято: {raised}, в кулдауне: {skipped}, ошибок: {errors}"


auto_lift = AutoLift()
