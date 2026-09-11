"""Shared engine types: every data shape lives here and only here."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from leadcentre.engine.reasons import (
    DEFAULT_LANGUAGE,
    Language,
    Reason,
    ReasonRenderError,
    render_all,
)


class Tier(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INVALID = "INVALID"  # an invariant broke; the card must not be shown


class AddressType(str, Enum):
    """Axis A: where the company sits according to its documents."""

    REGISTRAR = "A1_registrar"        # free-zone registrar building: flexi or virtual
    BUSINESS_CENTRE = "A2_business_centre"  # business centre or serviced office
    OWN = "A3_own"                    # own or leased office
    UNKNOWN = "A0_unknown"


class Event(str, Enum):
    """Axis B: what happened and when, giving a reason to talk."""

    LAPSED = "B1_lapsed"              # LEI registration is overdue
    NEW_ENTITY = "B2_new"             # entity was created recently
    RENEWAL_SOON = "B3_renewal_soon"  # LEI renewal is approaching
    NONE = "B0_none"


class CityMatch(str, Enum):
    """What the registry city string settled; UNRECOGNISED is not "wrong city"."""

    TARGET = "target"                # the target emirate in any spelling, or its district
    OFF_TARGET = "off_target"        # another emirate
    UNRECOGNISED = "unrecognised"    # a string is present but settles nothing
    NOT_SET = "not_set"              # the record carries no city at all


class EntityStatus(str, Enum):
    """What the registry said about the entity; UNKNOWN is not "inactive". Values are our
    machine keys, mapped from each source's own vocabulary by its adapter."""

    ACTIVE = "active"        # the registry said the entity is active
    INACTIVE = "inactive"    # the registry said the entity is not active
    UNKNOWN = "unknown"      # the registry said nothing, or an unknown word


@dataclass(frozen=True)
class Company:
    """A company from an external source, in the common shape."""

    source: str
    external_id: str            # LEI or another source identifier
    name: str
    city: str
    country: str
    address_lines: tuple[str, ...]
    registrar_id: str | None    # registration authority (GLEIF RA code)
    license_no: str | None
    created_on: date | None
    entity_status: EntityStatus
    registration_status: str    # the raw registry word: ISSUED / LAPSED / RETIRED / ...
    next_renewal_on: date | None
    entity_status_raw: str = ""  # the raw source value, for printing in a reason
    is_synthetic: bool = False

    @property
    def entity_active(self) -> bool:
        """Reports the status as a boolean for legacy consumers; engine code reads
        `entity_status`, since the third outcome is invisible here."""
        return self.entity_status is EntityStatus.ACTIVE


@dataclass(frozen=True)
class Evidence:
    """Evidence for an event; without it HIGH is never issued."""

    kind: str
    value: str


@dataclass(frozen=True)
class Score:
    """A priority and what produced it.

    Reasons are structural; rendered strings must not be passed alongside them.
    """

    tier: Tier
    address_type: AddressType
    event: Event
    reasons: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    violations: tuple[str, ...] = ()
    reason_items: tuple[Reason, ...] = ()

    def __post_init__(self) -> None:
        if self.reason_items:
            if self.reasons:
                raise ValueError(
                    "Score: причины заданы дважды — reason_items и reasons; "
                    "строки собирает отрисовка, передавать их отдельно нельзя"
                )
            object.__setattr__(
                self, "reasons", render_all(self.reason_items, DEFAULT_LANGUAGE)
            )

    def reasons_in(self, language: Language) -> tuple[str, ...]:
        """Returns the reasons in the given language; code-less stored strings raise."""
        if self.reason_items:
            return render_all(self.reason_items, language)
        if not self.reasons:
            return ()
        if language is DEFAULT_LANGUAGE:
            return self.reasons
        raise ReasonRenderError(
            f"причины восстановлены строками без кодов: язык {language.value} "
            f"не отрисовать (сохранено {len(self.reasons)} строк на "
            f"{DEFAULT_LANGUAGE.value})"
        )


class RequestType(str, Enum):
    OFFICE = "office"
    SETUP = "setup"
    VISA = "visa"
    ACCOUNTING = "accounting"
    RENEWAL = "renewal"          # licence, visa or Ejari renewal: recurring revenue
    BANK = "bank"
    OTHER = "other"


@dataclass(frozen=True)
class InboundMessage:
    """An inbound request from a customer channel: chat, messenger or web form."""

    external_id: str
    channel: str            # jivo | whatsapp | telegram | form
    text: str
    received_at: date
    is_synthetic: bool = True


@dataclass(frozen=True)
class LeadFacts:
    """Facts extracted from a request: the contract between extract.py and its readers."""

    request_types: tuple[RequestType, ...] = ()
    jurisdiction_hint: str | None = None      # mainland | freezone | a named zone | None
    headcount: int | None = None
    timeline_days: int | None = None          # in how many days the client needs a decision
    urgency_stated: bool = False              # urgency stated in words, with no date
    budget_hint: str | None = None
    language: str = "en"                      # ru | en | ar | mixed
    is_spam: bool = False
    has_contact: bool = False
    confidence: float = 0.0                   # 0..1, the model's confidence
    # Why a separate flag: a measured zero and an unmeasured zero look identical.
    confidence_measured: bool = True
    quotes: tuple[str, ...] = field(default_factory=tuple)  # verbatim evidence


def facts_from_dict(raw: dict) -> LeadFacts:
    """Rebuilds facts from their stored form; defined here so every reader shares one way."""
    return LeadFacts(
        request_types=tuple(RequestType(v) for v in raw.get("request_types", ())),
        jurisdiction_hint=raw.get("jurisdiction_hint"),
        headcount=raw.get("headcount"),
        timeline_days=raw.get("timeline_days"),
        urgency_stated=bool(raw.get("urgency_stated")),
        budget_hint=raw.get("budget_hint"),
        language=raw.get("language", "en"),
        is_spam=bool(raw.get("is_spam")),
        has_contact=bool(raw.get("has_contact")),
        confidence=float(raw.get("confidence") or 0.0),
        confidence_measured=bool(raw.get("confidence_measured", True)),
        quotes=tuple(raw.get("quotes", ())),
    )
