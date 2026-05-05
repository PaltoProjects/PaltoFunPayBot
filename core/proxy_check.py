"""Валидация golden_key (всё, что связано с прокси, удалено)."""
import re
from typing import Tuple


def validate_golden_key(key: str) -> Tuple[bool, str]:
    """golden_key — 32 символа, буквенно-цифровые."""
    key = key.strip()
    if len(key) != 32:
        return False, f"Некорректный формат токена. Он должен состоять из 32 символов (получено {len(key)})."
    if not re.match(r"^[a-zA-Z0-9]{32}$", key):
        return False, "golden_key должен содержать только буквы и цифры."
    return True, "OK"
