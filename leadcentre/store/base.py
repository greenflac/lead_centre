"""Хранилище — сменный адаптер, как источник компаний в `sources/base.py`.

Интерфейс один (`Store`), реализаций две: `LocalStore` (JSON-файл или память, режим
`OFFLINE=1` — сеть не трогаем, CI не ходит наружу) и `SupabaseStore` (PostgREST).
Движок и HTTP-слой про реализацию не знают: выбор делает `get_store()`.

Три исхода вместо двух (Р1) на каждой записи:
  * успех — вернулся идентификатор/число записанных;
  * `StoreRejected` — хранилище ответило «данные не приняты» (нарушен контракт данных);
  * `StoreUnavailable` — «не смогли»: сеть, доступ, схема не применена, 5xx.
Третий не сворачивается ни в первый, ни во второй: «не смогли записать» и «записали»
для отчёта разные вещи, а тихий `except` превращает потерю данных в успех.

Результат групповой записи — числа, а не булев флаг (Е3): `UpsertResult(3 из 8)`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

# --- исходы ---


class StoreError(RuntimeError):
    """Базовая ошибка хранилища. Наверх идёт исключением, чтобы исход не потерялся."""


class StoreRejected(StoreError):
    """Отказ: хранилище приняло запрос и отвергло данные (400/409/422). Чинится кодом."""


class StoreUnavailable(StoreError):
    """«Не смогли»: сеть, доступ, 5xx, схема не применена. Чинится не кодом, а средой."""


# --- строки таблиц ---


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True)
class LeadRow:
    """Обращение. `id` заполняет хранилище, поэтому он не часть входных данных."""

    source: str            # form | whatsapp | telegram | jivo | csv | api
    channel: str
    raw_text: str
    facts: dict[str, Any]
    language: str
    is_synthetic: bool
    received_at: date
    id: str | None = None

    def payload(self) -> dict[str, Any]:
        body = {
            "source": self.source,
            "channel": self.channel,
            "raw_text": self.raw_text,
            "facts": self.facts,
            "language": self.language,
            "is_synthetic": self.is_synthetic,
            "received_at": _iso(self.received_at),
        }
        if self.id:
            body["id"] = self.id
        return body


@dataclass(frozen=True)
class ScoreRow:
    """Оценка обращения или компании. `usage`/`latency_ms` — счёт за токены и время (П1)."""

    lead_id: str
    tier: str
    address_type: str
    event: str
    reasons: tuple[str, ...] = ()
    evidence: tuple[dict[str, str], ...] = ()
    violations: tuple[str, ...] = ()
    model: str = ""
    prompt_version: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    run_id: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "lead_id": self.lead_id,
            "tier": self.tier,
            "address_type": self.address_type,
            "event": self.event,
            "reasons": list(self.reasons),
            "evidence": [dict(e) for e in self.evidence],
            "violations": list(self.violations),
            "model": self.model,
            "prompt_version": self.prompt_version,
            "usage": self.usage,
            "latency_ms": self.latency_ms,
            "run_id": self.run_id,
        }


# Статусы черновика. Одно знание — одно место (Е1): и API, и схема берут их отсюда.
REPLY_DRAFT = "draft"
REPLY_APPROVED = "approved"
REPLY_REJECTED = "rejected"
REPLY_STATUSES = (REPLY_DRAFT, REPLY_APPROVED, REPLY_REJECTED)


@dataclass(frozen=True)
class ReplyRow:
    """Черновик ответа.

    `lint_ok` — булев, но его мало: у линтера три исхода (OK / VIOLATIONS / UNVERIFIABLE),
    и «не смогли проверить» нельзя записать как `false` (это отказ) или `true` (это успех).
    Поэтому рядом лежит `lint_status` с исходной строкой линтера, а `lint_ok` при
    UNVERIFIABLE равен `None`. Сворачивание третьего исхода в булев — ровно тот дефект,
    из-за которого написано правило Р1.
    """

    lead_id: str
    language: str
    body: str
    lint_ok: bool | None
    lint_status: str
    lint_violations: tuple[str, ...] = ()
    status: str = REPLY_DRAFT
    decided_at: datetime | None = None

    def payload(self) -> dict[str, Any]:
        if self.status not in REPLY_STATUSES:
            raise StoreRejected(
                f"недопустимый статус черновика {self.status!r}; ожидается один из "
                f"{', '.join(REPLY_STATUSES)}"
            )
        return {
            "lead_id": self.lead_id,
            "language": self.language,
            "body": self.body,
            "lint_ok": self.lint_ok,
            "lint_status": self.lint_status,
            "lint_violations": list(self.lint_violations),
            "status": self.status,
            "decided_at": _iso(self.decided_at),
        }


@dataclass(frozen=True)
class CompanyRow:
    """Компания из внешнего источника. Ключ — пара (source, external_id)."""

    external_id: str
    source: str
    name: str
    city: str
    license_no: str | None
    registrar_id: str | None
    created_on: date | None
    registration_status: str
    next_renewal_on: date | None
    facts: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "source": self.source,
            "name": self.name,
            "city": self.city,
            "license_no": self.license_no,
            "registrar_id": self.registrar_id,
            "created_on": _iso(self.created_on),
            "registration_status": self.registration_status,
            "next_renewal_on": _iso(self.next_renewal_on),
            "facts": self.facts,
        }


@dataclass(frozen=True)
class DisagreementRow:
    """Несогласие менеджера с оценкой — вход для eval, а не запись в журнал."""

    lead_id: str
    tier_shown: str
    reason: str
    author: str = ""
    created_at: datetime | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "lead_id": self.lead_id,
            "tier_shown": self.tier_shown,
            "reason": self.reason,
            "author": self.author,
            "created_at": _iso(self.created_at or datetime.now(UTC)),
        }


# --- результаты ---


@dataclass(frozen=True)
class UpsertResult:
    """Числами, а не флагом (Е3/Р2): агрегатный `true` читается как полная работа."""

    requested: int
    written: int
    failed: int
    unavailable: int
    store: str

    def summary(self) -> str:
        return (
            f"хранилище {self.store}: запрошено {self.requested}, записано {self.written}, "
            f"отказано {self.failed}, не смогли {self.unavailable}"
        )


@dataclass(frozen=True)
class StoreHealth:
    """Три исхода проверки доступности: `ok` / `rejected` / `unavailable` (Р1)."""

    store: str
    outcome: str          # ok | rejected | unavailable
    detail: str = ""

    OK = "ok"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


@dataclass
class LeadCard:
    """Карточка: обращение + оценка + черновик. То, что видит менеджер и отдаёт API."""

    lead: dict[str, Any]
    score: dict[str, Any] | None = None
    reply: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Store(Protocol):
    """Контракт хранилища. Реализации: LocalStore (offline) и SupabaseStore (PostgREST)."""

    name: str

    def health(self) -> StoreHealth: ...

    def save_lead(self, row: LeadRow) -> str: ...

    def save_score(self, row: ScoreRow) -> None: ...

    def save_reply(self, row: ReplyRow) -> None: ...

    def set_reply_status(self, lead_id: str, status: str, decided_at: datetime) -> bool: ...

    def get_card(self, lead_id: str) -> LeadCard | None: ...

    def list_cards(self, limit: int = 50) -> list[LeadCard]: ...

    def upsert_companies(self, rows: list[CompanyRow]) -> UpsertResult: ...

    def list_companies(self, limit: int = 50) -> list[dict[str, Any]]: ...

    def save_disagreement(self, row: DisagreementRow) -> None: ...

    def counters(self) -> dict[str, Any]: ...
