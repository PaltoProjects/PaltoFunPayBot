from __future__ import annotations

import os
import platform
import sys
from datetime import datetime


RESET = "\033[0m"
DIM = "\033[2m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"
GREEN = "\033[32m"
YELLOW = "\033[33m"


def _color(text: str, color: str) -> str:
    if not sys.stdout.isatty() or os.getenv("NO_COLOR"):
        return text
    return f"{color}{text}{RESET}"


def _line(label: str, value: str, color: str = CYAN) -> str:
    return f"  {_color(label.ljust(13), DIM)} {_color(value, color)}"


def _supports_unicode() -> bool:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in encoding


def _safe_print(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        safe = text.encode(getattr(sys.stdout, "encoding", None) or "ascii", errors="replace").decode(
            getattr(sys.stdout, "encoding", None) or "ascii",
            errors="replace",
        )
        print(safe)


def print_startup_banner(*, funpay_available: bool) -> None:
    if _supports_unicode():
        title = [
            "╭──────────────────────────────────────────────╮",
            "│              PALTO FUNPAY BOT                │",
            "│        Telegram control for FunPay shops      │",
            "╰──────────────────────────────────────────────╯",
        ]
        separator = "  " + "─" * 46
    else:
        title = [
            "+----------------------------------------------+",
            "|              PALTO FUNPAY BOT                |",
            "|        Telegram control for FunPay shops      |",
            "+----------------------------------------------+",
        ]
        separator = "  " + "-" * 46
    gradient = [CYAN, BLUE, MAGENTA, MAGENTA]
    api_status = "loaded" if funpay_available else "not loaded"
    _safe_print()
    for line, color in zip(title, gradient):
        _safe_print(_color(line, color))
    _safe_print(_color("     sell faster · answer cleaner · sleep longer", DIM))
    _safe_print()
    _safe_print(_line("Version", "1.0", GREEN))
    _safe_print(_line("Python", platform.python_version(), GREEN))
    _safe_print(_line("FunPay API", api_status, GREEN if funpay_available else YELLOW))
    _safe_print(_line("Started", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), CYAN))
    _safe_print(_color(separator, DIM))
    _safe_print()
