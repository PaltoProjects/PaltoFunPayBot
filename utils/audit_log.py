"""
Журнал действий — кто что нажимал, писал, отвечал.

Все действия пишутся в data/logs/audit.log (отдельный файл, чтобы не мешался
с основными логами). Формат:

    2026-04-26 17:45:33 | user=12345 | role=admin | action=BUTTON_CLICK | data=core:t:auto_lift
    2026-04-26 17:45:40 | user=12345 | role=admin | action=COMMAND | data=/menu
    2026-04-26 17:46:15 | user=12345 | role=admin | action=QUICK_REPLY | chat_id=987 | text="Спасибо за заказ!"
    2026-04-26 17:50:00 | user=12345 | role=admin | action=LOGIN | ip=1.2.3.4
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from utils.logger import logger

AUDIT_PATH = Path(__file__).resolve().parent.parent / "data" / "logs" / "audit.log"
AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)


def _user_role(user_id: int) -> str:
    """Возвращает роль юзера: admin / manager / unknown."""
    from config.settings import config_manager
    s = config_manager.settings.telegram
    if user_id in s.admin_ids:
        return "admin"
    if user_id in s.manager_ids:
        return "manager"
    return "unknown"


def audit(
    user_id: int,
    action: str,
    *,
    data: str = "",
    chat_id: Any = "",
    text: str = "",
    extra: Optional[str] = None,
) -> None:
    """
    Записывает действие в журнал.

    Args:
        user_id: TG user id
        action: COMMAND / BUTTON_CLICK / TEXT / QUICK_REPLY / LOGIN / LOGOUT /
                CONFIG_CHANGE / PLUGIN_TOGGLE / PHOTO_UPLOAD и т.д.
        data: основные данные действия (callback_data, имя команды, ключ настройки)
        chat_id: id чата FunPay (для отправок в FunPay)
        text: текст сообщения (обрежется до 200 символов)
        extra: любая доп. информация
    """
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    role = _user_role(user_id)
    parts = [
        ts,
        f"user={user_id}",
        f"role={role}",
        f"action={action}",
    ]
    if data:
        parts.append(f"data={data}")
    if chat_id:
        parts.append(f"chat_id={chat_id}")
    if text:
        # Усекаем длинный текст, экранируем переводы строк
        snippet = (text[:200] + "...") if len(text) > 200 else text
        snippet = snippet.replace("\n", "\\n").replace("\r", "")
        parts.append(f'text="{snippet}"')
    if extra:
        parts.append(extra)

    line = " | ".join(parts) + "\n"
    try:
        with AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        # Не падаем если не смогли записать
        logger.debug(f"audit log write failed: {e}")


def read_recent(lines: int = 50) -> str:
    """Возвращает последние N строк журнала (для команды /audit)."""
    if not AUDIT_PATH.exists():
        return "Журнал пуст."
    try:
        with AUDIT_PATH.open("r", encoding="utf-8") as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:]
        return "".join(tail)
    except Exception as e:
        return f"Не удалось прочитать журнал: {e}"
