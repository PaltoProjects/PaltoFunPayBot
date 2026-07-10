# -*- coding: utf-8 -*-
"""
Оффлайн-юниты PaltoFunPayBot (без сети и без FunPay-аккаунта).

Запуск:  pytest tests/ -v
"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ──────────────────────────────────────────────────────────────────────────────
# utils/passwords.py
# ──────────────────────────────────────────────────────────────────────────────

def test_password_hash_and_verify():
    from utils.passwords import BCRYPT_AVAILABLE, hash_password, is_hashed, verify_password

    assert BCRYPT_AVAILABLE, "bcrypt должен быть установлен (см. requirements.txt)"
    h = hash_password("secret123")
    assert is_hashed(h)
    assert h != "secret123"
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)


def test_password_legacy_plaintext():
    from utils.passwords import verify_password

    # Немигрированный конфиг: плейнтекст сравнивается (timing-safe)
    assert verify_password("legacy-pw", "legacy-pw")
    assert not verify_password("legacy-pw", "other")
    assert not verify_password("", "x")
    assert not verify_password("x", "")


# ──────────────────────────────────────────────────────────────────────────────
# config/settings.py — миграция пароля
# ──────────────────────────────────────────────────────────────────────────────

def test_config_password_migration(tmp_path):
    from config.settings import ConfigManager, Settings
    from utils.passwords import is_hashed, verify_password

    cfg_path = tmp_path / "config.json"
    s = Settings()
    s.telegram.admin_password = "plaintext-pw"
    cfg_path.write_text(json.dumps(s.model_dump(), ensure_ascii=False), encoding="utf-8")

    cm = ConfigManager(cfg_path)
    assert is_hashed(cm.settings.telegram.admin_password)
    assert verify_password("plaintext-pw", cm.settings.telegram.admin_password)

    # На диске тоже хэш
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["telegram"]["admin_password"].startswith("$2")

    # Повторная загрузка не перехэширует
    h = cm.settings.telegram.admin_password
    cm2 = ConfigManager(cfg_path)
    assert cm2.settings.telegram.admin_password == h


# ──────────────────────────────────────────────────────────────────────────────
# core/funpay_client.py — разбиение сообщений
# ──────────────────────────────────────────────────────────────────────────────

def test_split_message_20_lines():
    from core.funpay_client import FunPayClient

    chunks = FunPayClient._split_message("\n".join(f"line{i}" for i in range(45)))
    assert len(chunks) == 3
    assert chunks[0].count("\n") == 19
    assert chunks[2].count("\n") == 4


def test_split_message_short_and_empty():
    from core.funpay_client import FunPayClient

    assert FunPayClient._split_message("одна строка") == ["одна строка"]
    assert FunPayClient._split_message("a\n" + "\n" * 30) == ["a"]
    assert FunPayClient._split_message("\n\n\n") == []


# ──────────────────────────────────────────────────────────────────────────────
# modules/auto_delivery.py — расход и возврат ключей
# ──────────────────────────────────────────────────────────────────────────────

def test_keys_pop_and_push_back(tmp_path):
    from modules.auto_delivery import _pop_keys_from_file, _push_keys_back

    kp = tmp_path / "keys.txt"
    kp.write_text("k1\nk2\nk3", encoding="utf-8")

    taken = _pop_keys_from_file(kp, 2)
    assert taken == ["k1", "k2"]
    assert kp.read_text(encoding="utf-8").splitlines() == ["k3"]

    _push_keys_back(kp, taken)
    assert kp.read_text(encoding="utf-8").splitlines() == ["k1", "k2", "k3"]


def test_csv_pop_and_push_back(tmp_path):
    from modules.auto_delivery import _pop_csv_rows, _push_csv_rows_back

    cp = tmp_path / "data.csv"
    cp.write_text("login,pass\na,1\nb,2\nc,3", encoding="utf-8")

    header, rows = _pop_csv_rows(cp, 2)
    assert header == ["login", "pass"]
    assert rows == [["a", "1"], ["b", "2"]]

    _push_csv_rows_back(cp, header, rows)
    _, rows2 = _pop_csv_rows(cp, 3)
    assert rows2 == [["a", "1"], ["b", "2"], ["c", "3"]]


def test_build_delivery_text_restore(tmp_path):
    from modules.auto_delivery import _build_delivery_text

    kp = tmp_path / "keys.txt"
    kp.write_text("AAA\nBBB", encoding="utf-8")
    info = {"type": "combined", "instruction": "Инструкция:", "content": str(kp)}

    text, ok, restore = asyncio.run(_build_delivery_text("L1", info, 1))
    assert ok and "Инструкция:" in text and "AAA" in text
    assert kp.read_text(encoding="utf-8").splitlines() == ["BBB"]

    assert restore is not None
    restore()
    assert kp.read_text(encoding="utf-8").splitlines() == ["AAA", "BBB"]


def test_build_delivery_text_static_no_restore():
    from modules.auto_delivery import _build_delivery_text

    text, ok, restore = asyncio.run(_build_delivery_text("L2", {"type": "static", "content": "hi"}, 1))
    assert ok and text == "hi" and restore is None


# ──────────────────────────────────────────────────────────────────────────────
# core/funpay_client.py — old-mode fallback
# ──────────────────────────────────────────────────────────────────────────────

class _Chat:
    def __init__(self, cid, text, by_bot=False, unread=True):
        self.id = cid
        self.name = "buyer"
        self.last_message_text = text
        self.last_by_bot = by_bot
        self.unread = unread


class _Ev:
    def __init__(self, chat):
        self.chat = chat


class _Msg:
    def __init__(self, cid, text):
        self.chat_id = cid
        self.text = text


def _make_client_with_capture():
    from core.funpay_client import FunPayClient

    c = FunPayClient()
    emitted = []

    async def _fake_emit(chat):
        emitted.append(chat.last_message_text)

    c._emit_message_from_chat = _fake_emit
    return c, emitted


def test_fallback_activates_after_silence():
    c, emitted = _make_client_with_capture()
    c._last_new_message_ts = time.time() - 400  # NEW_MESSAGE молчит > 300с

    for i in range(3):
        asyncio.run(c._handle_last_chat_message_changed(_Ev(_Chat(100 + i, f"msg{i}"))))

    assert c._old_mode_fallback is True
    assert emitted == ["msg2"]  # эмитится начиная с активационного события


def test_fallback_dedup_and_deactivation():
    c, emitted = _make_client_with_capture()
    c._last_new_message_ts = time.time() - 400
    for i in range(3):
        asyncio.run(c._handle_last_chat_message_changed(_Ev(_Chat(102, f"msg{i}"))))
    assert c._old_mode_fallback

    # Дедуп: то же сообщение не эмитится повторно
    before = list(emitted)
    asyncio.run(c._handle_last_chat_message_changed(_Ev(_Chat(102, before[-1]))))
    assert emitted == before

    # NEW_MESSAGE вернулся — fallback выключается и дубль не эмитится
    c._note_new_message_alive(_Msg(102, "msg4"))
    assert c._old_mode_fallback is False
    asyncio.run(c._handle_last_chat_message_changed(_Ev(_Chat(102, "msg4"))))
    assert emitted == before


def test_fallback_ignores_bot_and_read_messages():
    c, emitted = _make_client_with_capture()
    c._last_new_message_ts = time.time() - 400

    for i in range(5):
        asyncio.run(c._handle_last_chat_message_changed(_Ev(_Chat(200, f"m{i}", by_bot=True))))
        asyncio.run(c._handle_last_chat_message_changed(_Ev(_Chat(201, f"m{i}", unread=False))))

    assert c._old_mode_fallback is False
    assert emitted == []


def test_fallback_not_triggered_when_new_message_fresh():
    c, emitted = _make_client_with_capture()
    c._last_new_message_ts = time.time()  # NEW_MESSAGE только что был

    for i in range(10):
        asyncio.run(c._handle_last_chat_message_changed(_Ev(_Chat(300, f"x{i}"))))

    assert c._old_mode_fallback is False
    assert emitted == []


# ──────────────────────────────────────────────────────────────────────────────
# Мультиаккаунт
# ──────────────────────────────────────────────────────────────────────────────

def test_config_multiaccount_migration(tmp_path):
    """Legacy-поля funpay.* мигрируют в accounts[0]."""
    from config.settings import ConfigManager, Settings

    cfg_path = tmp_path / "config.json"
    s = Settings()
    s.funpay.golden_key = "k" * 32
    s.funpay.username = "seller"
    s.funpay.account_id = 42
    s.funpay.proxy = "1.2.3.4:8080"
    cfg_path.write_text(json.dumps(s.model_dump(), ensure_ascii=False), encoding="utf-8")

    cm = ConfigManager(cfg_path)
    fp = cm.settings.funpay
    assert len(fp.accounts) == 1
    acc = fp.accounts[0]
    assert acc.golden_key == "k" * 32
    assert acc.username == "seller"
    assert acc.account_id == 42
    assert acc.proxy == "1.2.3.4:8080"
    assert fp.active_account() is acc
    # legacy-вид синхронизирован
    assert fp.golden_key == acc.golden_key

    # Повторная загрузка не создаёт дубликат
    cm2 = ConfigManager(cfg_path)
    assert len(cm2.settings.funpay.accounts) == 1


def test_accounts_manager_routing_and_tag():
    """client_for идёт по штампу _palto_acc; tag только при >1 аккаунте."""
    from config.settings import FunPayAccountSettings, config_manager
    from core.funpay_client import FunPayAccountsManager

    fp = config_manager.settings.funpay
    saved_accounts, saved_idx = fp.accounts, fp.active_index
    try:
        fp.accounts = [
            FunPayAccountSettings(alias="A", golden_key="a" * 32),
            FunPayAccountSettings(alias="B", golden_key="b" * 32),
        ]
        fp.active_index = 0

        mgr = FunPayAccountsManager()
        mgr.load_from_config()
        assert [c.alias for c in mgr.all()] == ["A", "B"]
        assert mgr.active().alias == "A"

        class Obj:
            pass

        stamped = Obj()
        stamped._palto_acc = 1
        assert mgr.client_for(stamped).alias == "B"

        unstamped = Obj()
        assert mgr.client_for(unstamped).alias == "A"  # fallback — активный

        assert mgr.tag(stamped) == "[B] "
        assert mgr.tag(unstamped) == "[A] "

        # Один аккаунт — тегов нет
        fp.accounts = fp.accounts[:1]
        mgr.load_from_config()
        assert mgr.tag(stamped) == ""
    finally:
        fp.accounts, fp.active_index = saved_accounts, saved_idx


def test_quick_reply_token_parse():
    from bot.handlers.quick_reply import _parse_token

    assert _parse_token("12345@1") == ("12345", 1)
    assert _parse_token("12345") == ("12345", -1)
    assert _parse_token("12345@oops") == ("12345", -1)
