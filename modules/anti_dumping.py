"""
Авто-снятие демпинга (фича №41).

Раз в `interval_minutes` обходит все наши активные лоты:
1. Для каждого лота смотрит цену в категории через FunPayAPI.
2. Если **минимальная** цена другого продавца в категории ниже нашей более чем на
   `threshold_percent` процентов — это явный демпинг → деактивируем наш лот.
3. Шлём уведомление в Telegram (если включено).

Логика: лучше дождаться, пока демпер уйдёт/повысит цены, чем продавать в убыток.

⚠️ Реализация максимально консервативна — если что-то не получилось распарсить
с FunPay, бот просто пропускает лот, ничего не делая.
"""
from __future__ import annotations

import asyncio
from typing import Optional, Tuple

from config.settings import config_manager
from core.event_bus import event_bus
from core.funpay_client import funpay_client
from utils.audit_log import audit
from utils.logger import logger


def _safe_float(v) -> Optional[float]:
    """Аккуратно вытаскиваем число из строки/числа цены FunPay."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        import re
        m = re.search(r"[\d.,]+", v.replace(",", "."))
        if m:
            try:
                return float(m.group())
            except ValueError:
                return None
    return None


async def _get_my_lots() -> list:
    """
    Возвращает список (lot_id, lot_obj, subcat_id) всех наших активных лотов.

    profile.get_sorted_lots(2) → {SubCategory: {lot_id: LotShortcut}}
    Ключи — объекты SubCategory (не int!), значения — dict лотов.
    Аналогично auto_lift._fetch_categories_with_lots().
    """
    if not funpay_client.account:
        return []
    loop = asyncio.get_event_loop()
    try:
        profile = await loop.run_in_executor(
            None, funpay_client.account.get_user, funpay_client.account.id
        )
        # get_sorted_lots(2) → {SubCategory: {lot_id: LotShortcut}}
        sorted_lots = profile.get_sorted_lots(2) or {}
        out = []
        for subcat, lots_dict in sorted_lots.items():
            subcat_id = getattr(subcat, "id", None)
            if not subcat_id:
                continue
            for lot_id, lot in lots_dict.items():
                # LotShortcut.active может отсутствовать — считаем активным по умолчанию
                if getattr(lot, "active", True) is False:
                    continue
                out.append((int(lot_id), lot, int(subcat_id)))
        return out
    except Exception as e:
        logger.warning(f"AntiDumping: не удалось получить мои лоты: {e}")
        return []


async def _get_min_competitor_price(subcat_id: int, my_lot_id: int) -> Optional[float]:
    """
    Возвращает минимальную цену конкурента в подкатегории, исключая наш лот.

    Правильный вызов: account.get_subcategory_public_lots(SubCategoryTypes.COMMON, subcat_id)
    Первый аргумент — тип подкатегории (enum), второй — числовой ID.
    """
    if not funpay_client.account:
        return None
    loop = asyncio.get_event_loop()
    try:
        from FunPayAPI.common.enums import SubCategoryTypes
        all_lots = await loop.run_in_executor(
            None,
            funpay_client.account.get_subcategory_public_lots,
            SubCategoryTypes.COMMON,
            subcat_id,
        )
        if not all_lots:
            return None

        prices = []
        for lot in all_lots:
            lid = getattr(lot, "id", None)
            if lid == my_lot_id:
                continue
            p = _safe_float(getattr(lot, "price", None))
            if p and p > 0:
                prices.append(p)
        return min(prices) if prices else None
    except Exception as e:
        logger.debug(f"get_min_competitor_price({subcat_id}): {e}")
        return None


async def _deactivate_lot(lot_id: int) -> bool:
    """
    Деактивирует лот через FunPayAPI.

    Правильный паттерн (из auto_deactivation.py, который работает):
      lot_fields = account.get_lot_fields(lot_id)
      lot_fields.active = False
      account.save_lot(lot_fields)
    """
    if not funpay_client.account:
        return False
    loop = asyncio.get_event_loop()
    try:
        lot_fields = await loop.run_in_executor(
            None, funpay_client.account.get_lot_fields, lot_id
        )
        lot_fields.active = False
        await loop.run_in_executor(
            None, funpay_client.account.save_lot, lot_fields
        )
        return True
    except Exception as e:
        logger.warning(f"AntiDumping deactivate({lot_id}): {e}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Фоновый цикл
# ──────────────────────────────────────────────────────────────────────────────

class AntiDumping:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("AntiDumping запущен.")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while self._running:
            try:
                cfg = config_manager.settings.anti_dumping
                if cfg.enabled and funpay_client.account:
                    await self._tick()
            except Exception as e:
                logger.exception(f"AntiDumping tick error: {e}")
            await asyncio.sleep(max(60, config_manager.settings.anti_dumping.interval_minutes * 60))

    async def _tick(self) -> None:
        cfg = config_manager.settings.anti_dumping
        my_lots = await _get_my_lots()
        if not my_lots:
            return

        deactivated = []
        for lot_id, lot, subcat_id in my_lots:
            my_price = _safe_float(getattr(lot, "price", None))
            if not my_price or my_price <= 0:
                continue

            min_competitor = await _get_min_competitor_price(subcat_id, lot_id)
            if not min_competitor:
                continue

            # Насколько ниже наша цена?
            diff_pct = ((my_price - min_competitor) / my_price) * 100
            if diff_pct < cfg.threshold_percent:
                continue  # демпинга нет

            # Демпинг! Деактивируем
            ok = await _deactivate_lot(lot_id)
            if ok:
                deactivated.append({
                    "lot_id": lot_id,
                    "title": getattr(lot, "title", "") or getattr(lot, "description", ""),
                    "my_price": my_price,
                    "competitor_price": min_competitor,
                    "diff_pct": diff_pct,
                })
                audit(
                    user_id=0,
                    action="ANTI_DUMPING_DEACTIVATE",
                    data=f"lot={lot_id}",
                    text=f"my={my_price} comp={min_competitor} diff={diff_pct:.1f}%",
                )

        if deactivated and cfg.notify:
            await self._send_report(deactivated)

    async def _send_report(self, items: list) -> None:
        lines = [f"⚠️ <b>Авто-снятие демпинга</b>\n"]
        lines.append(f"Снято с публикации лотов: <b>{len(items)}</b>\n")
        for it in items[:10]:
            title = (it["title"] or f"#{it['lot_id']}")[:50]
            lines.append(
                f"• {title}\n"
                f"  Наша: <b>{it['my_price']:.0f}₽</b> · "
                f"Минимум у конкурентов: <b>{it['competitor_price']:.0f}₽</b> "
                f"(дешевле на {it['diff_pct']:.0f}%)"
            )
        if len(items) > 10:
            lines.append(f"\n... и ещё {len(items) - 10}")

        from modules.notifications import _send_to_all
        await _send_to_all("\n".join(lines))


anti_dumping = AntiDumping()
