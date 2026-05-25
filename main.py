"""
PaltoFunPayBot — точка входа.

Если что-то падает на старте — пишет полный traceback в data/logs/startup_error.log
и не закрывает консоль, а ждёт Enter, чтобы можно было прочитать ошибку.
"""
import sys
import traceback
from pathlib import Path
from datetime import datetime


def _show_error_and_wait(err_text: str) -> None:
    """Показывает ошибку и держит консоль открытой."""
    log_dir = Path(__file__).resolve().parent / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    err_file = log_dir / "startup_error.log"
    try:
        with open(err_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 70}\n")
            f.write(f"СТАРТ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'=' * 70}\n")
            f.write(err_text)
            f.write("\n")
    except Exception:
        pass

    print()
    print("=" * 70)
    print("❌ ОШИБКА ПРИ ЗАПУСКЕ БОТА")
    print("=" * 70)
    print(err_text)
    print("=" * 70)
    print(f"📄 Полный лог сохранён: {err_file}")
    print("=" * 70)
    print()
    try:
        input("Нажмите Enter, чтобы закрыть окно...")
    except Exception:
        pass


def _main_safe() -> None:
    """Обёртка — ловит все ошибки старта и пишет их."""
    try:
        # Импорты внутри try — чтобы поймать даже импорт-ошибки
        import asyncio
        import getpass
        import os
        import secrets
        import sys

        from aiogram import Bot, Dispatcher
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        from aiogram.fsm.storage.memory import MemoryStorage
        from aiogram.types import BotCommand

        from utils.logger import logger, setup_logger
        setup_logger()

        # ─── Обработка CLI-аргументов ────────────────────────────────────
        # python main.py --setup-proxy   — заново настроить прокси без перезапуска визарда целиком
        if "--setup-proxy" in sys.argv or "--proxy" in sys.argv:
            from config.settings import config_manager

            def _ask_proxy_only() -> None:
                f = config_manager.settings.funpay
                print("\n" + "=" * 64)
                print(" 🌐 Настройка прокси")
                print("=" * 64 + "\n")

                if f.proxy:
                    print(f"Текущий прокси: {f.proxy.split('@')[-1]}")
                    print("Введите новый, '-' чтобы удалить, или Enter чтобы оставить.")
                else:
                    print("Прокси сейчас не задан.")

                print()
                print("Поддерживаемые форматы:")
                print("  • login:pass@ip:port   (с авторизацией)")
                print("  • ip:port              (без авторизации)")
                print("  • login:pass:ip:port   (альтернативный)")
                print()
                while True:
                    proxy = input("Прокси: ").strip()
                    if not proxy:
                        print("⏭ Без изменений.")
                        return
                    if proxy in ("-", "нет", "none", "off"):
                        f.proxy = ""
                        config_manager.save()
                        print("🗑 Прокси удалён.")
                        return
                    import re
                    if re.search(r"\d+\.\d+\.\d+\.\d+:\d+|@[^:]+:\d+", proxy):
                        f.proxy = proxy
                        config_manager.save()
                        host_port = proxy.split("@")[-1]
                        has_auth = "@" in proxy
                        print(f"✅ Прокси сохранён: {host_port} (авторизация: {'да' if has_auth else 'нет'})")
                        return
                    print("❌ Неверный формат. Пример: login:pass@1.2.3.4:8080")

            _ask_proxy_only()
            print("\nЗапустите бота: python main.py")
            return

        # python main.py --setup-ua   — заново настроить User-Agent
        if "--setup-ua" in sys.argv or "--ua" in sys.argv or "--user-agent" in sys.argv:
            from config.settings import config_manager

            DEFAULT_UA = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
            )

            def _ask_ua_only() -> None:
                f = config_manager.settings.funpay
                print("\n" + "=" * 64)
                print(" 🪪 Настройка User-Agent для FunPay")
                print("=" * 64 + "\n")
                print("⚠️ КРИТИЧЕСКИ ВАЖНО! Без браузерного User-Agent FunPay не отдаёт")
                print("   чаты и сообщения.\n")

                if f.user_agent:
                    short = f.user_agent[:80] + ("..." if len(f.user_agent) > 80 else "")
                    print(f"Текущий: {short}\n")

                print("Чтобы получить ваш РЕАЛЬНЫЙ User-Agent (рекомендуется):")
                print("  1. Откройте https://www.whatsmyua.info/ в браузере")
                print("  2. Скопируйте 'Your User Agent String'")
                print("  3. Вставьте сюда\n")
                print("Или нажмите Enter — поставится стандартный (Chrome 109).\n")

                ua = input("User-Agent: ").strip()
                if not ua:
                    f.user_agent = DEFAULT_UA
                    print(f"✅ Установлен стандартный: {DEFAULT_UA[:60]}...")
                elif "python-requests" in ua:
                    print("❌ Это python-requests — именно тот UA что не работает!")
                    print("   Попробуйте ещё раз.")
                    return _ask_ua_only()
                elif len(ua) < 30:
                    print("❌ User-Agent слишком короткий.")
                    return _ask_ua_only()
                else:
                    f.user_agent = ua
                    print(f"✅ User-Agent сохранён.")
                config_manager.save()

            _ask_ua_only()
            print("\nЗапустите бота: python main.py")
            return

        from config.settings import config_manager
        from core.event_bus import Event, event_bus
        from core.funpay_client import FUNPAY_AVAILABLE, FUNPAY_IMPORT_ERROR, funpay_client
        from utils.console_banner import print_startup_banner

        print_startup_banner(funpay_available=FUNPAY_AVAILABLE)

        # Предупреждение если FunPayAPI не загрузилась
        if not FUNPAY_AVAILABLE:
            print()
            print("⚠️ " + "=" * 66)
            print(" ВНИМАНИЕ: библиотека FunPayAPI не загрузилась.")
            print("=" * 70)
            if "operand" in str(FUNPAY_IMPORT_ERROR) or "unsupported" in str(FUNPAY_IMPORT_ERROR):
                py_ver = ".".join(map(str, sys.version_info[:3]))
                print(f" Причина: ваша версия Python ({py_ver}) не поддерживает")
                print(" современный синтаксис типов, который использует FunPayAPI.")
                print()
                print(" 🔧 РЕШЕНИЕ: установите Python 3.10 или новее с python.org")
                print(" После установки — переустановите зависимости в новом venv:")
                print("   python -m venv venv")
                print("   venv\\Scripts\\activate         (Windows)")
                print("   pip install -r requirements.txt")
            else:
                print(f" Ошибка: {FUNPAY_IMPORT_ERROR}")
                print(" Установите: pip install FunPayAPI")
            print()
            print(" Бот запустится, но БЕЗ подключения к FunPay (только Telegram-меню).")
            print("=" * 70)
            print()

        # ─── CLI-визард ──────────────────────────────────────────────────────

        def _ask(prompt: str, secret: bool = False) -> str:
            if secret:
                try:
                    return getpass.getpass(prompt).strip()
                except Exception:
                    return input(prompt).strip()
            return input(prompt).strip()

        def cli_initial_setup() -> None:
            s = config_manager.settings.telegram
            f = config_manager.settings.funpay

            DEFAULT_UA = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
            )

            print("\n" + "=" * 64)
            print(" 🤖 PaltoFunPayBot — первичная настройка")
            print("=" * 64 + "\n")

            if not s.token:
                print("Шаг 1/4. Создайте бота через @BotFather и получите токен.")
                print("Формат: 1234567890:AAH...30+ символов")
                while True:
                    token = _ask("Токен Telegram-бота: ")
                    if ":" in token and len(token) >= 35:
                        s.token = token
                        break
                    print("❌ Токен похоже некорректный.")
                print("✅ Токен сохранён.\n")

            if not s.admin_password:
                print("Шаг 2/4. Придумайте пароль администратора.")
                print("Этот пароль вы введёте при первом /start в TG-боте.")
                print("Оставьте пустым, чтобы сгенерировать случайный.")
                pwd = _ask("Пароль админа: ", secret=True)
                if not pwd:
                    pwd = secrets.token_urlsafe(12)
                    print(f"📋 Сгенерирован: {pwd}")
                    print("   ❗ Сохраните его!")
                s.admin_password = pwd
                print("✅ Пароль сохранён.\n")

            if not f.proxy:
                print("Шаг 3/4. Прокси (опционально, но РЕКОМЕНДУЕТСЯ).")
                print("Бот сам ходит через прокси с авторизацией — Proxifier не нужен.")
                print("Один и тот же прокси используется для FunPay и Telegram.")
                print()
                print("Поддерживаемые форматы:")
                print("  • login:pass@ip:port   (с авторизацией — рекомендуется)")
                print("  • ip:port              (без авторизации)")
                print("  • login:pass:ip:port   (альтернативный)")
                print()
                print("Оставьте пустым, чтобы пропустить (бот пойдёт напрямую).")
                while True:
                    proxy = _ask("Прокси: ").strip()
                    if not proxy:
                        print("⏭ Шаг пропущен. Прокси можно настроить позже:")
                        print("   /menu → 🔧 Система → 🌐 Прокси")
                        break
                    # Минимальная валидация — должно быть ip:port или с @
                    import re
                    if re.search(r"\d+\.\d+\.\d+\.\d+:\d+|@[^:]+:\d+", proxy):
                        f.proxy = proxy
                        host_port = proxy.split("@")[-1]
                        has_auth = "@" in proxy
                        print(f"✅ Прокси сохранён: {host_port} (авторизация: {'да' if has_auth else 'нет'})")
                        break
                    print("❌ Неверный формат. Пример: login:pass@1.2.3.4:8080")
                print()

            # User-Agent — критически важно! Без него FunPay видит python-requests
            # и не отдаёт чаты/сообщения. По умолчанию — Chrome на Win10 (как Cardinal).
            need_ua_setup = (not f.user_agent) or "python-requests" in f.user_agent
            if need_ua_setup:
                print("Шаг 4/4. User-Agent для FunPay.")
                print("⚠️ КРИТИЧЕСКИ ВАЖНО! Без браузерного User-Agent FunPay не отдаёт")
                print("   чаты и сообщения (видит что вы — бот).")
                print()
                print(f"По умолчанию: {DEFAULT_UA[:60]}...")
                print()
                print("Чтобы получить ваш РЕАЛЬНЫЙ User-Agent (рекомендуется):")
                print("  1. Откройте https://www.whatsmyua.info/ в браузере")
                print("  2. Скопируйте строку 'Your User Agent String'")
                print("  3. Вставьте сюда")
                print()
                print("Можно нажать Enter — будет использован стандартный (Chrome 109).")
                ua = _ask("User-Agent (Enter — по умолчанию): ").strip()
                f.user_agent = ua if ua else DEFAULT_UA
                print(f"✅ User-Agent сохранён.\n")

            config_manager.save()
            print("=" * 64)
            print(" ✅ Базовая настройка завершена.")
            print()
            print(" Откройте TG-бота → /start → введите пароль админа.")
            print(" Дальше — введёте golden_key прямо в боте.")
            print("=" * 64 + "\n")

        # ─── Слэш-команды в меню Telegram ───────────────────────────────────

        BOT_COMMANDS = [
            BotCommand(command="menu", description="открыть главное меню"),
            BotCommand(command="profile", description="посмотреть статистику аккаунта"),
            BotCommand(command="balance", description="посмотреть баланс аккаунта"),
            BotCommand(command="restart", description="перезапустить бота"),
            BotCommand(command="golden_key", description="изменить токен доступа"),
            BotCommand(command="ban", description="добавить пользователя в чёрный список"),
            BotCommand(command="unban", description="удалить пользователя из чёрного списка"),
            BotCommand(command="black_list", description="показать чёрный список"),
            BotCommand(command="upload_chat_img", description="(чат) загрузить изображение на FunPay"),
            BotCommand(command="upload_offer_img", description="(лот) загрузить изображение на FunPay"),
            BotCommand(command="test_lot", description="создать тестовый ключ автовыдачи"),
            BotCommand(command="logs", description="скачать текущий лог-файл"),
            BotCommand(command="about", description="информация о боте"),
            BotCommand(command="sys", description="системная информация и нагрузка"),
            BotCommand(command="del_logs", description="удалить старые лог-файлы"),
            BotCommand(command="power_off", description="выключить бота"),
            BotCommand(command="watermark", description="изменить водяной знак"),
            BotCommand(command="check_updates", description="проверить наличие обновлений"),
            BotCommand(command="plugins", description="каталог плагинов"),
            BotCommand(command="test_msg", description="тестовое уведомление о сообщении"),
            BotCommand(command="login", description="ввести 2FA-код"),
            BotCommand(command="2fa_setup", description="включить 2FA"),
            BotCommand(command="2fa_off", description="выключить 2FA"),
            BotCommand(command="audit", description="журнал действий"),
            BotCommand(command="export_settings", description="скачать бэкап настроек"),
            BotCommand(command="gen_desc", description="сгенерировать описание лота через ИИ"),
        ]

        # ─── Запуск фоновых сервисов ─────────────────────────────────────────

        async def start_background_modules() -> None:
            from modules.online_keeper import online_keeper
            from modules.auto_lift import auto_lift
            from modules.ai_assistant import ai_assistant
            from modules.auto_deactivation import auto_restore
            from modules.auto_withdraw import auto_withdraw
            from modules.anti_dumping import anti_dumping

            import modules.auto_greeting       # noqa: F401
            import modules.auto_response       # noqa: F401
            import modules.auto_delivery       # noqa: F401
            import modules.auto_review_reply   # noqa: F401
            import modules.order_reactions     # noqa: F401
            import modules.notifications       # noqa: F401
            import modules.analytics           # noqa: F401

            await online_keeper.start()
            await auto_lift.start()
            await ai_assistant.start()
            await auto_restore.start()
            await auto_withdraw.start()
            await anti_dumping.start()
            logger.info("Фоновые модули запущены.")

        async def start_funpay_if_configured() -> None:
            fp = config_manager.settings.funpay
            if not fp.golden_key:
                logger.info("FunPay не настроен — введите golden_key в Telegram-боте.")
                return
            if not FUNPAY_AVAILABLE:
                logger.warning("FunPayAPI не установлена. pip install FunPayAPI")
                return
            loop = asyncio.get_event_loop()
            success, message = await loop.run_in_executor(None, funpay_client.connect)
            if not success:
                logger.error(f"FunPay не подключился: {message}")
                return
            asyncio.create_task(funpay_client.start_polling())

            # Keep-alive чтобы прокси не закрывал idle-соединение
            try:
                from utils.proxy_keepalive import proxy_keepalive
                await proxy_keepalive.start()
            except Exception as e:
                logger.debug(f"ProxyKeepAlive не запустился: {e}")

        # ─── Главная корутина ────────────────────────────────────────────────

        async def main() -> None:
            cfg = config_manager.settings.telegram
            fcfg = config_manager.settings.funpay
            ua_broken = (not fcfg.user_agent) or "python-requests" in fcfg.user_agent
            if not cfg.token or not cfg.admin_password or ua_broken:
                cli_initial_setup()

            # Если задан прокси — Telegram-бот тоже идёт через него.
            # Это решает блокировку Telegram в РФ + работает с авторизованными прокси.
            from aiogram.client.session.aiohttp import AiohttpSession
            session = None
            proxy_str = config_manager.settings.funpay.proxy
            if proxy_str:
                # Нормализация: login:pass:ip:port → login:pass@ip:port
                if "@" not in proxy_str and proxy_str.count(":") == 3:
                    login, password, ip, port = proxy_str.split(":")
                    proxy_str = f"{login}:{password}@{ip}:{port}"
                proxy_url = f"http://{proxy_str}"
                # Проверяем доступна ли aiohttp-socks (нужна aiogram для прокси с авторизацией)
                try:
                    import aiohttp_socks  # noqa: F401
                except ImportError:
                    logger.error(
                        "❌ Прокси задан, но не установлена aiohttp-socks.\n"
                        "Установите её для поддержки прокси в Telegram-боте:\n"
                        "    pip install aiohttp-socks"
                    )
                    print("\n" + "=" * 64)
                    print(" ⚠️ ВНИМАНИЕ — нужна доп. библиотека для прокси")
                    print("=" * 64)
                    print(" Запустите команду:")
                    print("    pip install aiohttp-socks")
                    print(" И снова запустите бота.")
                    print("=" * 64 + "\n")
                    return
                try:
                    session = AiohttpSession(proxy=proxy_url)
                    logger.info(f"Telegram через прокси: {proxy_str.split('@')[-1]}")
                except Exception as e:
                    logger.warning(f"Не удалось настроить прокси для Telegram: {e}")

            bot = Bot(
                token=config_manager.settings.telegram.token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
                session=session,
            )
            dp = Dispatcher(storage=MemoryStorage())

            from modules.notifications import set_bot
            set_bot(bot)

            from bot.handlers.setup import router as setup_router
            from bot.handlers.commands import router as commands_router
            from bot.handlers.menu import router as menu_router
            from bot.handlers.auth import router as auth_router
            from bot.handlers.quick_reply import router as qr_router
            from bot.handlers.plugins import router as plugins_router
            from bot.handlers.twofa import router as twofa_router
            from bot.middleware import AuditMiddleware, TwoFAMiddleware, ErrorMiddleware

            # Middleware для всех событий (audit + error + 2FA)
            # ErrorMiddleware первая — она ловит все исключения
            dp.message.middleware(ErrorMiddleware())
            dp.callback_query.middleware(ErrorMiddleware())
            dp.message.middleware(AuditMiddleware())
            dp.callback_query.middleware(AuditMiddleware())
            dp.message.middleware(TwoFAMiddleware())
            dp.callback_query.middleware(TwoFAMiddleware())

            dp.include_router(setup_router)
            dp.include_router(twofa_router)      # /login, /2fa_setup и т.д. — до auth
            dp.include_router(commands_router)
            dp.include_router(qr_router)         # быстрый ответ из TG — до menu/auth
            dp.include_router(menu_router)
            dp.include_router(plugins_router)    # /plugins + диспатчер команд плагинов
            dp.include_router(auth_router)       # catch-all для неавторизованных — последним

            try:
                await bot.set_my_commands(BOT_COMMANDS)
            except Exception as e:
                logger.warning(f"set_my_commands: {e}")

            from plugins.manager import plugin_manager
            plugin_manager.reload_all()

            await start_background_modules()
            await start_funpay_if_configured()

            me = await bot.get_me()
            logger.info(f"Telegram-бот запущен: @{me.username}")
            logger.info(f"FunPayAPI: {'установлен' if FUNPAY_AVAILABLE else 'НЕ установлен'}")

            # Шлём BOT_STARTED — Telegram-бот точно подключился, можно уведомлять
            # Если FunPay подключён, передаём account, иначе None
            try:
                fp_account = funpay_client.account if funpay_client.account else None
                await event_bus.emit(Event.BOT_STARTED, fp_account)
            except Exception as e:
                logger.warning(f"Не удалось отправить BOT_STARTED: {e}")

            try:
                await dp.start_polling(bot, handle_signals=False)
            finally:
                # Сначала шлём уведомление в TG, ПОКА сессия живая
                try:
                    await event_bus.emit(Event.BOT_STOPPED)
                except Exception as e:
                    logger.warning(f"Не удалось отправить BOT_STOPPED: {e}")
                # Потом закрываем всё
                funpay_client.stop()
                try:
                    await bot.session.close()
                except Exception:
                    pass
                logger.info("Бот остановлен.")

        # ─── Запуск ──────────────────────────────────────────────────────────

        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\nОстановка по Ctrl+C.")
        except SystemExit:
            # Если кто-то позвал /power_off → sys.exit
            pass

    except Exception:
        # Полный traceback с указанием файла и строки
        err = traceback.format_exc()
        _show_error_and_wait(err)
        sys.exit(1)


if __name__ == "__main__":
    _main_safe()
