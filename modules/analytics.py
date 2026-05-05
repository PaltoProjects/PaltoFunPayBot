"""
Аналитика продаж.

Что внутри:
- SQLite-хранилище заказов (data/analytics.db)
- Подписка на события new_order/order_paid/order_confirmed/order_refunded
- Функции отчётов:
    revenue_chart()           — график доходов (день/неделя/месяц)
    top_products(N)           — топ-N товаров по выручке
    conversion()              — конверсия (продажи / уникальные чаты)
    average_check()           — средний чек + средний рейтинг
    forecast()                — прогноз дохода на след. период
    compare_periods()         — сравнение с прошлым периодом
    peak_hours()              — часы пик продаж
- Генерация PNG-графиков через matplotlib

⚠️ matplotlib подгружается лениво — если не установлен, всё текстовое продолжает работать.
"""
from __future__ import annotations

import asyncio
import io
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import config_manager
from core.event_bus import Event, event_bus
from utils.logger import logger

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "analytics.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# Схема БД
# ──────────────────────────────────────────────────────────────────────────────

def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id      TEXT PRIMARY KEY,
                created_ts    REAL NOT NULL,
                paid_ts       REAL,
                confirmed_ts  REAL,
                refunded_ts   REAL,
                status        TEXT,
                amount_rub    REAL DEFAULT 0,
                title         TEXT,
                buyer_id      TEXT,
                buyer_name    TEXT,
                lot_id        TEXT,
                category      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_ts);
            CREATE INDEX IF NOT EXISTS idx_orders_paid    ON orders(paid_ts);

            CREATE TABLE IF NOT EXISTS chats (
                buyer_id   TEXT PRIMARY KEY,
                first_seen REAL NOT NULL,
                last_seen  REAL NOT NULL,
                msg_count  INTEGER DEFAULT 0
            );
        """)


_init_db()


# ──────────────────────────────────────────────────────────────────────────────
# Извлечение полей из объекта order (универсально под FunPayAPI)
# ──────────────────────────────────────────────────────────────────────────────

def _extract_order_fields(order) -> Dict[str, Any]:
    """Достаёт нужные поля из объекта order, какой бы он ни был."""
    def _g(*names, default=None):
        for n in names:
            v = getattr(order, n, None)
            if v is not None:
                return v
        return default

    # Сумма — пытаемся достать число из price (может быть строкой "100.00 ₽")
    raw_price = _g("price", "amount", "total", default=0)
    amount = 0.0
    if isinstance(raw_price, (int, float)):
        amount = float(raw_price)
    elif isinstance(raw_price, str):
        # Берём первое число из строки
        import re
        m = re.search(r"[\d.,]+", raw_price.replace(",", "."))
        if m:
            try:
                amount = float(m.group())
            except ValueError:
                amount = 0.0

    return {
        "order_id":   str(_g("id", "order_id", default=f"unknown_{int(time.time())}")),
        "amount_rub": amount,
        "title":      str(_g("description", "title", "name", default=""))[:200],
        "buyer_id":   str(_g("buyer_id", "buyer", default="")),
        "buyer_name": str(_g("buyer_username", "buyer", default="")),
        "lot_id":     str(_g("lot_id", "subcategory_id", "offer_id", default="")),
        "category":   str(_g("category", "subcategory", default="")),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Подписки на события заказов
# ──────────────────────────────────────────────────────────────────────────────

@event_bus.on(Event.NEW_ORDER)
async def _on_new_order(order) -> None:
    if not config_manager.settings.analytics.enabled:
        return
    f = _extract_order_fields(order)
    try:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("""
                INSERT OR REPLACE INTO orders (
                    order_id, created_ts, status, amount_rub,
                    title, buyer_id, buyer_name, lot_id, category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f["order_id"], time.time(), "new", f["amount_rub"],
                f["title"], f["buyer_id"], f["buyer_name"], f["lot_id"], f["category"]
            ))
        logger.debug(f"Analytics: новый заказ #{f['order_id']} ({f['amount_rub']}₽)")
    except Exception as e:
        logger.warning(f"Analytics on_new_order: {e}")


@event_bus.on(Event.ORDER_PAID)
async def _on_paid(order) -> None:
    f = _extract_order_fields(order)
    try:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("""
                UPDATE orders SET paid_ts = ?, status = 'paid', amount_rub = ?
                WHERE order_id = ?
            """, (time.time(), f["amount_rub"], f["order_id"]))
            # Если NEW_ORDER не пришёл — INSERT
            if con.total_changes == 0:
                con.execute("""
                    INSERT OR IGNORE INTO orders (
                        order_id, created_ts, paid_ts, status, amount_rub,
                        title, buyer_id, buyer_name, lot_id
                    ) VALUES (?, ?, ?, 'paid', ?, ?, ?, ?, ?)
                """, (
                    f["order_id"], time.time(), time.time(), f["amount_rub"],
                    f["title"], f["buyer_id"], f["buyer_name"], f["lot_id"]
                ))
    except Exception as e:
        logger.warning(f"Analytics on_paid: {e}")


@event_bus.on(Event.ORDER_CONFIRMED)
async def _on_confirmed(order) -> None:
    f = _extract_order_fields(order)
    try:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("""
                UPDATE orders SET confirmed_ts = ?, status = 'confirmed'
                WHERE order_id = ?
            """, (time.time(), f["order_id"]))
    except Exception as e:
        logger.warning(f"Analytics on_confirmed: {e}")


@event_bus.on(Event.ORDER_REFUNDED)
async def _on_refunded(order) -> None:
    f = _extract_order_fields(order)
    try:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("""
                UPDATE orders SET refunded_ts = ?, status = 'refunded'
                WHERE order_id = ?
            """, (time.time(), f["order_id"]))
    except Exception as e:
        logger.warning(f"Analytics on_refunded: {e}")


@event_bus.on(Event.NEW_MESSAGE)
async def _on_message(message) -> None:
    """Записываем покупателя как уникальный чат (для конверсии)."""
    if not config_manager.settings.analytics.enabled:
        return
    own_id = config_manager.settings.funpay.account_id
    author_id = getattr(message, "author_id", None)
    if not author_id or author_id == own_id or author_id == 0:
        return
    try:
        with sqlite3.connect(DB_PATH) as con:
            now = time.time()
            con.execute("""
                INSERT INTO chats (buyer_id, first_seen, last_seen, msg_count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(buyer_id) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    msg_count = msg_count + 1
            """, (str(author_id), now, now))
    except Exception as e:
        logger.debug(f"Analytics on_message: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Функции отчётов
# ──────────────────────────────────────────────────────────────────────────────

def _period_bounds(period: str) -> Tuple[float, float]:
    """Возвращает (start_ts, end_ts) для названия периода."""
    now = datetime.now()
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.timestamp(), now.timestamp()
    if period == "week":
        return (now - timedelta(days=7)).timestamp(), now.timestamp()
    if period == "month":
        return (now - timedelta(days=30)).timestamp(), now.timestamp()
    if period == "year":
        return (now - timedelta(days=365)).timestamp(), now.timestamp()
    # all
    return 0.0, now.timestamp()


def revenue_summary(period: str = "month") -> Dict[str, Any]:
    """Сводка по выручке за период: total, count, avg, по дням."""
    start, end = _period_bounds(period)
    with sqlite3.connect(DB_PATH) as con:
        # Всего: сумма paid_ts заказов в периоде
        rows = con.execute("""
            SELECT COUNT(*), COALESCE(SUM(amount_rub), 0), COALESCE(AVG(amount_rub), 0)
            FROM orders
            WHERE paid_ts BETWEEN ? AND ? AND status != 'refunded'
        """, (start, end)).fetchone()
        count, total, avg = rows[0] or 0, float(rows[1] or 0), float(rows[2] or 0)

        # Группировка по дням
        daily = con.execute("""
            SELECT DATE(paid_ts, 'unixepoch', 'localtime') AS d,
                   SUM(amount_rub) AS s,
                   COUNT(*) AS c
            FROM orders
            WHERE paid_ts BETWEEN ? AND ? AND status != 'refunded'
            GROUP BY d
            ORDER BY d
        """, (start, end)).fetchall()
    return {
        "period": period,
        "count": count,
        "total": total,
        "avg": avg,
        "daily": [(d, float(s), c) for d, s, c in daily],
    }


def top_products(n: int = 10, period: str = "month") -> List[Dict[str, Any]]:
    """Топ-N товаров по выручке."""
    start, end = _period_bounds(period)
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute("""
            SELECT title, COUNT(*) AS sales, SUM(amount_rub) AS total
            FROM orders
            WHERE paid_ts BETWEEN ? AND ? AND status != 'refunded' AND title != ''
            GROUP BY title
            ORDER BY total DESC
            LIMIT ?
        """, (start, end, n)).fetchall()
    return [{"title": t, "sales": s, "total": float(tot)} for t, s, tot in rows]


def conversion_rate(period: str = "month") -> Dict[str, Any]:
    """Конверсия = заказы / уникальные чаты."""
    start, end = _period_bounds(period)
    with sqlite3.connect(DB_PATH) as con:
        chats = con.execute("""
            SELECT COUNT(DISTINCT buyer_id) FROM chats
            WHERE first_seen BETWEEN ? AND ?
        """, (start, end)).fetchone()[0] or 0

        buyers = con.execute("""
            SELECT COUNT(DISTINCT buyer_id) FROM orders
            WHERE paid_ts BETWEEN ? AND ? AND status != 'refunded'
        """, (start, end)).fetchone()[0] or 0
    rate = (buyers / chats * 100) if chats else 0
    return {"chats": chats, "buyers": buyers, "rate": rate}


def average_check(period: str = "month") -> Dict[str, Any]:
    """Средний чек."""
    s = revenue_summary(period)
    return {"avg": s["avg"], "count": s["count"], "total": s["total"]}


def forecast(period: str = "month") -> Dict[str, Any]:
    """Прогноз дохода — линейная экстраполяция от среднего за день."""
    summary = revenue_summary(period)
    daily = summary["daily"]
    if not daily:
        return {"forecast": 0, "based_on_days": 0}
    total = sum(s for _, s, _ in daily)
    days = len(daily)
    avg_per_day = total / days if days else 0

    horizon = {"today": 1, "week": 7, "month": 30, "year": 365, "all": 30}[period]
    return {
        "forecast": avg_per_day * horizon,
        "based_on_days": days,
        "avg_per_day": avg_per_day,
    }


def compare_periods(period: str = "month") -> Dict[str, Any]:
    """Сравнение текущего периода с прошлым."""
    now = datetime.now()
    if period == "week":
        delta = timedelta(days=7)
    elif period == "month":
        delta = timedelta(days=30)
    elif period == "year":
        delta = timedelta(days=365)
    else:
        delta = timedelta(days=30)

    cur_start = (now - delta).timestamp()
    prev_start = (now - delta * 2).timestamp()
    prev_end = cur_start
    cur_end = now.timestamp()

    with sqlite3.connect(DB_PATH) as con:
        def _stat(s, e):
            r = con.execute("""
                SELECT COUNT(*), COALESCE(SUM(amount_rub), 0)
                FROM orders
                WHERE paid_ts BETWEEN ? AND ? AND status != 'refunded'
            """, (s, e)).fetchone()
            return int(r[0] or 0), float(r[1] or 0)

        cur_count, cur_total = _stat(cur_start, cur_end)
        prev_count, prev_total = _stat(prev_start, prev_end)

    pct_count = ((cur_count - prev_count) / prev_count * 100) if prev_count else 0
    pct_total = ((cur_total - prev_total) / prev_total * 100) if prev_total else 0
    return {
        "current": {"count": cur_count, "total": cur_total},
        "previous": {"count": prev_count, "total": prev_total},
        "diff_count_pct": pct_count,
        "diff_total_pct": pct_total,
    }


def peak_hours() -> List[Tuple[int, int]]:
    """Возвращает [(hour, count)] — сколько заказов в каждый час суток."""
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute("""
            SELECT CAST(strftime('%H', paid_ts, 'unixepoch', 'localtime') AS INTEGER) AS h,
                   COUNT(*)
            FROM orders
            WHERE paid_ts IS NOT NULL AND status != 'refunded'
            GROUP BY h
            ORDER BY h
        """).fetchall()
    by_hour = {h: c for h, c in rows}
    return [(h, by_hour.get(h, 0)) for h in range(24)]


# ──────────────────────────────────────────────────────────────────────────────
# Генерация графиков (matplotlib)
# ──────────────────────────────────────────────────────────────────────────────

def _has_matplotlib() -> bool:
    try:
        import matplotlib  # noqa
        return True
    except ImportError:
        return False


def chart_revenue(period: str = "month") -> Optional[bytes]:
    """График доходов по дням. Возвращает PNG как байты."""
    if not _has_matplotlib():
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    summary = revenue_summary(period)
    daily = summary["daily"]
    if not daily:
        return None

    dates = [d for d, _, _ in daily]
    sums = [s for _, s, _ in daily]

    fig, ax = plt.subplots(figsize=(10, 5), dpi=100)
    ax.bar(dates, sums, color="#4CAF50", edgecolor="#2E7D32")
    ax.set_title(f"Доходы за {period}", fontsize=14, fontweight="bold")
    ax.set_xlabel("Дата")
    ax.set_ylabel("Сумма, ₽")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def chart_top_products(n: int = 10, period: str = "month") -> Optional[bytes]:
    """Горизонтальный bar chart топ-N товаров."""
    if not _has_matplotlib():
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    top = top_products(n, period)
    if not top:
        return None
    titles = [t["title"][:35] for t in reversed(top)]
    totals = [t["total"] for t in reversed(top)]

    fig, ax = plt.subplots(figsize=(10, max(4, len(top) * 0.5)), dpi=100)
    ax.barh(titles, totals, color="#2196F3", edgecolor="#1565C0")
    ax.set_title(f"Топ-{n} товаров за {period}", fontsize=14, fontweight="bold")
    ax.set_xlabel("Выручка, ₽")
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def chart_peak_hours() -> Optional[bytes]:
    """Bar chart распределения заказов по часам."""
    if not _has_matplotlib():
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = peak_hours()
    hours = [f"{h:02d}" for h, _ in data]
    counts = [c for _, c in data]
    if sum(counts) == 0:
        return None

    fig, ax = plt.subplots(figsize=(10, 4), dpi=100)
    ax.bar(hours, counts, color="#FF9800", edgecolor="#E65100")
    ax.set_title("Часы пик продаж", fontsize=14, fontweight="bold")
    ax.set_xlabel("Час дня")
    ax.set_ylabel("Заказов")
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────────
# Полный отчёт (текст для Telegram)
# ──────────────────────────────────────────────────────────────────────────────

def full_text_report(period: str = "month") -> str:
    """Текстовый отчёт со всеми метриками сразу."""
    period_label = {"today": "сегодня", "week": "неделю", "month": "месяц",
                    "year": "год", "all": "всё время"}.get(period, period)

    rev = revenue_summary(period)
    conv = conversion_rate(period)
    fc = forecast(period)
    cmp = compare_periods(period)

    lines = [f"📊 <b>Аналитика за {period_label}</b>\n"]

    # Доходы
    lines.append(f"💰 <b>Выручка:</b> {rev['total']:.2f}₽")
    lines.append(f"📦 Заказов: {rev['count']}")
    lines.append(f"💵 Средний чек: {rev['avg']:.2f}₽")

    # Сравнение
    if cmp["previous"]["total"] > 0:
        sign = "📈" if cmp["diff_total_pct"] >= 0 else "📉"
        lines.append(
            f"\n{sign} <b>vs прошлый период:</b> "
            f"{cmp['diff_total_pct']:+.1f}% по выручке, "
            f"{cmp['diff_count_pct']:+.1f}% по заказам"
        )

    # Конверсия
    if conv["chats"] > 0:
        lines.append(f"\n👥 Уникальных чатов: {conv['chats']}")
        lines.append(f"🛒 Купили: {conv['buyers']}")
        lines.append(f"🎯 Конверсия: <b>{conv['rate']:.1f}%</b>")

    # Прогноз
    if fc["based_on_days"] > 0:
        lines.append(f"\n🔮 <b>Прогноз на следующий период:</b> ~{fc['forecast']:.0f}₽")
        lines.append(f"<i>(на основе среднего {fc['avg_per_day']:.0f}₽/день)</i>")

    # Топ-5 товаров
    top = top_products(5, period)
    if top:
        lines.append(f"\n🏆 <b>Топ-5 товаров:</b>")
        for i, t in enumerate(top, 1):
            lines.append(f"  {i}. {t['title'][:40]} — {t['total']:.0f}₽ ({t['sales']} прод.)")

    # Часы пик
    ph = peak_hours()
    if any(c > 0 for _, c in ph):
        peak = max(ph, key=lambda x: x[1])
        lines.append(f"\n⏰ <b>Час пик:</b> {peak[0]:02d}:00 — {peak[1]} заказов")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Экспорт в CSV
# ──────────────────────────────────────────────────────────────────────────────

def export_csv() -> str:
    """Экспортирует все заказы в CSV-файл. Возвращает путь к файлу."""
    import csv
    out_path = Path(__file__).resolve().parent.parent / "data" / "analytics_export.csv"
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute("""
            SELECT order_id, datetime(created_ts, 'unixepoch', 'localtime') AS created,
                   datetime(paid_ts, 'unixepoch', 'localtime') AS paid,
                   status, amount_rub, title, buyer_name, lot_id, category
            FROM orders ORDER BY created_ts DESC
        """).fetchall()

    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["order_id", "created", "paid", "status", "amount_rub",
                    "title", "buyer", "lot_id", "category"])
        w.writerows(rows)
    return str(out_path)
