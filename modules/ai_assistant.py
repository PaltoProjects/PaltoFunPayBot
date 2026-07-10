"""
ИИ-помощник.

Функция 28 со скрина: если продавец не отвечает покупателю дольше, чем
ai.auto_reply_after_hours часов, ИИ-ассистент отвечает по заданной
инструкции.

Логика:
- На каждое входящее сообщение запоминаем timestamp.
- Раз в минуту проверяем: есть ли висящие чаты, в которых юзер ждёт ответа > N часов.
- Шлём один ИИ-ответ и помечаем чат как "уже ответили", чтобы не спамить.

Поддерживаемые провайдеры: anthropic (Claude API), openai (стиль OpenAI).
"""
from __future__ import annotations

from typing import Optional

import asyncio
import json
import time
from pathlib import Path

import aiohttp

from config.settings import config_manager
from core.event_bus import Event, event_bus
from core.funpay_client import accounts_manager
from utils.logger import logger

PENDING_PATH = Path(__file__).resolve().parent.parent / "data" / "ai_pending.json"
PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)

# Блокировка для безопасного конкурентного доступа к файлу:
# track_message (обработчик событий) и AIAssistant._tick (фоновый цикл)
# могут обращаться к файлу одновременно → без Lock возможна гонка данных.
_pending_lock = asyncio.Lock()


def _load_pending() -> dict:
    if not PENDING_PATH.exists():
        return {}
    try:
        return json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_pending(data: dict) -> None:
    PENDING_PATH.write_text(json.dumps(data), encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────────
# Слежение за входящими и нашими ответами
# ──────────────────────────────────────────────────────────────────────────────

@event_bus.on(Event.NEW_MESSAGE)
async def track_message(message) -> None:
    cfg = config_manager.settings.ai
    if not cfg.enabled:
        return

    chat_id = getattr(message, "chat_id", None)
    author_id = getattr(message, "author_id", None)
    text = getattr(message, "text", "") or ""
    # Мультиаккаунт: сообщение обрабатывает аккаунт, на который оно пришло
    client = accounts_manager.client_for(message)
    own_id = client.own_id

    if not chat_id:
        return

    async with _pending_lock:
        pending = _load_pending()
        key = str(chat_id)

        if author_id == own_id:
            # Мы (продавец или ИИ) ответили — снимаем pending
            pending.pop(key, None)
        else:
            # Покупатель написал — фиксируем время и контекст
            pending[key] = {
                "ts": time.time(),
                "username": getattr(message, "author", "") or "",
                "text": text[:500],
                "ai_replied": False,
                "acc": client.index,  # каким аккаунтом отвечать
            }
        _save_pending(pending)


# ──────────────────────────────────────────────────────────────────────────────
# Фоновый цикл: проверяем висящие чаты
# ──────────────────────────────────────────────────────────────────────────────

class AIAssistant:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("AIAssistant запущен.")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.exception(f"AIAssistant tick: {e}")
            await asyncio.sleep(60)

    async def _tick(self) -> None:
        cfg = config_manager.settings.ai
        if not cfg.enabled or not cfg.api_key:
            return
        threshold = cfg.auto_reply_after_hours * 3600

        # Шаг 1: читаем под блокировкой, собираем список кандидатов
        async with _pending_lock:
            pending = _load_pending()

        now = time.time()
        candidates = [
            (chat_id_str, data)
            for chat_id_str, data in pending.items()
            if not data.get("ai_replied") and (now - data.get("ts", now)) >= threshold
        ]

        if not candidates:
            return

        # Шаг 2: LLM-запросы ВНЕ блокировки — не держим lock пока ждём сеть
        for chat_id_str, data in candidates:
            answer = await self._ask_llm(
                cfg.provider, cfg.api_key, cfg.model,
                cfg.auto_reply_instruction,
                data.get("username", ""),
                data.get("text", ""),
            )
            if not answer:
                continue

            client = accounts_manager.get(data.get("acc")) or accounts_manager.active()
            sent = client.send_message(int(chat_id_str), answer)
            if sent:
                # Шаг 3: обновляем только этот чат под блокировкой
                async with _pending_lock:
                    pending = _load_pending()
                    if chat_id_str in pending:
                        pending[chat_id_str]["ai_replied"] = True
                        _save_pending(pending)
                logger.info(f"AI ответил в чат {chat_id_str}")

    @staticmethod
    async def _ask_llm(
        provider: str, api_key: str, model: str,
        instruction: str, username: str, user_text: str,
    ) -> Optional[str]:
        """Вызов Anthropic / OpenAI."""
        prompt = (
            f"Покупатель {username} написал: «{user_text}»\n\n"
            f"Ответь ему согласно инструкции выше. Не больше 2-3 коротких предложений."
        )

        if provider == "deepseek":
            return await _call_deepseek(api_key, model, instruction, prompt)
        elif provider == "anthropic":
            return await _call_anthropic(api_key, model, instruction, prompt)
        elif provider == "openai":
            return await _call_openai(api_key, model, instruction, prompt)
        else:
            logger.warning(f"Неизвестный провайдер: {provider}")
            return None


async def _call_deepseek(api_key: str, model: str, system: str, user: str) -> Optional[str]:
    """DeepSeek API — OpenAI-совместимый формат, но другой base URL.

    Регистрация: https://platform.deepseek.com (5М токенов бесплатно, без карты).
    Документация: https://api-docs.deepseek.com
    """
    url = "https://api.deepseek.com/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}
    payload = {
        "model": model,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=30) as r:
                if r.status != 200:
                    logger.warning(f"deepseek {r.status}: {await r.text()}")
                    return None
                data = await r.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"deepseek call: {e}")
    return None


async def _call_anthropic(api_key: str, model: str, system: str, user: str) -> Optional[str]:
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 300,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=30) as r:
                if r.status != 200:
                    logger.warning(f"anthropic {r.status}: {await r.text()}")
                    return None
                data = await r.json()
                blocks = data.get("content", [])
                for b in blocks:
                    if b.get("type") == "text":
                        return b.get("text", "").strip()
    except Exception as e:
        logger.warning(f"anthropic call: {e}")
    return None


async def _call_openai(api_key: str, model: str, system: str, user: str) -> Optional[str]:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}
    payload = {
        "model": model,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=30) as r:
                if r.status != 200:
                    logger.warning(f"openai {r.status}: {await r.text()}")
                    return None
                data = await r.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"openai call: {e}")
    return None


ai_assistant = AIAssistant()


# ──────────────────────────────────────────────────────────────────────────────
# Утилита для функции 29: переписать черновик
# ──────────────────────────────────────────────────────────────────────────────

async def rewrite_draft(text: str) -> str:
    """Улучшает черновик сообщения через ИИ."""
    cfg = config_manager.settings.ai
    if not cfg.enabled or not cfg.api_key or not cfg.rewrite_drafts:
        return text
    instruction = (
        "Ты помогаешь продавцу на FunPay писать профессиональные сообщения. "
        "Возьми черновик и переформулируй его: сделай вежливо, кратко и грамотно. "
        "Без лишних добавлений. Сохрани смысл и тон. Отвечай только переписанным текстом."
    )
    return await call_llm(instruction, text) or text


async def call_llm(system: str, user: str) -> Optional[str]:
    """
    Универсальный вызов LLM (для других модулей: auto_dumping, lot_descriptions и т.д.).

    Использует провайдер и API-ключ из настроек ИИ. Если ИИ выключен или
    нет ключа — возвращает None.
    """
    cfg = config_manager.settings.ai
    if not cfg.enabled or not cfg.api_key:
        return None
    try:
        if cfg.provider == "deepseek":
            return await _call_deepseek(cfg.api_key, cfg.model, system, user)
        elif cfg.provider == "anthropic":
            return await _call_anthropic(cfg.api_key, cfg.model, system, user)
        elif cfg.provider == "openai":
            return await _call_openai(cfg.api_key, cfg.model, system, user)
    except Exception as e:
        logger.warning(f"call_llm: {e}")
    return None
