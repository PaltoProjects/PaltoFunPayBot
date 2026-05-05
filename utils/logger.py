"""
Логгер на базе loguru. Формат — как у FunPayCardinal:
    [29-04-2026 17:07:00]> I: Сообщение

I = INFO, W = WARNING, E = ERROR, D = DEBUG, C = CRITICAL
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from loguru import logger

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


# Сокращения уровней — Cardinal-стиль
_LEVEL_SHORT = {
    "DEBUG":    "D",
    "INFO":     "I",
    "WARNING":  "W",
    "ERROR":    "E",
    "CRITICAL": "C",
    "TRACE":    "T",
    "SUCCESS":  "I",
}


# Шум который подавляется в консоли (в файл всё пишется)
_NOISE_PHRASES = (
    # FunPayAPI
    "Не удалось получить истории чатов",
    "не удалось получить истории чатов",
    "превышено кол-во попыток",
    "SEND_MESSAGE RESPONSE",

    # Прокси / сеть — это нормальные обрывы при простое
    "Удаленный хост принудительно разорвал",
    "Подключение к сети было разорвано",
    "недоступного хоста",
    "Cannot connect to proxy",
    "ProxyError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "WinError 1236",
    "WinError 10053",
    "WinError 10054",
    "WinError 10065",
    "Failed to establish a new connection",
    "Failed to fetch updates",
    "Sleep for ",
    "Connection established",
    "tryings = ",
    "ClientOSError",
    "TelegramNetworkError",
    "Retrying (Retry",
)


def _filter_console_noise(record):
    """Скрывает шумные сообщения из консоли (в файл пишется всё)."""
    msg = str(record["message"])
    if any(p in msg for p in _NOISE_PHRASES):
        return False
    return True


def _cardinal_format(record):
    """
    Формат: [29-04-2026 17:07:00]> I: Сообщение
    """
    level_short = _LEVEL_SHORT.get(record["level"].name, record["level"].name[0])
    record["extra"]["level_short"] = level_short
    return (
        "<green>[{time:DD-MM-YYYY HH:mm:ss}]></green> "
        "<level>{extra[level_short]}: {message}</level>\n"
    )


class _StdlibToLoguru(logging.Handler):
    """Перенаправляет stdlib-логи (FunPayAPI, urllib3, asyncio) в loguru."""
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except Exception:
            level = record.levelno
        msg = record.getMessage()

        # Понижаем уровень шумных сообщений до DEBUG (видны только в файле)
        if any(p in msg for p in _NOISE_PHRASES):
            level = "DEBUG"

        depth = 2
        frame = logging.currentframe()
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, msg)


def setup_logger(level: str = "INFO") -> None:
    """Настраивает loguru: в консоль INFO+ Cardinal-стиль, в файл DEBUG+ с ротацией."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=_cardinal_format,
        colorize=True,
        filter=_filter_console_noise,
    )
    logger.add(
        LOG_DIR / "bot.log",
        level="DEBUG",
        rotation="10 MB",
        retention="14 days",
        compression="zip",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )

    # Перенаправляем stdlib logging (FunPayAPI, urllib3, requests) в loguru
    handler = _StdlibToLoguru()
    logging.basicConfig(handlers=[handler], level=0, force=True)
    for noisy in ("urllib3", "requests", "asyncio", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


__all__ = ["logger", "setup_logger"]
