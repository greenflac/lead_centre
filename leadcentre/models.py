"""Общие типы движка. Одно знание — одно место (Е1): формы данных живут здесь."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Tier(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INVALID = "INVALID"  # инвариант нарушен: карточку показывать нельзя


class AddressType(str, Enum):
    """Ось A: где компания сидит по документам."""

    REGISTRAR = "A1_registrar"        # адрес здания фризоны-регистратора: флекси/виртуальный
    BUSINESS_CENTRE = "A2_business_centre"  # бизнес-центр/сервисный офис
    OWN = "A3_own"                    # собственный/арендованный офис
    UNKNOWN = "A0_unknown"


class Event(str, Enum):
    """Ось B: что произошло и когда — повод для разговора."""

    LAPSED = "B1_lapsed"              # регистрация LEI просрочена
    NEW_ENTITY = "B2_new"             # юрлицо создано недавно
    RENEWAL_SOON = "B3_renewal_soon"  # продление LEI на подходе
    NONE = "B0_none"


@dataclass(frozen=True)
class Company:
    """Компания из внешнего источника, приведённая к общему виду."""

    source: str
    external_id: str            # LEI или иной идентификатор источника
    name: str
    city: str
    country: str
    address_lines: tuple[str, ...]
    registrar_id: str | None    # орган регистрации (RA-код GLEIF)
    license_no: str | None
    created_on: date | None
    entity_active: bool
    registration_status: str    # ISSUED / LAPSED / ...
    next_renewal_on: date | None
    is_synthetic: bool = False


@dataclass(frozen=True)
class Evidence:
    """Доказательство повода: без него HIGH не выдаётся (инвариант в score.py)."""

    kind: str
    value: str


@dataclass(frozen=True)
class Score:
    tier: Tier
    address_type: AddressType
    event: Event
    reasons: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    violations: tuple[str, ...] = ()


# --- входящие обращения ---


class RequestType(str, Enum):
    OFFICE = "office"
    SETUP = "setup"
    VISA = "visa"
    ACCOUNTING = "accounting"
    BANK = "bank"
    OTHER = "other"


@dataclass(frozen=True)
class InboundMessage:
    """Обращение из канала SORP: чат Jivo, WhatsApp, Telegram, форма сайта."""

    external_id: str
    channel: str            # jivo | whatsapp | telegram | form
    text: str
    received_at: date
    is_synthetic: bool = True


@dataclass(frozen=True)
class LeadFacts:
    """Факты, извлечённые моделью из текста обращения. Модель предлагает — код решает.

    Контракт между extract.py (заполняет) и score.py/reply.py (читают). Менять только
    вместе с тестами, которые держат литералы схемы.
    """

    request_types: tuple[RequestType, ...] = ()
    jurisdiction_hint: str | None = None      # mainland | freezone | конкретная зона | None
    headcount: int | None = None
    timeline_days: int | None = None          # через сколько дней клиенту нужно решение
    budget_hint: str | None = None
    language: str = "en"                      # ru | en | ar | mixed
    is_spam: bool = False
    has_contact: bool = False
    confidence: float = 0.0                   # 0..1, уверенность модели в извлечении
    quotes: tuple[str, ...] = field(default_factory=tuple)  # цитаты-доказательства
