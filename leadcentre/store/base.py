"""Хранилище — сменный адаптер, как источник компаний в `sources/base.py`.

Интерфейс один (`Store`), реализаций две: `LocalStore` (JSON-файл или память, режим
`OFFLINE=1` — сеть не трогаем, CI не ходит наружу) и `SupabaseStore` (PostgREST).
Движок и HTTP-слой про реализацию не знают: выбор делает `get_store()`.

Три исхода вместо двух на каждой записи:
  * успех — вернулся идентификатор/число записанных;
  * `StoreRejected` — хранилище ответило «данные не приняты» (нарушен контракт данных);
  * `StoreUnavailable` — «не смогли»: сеть, доступ, схема не применена, 5xx.
Третий не сворачивается ни в первый, ни во второй: «не смогли записать» и «записали»
для отчёта разные вещи, а тихий `except` превращает потерю данных в успех.

Результат групповой записи — числа, а не булев флаг: `UpsertResult(3 из 8)`.

Причины приоритета хранятся ДАННЫМИ, а не текстом: колонка `reason_items` — это список
`{"code": ..., "params": {...}}`, из которого `engine/reasons.py` собирает текст на любом
языке. Готовые русские строки в колонке `reasons` остались, но перестали быть источником:
их пишет отрисовка, а читатель, которому нужен английский, берёт коды. Старая строка
без кодов — это отдельный исход `no_codes` («не смогли восстановить причины»), а не
молчаливая подмена английского русским.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from leadcentre.engine.reasons import (
    DEFAULT_LANGUAGE,
    Language,
    Reason,
    ReasonCode,
    ReasonError,
    reason,
    render_all,
)
from leadcentre.models import AddressType, Event, Evidence, Score, Tier

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


# --- причины как данные ---

# Исходы восстановления причин из строки хранилища. Их четыре, а не два, и ни один
# не сворачивается в другой:
REASONS_OK = "ok"                # есть коды — рисуется любой язык
REASONS_EMPTY = "empty"          # причин нет вовсе, и это законно (например, LOW без сигналов)
REASONS_NO_CODES = "no_codes"    # «не смогли»: старая строка, только русский текст
REASONS_INVALID = "invalid"      # «не годно»: коды есть, но разобрать их нельзя
REASONS_OUTCOMES = (REASONS_OK, REASONS_EMPTY, REASONS_NO_CODES, REASONS_INVALID)


def reason_items_payload(items: tuple[Reason, ...]) -> list[dict[str, Any]]:
    """Причины → JSON для колонки `reason_items`.

    Форма — `{"code": "...", "params": {...}}`, а не голая строка кода: параметры
    («срок 14 дней») — часть причины, без них текст не собрать ни на одном языке.
    """
    return [{"code": item.code.value, "params": item.values()} for item in items]


def parse_reason_items(raw: Any) -> tuple[Reason, ...]:
    """JSON из колонки → причины. Любая кривизна — `StoreRejected` («не годно»).

    Молчаливо пропускать нечитаемую причину нельзя: пропущенная причина — это карточка,
    у которой в интерфейсе на один довод меньше, и заметить это некому.
    """
    if raw in (None, ""):
        return ()
    if not isinstance(raw, (list, tuple)):
        raise StoreRejected(f"reason_items: ожидался список, получено {type(raw).__name__}")
    items: list[Reason] = []
    for position, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise StoreRejected(
                f"reason_items[{position}]: ожидался объект, получено {type(entry).__name__}"
            )
        code_value = entry.get("code")
        try:
            code = ReasonCode(code_value)
        except ValueError as exc:
            raise StoreRejected(
                f"reason_items[{position}]: неизвестный код причины {code_value!r}"
            ) from exc
        params = entry.get("params") or {}
        if not isinstance(params, Mapping):
            raise StoreRejected(
                f"reason_items[{position}] ({code.value}): params не объект"
            )
        try:
            items.append(reason(code, **{str(k): v for k, v in params.items()}))
        except ReasonError as exc:
            raise StoreRejected(f"reason_items[{position}]: {exc}") from exc
    return tuple(items)


@dataclass(frozen=True)
class RestoredReasons:
    """Причины, поднятые из хранилища, вместе с исходом восстановления.

    `texts` содержит язык, только если он действительно собран. У исхода `no_codes`
    там лежит один русский ключ — и отсутствие ключа `en` и есть тот сигнал, по которому
    интерфейс обязан показать «причины недоступны», а не русский текст под видом
    английского. Отдавать русский на запрос английского — тот самый дефект, ради которого
    причины стали данными.
    """

    outcome: str
    items: tuple[Reason, ...] = ()
    texts: dict[str, list[str]] = field(default_factory=dict)
    stored_texts: tuple[str, ...] = ()
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == REASONS_OK

    def summary(self) -> str:
        return (
            f"причины: исход {self.outcome}, кодов {len(self.items)}, "
            f"языков {len(self.texts)}, сохранённых строк {len(self.stored_texts)}"
        )


def restore_reasons(score_row: Mapping[str, Any] | None) -> RestoredReasons:
    """Строка таблицы `scores` → причины на всех языках либо честное «не смогли»."""
    if not score_row:
        return RestoredReasons(REASONS_EMPTY, detail="оценки нет")
    stored = tuple(str(text) for text in (score_row.get("reasons") or ()))
    raw_items = score_row.get("reason_items")
    if raw_items:
        try:
            items = parse_reason_items(raw_items)
        except StoreRejected as exc:
            return RestoredReasons(REASONS_INVALID, stored_texts=stored, detail=str(exc))
        try:
            texts = {
                language.value: list(render_all(items, language)) for language in Language
            }
        except ReasonError as exc:   # каталог разъехался с сохранёнными кодами
            return RestoredReasons(REASONS_INVALID, items, stored_texts=stored, detail=str(exc))
        return RestoredReasons(REASONS_OK, items, texts, stored)
    if not stored:
        return RestoredReasons(
            REASONS_EMPTY,
            texts={language.value: [] for language in Language},
            detail="причин нет",
        )
    return RestoredReasons(
        REASONS_NO_CODES,
        texts={DEFAULT_LANGUAGE.value: list(stored)},
        stored_texts=stored,
        detail=(
            f"строка сохранена без кодов причин: {len(stored)} готовых строк на "
            f"{DEFAULT_LANGUAGE.value}, другие языки не восстановить — "
            f"перескорьте обращение"
        ),
    )


def restore_score(score_row: Mapping[str, Any] | None) -> Score | None:
    """Строка таблицы `scores` → `Score`.

    Есть коды — `Score` отдаёт оба языка через `reasons_in()`. Кодов нет — `Score`
    собирается со строками, и `reasons_in(EN)` на нём осознанно бросает
    `ReasonRenderError`: вызывающему нужен `restore_reasons()`, чтобы отличить
    «причин нет» от «не смогли восстановить».
    """
    if not score_row:
        return None
    restored = restore_reasons(score_row)
    try:
        tier = Tier(score_row.get("tier"))
        address_type = AddressType(score_row.get("address_type"))
        event = Event(score_row.get("event"))
    except ValueError as exc:
        raise StoreRejected(f"строка оценки не разбирается: {exc}") from exc
    evidence = tuple(
        Evidence(kind=str(e.get("kind", "")), value=str(e.get("value", "")))
        for e in (score_row.get("evidence") or [])
        if isinstance(e, Mapping)
    )
    violations = tuple(str(v) for v in (score_row.get("violations") or ()))
    common = {
        "tier": tier,
        "address_type": address_type,
        "event": event,
        "evidence": evidence,
        "violations": violations,
    }
    if restored.ok:
        return Score(reason_items=restored.items, **common)
    return Score(reasons=restored.stored_texts, **common)


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
    """Оценка обращения или компании. `usage`/`latency_ms` — счёт за токены и время.

    Причины задаются кодами (`reason_items`); колонка `reasons` при этом заполняется
    отрисовкой на русском и передавать её отдельно нельзя — иначе в базе окажутся два
    источника текста, которые разъедутся (тем же машинным способом, что и в `Score`).
    Голые строки без кодов принимаются ровно для одного случая: перезапись старой записи,
    для которой кодов уже не восстановить.
    """

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
    reason_items: tuple[Reason, ...] = ()

    def __post_init__(self) -> None:
        if self.reason_items:
            if self.reasons:
                raise StoreRejected(
                    "ScoreRow: причины заданы дважды — reason_items и reasons; "
                    "строки собирает отрисовка, передавать их отдельно нельзя"
                )
            object.__setattr__(
                self, "reasons", render_all(self.reason_items, DEFAULT_LANGUAGE)
            )

    def payload(self) -> dict[str, Any]:
        return {
            "lead_id": self.lead_id,
            "tier": self.tier,
            "address_type": self.address_type,
            "event": self.event,
            "reasons": list(self.reasons),
            "reason_items": reason_items_payload(self.reason_items),
            "evidence": [dict(e) for e in self.evidence],
            "violations": list(self.violations),
            "model": self.model,
            "prompt_version": self.prompt_version,
            "usage": self.usage,
            "latency_ms": self.latency_ms,
            "run_id": self.run_id,
        }


# Статусы черновика. Одно знание — одно место: и API, и схема берут их отсюда.
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
    UNVERIFIABLE равен `None`: свёрнутый в булев третий исход теряется молча.
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
    """Числами, а не флагом: агрегатный `true` читается как полная работа."""

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
    """Три исхода проверки доступности: `ok` / `rejected` / `unavailable`."""

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

    def reasons(self) -> RestoredReasons:
        """Причины карточки на всех языках либо исход «не смогли».

        Живёт здесь, а не в `api.py`: карточку поднимают и CLI, и отчёт, и HTTP —
        способ восстановить причины должен быть один.
        """
        return restore_reasons(self.score)


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
