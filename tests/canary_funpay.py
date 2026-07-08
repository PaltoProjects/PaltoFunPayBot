# -*- coding: utf-8 -*-
"""
Канарейка: жив ли парсер FunPayAPI против настоящего funpay.com.

Ходит на сайт БЕЗ авторизации и проверяет все якоря вёрстки, на которые
опирается встроенный форк FunPayAPI. Если FunPay изменил HTML — канарейка
падает раньше, чем это заметят пользователи бота.

Запуск вручную:            python tests/canary_funpay.py
В CI: .github/workflows/canary.yml (ежедневно, при падении создаёт issue).

Файл намеренно называется без префикса test_ — pytest его не собирает,
чтобы обычный прогон тестов не ходил в сеть.
"""
import json
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36")

ok = []
fail = []


def check(name, cond, detail=""):
    (ok if cond else fail).append(name)
    print(("[PASS] " if cond else "[FAIL] ") + name + (f" — {detail}" if detail and not cond else ""))


def main() -> int:
    # ── 1. Главная страница ──────────────────────────────────────────────────
    r = requests.get("https://funpay.com/", headers={"User-Agent": UA}, timeout=30)
    check("GET funpay.com == 200", r.status_code == 200, f"status={r.status_code}")
    parser = BeautifulSoup(r.content.decode("utf-8", errors="replace"), "lxml")

    body = parser.find("body")
    app_data_raw = body.get("data-app-data") if body else None
    check("body[data-app-data] присутствует", bool(app_data_raw))
    app_data = {}
    if app_data_raw:
        try:
            app_data = json.loads(app_data_raw)
        except Exception as e:
            check("data-app-data валидный JSON", False, str(e))
    check("app-data содержит csrf-token", "csrf-token" in app_data, f"keys={list(app_data)[:8]}")
    check("app-data содержит userId", "userId" in app_data, f"keys={list(app_data)[:8]}")
    check("аноним: нет user-link-name", parser.find("div", {"class": "user-link-name"}) is None)
    check("аноним: нет menu-item-logout", parser.find("a", class_="menu-item-logout") is None)

    # ── 2. Категории (__setup_categories) ────────────────────────────────────
    games_table = parser.find_all("div", {"class": "promo-game-list"})
    check("promo-game-list найден", bool(games_table))
    subcat_urls = []
    if games_table:
        gt = games_table[1] if len(games_table) > 1 else games_table[0]
        games_divs = gt.find_all("div", {"class": "promo-game-item"})
        check("promo-game-item найдены", bool(games_divs), "категорий: 0")
        if games_divs:
            with_id = [g for g in games_divs if g.find("div", {"class": "game-title"})
                       and g.find("div", {"class": "game-title"}).get("data-id")]
            check("game-title[data-id] парсится", bool(with_id))
            print(f"       (категорий игр на главной: {len(games_divs)})")
            for g in games_divs:
                for a in g.find_all("a", href=re.compile(r"/lots/\d+")):
                    subcat_urls.append(a["href"])

    # ── 3. Страница лотов подкатегории ───────────────────────────────────────
    # Подкатегория может быть легально пустой (0 лотов) — перебираем несколько,
    # падаем только если лоты не нашлись НИ на одной из проверенных страниц.
    check("ссылки на подкатегории лотов найдены", bool(subcat_urls))
    items, checked_url, pages_tried = [], None, 0
    for url in subcat_urls[:8]:
        if url.startswith("/"):
            url = "https://funpay.com" + url
        r2 = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        pages_tried += 1
        if r2.status_code != 200:
            continue
        p2 = BeautifulSoup(r2.content.decode("utf-8", errors="replace"), "lxml")
        items = p2.find_all("a", {"class": "tc-item"})
        if items:
            checked_url = url
            break
    check("tc-item (лоты) найдены хотя бы в одной подкатегории", bool(items),
          f"проверено страниц: {pages_tried}")
    if items:
        it = items[0]
        check("tc-desc-text парсится", it.find("div", {"class": "tc-desc-text"}) is not None)
        price = it.find("div", {"class": "tc-price"})
        check("tc-price парсится", price is not None)
        check("tc-price span.unit (валюта) парсится",
              price is not None and price.find("span", class_="unit") is not None)
        print(f"       (лотов: {len(items)} на {checked_url})")

    # ── 4. Account с фейковым ключом → UnauthorizedError ─────────────────────
    from FunPayAPI import Account
    from FunPayAPI.common import exceptions

    acc = Account("a" * 32, user_agent=UA)
    try:
        acc.get()
        check("fake golden_key -> UnauthorizedError", False, "get() прошёл без ошибки?!")
    except exceptions.UnauthorizedError:
        check("fake golden_key -> UnauthorizedError", True)
    except Exception as e:
        check("fake golden_key -> UnauthorizedError", False, f"{type(e).__name__}: {e}")

    # ── Итог ─────────────────────────────────────────────────────────────────
    print()
    if fail:
        print(f"ПРОВАЛОВ: {len(fail)} из {len(ok) + len(fail)}")
        for f in fail:
            print(f"  • {f}")
        return 1
    print(f"ВСЕ {len(ok)} ПРОВЕРОК ПРОШЛИ — парсер FunPayAPI актуален")
    return 0


if __name__ == "__main__":
    sys.exit(main())
