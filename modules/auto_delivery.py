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
import os
import random
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from config.settings import config_manager
from core.event_bus import Event, event_bus
from core.funpay_client import accounts_manager, funpay_client
from utils.audit_log import audit
from utils.logger import logger

DELIVERY_FILES_DIR = Path(__file__).resolve().parent.parent / "data" / "delivery_files"
DELIVERY_FILES_DIR.mkdir(parents=True, exist_ok=True)

# Файл для отслеживания "уже предупредил о малом остатке"
LOW_STOCK_NOTIFIED_PATH = Path(__file__).resolve().parent.parent / "data" / "low_stock_notified.json"

# Заказы, по которым выдача уже произведена — защита от повторной выдачи,
# если FunPay пришлёт событие об оплате дважды (new_order + status_changed)
# или после перезапуска бота.
DELIVERED_ORDERS_PATH = Path(__file__).resolve().parent.parent / "data" / "delivered_orders.json"
_DELIVERED_CAP = 5000  # сколько последних id заказов помним

# Блокировки на лот — чтобы два одновременных заказа не вычитали один и тот
# же ключ из файла и не затёрли файл с ключами.
_lot_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _file_path(lot_id: str, ext: str = "txt") -> Path:
    return DELIVERY_FILES_DIR / f"lot_{lot_id}.{ext}"


def _atomic_write_text(path: Path, text: str) -> None:
    """Атомарная запись в файл (tmp + os.replace) — не теряем ключи при сбое."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


# ──────────────────────────────────────────────────────────────────────────────
# Сопоставление заказа с лотом — по описанию (как FunPay Cardinal).
#
# FunPay в событии заказа НЕ отдаёт числовой offer_id. У объекта заказа есть
# только order.description (название/краткое описание лота). Поэтому для каждого
# настроенного лота мы храним match_text — текст, который ищем в описании заказа.
# Если лот добавлен по числовому ID и аккаунт подключён, match_text берём из
# названия лота (get_lot_fields(...).title_ru/title_en).
# ──────────────────────────────────────────────────────────────────────────────

def resolve_match_text(lot_id: str) -> str:
    """
    Возвращает текст для сопоставления с order.description.

    Для числового lot_id пытается получить название лота с FunPay.
    Если не вышло (нет подключения / лот чужой) — возвращает сам lot_id
    (для нечисловых ключей это и есть текст названия, введённый вручную).
    """
    key = str(lot_id).strip()
    if not key.isdigit():
        # Пользователь ввёл текст названия лота — используем как есть.
        return key
    acc = funpay_client.account
    if not acc:
        return ""
    try:
        fields = acc.get_lot_fields(int(key))
        title = (getattr(fields, "title_ru", "") or getattr(fields, "title_en", "") or "").strip()
        return title
    except Exception as e:
        logger.debug(f"resolve_match_text({key}): {e}")
        return ""


def _store_match_text(lot_id: str) -> None:
    """Резолвит и сохраняет match_text в конфиг лота (best-effort)."""
    info = config_manager.settings.auto_delivery.lots.get(lot_id)
    if info is None:
        return
    mt = resolve_match_text(lot_id)
    if mt:
        info["match_text"] = mt


def backfill_match_texts() -> int:
    """
    Дозаполняет match_text для лотов, у которых его нет (старые конфиги).
    Вызывается на старте после подключения к FunPay. Возвращает кол-во заполненных.
    """
    n = 0
    for lot_id, info in config_manager.settings.auto_delivery.lots.items():
        if info.get("match_text"):
            continue
        mt = resolve_match_text(lot_id)
        if mt:
            info["match_text"] = mt
            n += 1
    if n:
        config_manager.save()
        logger.info(f"auto_delivery: распознано названий лотов для сопоставления: {n}")
    return n


def _find_lot_for_order(order) -> Tuple[Optional[str], Optional[dict]]:
    """
    Ищет настроенный лот для заказа по order.description (Cardinal-стиль).
    Среди подходящих выбирает самый специфичный (с самым длинным match_text).
    """
    lots = config_manager.settings.auto_delivery.lots
    if not lots:
        return None, None

    desc = (getattr(order, "description", "") or "").strip().lower()

    best_id: Optional[str] = None
    best_info: Optional[dict] = None
    best_len = -1

    for lot_id, info in lots.items():
        match_text = (info.get("match_text") or str(lot_id)).strip().lower()
        if not match_text:
            continue
        if desc and match_text in desc:
            if len(match_text) > best_len:
                best_id, best_info, best_len = lot_id, info, len(match_text)

    return best_id, best_info


# ──────────────────────────────────────────────────────────────────────────────
# Дедупликация выданных заказов
# ──────────────────────────────────────────────────────────────────────────────

def _load_delivered() -> List[str]:
    if not DELIVERED_ORDERS_PATH.exists():
        return []
    try:
        return list(json.loads(DELIVERED_ORDERS_PATH.read_text(encoding="utf-8")))
    except Exception:
        return []


def _is_delivered(order_id: str) -> bool:
    return order_id in set(_load_delivered())


def _mark_delivered(order_id: str) -> None:
    ids = _load_delivered()
    if order_id in ids:
        return
    ids.append(order_id)
    if len(ids) > _DELIVERED_CAP:
        ids = ids[-_DELIVERED_CAP:]
    try:
        _atomic_write_text(DELIVERED_ORDERS_PATH, json.dumps(ids))
    except Exception as e:
        logger.debug(f"_mark_delivered: {e}")


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
    _store_match_text(lot_id)
    config_manager.save()


def add_lot_file(lot_id: str, lines: List[str], *, delay_sec: int = 0,
                 low_stock_threshold: int = 5) -> None:
    """Тип FILE — txt-файл, строки расходуются."""
    path = _file_path(lot_id, "txt")
    cleaned = [ln.strip() for ln in lines if ln.strip()]
    _atomic_write_text(path, "\n".join(cleaned))
    config_manager.settings.auto_delivery.lots[lot_id] = {
        "type": "file",
        "content": str(path),
        "delay_sec": delay_sec,
        "low_stock_threshold": low_stock_threshold,
    }
    _store_match_text(lot_id)
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
    _atomic_write_text(path, csv_text.strip())
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
    _store_match_text(lot_id)
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
    _store_match_text(lot_id)
    config_manager.save()


def add_lot_combined(lot_id: str, instruction: str, keys: List[str], *,
                     delay_sec: int = 0, low_stock_threshold: int = 5) -> None:
    """Тип COMBINED — общая инструкция + уникальный ключ из файла."""
    path = _file_path(lot_id, "txt")
    cleaned = [k.strip() for k in keys if k.strip()]
    _atomic_write_text(path, "\n".join(cleaned))
    config_manager.settings.auto_delivery.lots[lot_id] = {
        "type": "combined",
        "instruction": instruction,
        "content": str(path),
        "delay_sec": delay_sec,
        "low_stock_threshold": low_stock_threshold,
    }
    _store_match_text(lot_id)
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
    _atomic_write_text(path, "\n".join(rest))
    return taken


def _push_keys_back(path: Path, keys: List[str]) -> None:
    """Возвращает ключи В НАЧАЛО txt-файла (при неудачной отправке)."""
    if not keys:
        return
    existing = []
    if path.exists():
        existing = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    _atomic_write_text(path, "\n".join(keys + existing))


def _push_csv_rows_back(path: Path, header: List[str], rows: List[List[str]]) -> None:
    """Возвращает строки в CSV сразу после заголовка (при неудачной отправке)."""
    if not rows:
        return
    existing_rows: List[List[str]] = []
    if path.exists():
        reader = csv.reader(io.StringIO(path.read_text(encoding="utf-8")))
        all_rows = [r for r in reader if any(c.strip() for c in r)]
        if all_rows:
            header = all_rows[0]
            existing_rows = all_rows[1:]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    for row in rows + existing_rows:
        w.writerow(row)
    _atomic_write_text(path, out.getvalue())


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
    _atomic_write_text(path, out.getvalue())
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

    # Защита от повторной выдачи (FunPay может прислать оплату дважды).
    order_id = str(getattr(order, "id", "") or "")
    if order_id and _is_delivered(order_id):
        logger.debug(f"auto_delivery: заказ #{order_id} уже выдан — пропуск (дубль)")
        return

    # Выдаём через аккаунт, на который пришёл заказ (мультиаккаунт)
    client = accounts_manager.client_for(order)

    # ID чата
    chat_id = getattr(order, "chat_id", None) or getattr(order, "buyer_chat_id", None)
    amount = max(1, int(getattr(order, "amount", 1) or 1))

    if not chat_id:
        logger.warning(f"auto_delivery: не нашёл chat_id (заказ {order})")
        return

    # Ищем лот по описанию заказа (Cardinal-стиль).
    lot_id, info = _find_lot_for_order(order)
    if not info:
        logger.info(
            f"auto_delivery: для заказа #{order_id} "
            f"(«{(getattr(order, 'description', '') or '')[:50]}») нет настроенного лота — пропуск"
        )
        return

    # Всё, что меняет файл ключей, делаем под блокировкой лота, чтобы
    # параллельные заказы не вычитали один ключ дважды и не затёрли файл.
    async with _lot_locks[lot_id]:
        # Повторная проверка дедупа уже под локом (на случай гонки).
        if order_id and _is_delivered(order_id):
            return

        # Определяем сколько штук выдавать (мульти-выдача)
        multi_enabled = config_manager.settings.multi_delivery.enabled
        count = amount if multi_enabled else 1

        # Отложенная выдача
        delay_sec = int(info.get("delay_sec", 0))
        if delay_sec > 0:
            logger.info(f"auto_delivery: отложенная выдача лота {lot_id} на {delay_sec}с")
            await asyncio.sleep(delay_sec)

        # Формируем текст по типу (расходные типы вычитают ключи из файла)
        text, success, restore = await _build_delivery_text(lot_id, info, count)

        loop = asyncio.get_event_loop()

        if not success:
            # Ключи закончились — шлём out_of_stock + автодеактивация
            await loop.run_in_executor(
                None, client.send_message, chat_id, cfg.out_of_stock_message
            )
            logger.warning(f"auto_delivery: лот {lot_id} — ключи закончились")
            await event_bus.emit("delivery_out_of_stock", order)
            if config_manager.settings.auto_activation.enabled:
                try:
                    from modules.auto_deactivation import deactivate_lot
                    await deactivate_lot(lot_id, client=client)
                except Exception as e:
                    logger.warning(f"auto-deactivate {lot_id}: {e}")
            return

        # Отправляем ДО пометки «выдано». send_message сам режет длинный текст
        # по 20 строк и делает ретраи; в executor — чтобы ретраи не блокировали
        # event loop (мы всё ещё под локом лота, гонок нет).
        sent = await loop.run_in_executor(None, client.send_message, chat_id, text)

        if not sent:
            # Отправка не подтвердилась: возвращаем вычтенные ключи в файл
            # и НЕ помечаем заказ выданным — повторное событие об оплате
            # (или рестарт) попробует выдать снова.
            if restore is not None:
                try:
                    restore()
                    logger.warning(
                        f"auto_delivery: лот {lot_id} — отправка не удалась, "
                        f"ключи возвращены в файл"
                    )
                except Exception as e:
                    logger.error(f"auto_delivery: не смог вернуть ключи лота {lot_id}: {e}")
            logger.warning(
                f"auto_delivery: лот {lot_id} — не удалось отправить товар. "
                f"Проверьте заказ #{order_id} вручную."
            )
            try:
                from modules.notifications import _send_to_all
                await _send_to_all(
                    f"❌ <b>Автовыдача не удалась</b>\n\n"
                    f"Заказ <code>#{order_id}</code>, лот <code>{lot_id}</code>: "
                    f"сообщение с товаром не доставлено.\n"
                    f"Ключи возвращены в файл. Выдайте вручную или дождитесь повторного события."
                )
            except Exception as e:
                logger.debug(f"delivery fail notify: {e}")
            return

        # Только теперь помечаем заказ выданным.
        if order_id:
            _mark_delivered(order_id)

        logger.info(f"auto_delivery → лот {lot_id} ({info['type']}, {count} шт.)")
        audit(0, "AUTO_DELIVERY", data=f"lot={lot_id} type={info['type']} count={count} order={order_id}")
        await event_bus.emit("delivery_sent", order, text)

        # Проверка низкого остатка
        await _check_low_stock(lot_id, info)


async def _build_delivery_text(
    lot_id: str, info: dict, count: int
) -> Tuple[str, bool, Optional[Callable[[], None]]]:
    """
    Формирует текст выдачи. Возвращает (text, success, restore).

    restore — замыкание, возвращающее вычтенные ключи обратно в файл;
    None для нерасходных типов (static, random). Вызывать, если отправка
    покупателю не подтвердилась.
    """
    t = info.get("type")

    if t == "static":
        return info.get("content", ""), True, None

    if t == "random":
        options = info.get("options", [])
        if not options:
            return "", False, None
        return random.choice(options), True, None

    if t == "file":
        path = Path(info["content"])
        keys = _pop_keys_from_file(path, count)
        if not keys:
            return "", False, None
        return "\n".join(keys), True, (lambda: _push_keys_back(path, keys))

    if t == "combined":
        path = Path(info["content"])
        keys = _pop_keys_from_file(path, count)
        if not keys:
            return "", False, None
        instruction = info.get("instruction", "")
        body = "\n".join(keys)
        return f"{instruction}\n\n{body}".strip(), True, (lambda: _push_keys_back(path, keys))

    if t == "csv":
        path = Path(info["content"])
        header, rows = _pop_csv_rows(path, count)
        if not rows:
            return "", False, None
        template = info.get("template", "")
        formatted = [_format_csv_row(header, row, template) for row in rows]
        return "\n\n".join(formatted), True, (lambda: _push_csv_rows_back(path, header, rows))

    return "", False, None


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
