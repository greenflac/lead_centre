"""Deterministic priority scoring: the model extracts facts, this code assigns the tier.

INVALID ("could not score") is a separate outcome and never collapses into LOW.
This module names reason codes and parameters only; the wording for both languages
lives in engine/reasons.py, and the thresholds in engine/rubric.py.
"""
from __future__ import annotations

from datetime import date
from functools import cache

from leadcentre.engine import rubric
from leadcentre.engine.reasons import (
    Reason,
    ReasonCode,
    Violation,
    ViolationCode,
    reason,
    rendered_all,
    violation,
)
from leadcentre.models import (
    AddressType,
    CityMatch,
    Company,
    EntityStatus,
    Event,
    Evidence,
    InboundMessage,
    LeadFacts,
    Score,
    Tier,
)


def classify_address(company: Company) -> AddressType:
    """Returns axis A from the address lines and the registration authority."""
    haystack = " ".join(company.address_lines).lower()
    if any(m in haystack for m in rubric.REGISTRAR_ADDRESS_MARKERS):
        return AddressType.REGISTRAR
    if company.registrar_id in rubric.REGISTRAR_AUTHORITY_IDS:
        return AddressType.REGISTRAR
    if any(m in haystack for m in rubric.BUSINESS_CENTRE_MARKERS):
        return AddressType.BUSINESS_CENTRE
    if haystack.strip():
        return AddressType.OWN
    return AddressType.UNKNOWN


def normalize_city(value: str) -> str:
    """Folds a city string for comparison: lower case, one alef form, collapsed spaces.

    Both sides of the comparison are folded, so the marker lists need one spelling per name.
    """
    folded = value.strip().lower()
    for form in rubric.CITY_ALEF_FORMS:
        folded = folded.replace(form, rubric.CITY_ALEF_CANONICAL)
    return " ".join(folded.split())


def _city_parts(normalized: str) -> tuple[str, ...]:
    """Splits a city field written as an address line into its parts.

    Districts are matched against whole parts, since as a substring "al ain" would be
    found inside unrelated words.
    """
    parts = [normalized]
    for separator in rubric.CITY_PART_SEPARATORS:
        parts = [chunk for part in parts for chunk in part.split(separator)]
    return tuple(p for p in (" ".join(x.split()) for x in parts) if p)


@cache
def _normalized(markers: tuple[str, ...]) -> frozenset[str]:
    """Folds rubric markers with the same normaliser as the input.

    Cached on the tuple rather than precomputed, so that replacing a rubric list in a
    mutation test still reaches the comparison.
    """
    return frozenset(normalize_city(m) for m in markers)


def classify_city(city: str) -> CityMatch:
    """Classifies a registry city string into one of four outcomes.

    The order of the checks is the rule itself: off-target districts, then off-target
    city names, then target districts, then target city names, else UNRECOGNISED.
    Off-target wins over target because raising to HIGH on a guess costs more than
    not raising.
    """
    normalized = normalize_city(city)
    if not normalized:
        return CityMatch.NOT_SET
    parts = _city_parts(normalized)
    if any(part in _normalized(rubric.NON_TARGET_DISTRICTS) for part in parts):
        return CityMatch.OFF_TARGET
    if any(m in normalized for m in _normalized(rubric.NON_TARGET_CITY_MARKERS)):
        return CityMatch.OFF_TARGET
    if any(part in _normalized(rubric.TARGET_DISTRICTS) for part in parts):
        return CityMatch.TARGET
    if any(m in normalized for m in _normalized(rubric.TARGET_CITY_MARKERS)):
        return CityMatch.TARGET
    return CityMatch.UNRECOGNISED


#: Reason per city outcome. A table, not an if-chain: a missing branch would be a tier
#: without an explanation.
CITY_REASON: dict[CityMatch, ReasonCode] = {
    CityMatch.OFF_TARGET: ReasonCode.CITY_OFF_TARGET,
    CityMatch.UNRECOGNISED: ReasonCode.CITY_UNRECOGNISED,
    CityMatch.NOT_SET: ReasonCode.CITY_NOT_SET,
}


def normalize_status(value: str) -> str:
    """Folds a registry status for comparison; another source may not use upper case."""
    return value.strip().upper()


def status_reason(company: Company) -> Reason | None:
    """Explains why the registration status produced no event; None when it did.

    Three outcomes: status not set, status outside the registry vocabulary ("could not
    determine"), and a known status that axis B counts no event for. None of them moves
    the tier; they only make an already taken decision visible.
    """
    raw = company.registration_status.strip()
    status = normalize_status(raw)
    if not status:
        return reason(ReasonCode.REGISTRATION_STATUS_NOT_SET)
    if status not in rubric.KNOWN_REGISTRATION_STATUSES:
        return reason(ReasonCode.REGISTRATION_STATUS_UNKNOWN, status=raw[:40])
    if status in (rubric.STATUS_LAPSED, rubric.STATUS_ISSUED):
        return None
    return reason(ReasonCode.REGISTRATION_STATUS_NO_EVENT, status=raw[:40])


def classify_event(company: Company, today: date) -> tuple[Event, tuple[Reason, ...]]:
    """Returns axis B: the strongest event plus its reasons as codes and parameters."""
    reasons: list[Reason] = []
    status = normalize_status(company.registration_status)
    if status == rubric.STATUS_LAPSED and company.next_renewal_on:
        overdue = (today - company.next_renewal_on).days
        if 0 <= overdue <= rubric.LAPSED_FRESH_DAYS:
            reasons.append(reason(ReasonCode.LEI_LAPSED_FRESH, days=overdue))
            return Event.LAPSED, tuple(reasons)
        if overdue > rubric.LAPSED_FRESH_DAYS:
            reasons.append(reason(ReasonCode.LEI_LAPSED_LONG_AGO, days=overdue))
            return Event.NONE, tuple(reasons)
    if company.created_on:
        age = (today - company.created_on).days
        if 0 <= age <= rubric.NEW_ENTITY_DAYS:
            reasons.append(reason(ReasonCode.ENTITY_RECENTLY_CREATED, days=age))
            return Event.NEW_ENTITY, tuple(reasons)
    if company.next_renewal_on and status == rubric.STATUS_ISSUED:
        left = (company.next_renewal_on - today).days
        if 0 <= left <= rubric.RENEWAL_SOON_DAYS:
            reasons.append(reason(ReasonCode.LEI_RENEWAL_SOON, days=left))
            return Event.RENEWAL_SOON, tuple(reasons)
    # Why: no event still needs a named cause; see status_reason.
    about_status = status_reason(company)
    if about_status is not None:
        reasons.append(about_status)
    return Event.NONE, tuple(reasons)


def collect_evidence(company: Company) -> tuple[Evidence, ...]:
    """Returns the registry facts that back a company score."""
    items = [Evidence("lei", company.external_id)]
    if company.license_no:
        items.append(Evidence("license_no", company.license_no))
    if company.next_renewal_on:
        items.append(Evidence("next_renewal_on", company.next_renewal_on.isoformat()))
    if company.created_on:
        items.append(Evidence("created_on", company.created_on.isoformat()))
    return tuple(items)


def score(company: Company, today: date) -> Score:
    """Scores a company from the registry: matrix tier, then modifiers and invariants."""
    address_type = classify_address(company)
    event, event_reasons = classify_event(company, today)
    tier = rubric.MATRIX[(address_type, event)]
    reasons: list[Reason] = list(event_reasons)
    violations: list[Violation] = []

    # Why every non-target outcome lowers the tier but keeps its own reason: "another
    # emirate" and "spelling not recognised" read differently and are counted apart.
    city_match = classify_city(company.city)
    if tier is Tier.HIGH and city_match is not CityMatch.TARGET:
        tier = Tier.MEDIUM
        code = CITY_REASON[city_match]
        city = company.city.strip()
        reasons.append(
            reason(code) if code is ReasonCode.CITY_NOT_SET else reason(code, city=city)
        )

    # Why only an explicit INACTIVE condemns the entity: the registry also says NULL,
    # meaning "status not reported", and silence caps the tier without burying the lead.
    if company.entity_status is EntityStatus.INACTIVE:
        tier = Tier.LOW
        reasons.append(reason(ReasonCode.ENTITY_INACTIVE))
    elif company.entity_status is EntityStatus.UNKNOWN:
        tier = min(tier, Tier.MEDIUM, key=rubric.TIER_LADDER.index)
        raw_status = company.entity_status_raw.strip()
        reasons.append(
            reason(ReasonCode.ENTITY_STATUS_UNKNOWN, status=raw_status[:40])
            if raw_status
            else reason(ReasonCode.ENTITY_STATUS_NOT_SET)
        )
    evidence = collect_evidence(company)
    if tier is Tier.HIGH and not evidence:
        violations.append(violation(ViolationCode.HIGH_WITHOUT_EVIDENCE))
    if company.country != "AE":
        violations.append(
            violation(ViolationCode.COMPANY_OUTSIDE_UAE, country=company.country)
        )
    if violations:
        tier = Tier.INVALID

    return Score(
        tier=tier,
        address_type=address_type,
        event=event,
        reason_items=tuple(reasons),
        evidence=evidence,
        violations=rendered_all(tuple(violations)),
    )


def _step(tier: Tier, delta: int) -> Tier:
    """Moves one step along the tier ladder, clamped at both ends."""
    ladder = rubric.TIER_LADDER
    index = min(max(ladder.index(tier) + delta, 0), len(ladder) - 1)
    return ladder[index]


def score_inbound(message: InboundMessage, facts: LeadFacts) -> Score:
    """Scores an inbound request: axis C modifiers over a base tier.

    Spam and a request with no recognised type are LOW, not "could not score" — the text
    was read. A broken invariant is INVALID, the separate third outcome.
    """
    reasons: list[Reason] = []
    violations: list[Violation] = []

    if facts.is_spam:
        return Score(
            tier=Tier.LOW,
            address_type=AddressType.UNKNOWN,
            event=Event.NONE,
            reason_items=(reason(ReasonCode.SPAM_OR_OFF_TOPIC),),
            evidence=tuple(Evidence("quote", q) for q in facts.quotes),
        )

    tier = rubric.INBOUND_BASE if facts.request_types else rubric.INBOUND_BASE_NO_REQUEST
    if not facts.request_types:
        reasons.append(reason(ReasonCode.NO_REQUEST_TYPE))

    bumps = 0
    if facts.timeline_days is not None and facts.timeline_days <= rubric.URGENT_TIMELINE_DAYS:
        bumps += 1
        reasons.append(reason(
            ReasonCode.URGENT_TIMELINE,
            days=facts.timeline_days,
            limit=rubric.URGENT_TIMELINE_DAYS,
        ))
    elif facts.urgency_stated:
        # Why a signal but no date: "срочно" counts as substance, yet invents no deadline.
        bumps += 1
        reasons.append(reason(ReasonCode.URGENT_STATED))
    if len(facts.request_types) >= rubric.PACKAGE_MIN_REQUEST_TYPES:
        bumps += 1
        reasons.append(reason(ReasonCode.PACKAGE_REQUEST, count=len(facts.request_types)))
    if facts.headcount is not None and facts.headcount >= rubric.TEAM_MIN_HEADCOUNT:
        bumps += 1
        reasons.append(reason(ReasonCode.TEAM_OVER_FLEXI_QUOTA, headcount=facts.headcount))
    # Why a digit is required: the model readily puts the question "how much" in this
    # field, and asking a price does not make a lead hot.
    if facts.budget_hint and any(ch.isdigit() for ch in facts.budget_hint):
        bumps += 1
        reasons.append(reason(ReasonCode.BUDGET_NAMED, budget=facts.budget_hint[:40]))

    substantive = bumps

    # Why: language is an add-on, not a signal of its own.
    if facts.language in rubric.TARGET_LANGUAGES:
        if substantive or not rubric.LANGUAGE_NEEDS_ANOTHER_SIGNAL:
            reasons.append(reason(ReasonCode.TARGET_LANGUAGE, language=facts.language))
        else:
            reasons.append(
                reason(ReasonCode.TARGET_LANGUAGE_ALONE, language=facts.language)
            )

    # Why two named conditions instead of one `or`: a set of signals makes a request hot,
    # while a single signal only rescues one whose request type went unrecognised.
    enough_signals = substantive >= rubric.SIGNALS_FOR_HIGH
    rescued_from_low = bool(substantive) and tier is Tier.LOW
    if enough_signals or rescued_from_low:
        tier = _step(tier, 1)

    # Why three confidence outcomes: measured-and-low and never-measured both cap the
    # tier, but an unmeasured number must not be printed next to a threshold.
    if not facts.confidence_measured:
        tier = min(tier, Tier.MEDIUM, key=rubric.TIER_LADDER.index)
        reasons.append(reason(ReasonCode.CONFIDENCE_NOT_MEASURED))
    elif facts.confidence < rubric.LOW_CONFIDENCE:
        tier = min(tier, Tier.MEDIUM, key=rubric.TIER_LADDER.index)
        reasons.append(reason(
            ReasonCode.LOW_CONFIDENCE,
            confidence=facts.confidence,
            threshold=rubric.LOW_CONFIDENCE,
        ))

    evidence = tuple(Evidence("quote", q) for q in facts.quotes)
    if tier is Tier.HIGH and not evidence:
        violations.append(violation(ViolationCode.HIGH_WITHOUT_QUOTE))
    if tier is Tier.HIGH and not message.text.strip():
        violations.append(violation(ViolationCode.HIGH_ON_EMPTY_TEXT))
    if violations:
        tier = Tier.INVALID

    return Score(
        tier=tier,
        address_type=AddressType.UNKNOWN,
        event=Event.NONE,
        reason_items=tuple(reasons),
        evidence=evidence,
        violations=rendered_all(tuple(violations)),
    )
