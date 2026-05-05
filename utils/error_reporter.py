"""
Красивые уведомления об ошибках в Telegram.

- Пишет в TG только ВАЖНЫЕ ошибки (не каждый чих)
- Группирует одинаковые ошибки (rate limit) — не спамит при повторах
- Форматирует: эмодзи + место + причина + что делать
- Для разработчика всегда пишет в audit-лог полный traceback
"""
from __future__ import annotations

import time
import traceback as tb
from typing import Dict, Optional

from utils.logger import logger

# Анти-спам: помним когда последний раз слали ошибку с таким же ключом
_last_sent: Dict[str, float] = {}
_RATE_LIMIT_SEC = 300  # минимум 5 минут между одинаковыми ошибками


def _short_summary(error: BaseException) -> str:
    """Короткая первая строка ошибки — для дедупликации."""
    return f"{type(error).__name__}: {str(error)[:200]}"


# ─── Категоризация — что важно, что нет ──────────────────────────────────────

# Эти ошибки — обычные сетевые сбои, в TG их слать НЕ нужно (только в логи)
_NETWORK_NOISE = (
    "TelegramNetworkError",
    "ClientConnectorError",
    "ServerDisconnectedError",
    "TimeoutError",
    "ConnectionError",
    "ConnectionResetError",
    "ProxyError",
    "ssl:default",
)

# Эти ошибки нужно показывать СРАЗУ — бизнес-критичные
_IMPORTANT_KEYWORDS = (
    "UnauthorizedError",        # golden_key не работает
    "TypeError",                # баги в коде
    "AttributeError",
    "KeyError",
    "ValueError",
    "InvalidToken",             # токен Telegram неверен
    "FileNotFoundError",
    "PermissionError",
)


def _is_important(error: BaseException) -> bool:
    """Стоит ли вообще слать эту ошибку в TG?"""
    s = f"{type(error).__name__} {str(error)}"
    # Сначала фильтруем сетевой шум
    if any(k in s for k in _NETWORK_NOISE):
        return False
    # Потом смотрим важные паттерны
    if any(k in s for k in _IMPORTANT_KEYWORDS):
        return True
    # По умолчанию — не важно (в TG не шлём, только в логи)
    return False


# ─── Главная функция ─────────────────────────────────────────────────────────

async def report_error(
    error: BaseException,
    *,
    context: str = "",
    important: Optional[bool] = None,
    show_traceback: bool = False,
) -> None:
    """
    Сообщает об ошибке. В лог пишет всегда полный traceback.
    В TG — только если ошибка важная и прошёл rate-limit.

    Args:
        error:          сама ошибка
        context:        что случилось (например "AutoLift", "QuickReply")
        important:      принудительно True/False; по умолчанию авто-определение
        show_traceback: показать ли в TG короткий traceback (3 последние строки)
    """
    summary = _short_summary(error)

    # 1) В лог всегда — с полным трейсом
    full_tb = tb.format_exception(type(error), error, error.__traceback__)
    logger.error(f"[{context}] {summary}\n{''.join(full_tb)}")

    # 2) В audit-лог
    try:
        from utils.audit_log import audit
        audit(0, "ERROR", data=f"context={context}", text=summary)
    except Exception:
        pass

    # 3) В TG — только важные
    if important is None:
        important = _is_important(error)
    if not important:
        return

    # 4) Rate limit — одинаковые ошибки не чаще раза в 5 минут
    key = f"{context}|{summary}"
    now = time.time()
    last = _last_sent.get(key, 0)
    if now - last < _RATE_LIMIT_SEC:
        return
    _last_sent[key] = now

    # 5) Формируем красивое сообщение и шлём
    icon = _icon_for(error)
    text_lines = [f"{icon} <b>{_human_title(error)}</b>"]
    if context:
        text_lines.append(f"📍 Где: <code>{context}</code>")
    text_lines.append(f"💬 Причина:\n<code>{_escape_html(str(error)[:500])}</code>")

    hint = _hint_for(error)
    if hint:
        text_lines.append(f"\n💡 <b>Что делать:</b>\n{hint}")

    if show_traceback:
        # Только последние 3 строки трейса
        tail = "".join(full_tb[-3:])[:600]
        text_lines.append(f"\n<code>{_escape_html(tail)}</code>")

    text_lines.append(f"\n<i>Подробности — в журнале /audit</i>")

    try:
        from modules.notifications import _send_to_all
        await _send_to_all("\n".join(text_lines))
    except Exception as e:
        logger.warning(f"Не удалось отправить отчёт об ошибке в TG: {e}")


def _escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _icon_for(error: BaseException) -> str:
    s = f"{type(error).__name__} {str(error)}"
    if "Unauthorized" in s:
        return "🔒"
    if "Token" in s or "InvalidToken" in s:
        return "🔑"
    if any(k in s for k in ("File", "Path", "Permission")):
        return "📂"
    if any(k in s for k in ("TypeError", "AttributeError", "KeyError", "ValueError")):
        return "🐛"
    return "⚠️"


def _human_title(error: BaseException) -> str:
    s = f"{type(error).__name__} {str(error)}"
    if "UnauthorizedError" in s:
        return "FunPay не принимает golden_key"
    if "InvalidToken" in s:
        return "Неверный токен Telegram"
    if "TypeError" in s:
        return "Ошибка в коде (TypeError)"
    if "AttributeError" in s:
        return "Ошибка в коде (AttributeError)"
    if "KeyError" in s:
        return "Не найден ключ (KeyError)"
    if "FileNotFoundError" in s:
        return "Файл не найден"
    if "PermissionError" in s:
        return "Нет прав доступа"
    return f"Ошибка: {type(error).__name__}"


def _hint_for(error: BaseException) -> str:
    """Подсказка пользователю что делать."""
    s = f"{type(error).__name__} {str(error)}"
    if "UnauthorizedError" in s:
        return (
            "Получите новый <b>golden_key</b> через тот же IP/прокси, "
            "которым ходит бот (FunPay → F12 → Cookies → golden_key) "
            "и обновите в /menu → 🔧 Система → 🔑 golden_key"
        )
    if "InvalidToken" in s:
        return "Обновите токен в config.json или через /restart с новым токеном."
    if "FileNotFoundError" in s:
        return "Проверьте, что нужный файл создан и доступен боту."
    if "TypeError" in s or "AttributeError" in s:
        return "Это похоже на баг в коде — пришлите этот отчёт разработчику."
    return ""


# ─── Синхронная версия (для callbacks из не-async кода) ──────────────────────

def report_error_sync(error: BaseException, *, context: str = "", **kwargs) -> None:
    """Версия для не-async кода — запускает report_error через событийный цикл."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(report_error(error, context=context, **kwargs))
        else:
            loop.run_until_complete(report_error(error, context=context, **kwargs))
    except Exception as e:
        logger.warning(f"report_error_sync: {e}")
