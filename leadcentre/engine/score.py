"""Детерминированный расчёт приоритета. Модель извлекает факты, приоритет считает код.

Три исхода вместо двух (Р1): годный tier, LOW и INVALID — «не смогли оценить». INVALID
не сворачивается в LOW: это разные вещи для отчёта и для менеджера.

Текста здесь нет и быть не должно — ни у причин, ни у нарушений инвариантов: модуль
называет код и параметры, формулировки на русском и английском живут в
`engine/reasons.py` (Е1). `Score.violations` остаётся кортежем строк на русском, но
строки эти собирает отрисовка каталога, а не этот модуль; на английский те же нарушения
отдаёт `reasons.texts_in(score.violations, Language.EN)`.
"""
from __future__ import annotations

from datetime import date

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
    Company,
    Event,
    Evidence,
    InboundMessage,
    LeadFacts,
    Score,
    Tier,
)


def classify_address(company: Company) -> AddressType:
    """Ось A по тексту адреса и органу регистрации."""
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


def classify_event(company: Company, today: date) -> tuple[Event, tuple[Reason, ...]]:
    """Ось B: самый сильный из поводов плюс причины кодами и параметрами."""
    reasons: list[Reason] = []
    if company.registration_status == "LAPSED" and company.next_renewal_on:
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
    if company.next_renewal_on and company.registration_status == "ISSUED":
        left = (company.next_renewal_on - today).days
        if 0 <= left <= rubric.RENEWAL_SOON_DAYS:
            reasons.append(reason(ReasonCode.LEI_RENEWAL_SOON, days=left))
            return Event.RENEWAL_SOON, tuple(reasons)
    return Event.NONE, tuple(reasons)


def collect_evidence(company: Company) -> tuple[Evidence, ...]:
    items = [Evidence("lei", company.external_id)]
    if company.license_no:
        items.append(Evidence("license_no", company.license_no))
    if company.next_renewal_on:
        items.append(Evidence("next_renewal_on", company.next_renewal_on.isoformat()))
    if company.created_on:
        items.append(Evidence("created_on", company.created_on.isoformat()))
    return tuple(items)


def score(company: Company, today: date) -> Score:
    address_type = classify_address(company)
    event, event_reasons = classify_event(company, today)
    tier = rubric.MATRIX[(address_type, event)]
    reasons: list[Reason] = list(event_reasons)
    violations: list[Violation] = []

    # Модификаторы
    if tier is Tier.HIGH and company.city.strip().lower() not in rubric.TARGET_CITIES:
        tier = Tier.MEDIUM
        city = company.city.strip()
        reasons.append(
            reason(ReasonCode.CITY_OFF_TARGET, city=city) if city
            else reason(ReasonCode.CITY_NOT_SET)
        )

    # Инварианты. Нарушение — не LOW, а INVALID (Р1). Нарушение — такой же код с
    # параметрами, как причина: текст ему собирает каталог, оба языка сразу.
    if not company.entity_active:
        tier = Tier.LOW
        reasons.append(reason(ReasonCode.ENTITY_INACTIVE))
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


# --- входящие обращения: ось C поверх базового уровня ---


def _step(tier: Tier, delta: int) -> Tier:
    """Сдвиг на ступень по лестнице приоритетов, без выхода за края."""
    ladder = rubric.TIER_LADDER
    index = min(max(ladder.index(tier) + delta, 0), len(ladder) - 1)
    return ladder[index]


def score_inbound(message: InboundMessage, facts: LeadFacts) -> Score:
    """Приоритет входящего обращения.

    Модель извлекла факты, дальше решает код: повышают срочность, пакет услуг и язык,
    понижают отсутствие деталей и низкая уверенность извлечения. Спам и обращение без
    единого извлечённого запроса — это LOW, а не «не смогли»: текст мы прочитали.
    Нарушение инварианта — INVALID, отдельный третий исход (Р1).
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
    if len(facts.request_types) >= rubric.PACKAGE_MIN_REQUEST_TYPES:
        bumps += 1
        reasons.append(reason(ReasonCode.PACKAGE_REQUEST, count=len(facts.request_types)))
    if facts.headcount is not None and facts.headcount >= rubric.TEAM_MIN_HEADCOUNT:
        bumps += 1
        reasons.append(reason(ReasonCode.TEAM_OVER_FLEXI_QUOTA, headcount=facts.headcount))
    # Бюджетом считается только сумма. Модель охотно кладёт в это поле сам вопрос
    # «сколько стоит» — из вопроса о цене горячий лид не следует, скорее наоборот.
    if facts.budget_hint and any(ch.isdigit() for ch in facts.budget_hint):
        bumps += 1
        reasons.append(reason(ReasonCode.BUDGET_NAMED, budget=facts.budget_hint[:40]))

    substantive = bumps

    # Язык — довесок, а не самостоятельный повод (см. LANGUAGE_NEEDS_ANOTHER_SIGNAL).
    if facts.language in rubric.TARGET_LANGUAGES:
        if substantive or not rubric.LANGUAGE_NEEDS_ANOTHER_SIGNAL:
            reasons.append(reason(ReasonCode.TARGET_LANGUAGE, language=facts.language))
        else:
            reasons.append(
                reason(ReasonCode.TARGET_LANGUAGE_ALONE, language=facts.language)
            )

    # Горячим делает только набор содержательных признаков: одного мало, иначе HIGH
    # достаётся половине входящих и перестаёт что-либо значить (см. SIGNALS_FOR_HIGH).
    # Две разные причины подняться на ступень, намеренно не слитые в одно условие:
    # набор признаков делает обращение горячим, а один признак вытаскивает из LOW
    # обращение, где не распознан тип запроса. Слияние через or прячет вторую причину.
    enough_signals = substantive >= rubric.SIGNALS_FOR_HIGH
    rescued_from_low = bool(substantive) and tier is Tier.LOW
    if enough_signals or rescued_from_low:
        tier = _step(tier, 1)

    # Понижающие. Низкая уверенность извлечения не даёт подняться выше среднего:
    # приоритет, выведенный из ненадёжных фактов, дороже пропущенного лида.
    if facts.confidence < rubric.LOW_CONFIDENCE:
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
