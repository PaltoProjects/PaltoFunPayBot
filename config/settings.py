"""
Менеджер конфигурации бота.

Конфиг хранится в data/config.json. Pydantic используется для валидации.
Совместим с Python 3.8 — поэтому везде typing.Optional / typing.List / typing.Dict.
"""
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config.json"
CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# Схемы настроек по модулям
# ──────────────────────────────────────────────────────────────────────────────

class TelegramSettings(BaseModel):
    """Telegram-бот: токен и список авторизованных пользователей."""
    token: str = ""
    admin_password: str = ""              # bcrypt-хэш пароля администратора (см. utils/passwords.py)
    manager_keys: List[str] = Field(default_factory=list)  # Ключи регистрации менеджеров
    admin_ids: List[int] = Field(default_factory=list)     # tg-id админов
    manager_ids: List[int] = Field(default_factory=list)   # tg-id менеджеров

    # 2FA — двухфакторная авторизация при входе
    twofa_enabled: bool = False
    twofa_secret: str = ""                # секрет для генерации TOTP-кода (RFC 6238)


class FunPaySettings(BaseModel):
    """FunPay-аккаунт: golden_key и прокси."""
    golden_key: str = ""
    proxy: str = ""                       # формат login:pass@ip:port или ip:port
    # ВАЖНО: User-Agent должен быть реальным браузерным! Без него FunPay
    # видит python-requests и не отдаёт чаты. Cardinal использует именно этот.
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
    )
    account_id: Optional[int] = None      # сохраняем после успешного коннекта
    username: Optional[str] = None


class AutoLiftSettings(BaseModel):
    """Автоподнятие лотов."""
    enabled: bool = False
    interval_minutes: int = 240           # FunPay позволяет ~раз в 4 часа
    only_active_categories: bool = True


class AutoGreetingSettings(BaseModel):
    """Приветствие новых диалогов."""
    enabled: bool = False
    text: str = "👋 Привет, $username! Если нужна помощь — пиши, я на связи."
    cooldown_days: int = 2                # не приветствовать одного и того же чаще раза в N дней
    ignore_system_messages: bool = True


class AutoResponseSettings(BaseModel):
    """Автоответчик по ключевым словам."""
    enabled: bool = False
    templates: Dict[str, str] = Field(default_factory=dict)  # keyword -> response


class AutoReviewReplySettings(BaseModel):
    """Автоответ на отзывы."""
    enabled: bool = False
    reply_5: str = "Спасибо за оценку! 🌟"
    reply_4: str = "Спасибо за отзыв!"
    reply_3: str = "Спасибо за обратную связь, мы стараемся стать лучше."
    reply_2: str = "Извините, что не оправдали ожиданий. Свяжитесь с нами для решения проблемы."
    reply_1: str = "Извините за неудобства. Напишите в ЛС — разберёмся."


class AskReviewSettings(BaseModel):
    """Просьба оставить отзыв после подтверждения заказа."""
    enabled: bool = False
    text: str = "Спасибо за покупку! Будем благодарны за отзыв 🙏"
    delay_minutes: int = 10


class AutoActivationSettings(BaseModel):
    """Авто-активация / деактивация лотов."""
    enabled: bool = False
    deactivate_on_zero_stock: bool = True
    activate_on_restock: bool = True


class AutoRefundSettings(BaseModel):
    """Авто-возврат денег за заказы."""
    enabled: bool = False
    refund_on_bad_review: bool = False    # возвращать при оценке ниже X
    bad_review_threshold: int = 2         # <= этой оценки


class AutoWithdrawSettings(BaseModel):
    """Авто-вывод средств с баланса."""
    enabled: bool = False
    # Триггер вывода:
    #   "schedule" — по расписанию (раз в день/неделю/месяц)
    #   "amount"   — при достижении суммы
    trigger: str = "schedule"
    # Минимальная сумма для вывода (в рублях)
    min_amount_rub: int = 1000
    # Расписание (если trigger=schedule):
    schedule: str = "weekly"              # daily / weekly / monthly
    # Час дня вывода (0-23)
    schedule_hour: int = 12
    # Куда выводить (тип счёта FunPay): card / qiwi / yoomoney / usdt / btc и т.д.
    # Возможные значения зависят от FunPayAPI — оставляем строкой.
    payment_method: str = "card"
    # Реквизиты получателя (карта/кошелёк/адрес)
    payment_details: str = ""


class CompetitorParserSettings(BaseModel):
    """Парсер цен конкурентов."""
    enabled: bool = False
    interval_minutes: int = 60
    auto_adjust: bool = False             # автоматически корректировать свои цены
    margin_percent: float = 1.0           # быть на N% ниже минимальной цены конкурента


class AntiDumpingSettings(BaseModel):
    """
    Авто-снятие демпинга (фича №39).

    Если по нашему лоту минимальная цена конкурентов УПАЛА более чем на X%,
    бот автоматически снимает наш лот с публикации (деактивирует).
    Когда конкурент пропадёт или цена восстановится — лот можно вернуть руками
    или включить auto_restore.
    """
    enabled: bool = False
    interval_minutes: int = 30
    threshold_percent: float = 15.0       # на сколько % просадки ниже нашей цены = демпинг
    notify: bool = True                   # уведомлять в TG когда сняли


class LotDescGeneratorSettings(BaseModel):
    """
    Генерация описаний лотов через ИИ (фича №41).

    Когда юзер вызывает /gen_desc <lot_id>, бот шлёт промпт в ИИ
    (используя текущий ключ из AISettings) и возвращает 2-3 варианта описания.
    Можно настроить тон и длину.
    """
    enabled: bool = False
    tone: str = "профессиональный"        # дружелюбный / профессиональный / краткий
    max_length: int = 500                 # символов в одном описании
    include_emoji: bool = True
    custom_prompt: str = ""               # опциональный кастомный системный промпт


class BlacklistSettings(BaseModel):
    """Чёрный список покупателей."""
    enabled: bool = True
    nicknames: List[str] = Field(default_factory=list)


class OnlineKeeperSettings(BaseModel):
    """Поддержание онлайн-статуса."""
    enabled: bool = True
    poll_interval_seconds: int = 60


class AntibanSettings(BaseModel):
    """Защита от банов."""
    human_like_delays: bool = True
    min_delay_ms: int = 800
    max_delay_ms: int = 2200


class AnalyticsSettings(BaseModel):
    """Аналитика и статистика."""
    enabled: bool = True
    save_orders_history: bool = True


class AISettings(BaseModel):
    """ИИ-функции (28-30 из списка)."""
    enabled: bool = False
    api_key: str = ""                     # ключ DeepSeek / Anthropic / OpenAI
    provider: str = "deepseek"            # deepseek / anthropic / openai
    model: str = "deepseek-v4-flash"      # дешёвая модель DeepSeek (5М токенов бесплатно при регистрации)

    # 28: автоответ ИИ если продавец не отвечает > N часов
    auto_reply_after_hours: int = 5
    auto_reply_instruction: str = (
        "Ты — вежливый ассистент продавца на FunPay. "
        "Отвечай кратко, по делу, на русском. Не обещай скидок и не выдавай товар. "
        "Если вопрос сложный — пиши, что продавец скоро ответит."
    )

    # 29: улучшение черновиков сообщений
    rewrite_drafts: bool = False

    # 30: ИИ-аудит магазина
    shop_audit_enabled: bool = False


class UpdateSettings(BaseModel):
    """Обновление бота с GitHub (команда /update)."""
    enabled: bool = True
    repo: str = "PaltoProjects/PaltoFunPayBot"   # owner/repo на GitHub
    branch: str = "main"                          # ветка для обновлений


class PluginsSettings(BaseModel):
    """Система плагинов."""
    enabled: bool = True
    plugins_dir: str = "plugins_user"
    enabled_plugins: List[str] = Field(default_factory=list)
    pinned_plugins: List[str] = Field(default_factory=list)


class AutoDeliverySettings(BaseModel):
    """Автовыдача товаров — конфиг на каждый лот вручную."""
    enabled: bool = False
    # lot_id (str) → {type: 'static'|'file', content: ...}
    lots: Dict[str, dict] = Field(default_factory=dict)
    out_of_stock_message: str = (
        "К сожалению, товар временно закончился. "
        "Деньги будут возвращены автоматически. Извините за неудобства!"
    )


class MultiDeliverySettings(BaseModel):
    """Мульти-выдача — несколько ключей за один заказ."""
    enabled: bool = False


class AutoRestoreSettings(BaseModel):
    """Автовосстановление лотов после автоматической деактивации."""
    enabled: bool = False
    check_interval_minutes: int = 30


class LegacyMessageModeSettings(BaseModel):
    """Устаревший режим сообщений (для совместимости с FunPay)."""
    enabled: bool = False


class ReadReceiptSettings(BaseModel):
    """Не отмечать прочитанным при отправке."""
    enabled: bool = False  # включено = НЕ отмечать


class OrderConfirmReplySettings(BaseModel):
    """Ответ на подтверждение заказа покупателем."""
    enabled: bool = False
    text: str = "Спасибо за подтверждение! Будем рады видеть вас снова 🙏"


class WatermarkSettings(BaseModel):
    """Водяной знак на исходящие сообщения бота."""
    enabled: bool = False
    text: str = "\n\n— Отправлено через FunPayBot"


class NotificationSettings(BaseModel):
    """Уведомления в Telegram о событиях FunPay."""
    new_orders: bool = True
    order_paid: bool = True
    order_confirmed: bool = True
    order_refunded: bool = True
    new_messages: bool = True             # ← ВКЛ по умолчанию (главная фича — пересылка чатов в TG)
    new_reviews: bool = True
    bot_started_stopped: bool = True
    lot_lifted: bool = False
    delivery_sent: bool = True


class NotificationStyleSettings(BaseModel):
    """Внешний вид уведомлений в TG."""
    use_emoji: bool = True
    show_buyer_link: bool = True
    show_lot_link: bool = True
    show_balance_after_order: bool = True
    compact_mode: bool = False
    show_own_messages: bool = False  # показывать свои сообщения в TG
    muted_chats: Dict[str, float] = Field(default_factory=dict)  # chat_id -> до какого времени заглушён


class ResponseTemplatesSettings(BaseModel):
    """Шаблоны ответов (отдельно от автоответчика — для быстрых ответов в чате)."""
    templates: Dict[str, str] = Field(default_factory=lambda: {
        "Здравствуйте": "Здравствуйте! Чем могу помочь?",
        "Спасибо": "Пожалуйста! Обращайтесь ещё 🙂",
    })


# ──────────────────────────────────────────────────────────────────────────────
# Корневой конфиг
# ──────────────────────────────────────────────────────────────────────────────

class Settings(BaseModel):
    """Корневая модель всех настроек бота."""
    version: str = "1.1"
    setup_completed: bool = False

    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    funpay: FunPaySettings = Field(default_factory=FunPaySettings)

    # Основные модули (Core)
    auto_lift: AutoLiftSettings = Field(default_factory=AutoLiftSettings)
    auto_response: AutoResponseSettings = Field(default_factory=AutoResponseSettings)
    auto_delivery: AutoDeliverySettings = Field(default_factory=AutoDeliverySettings)
    multi_delivery: MultiDeliverySettings = Field(default_factory=MultiDeliverySettings)
    auto_restore: AutoRestoreSettings = Field(default_factory=AutoRestoreSettings)
    auto_activation: AutoActivationSettings = Field(default_factory=AutoActivationSettings)
    legacy_message_mode: LegacyMessageModeSettings = Field(default_factory=LegacyMessageModeSettings)
    read_receipt: ReadReceiptSettings = Field(default_factory=ReadReceiptSettings)

    # Автоматизация (детальные настройки)
    auto_greeting: AutoGreetingSettings = Field(default_factory=AutoGreetingSettings)
    auto_review_reply: AutoReviewReplySettings = Field(default_factory=AutoReviewReplySettings)
    ask_review: AskReviewSettings = Field(default_factory=AskReviewSettings)
    order_confirm_reply: OrderConfirmReplySettings = Field(default_factory=OrderConfirmReplySettings)

    # Прочее
    auto_refund: AutoRefundSettings = Field(default_factory=AutoRefundSettings)
    auto_withdraw: AutoWithdrawSettings = Field(default_factory=AutoWithdrawSettings)
    competitor_parser: CompetitorParserSettings = Field(default_factory=CompetitorParserSettings)
    anti_dumping: AntiDumpingSettings = Field(default_factory=AntiDumpingSettings)
    lot_desc_generator: LotDescGeneratorSettings = Field(default_factory=LotDescGeneratorSettings)
    blacklist: BlacklistSettings = Field(default_factory=BlacklistSettings)
    online_keeper: OnlineKeeperSettings = Field(default_factory=OnlineKeeperSettings)
    antiban: AntibanSettings = Field(default_factory=AntibanSettings)
    analytics: AnalyticsSettings = Field(default_factory=AnalyticsSettings)
    ai: AISettings = Field(default_factory=AISettings)
    plugins: PluginsSettings = Field(default_factory=PluginsSettings)
    update: UpdateSettings = Field(default_factory=UpdateSettings)

    # Система и конфиги
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    notification_style: NotificationStyleSettings = Field(default_factory=NotificationStyleSettings)
    response_templates: ResponseTemplatesSettings = Field(default_factory=ResponseTemplatesSettings)
    watermark: WatermarkSettings = Field(default_factory=WatermarkSettings)


class ConfigManager:
    """Загрузка / сохранение / горячее обновление конфига."""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.settings: Settings = self._load()

    def _load(self) -> Settings:
        if not self.path.exists():
            settings = Settings()
            self._write(settings)
            return settings
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            settings = Settings.model_validate(data)

            # ── Миграции для старых конфигов ────────────────────────────────
            migrations = data.get("_migrations_applied", {}) or {}
            need_save = False

            # На старых конфигах new_messages=False по дефолту → включаем
            if not migrations.get("notif_new_messages_default_true"):
                settings.notifications.new_messages = True
                migrations["notif_new_messages_default_true"] = True
                need_save = True

            # Пароль администратора: плейнтекст → bcrypt-хэш.
            # Без флага миграции: условие самодостаточно (is_hashed), а пароль
            # мог быть перезаписан плейнтекстом старой версией бота.
            from utils.passwords import BCRYPT_AVAILABLE, hash_password, is_hashed
            if (BCRYPT_AVAILABLE and settings.telegram.admin_password
                    and not is_hashed(settings.telegram.admin_password)):
                settings.telegram.admin_password = hash_password(settings.telegram.admin_password)
                need_save = True

            # На старых конфигах user_agent был пустым → ставим браузерный.
            # Без него FunPay видит python-requests/2.28.1 и не отдаёт чаты.
            if not migrations.get("user_agent_browser_default"):
                if not settings.funpay.user_agent or "python-requests" in settings.funpay.user_agent:
                    settings.funpay.user_agent = (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
                    )
                migrations["user_agent_browser_default"] = True
                need_save = True

            if need_save:
                # Пишем миграции прямо в файл (pydantic-модель их не хранит)
                self._write(settings)
                full = json.loads(self.path.read_text(encoding="utf-8"))
                full["_migrations_applied"] = migrations
                self._atomic_write(json.dumps(full, ensure_ascii=False, indent=2))
            return settings
        except Exception:
            # Файл повреждён — делаем backup и создаём новый.
            # os.replace перезаписывает существующий .broken.json (на Windows
            # Path.rename падает с FileExistsError) — поэтому бот не зависнет
            # на старте, если конфиг повредился повторно.
            try:
                backup = self.path.with_suffix(".broken.json")
                os.replace(self.path, backup)
            except Exception:
                pass
            settings = Settings()
            self._write(settings)
            return settings

    def _write(self, settings: Settings) -> None:
        self._atomic_write(json.dumps(settings.model_dump(), ensure_ascii=False, indent=2))

    def _atomic_write(self, text: str) -> None:
        """
        Атомарная запись: пишем во временный файл в той же папке и заменяем
        целевой через os.replace. Прерывание (рестарт/выключение) посреди
        записи больше не оставит битый config.json.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(self.path.parent), prefix=".config.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise

    def save(self) -> None:
        with self._lock:
            self._write(self.settings)

    def update(self, **kwargs: Any) -> None:
        """Точечное обновление полей корневого уровня (редко используется)."""
        with self._lock:
            for k, v in kwargs.items():
                setattr(self.settings, k, v)
            self._write(self.settings)


# Глобальный синглтон конфига
config_manager = ConfigManager()
