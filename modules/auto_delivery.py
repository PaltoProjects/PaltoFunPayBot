"""
Автовыдача товаров — расширенная версия (фичи №5-10).

Типы выдачи:
1. **static** — один и тот же текст для всех (как было раньше)
2. **file** — txt-файл, строки расходуются по одной на заказ
3. **csv** — CSV с колонками key,instruction (или другими); первая строка — ключ
4. **random** — случайный выбор из списка фраз/ключей (без расхода)
5. **combined** — уникальный ключ (расходуется) + общая инструкция

Поддерживается:
- **Мульти-выдача**: при покупке N штук → N ключей
- **Отложенная выдача**: задержка X секунд перед отправкой (имитация ручной работы)
- **Уведомление о малом остатке**: если в file/csv осталось < threshold ключей — пинг в TG

При оплате:
1. Ищем lot_id среди настроенных
2. Достаём контент по типу
3. Если delay > 0 — ждём
4. Шлём покупателю
5. Логируем + проверяем остаток
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config.settings import config_manager
from core.event_bus import Event, event_bus
from core.funpay_client import funpay_client
from utils.audit_log import audit
from utils.logger import logger

DELIVERY_FILES_DIR = Path(__file__).resolve().parent.parent / "data" / "delivery_files"
DELIVERY_FILES_DIR.mkdir(parents=True, exist_ok=True)

# Файл для отслеживания "уже предупредил о малом остатке"
LOW_STOCK_NOTIFIED_PATH = Path(__file__).resolve().parent.parent / "data" / "low_stock_notified.json"


def _file_path(lot_id: str, ext: str = "txt") -> Path:
    return DELIVERY_FILES_DIR / f"lot_{lot_id}.{ext}"


def _load_notified() -> set:
    if not LOW_STOCK_NOTIFIED_PATH.exists():
        return set()
    try:
        return set(json.loads(LOW_STOCK_NOTIFIED_PATH.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_notified(notified: set) -> None:
    LOW_STOCK_NOTIFIED_PATH.write_text(
        json.dumps(list(notified)), encoding="utf-8"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Конфигурация лота
# ──────────────────────────────────────────────────────────────────────────────

def add_lot_static(lot_id: str, text: str, *, delay_sec: int = 0) -> None:
    """Тип STATIC — каждому одно и то же."""
    config_manager.settings.auto_delivery.lots[lot_id] = {
        "type": "static",
        "content": text,
        "delay_sec": delay_sec,
    }
    config_manager.save()


def add_lot_file(lot_id: str, lines: List[str], *, delay_sec: int = 0,
                 low_stock_threshold: int = 5) -> None:
    """Тип FILE — txt-файл, строки расходуются."""
    path = _file_path(lot_id, "txt")
    cleaned = [ln.strip() for ln in lines if ln.strip()]
    path.write_text("\n".join(cleaned), encoding="utf-8")
    config_manager.settings.auto_delivery.lots[lot_id] = {
        "type": "file",
        "content": str(path),
        "delay_sec": delay_sec,
        "low_stock_threshold": low_stock_threshold,
    }
    # Сбрасываем флаг "уже предупредил" чтобы получить уведомление снова
    notified = _load_notified()
    notified.discard(str(lot_id))
    _save_notified(notified)
    config_manager.save()


def add_lot_csv(lot_id: str, csv_text: str, *, template: str = "",
                delay_sec: int = 0, low_stock_threshold: int = 5) -> int:
    """
    Тип CSV — каждая строка вида: key,instruction (или другие колонки).

    template — шаблон выдачи с подстановками {col_name} или {0}, {1} (по индексу).
    Например: 'Логин: {0}\\nПароль: {1}'

    Возвращает количество загруженных строк.
    """
    path = _file_path(lot_id, "csv")
    # Сохраняем как есть
    path.write_text(csv_text.strip(), encoding="utf-8")
    # Считаем строки (не пустые, без заголовка)
    lines = [l for l in csv_text.strip().splitlines() if l.strip()]
    count = max(0, len(lines) - 1) if lines else 0  # минус заголовок если есть
    config_manager.settings.auto_delivery.lots[lot_id] = {
        "type": "csv",
        "content": str(path),
        "template": template,
        "delay_sec": delay_sec,
        "low_stock_threshold": low_stock_threshold,
    }
    notified = _load_notified()
    notified.discard(str(lot_id))
    _save_notified(notified)
    config_manager.save()
    return count


def add_lot_random(lot_id: str, options: List[str], *, delay_sec: int = 0) -> None:
    """Тип RANDOM — случайный выбор из списка (без расхода)."""
    cleaned = [opt.strip() for opt in options if opt.strip()]
    config_manager.settings.auto_delivery.lots[lot_id] = {
        "type": "random",
        "options": cleaned,
        "delay_sec": delay_sec,
    }
    config_manager.save()


def add_lot_combined(lot_id: str, instruction: str, keys: List[str], *,
                     delay_sec: int = 0, low_stock_threshold: int = 5) -> None:
    """Тип COMBINED — общая инструкция + уникальный ключ из файла."""
    path = _file_path(lot_id, "txt")
    cleaned = [k.strip() for k in keys if k.strip()]
    path.write_text("\n".join(cleaned), encoding="utf-8")
    config_manager.settings.auto_delivery.lots[lot_id] = {
        "type": "combined",
        "instruction": instruction,
        "content": str(path),
        "delay_sec": delay_sec,
        "low_stock_threshold": low_stock_threshold,
    }
    notified = _load_notified()
    notified.discard(str(lot_id))
    _save_notified(notified)
    config_manager.save()


def remove_lot(lot_id: str) -> bool:
    """Удаляет настройку автовыдачи лота."""
    info = config_manager.settings.auto_delivery.lots.pop(lot_id, None)
    if info and info.get("content"):
        try:
            Path(info["content"]).unlink(missing_ok=True)
        except Exception:
            pass
    config_manager.save()
    return info is not None


# ──────────────────────────────────────────────────────────────────────────────
# Получение остатков и расход ключей
# ──────────────────────────────────────────────────────────────────────────────

def get_remaining_keys(lot_id: str) -> int:
    """Количество доступных ключей. -1 если тип не подразумевает расход."""
    info = config_manager.settings.auto_delivery.lots.get(lot_id)
    if not info:
        return -1
    t = info.get("type")
    if t in ("file", "combined"):
        path = Path(info["content"])
        if not path.exists():
            return 0
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    if t == "csv":
        path = Path(info["content"])
        if not path.exists():
            return 0
        # Без заголовка
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return max(0, len(lines) - 1) if len(lines) > 1 else 0
    return -1  # static, random — бесконечны


def _pop_keys_from_file(path: Path, count: int) -> List[str]:
    """Достаёт N ключей из txt-файла, удаляет их."""
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    taken = lines[:count]
    rest = lines[count:]
    path.write_text("\n".join(rest), encoding="utf-8")
    return taken


def _pop_csv_rows(path: Path, count: int) -> Tuple[List[str], List[List[str]]]:
    """
    Достаёт N строк из CSV. Возвращает (header, rows).
    Удаляет использованные строки, заголовок остаётся.
    """
    if not path.exists():
        return [], []
    text = path.read_text(encoding="utf-8")
    reader = csv.reader(io.StringIO(text))
    all_rows = [r for r in reader if any(c.strip() for c in r)]
    if not all_rows:
        return [], []

    header = all_rows[0]
    data = all_rows[1:]
    taken = data[:count]
    rest = data[count:]

    # Перезаписываем
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    for row in rest:
        w.writerow(row)
    path.write_text(out.getvalue(), encoding="utf-8")
    return header, taken


def _format_csv_row(header: List[str], row: List[str], template: str) -> str:
    """Формирует текст из строки CSV по шаблону."""
    # Простые placeholders: {key}, {instruction}, {0}, {1}, ...
    if not template:
        # По умолчанию выводим все колонки в формате "Header: value"
        if not header:
            return ", ".join(row)
        return "\n".join(f"{h}: {v}" for h, v in zip(header, row))

    out = template
    # Замена по имени колонки
    for h, v in zip(header, row):
        out = out.replace(f"{{{h}}}", v)
    # Замена по индексу
    for i, v in enumerate(row):
        out = out.replace(f"{{{i}}}", v)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Обработчик события "заказ оплачен"
# ──────────────────────────────────────────────────────────────────────────────

@event_bus.on(Event.ORDER_PAID)
async def handle_order_paid(order) -> None:
    cfg = config_manager.settings.auto_delivery
    if not cfg.enabled:
        return

    # ID лота
    lot_id = (
        str(getattr(order, "subcategory_id", "") or "")
        or str(getattr(order, "lot_id", "") or "")
        or str(getattr(order, "offer_id", "") or "")
        or str(getattr(order, "id", "") or "")
    )

    # ID чата
    chat_id = getattr(order, "chat_id", None) or getattr(order, "buyer_chat_id", None)
    amount = max(1, int(getattr(order, "amount", 1) or 1))

    if not lot_id or not chat_id:
        logger.warning(f"auto_delivery: не нашёл lot_id или chat_id (заказ {order})")
        return

    info = cfg.lots.get(lot_id)
    if not info:
        logger.info(f"auto_delivery: лот {lot_id} не настроен — пропуск")
        return

    # Определяем сколько штук выдавать (мульти-выдача)
    multi_enabled = config_manager.settings.multi_delivery.enabled
    count = amount if multi_enabled else 1

    # Отложенная выдача
    delay_sec = int(info.get("delay_sec", 0))
    if delay_sec > 0:
        logger.info(f"auto_delivery: отложенная выдача лота {lot_id} на {delay_sec}с")
        await asyncio.sleep(delay_sec)

    # Формируем текст по типу
    text, success = await _build_delivery_text(lot_id, info, count)

    if not success:
        # Ключи закончились — шлём out_of_stock + автодеактивация
        funpay_client.send_message(chat_id, cfg.out_of_stock_message)
        logger.warning(f"auto_delivery: лот {lot_id} — ключи закончились")
        await event_bus.emit("delivery_out_of_stock", order)
        if config_manager.settings.auto_activation.enabled:
            try:
                from modules.auto_deactivation import deactivate_lot
                await deactivate_lot(lot_id)
            except Exception as e:
                logger.warning(f"auto-deactivate {lot_id}: {e}")
        return

    # Отправляем
    funpay_client.send_message(chat_id, text)
    logger.info(f"auto_delivery → лот {lot_id} ({info['type']}, {count} шт.)")
    audit(0, "AUTO_DELIVERY", data=f"lot={lot_id} type={info['type']} count={count}")
    await event_bus.emit("delivery_sent", order, text)

    # Проверка низкого остатка
    await _check_low_stock(lot_id, info)


async def _build_delivery_text(
    lot_id: str, info: dict, count: int
) -> Tuple[str, bool]:
    """Формирует текст выдачи. Возвращает (text, success)."""
    t = info.get("type")

    if t == "static":
        return info.get("content", ""), True

    if t == "random":
        options = info.get("options", [])
        if not options:
            return "", False
        return random.choice(options), True

    if t == "file":
        keys = _pop_keys_from_file(Path(info["content"]), count)
        if not keys:
            return "", False
        return "\n".join(keys), True

    if t == "combined":
        keys = _pop_keys_from_file(Path(info["content"]), count)
        if not keys:
            return "", False
        instruction = info.get("instruction", "")
        body = "\n".join(keys)
        return f"{instruction}\n\n{body}".strip(), True

    if t == "csv":
        header, rows = _pop_csv_rows(Path(info["content"]), count)
        if not rows:
            return "", False
        template = info.get("template", "")
        formatted = [_format_csv_row(header, row, template) for row in rows]
        return "\n\n".join(formatted), True

    return "", False


async def _check_low_stock(lot_id: str, info: dict) -> None:
    """Если в file/csv/combined осталось < threshold — шлём уведомление."""
    if info.get("type") not in ("file", "csv", "combined"):
        return
    threshold = int(info.get("low_stock_threshold", 5))
    if threshold <= 0:
        return
    remaining = get_remaining_keys(lot_id)
    if remaining < 0 or remaining > threshold:
        return

    # Уведомляем только один раз пока не пополнят
    notified = _load_notified()
    if str(lot_id) in notified:
        return

    text = (
        f"⚠️ <b>Малый остаток ключей</b>\n\n"
        f"Лот <code>{lot_id}</code>: осталось <b>{remaining}</b> шт.\n"
        f"Пополните файл, иначе следующий покупатель получит сообщение «товар закончился»."
    )
    try:
        from modules.notifications import _send_to_all
        await _send_to_all(text)
    except Exception as e:
        logger.debug(f"low_stock notify: {e}")

    notified.add(str(lot_id))
    _save_notified(notified)


# ──────────────────────────────────────────────────────────────────────────────
# Тестовая выдача — для команды /test_lot
# ──────────────────────────────────────────────────────────────────────────────

async def test_delivery(lot_id: str) -> str:
    """Что отправилось бы покупателю при оплате."""
    info = config_manager.settings.auto_delivery.lots.get(lot_id)
    if not info:
        return f"Лот {lot_id} не настроен."
    t = info["type"]

    if t == "static":
        return f"[STATIC] {info.get('content', '')}"

    if t == "random":
        opts = info.get("options", [])
        if not opts:
            return "[RANDOM] Список пуст."
        return f"[RANDOM, вариантов: {len(opts)}] напр.: {random.choice(opts)}"

    if t == "file":
        path = Path(info["content"])
        keys = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()][:1] if path.exists() else []
        if not keys:
            return "[FILE] Ключи закончились."
        return f"[FILE, осталось {get_remaining_keys(lot_id)}] следующий: {keys[0]}"

    if t == "combined":
        path = Path(info["content"])
        keys = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()][:1] if path.exists() else []
        if not keys:
            return "[COMBINED] Ключи закончились."
        instr = info.get("instruction", "")
        return f"[COMBINED, осталось {get_remaining_keys(lot_id)}]\n{instr}\n\n{keys[0]}"

    if t == "csv":
        path = Path(info["content"])
        if not path.exists():
            return "[CSV] Файл не найден."
        text = path.read_text(encoding="utf-8")
        reader = csv.reader(io.StringIO(text))
        rows = [r for r in reader if any(c.strip() for c in r)]
        if len(rows) < 2:
            return "[CSV] Данных нет (только заголовок?)."
        header, first = rows[0], rows[1]
        formatted = _format_csv_row(header, first, info.get("template", ""))
        return f"[CSV, осталось {get_remaining_keys(lot_id)}]\n{formatted}"

    return f"[{t}] неизвестный тип."
