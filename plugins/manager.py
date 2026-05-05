"""
Менеджер плагинов PaltoFunPayBot.

Использует расширенный PluginAPI из plugins.api.
Поддерживает: команды, callback-кнопки, подписки на события, обработчики тегов лотов.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, List, Optional, Tuple

from config.settings import config_manager
from core.event_bus import event_bus
from core.funpay_client import funpay_client
from utils.logger import logger

from plugins.api import PluginAPI, PluginScheduler


# ──────────────────────────────────────────────────────────────────────────────
# Плагин в памяти
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class LoadedPlugin:
    id: str
    name: str
    version: str
    author: str
    description: str
    command: str = ""
    lot_tag: str = ""
    emoji: str = "🧩"
    available: bool = True
    module: Optional[ModuleType] = None
    enabled: bool = False
    pinned: bool = False
    api: Optional[PluginAPI] = None
    # Регистрации
    commands: Dict[str, Callable] = field(default_factory=dict)
    callbacks: Dict[str, Callable] = field(default_factory=dict)  # prefix → handler
    event_handlers: List[Tuple[str, Callable]] = field(default_factory=list)  # (event_name, fn)
    tag_handlers: Dict[str, Callable] = field(default_factory=dict)  # tag → handler


# ──────────────────────────────────────────────────────────────────────────────
# Менеджер
# ──────────────────────────────────────────────────────────────────────────────

class PluginManager:
    def __init__(self) -> None:
        self.plugins_dir = Path(__file__).resolve().parent.parent / "plugins_user"
        self.plugins_dir.mkdir(exist_ok=True)
        self._loaded: Dict[str, LoadedPlugin] = {}

    # ─── Список / получение ─────────────────────────────────────────────────

    def list_plugins(self) -> List[dict]:
        items = list(self._loaded.values())
        items.sort(key=lambda p: (not p.pinned, p.name.lower()))
        return [self._to_dict(p) for p in items]

    def get(self, plugin_id: str) -> Optional[dict]:
        p = self._loaded.get(plugin_id)
        return self._to_dict(p) if p else None

    @staticmethod
    def _to_dict(p: LoadedPlugin) -> dict:
        return {
            "id": p.id, "name": p.name, "version": p.version,
            "author": p.author, "description": p.description,
            "command": p.command, "lot_tag": p.lot_tag, "emoji": p.emoji,
            "available": p.available, "enabled": p.enabled, "pinned": p.pinned,
        }

    # ─── Загрузка ────────────────────────────────────────────────────────────

    def reload_all(self) -> int:
        # Выгружаем все
        for p in list(self._loaded.values()):
            self._teardown(p)
        self._loaded.clear()

        if not config_manager.settings.plugins.enabled:
            logger.info("Система плагинов выключена.")
            return 0

        for path in sorted(self.plugins_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                self._load_one(path)
            except Exception as e:
                logger.exception(f"Не удалось загрузить плагин {path.name}: {e}")

        logger.info(f"Загружено плагинов: {len(self._loaded)}")
        return len(self._loaded)

    def _load_one(self, path: Path) -> None:
        spec = importlib.util.spec_from_file_location(f"user_plugin_{path.stem}", path)
        if not spec or not spec.loader:
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        info = getattr(module, "PLUGIN_INFO", None)
        if not isinstance(info, dict) or "id" not in info:
            logger.warning(f"Плагин {path.name} не содержит PLUGIN_INFO — пропускаю.")
            return

        pid = info["id"]
        cfg = config_manager.settings.plugins
        enabled = pid in cfg.enabled_plugins
        pinned = pid in cfg.pinned_plugins

        plugin = LoadedPlugin(
            id=pid,
            name=info.get("name", pid),
            version=info.get("version", "0.0.1"),
            author=info.get("author", "—"),
            description=info.get("description", ""),
            command=(info.get("command") or "").lstrip("/"),
            lot_tag=info.get("lot_tag", ""),
            emoji=info.get("emoji", "🧩"),
            available=bool(info.get("available", True)),
            module=module,
            enabled=enabled,
            pinned=pinned,
        )
        self._loaded[pid] = plugin

        if enabled and plugin.available and hasattr(module, "setup"):
            try:
                api = PluginAPI(self, pid)
                plugin.api = api
                module.setup(api)
            except Exception as e:
                logger.exception(f"setup({pid}): {e}")
                plugin.enabled = False

        logger.info(f"Плагин загружен: {plugin.name} v{plugin.version} ({'✅' if enabled else '❌'})")

    def _teardown(self, p: LoadedPlugin) -> None:
        # 1) Вызываем teardown() если есть
        if p.module and hasattr(p.module, "teardown") and p.api:
            try:
                p.module.teardown(p.api)
            except Exception as e:
                logger.warning(f"teardown {p.id}: {e}")

        # 2) Снимаем подписки на event_bus
        for event_name, fn in p.event_handlers:
            try:
                if event_name in event_bus._handlers:
                    handlers_list = event_bus._handlers[event_name]
                    handlers_list[:] = [h for h in handlers_list if getattr(h, "fn", None) != fn and h != fn]
            except Exception:
                pass

        # 3) Останавливаем все scheduled tasks
        if p.api and p.api.scheduler:
            p.api.scheduler.cancel_all()

        # 4) Закрываем HTTP-сессию
        if p.api and p.api.http:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(p.api.http.close())
            except Exception:
                pass

        # 5) Чистим регистрации
        p.commands.clear()
        p.callbacks.clear()
        p.event_handlers.clear()
        p.tag_handlers.clear()

    # ─── ВКЛ/ВЫКЛ ────────────────────────────────────────────────────────────

    def toggle(self, plugin_id: str) -> bool:
        if plugin_id not in self._loaded:
            return False
        cfg = config_manager.settings.plugins
        if plugin_id in cfg.enabled_plugins:
            cfg.enabled_plugins.remove(plugin_id)
        else:
            cfg.enabled_plugins.append(plugin_id)
        config_manager.save()
        self.reload_all()
        return True

    def toggle_pin(self, plugin_id: str) -> bool:
        if plugin_id not in self._loaded:
            return False
        cfg = config_manager.settings.plugins
        if plugin_id in cfg.pinned_plugins:
            cfg.pinned_plugins.remove(plugin_id)
            self._loaded[plugin_id].pinned = False
        else:
            cfg.pinned_plugins.append(plugin_id)
            self._loaded[plugin_id].pinned = True
        config_manager.save()
        return True

    # ─── Регистрация хэндлеров ───────────────────────────────────────────────

    def register_command(self, plugin_id: str, name: str, fn: Callable) -> None:
        if plugin_id in self._loaded:
            self._loaded[plugin_id].commands[name] = fn

    def register_callback(self, plugin_id: str, prefix: str, fn: Callable) -> None:
        if plugin_id in self._loaded:
            self._loaded[plugin_id].callbacks[prefix] = fn

    def register_handler(self, plugin_id: str, event_name: str, fn: Callable) -> None:
        if plugin_id in self._loaded:
            self._loaded[plugin_id].event_handlers.append((event_name, fn))

    def register_tag_handler(self, plugin_id: str, tag: str, fn: Callable) -> None:
        if plugin_id in self._loaded:
            self._loaded[plugin_id].tag_handlers[tag] = fn

    # ─── Поиск активных хэндлеров ────────────────────────────────────────────

    def get_command(self, name: str) -> Optional[Callable]:
        """Активный обработчик команды (только включённых плагинов)."""
        name = name.lstrip("/")
        for p in self._loaded.values():
            if not p.enabled or not p.available:
                continue
            if name in p.commands:
                return p.commands[name]
        return None

    def find_plugin_by_command(self, name: str) -> Optional[LoadedPlugin]:
        name = name.lstrip("/")
        for p in self._loaded.values():
            if name in p.commands or p.command == name:
                return p
        return None

    def get_callback_handler(self, callback_data: str) -> Optional[Tuple[Callable, str]]:
        """
        По callback_data вида 'plg_<id>:<prefix>:<rest>' находит обработчик плагина.
        Возвращает (handler, rest_data) или None.
        """
        if not callback_data.startswith("plg_"):
            return None
        try:
            _, rest = callback_data.split(":", 1)
            full = callback_data[len("plg_"):]  # <id>:<prefix>:<rest>
            parts = full.split(":", 2)
            if len(parts) < 2:
                return None
            plugin_id = parts[0]
            prefix = parts[1]
            data = parts[2] if len(parts) > 2 else ""
        except Exception:
            return None

        plugin = self._loaded.get(plugin_id)
        if not plugin or not plugin.enabled or not plugin.available:
            return None
        handler = plugin.callbacks.get(prefix)
        if not handler:
            return None
        return handler, data

    def find_tag_handlers(self, lot_description: str) -> List[Tuple[str, Callable]]:
        """
        Возвращает список (plugin_id, handler) для тех плагинов, чей tag найден в описании.
        Только включённые плагины.
        """
        out = []
        if not lot_description:
            return out
        for p in self._loaded.values():
            if not p.enabled or not p.available:
                continue
            for tag, fn in p.tag_handlers.items():
                if tag and tag in lot_description:
                    out.append((p.id, fn))
        return out


plugin_manager = PluginManager()


# ──────────────────────────────────────────────────────────────────────────────
# Глобальная подписка на ORDER_PAID для tag-handlers
# ──────────────────────────────────────────────────────────────────────────────

@event_bus.on("order_paid")
async def _dispatch_tag_handlers(order) -> None:
    """
    При оплате заказа смотрит описание лота на наличие тегов плагинов.
    Если найден — вызывает handler плагина.
    """
    description = (
        getattr(order, "description", "") or
        getattr(order, "title", "") or
        ""
    )
    if not description:
        return

    handlers = plugin_manager.find_tag_handlers(description)
    for plugin_id, handler in handlers:
        try:
            await handler(order, description)
            logger.info(f"Tag-handler[{plugin_id}] обработал заказ {getattr(order, 'id', '?')}")
        except Exception as e:
            logger.exception(f"Tag-handler[{plugin_id}]: {e}")
