"""
TOTP (Time-based One-Time Password) — RFC 6238.

Реализация без сторонних библиотек (pyotp), чтобы не добавлять зависимостей.
Совместимо с Google Authenticator / Authy / 1Password / etc.

Usage:
    secret = generate_secret()           # сохраните в config
    print(otpauth_url(secret, "PaltoFunPayBot", "user@example.com"))
    # → юзер сканирует QR в Google Authenticator

    if verify(secret, "123456"):
        print("OK")
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


# ──────────────────────────────────────────────────────────────────────────────
# Секрет
# ──────────────────────────────────────────────────────────────────────────────

def generate_secret(length_bytes: int = 20) -> str:
    """Генерирует случайный секрет в base32 (для отображения и QR)."""
    raw = secrets.token_bytes(length_bytes)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _b32_decode(secret: str) -> bytes:
    """base32 → bytes. Padding до длины кратной 8."""
    s = secret.upper().replace(" ", "")
    pad = "=" * ((8 - len(s) % 8) % 8)
    return base64.b32decode(s + pad)


# ──────────────────────────────────────────────────────────────────────────────
# Генерация и проверка кода
# ──────────────────────────────────────────────────────────────────────────────

def _hotp(secret_bytes: bytes, counter: int, digits: int = 6) -> str:
    """HOTP по RFC 4226."""
    msg = struct.pack(">Q", counter)
    h = hmac.new(secret_bytes, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = (struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def now_code(secret: str, period: int = 30, digits: int = 6) -> str:
    """Возвращает текущий TOTP-код."""
    secret_bytes = _b32_decode(secret)
    counter = int(time.time()) // period
    return _hotp(secret_bytes, counter, digits)


def verify(secret: str, code: str, *, window: int = 1, period: int = 30, digits: int = 6) -> bool:
    """
    Проверяет код. window=1 значит принимаем коды +/- 1 период (30s) от текущего —
    чтобы покрыть случаи когда юзер ввёл код в самом конце его срока действия.
    """
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != digits:
        return False
    secret_bytes = _b32_decode(secret)
    counter = int(time.time()) // period
    for delta in range(-window, window + 1):
        if hmac.compare_digest(_hotp(secret_bytes, counter + delta, digits), code):
            return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# URL для добавления в authenticator-приложения
# ──────────────────────────────────────────────────────────────────────────────

def otpauth_url(secret: str, issuer: str = "PaltoFunPayBot", account: str = "user") -> str:
    """
    Создаёт otpauth:// URL который можно открыть в Google Authenticator
    или показать в виде QR-кода.
    """
    label = f"{quote(issuer)}:{quote(account)}"
    params = {
        "secret": secret,
        "issuer": quote(issuer),
        "algorithm": "SHA1",
        "digits": "6",
        "period": "30",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"otpauth://totp/{label}?{query}"
