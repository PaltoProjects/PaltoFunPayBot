"""
Диагностика установки PaltoFunPayBot.

Запускается отдельно: python check.py
Проверяет всё нужное и говорит, что не так. Окно не закрывается.
"""
import sys
import platform
from pathlib import Path

# Windows: при перенаправленном выводе (pipe, > файл) stdout использует
# cp1251/cp866 и падает на эмодзи. Принудительно UTF-8 с заменой символов.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    print("=" * 70)
    print(" 🔍 ДИАГНОСТИКА FUNPAYBOT")
    print("=" * 70)
    print()

    errors = []
    warnings = []

    # ─── 1. Версия Python ────────────────────────────────────────────────────
    py_ver = sys.version_info
    print(f"1. Python: {platform.python_version()} ({sys.executable})")
    if py_ver < (3, 8):
        errors.append(f"Python {platform.python_version()} слишком старый. Нужен 3.10+")
    elif py_ver < (3, 10):
        warnings.append(
            f"Python {platform.python_version()} устарел. "
            f"FunPayAPI требует Python 3.10+. "
            f"Скачайте новый Python с https://www.python.org/downloads/"
        )
    print()

    # ─── 2. Зависимости ──────────────────────────────────────────────────────
    print("2. Проверка зависимостей:")
    deps = [
        ("aiogram", "3.4.0"),
        ("FunPayAPI", "1.0.0"),
        ("pydantic", "2.0.0"),
        ("loguru", "0.7.0"),
        ("aiohttp", "3.9.0"),
        ("requests", "2.28.1"),
        ("psutil", "5.9.0"),
    ]
    for pkg, min_ver in deps:
        try:
            mod = __import__(pkg.lower().replace("-", "_") if pkg != "FunPayAPI" else "FunPayAPI")
            ver = getattr(mod, "__version__", "?")
            print(f"   ✅ {pkg} {ver}")
        except ImportError:
            print(f"   ❌ {pkg} — НЕ УСТАНОВЛЕН")
            errors.append(f"Не установлен пакет {pkg}. Запустите: pip install -r requirements.txt")
        except (TypeError, SyntaxError) as e:
            # FunPayAPI на Python 3.8 падает с TypeError из-за PEP-604 синтаксиса
            print(f"   ❌ {pkg} — несовместим с вашим Python")
            if pkg == "FunPayAPI":
                errors.append(
                    f"FunPayAPI не работает на Python {platform.python_version()}. "
                    f"Установите Python 3.10+ с python.org и переустановите зависимости."
                )
            else:
                errors.append(f"{pkg}: {e}")
        except Exception as e:
            print(f"   ⚠️  {pkg} — ошибка импорта: {e}")
            warnings.append(f"{pkg}: {e}")
    print()

    # ─── 3. Структура проекта ────────────────────────────────────────────────
    print("3. Структура проекта:")
    base = Path(__file__).resolve().parent
    required = [
        "config/settings.py",
        "core/funpay_client.py",
        "bot/handlers/menu.py",
        "modules/auto_delivery.py",
        "main.py",
    ]
    for rel in required:
        p = base / rel
        if p.exists():
            print(f"   ✅ {rel}")
        else:
            print(f"   ❌ {rel} — ОТСУТСТВУЕТ")
            errors.append(f"Не найден файл {rel}")
    print()

    # ─── 4. Импорт всех модулей бота ─────────────────────────────────────────
    print("4. Импорт модулей бота:")
    sys.path.insert(0, str(base))
    modules_to_check = [
        "config.settings",
        "core.event_bus",
        "core.proxy_check",
        "core.funpay_client",
        "bot.states",
        "bot.keyboards",
        "bot.handlers.auth",
        "bot.handlers.setup",
        "bot.handlers.commands",
        "bot.handlers.menu",
        "modules.online_keeper",
        "modules.auto_lift",
        "modules.auto_greeting",
        "modules.auto_response",
        "modules.auto_delivery",
        "modules.auto_review_reply",
        "modules.order_reactions",
        "modules.auto_deactivation",
        "modules.notifications",
        "modules.ai_assistant",
        "plugins.manager",
    ]
    import importlib
    for mod in modules_to_check:
        try:
            importlib.import_module(mod)
            print(f"   ✅ {mod}")
        except Exception as e:
            print(f"   ❌ {mod}")
            print(f"       {type(e).__name__}: {e}")
            errors.append(f"{mod}: {type(e).__name__}: {e}")
    print()

    # ─── 5. Доступ к директории data ─────────────────────────────────────────
    print("5. Запись в директорию data:")
    try:
        data_dir = base / "data"
        data_dir.mkdir(exist_ok=True)
        test_file = data_dir / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        print(f"   ✅ {data_dir} доступна для записи")
    except Exception as e:
        print(f"   ❌ {data_dir}: {e}")
        errors.append(f"Нет доступа к data/: {e}")
    print()

    # ─── ИТОГ ────────────────────────────────────────────────────────────────
    print("=" * 70)
    if errors:
        print(f"❌ ПРОБЛЕМЫ ({len(errors)}):")
        for e in errors:
            print(f"   • {e}")
        print()
        print("Исправьте ошибки выше и запустите check.py снова.")
    elif warnings:
        print(f"⚠️  ПРЕДУПРЕЖДЕНИЯ ({len(warnings)}):")
        for w in warnings:
            print(f"   • {w}")
        print()
        print("Эти проблемы не критичны — бот должен работать. Запускайте python main.py")
    else:
        print("🎉 ВСЁ В ПОРЯДКЕ! Можно запускать: python main.py")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print()
        print("=" * 70)
        print("❌ ОШИБКА при работе диагностики:")
        print("=" * 70)
        print(traceback.format_exc())
    finally:
        print()
        try:
            input("Нажмите Enter для выхода...")
        except Exception:
            pass
