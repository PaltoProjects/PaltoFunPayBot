"""
Хэширование пароля администратора (bcrypt).

В data/config.json хранится bcrypt-хэш, а не плейнтекст: утечка конфига
больше не означает утечку пароля. Старые конфиги с плейнтекстом мигрируются
автоматически при загрузке (см. config/settings.py).

Если bcrypt не установлен (например, бот обновился через /update без
pip install) — модуль деградирует до плейнтекста и бот продолжает работать;
хэширование включится после установки зависимости.

ВАЖНО: модуль не должен импортировать ничего из проекта (его тянет
config/settings.py на самом раннем этапе — циклические импорты).
"""
from __future__ import annotations

import hmac

try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    bcrypt = None  # type: ignore[assignment]
    BCRYPT_AVAILABLE = False

_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")


def is_hashed(value: str) -> bool:
    """True, если значение похоже на bcrypt-хэш."""
    return isinstance(value, str) and value.startswith(_BCRYPT_PREFIXES)


def hash_password(password: str) -> str:
    """
    Возвращает bcrypt-хэш пароля.
    Без bcrypt возвращает пароль как есть (легаси-режим).
    """
    if not BCRYPT_AVAILABLE:
        return password
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(candidate: str, stored: str) -> bool:
    """
    Проверяет пароль против сохранённого значения.
    Понимает и bcrypt-хэш, и легаси-плейнтекст (немигрированный конфиг).
    """
    if not candidate or not stored:
        return False
    if is_hashed(stored):
        if not BCRYPT_AVAILABLE:
            return False  # хэш есть, а проверить нечем — доступ закрыт
        try:
            return bcrypt.checkpw(candidate.encode("utf-8"), stored.encode("ascii"))
        except ValueError:
            return False
    # Легаси-плейнтекст: сравнение, устойчивое к timing-атакам
    return hmac.compare_digest(candidate.encode("utf-8"), stored.encode("utf-8"))
