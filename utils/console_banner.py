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


def print_startup_banner(*, funpay_available: bool) -> None:
    title = [
        "╭──────────────────────────────────────────────╮",
        "│              PALTO FUNPAY BOT                │",
        "│        Telegram control for FunPay shops      │",
        "╰──────────────────────────────────────────────╯",
    ]
    gradient = [CYAN, BLUE, MAGENTA, MAGENTA]
    print()
    for line, color in zip(title, gradient):
        print(_color(line, color))
    print(_color("     sell faster · answer cleaner · sleep longer", DIM))
    print()
    print(_line("Version", "1.0", GREEN))
    print(_line("Python", platform.python_version(), GREEN))
    print(_line("FunPay API", "local Cardinal fork" if funpay_available else "not loaded", GREEN if funpay_available else YELLOW))
    print(_line("Started", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), CYAN))
    print(_color("  " + "─" * 46, DIM))
    print()
