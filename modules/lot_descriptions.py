"""
Генерация описаний лотов через ИИ (фича №42).

Использование: команда /gen_desc <lot_id> [подсказка]
  → бот берёт текущее описание лота с FunPay, отдаёт ИИ
  → ИИ возвращает 2-3 варианта улучшенного описания
  → юзер сам выбирает и применяет (вручную или подтверждает кнопкой)

Тон, длина и эмодзи настраиваются в /menu → 🤖 Автоматизация → 🧠 ИИ-помощник →
"Описания лотов".
"""
from __future__ import annotations

import asyncio
from typing import List, Optional

from config.settings import config_manager
from core.funpay_client import funpay_client
from utils.logger import logger


def _build_prompt(current_desc: str, lot_title: str, hint: str = "") -> tuple:
    """Собирает (system_prompt, user_prompt) для ИИ."""
    cfg = config_manager.settings.lot_desc_generator

    if cfg.custom_prompt:
        system = cfg.custom_prompt
    else:
        system = (
            f"Ты — копирайтер для маркетплейса игровых товаров FunPay. "
            f"Тебе нужно написать описание лота в {cfg.tone} тоне на русском языке. "
            f"Ограничение: {cfg.max_length} символов в одном варианте. "
            f"{'Используй эмодзи для эмоций и привлекательности.' if cfg.include_emoji else 'НЕ используй эмодзи.'}"
            f"\n\nТребования:\n"
            f"- Пиши по-человечески, естественно, без штампов «уникальное предложение» и «лучшие цены».\n"
            f"- Ключевые преимущества — в первых 1-2 предложениях.\n"
            f"- Если в исходнике есть конкретика (цена, сроки, гарантии) — сохрани её.\n"
            f"- Не выдумывай несуществующих характеристик.\n"
            f"- Сделай 3 разных варианта, разделив их строкой '---'.\n"
            f"- Никаких преамбул вроде «вот варианты» — сразу варианты, разделённые '---'."
        )

    user = (
        f"Название лота: {lot_title or '(не задано)'}\n\n"
        f"Текущее описание:\n«{current_desc or '(пусто)'}»"
    )
    if hint:
        user += f"\n\nДополнительные пожелания продавца: {hint}"

    return system, user


async def generate_descriptions(
    lot_id: int,
    hint: str = "",
) -> List[str]:
    """
    Генерирует варианты описаний для лота через ИИ.

    Возвращает список вариантов (обычно 3). Если ИИ выключен / нет ключа /
    лот не найден / ошибка API — возвращает пустой список.
    """
    cfg = config_manager.settings.lot_desc_generator
    if not cfg.enabled:
        logger.info("Генератор описаний выключен.")
        return []

    if not funpay_client.account:
        return []

    # 1. Получаем текущие данные лота с FunPay
    loop = asyncio.get_event_loop()
    current_desc = ""
    lot_title = ""
    try:
        lot_fields = await loop.run_in_executor(
            None, funpay_client.account.get_lot_fields, lot_id
        )
        # Поля могут называться по-разному в разных версиях FunPayAPI
        for attr in ("description_ru", "description", "desc", "fields_ru_description"):
            v = getattr(lot_fields, attr, None)
            if v:
                current_desc = str(v)
                break
        for attr in ("title_ru", "title", "name"):
            v = getattr(lot_fields, attr, None)
            if v:
                lot_title = str(v)
                break
    except Exception as e:
        logger.warning(f"gen_desc: не получили лот {lot_id}: {e}")
        return []

    # 2. Формируем промпт и зовём ИИ
    system, user = _build_prompt(current_desc, lot_title, hint)
    from modules.ai_assistant import call_llm
    raw = await call_llm(system, user)
    if not raw:
        return []

    # 3. Делим ответ на варианты по '---'
    variants = [v.strip() for v in raw.split("---") if v.strip()]
    if not variants:
        # ИИ не разделил — берём как один вариант
        variants = [raw.strip()]

    # Обрезаем по max_length
    out = []
    for v in variants:
        if len(v) > cfg.max_length:
            v = v[: cfg.max_length].rsplit(" ", 1)[0] + "..."
        out.append(v)

    return out[:3]
