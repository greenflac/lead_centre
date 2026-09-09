"""Детерминированный расчёт приоритета. Модель извлекает факты, приоритет считает код.

Три исхода вместо двух (Р1): годный tier, LOW и INVALID — «не смогли оценить». INVALID
не сворачивается в LOW: это разные вещи для отчёта и для менеджера.
"""
from __future__ import annotations

from datetime import date

from leadcentre.engine import rubric
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


def classify_event(company: Company, today: date) -> tuple[Event, tuple[str, ...]]:
    """Ось B: самый сильный из поводов плюс человекочитаемые причины."""
    reasons: list[str] = []
    if company.registration_status == "LAPSED" and company.next_renewal_on:
        overdue = (today - company.next_renewal_on).days
        if 0 <= overdue <= rubric.LAPSED_FRESH_DAYS:
            reasons.append(f"регистрация LEI просрочена {overdue} дн.")
            return Event.LAPSED, tuple(reasons)
        if overdue > rubric.LAPSED_FRESH_DAYS:
            reasons.append(f"регистрация LEI просрочена давно ({overdue} дн.)")
            return Event.NONE, tuple(reasons)
    if company.created_on:
        age = (today - company.created_on).days
        if 0 <= age <= rubric.NEW_ENTITY_DAYS:
            reasons.append(f"юрлицо создано {age} дн. назад")
            return Event.NEW_ENTITY, tuple(reasons)
    if company.next_renewal_on and company.registration_status == "ISSUED":
        left = (company.next_renewal_on - today).days
        if 0 <= left <= rubric.RENEWAL_SOON_DAYS:
            reasons.append(f"продление LEI через {left} дн.")
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
    event, reasons = classify_event(company, today)
    tier = rubric.MATRIX[(address_type, event)]
    reasons = list(reasons)
    violations: list[str] = []

    # Модификаторы
    if tier is Tier.HIGH and company.city.strip().lower() not in rubric.TARGET_CITIES:
        tier = Tier.MEDIUM
        reasons.append(f"город вне целевых ({company.city or 'не указан'}) — ступень понижена")

    # Инварианты. Нарушение — не LOW, а INVALID (Р1).
    if not company.entity_active:
        tier = Tier.LOW
        reasons.append("юрлицо неактивно")
    evidence = collect_evidence(company)
    if tier is Tier.HIGH and not evidence:
        violations.append("HIGH без доказательства")
    if company.country != "AE":
        violations.append(f"компания вне ОАЭ: {company.country}")
    if violations:
        tier = Tier.INVALID

    return Score(
        tier=tier,
        address_type=address_type,
        event=event,
        reasons=tuple(reasons),
        evidence=evidence,
        violations=tuple(violations),
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
    reasons: list[str] = []
    violations: list[str] = []

    if facts.is_spam:
        return Score(
            tier=Tier.LOW,
            address_type=AddressType.UNKNOWN,
            event=Event.NONE,
            reasons=("обращение помечено как спам или не по теме",),
            evidence=tuple(Evidence("quote", q) for q in facts.quotes),
        )

    tier = rubric.INBOUND_BASE if facts.request_types else rubric.INBOUND_BASE_NO_REQUEST
    if not facts.request_types:
        reasons.append("из текста не извлечён ни один тип запроса")

    bumps = 0
    if facts.timeline_days is not None and facts.timeline_days <= rubric.URGENT_TIMELINE_DAYS:
        bumps += 1
        reasons.append(f"срок {facts.timeline_days} дн. — не больше {rubric.URGENT_TIMELINE_DAYS}")
    if len(facts.request_types) >= rubric.PACKAGE_MIN_REQUEST_TYPES:
        bumps += 1
        reasons.append(f"запрошено услуг: {len(facts.request_types)} — нужен пакет")
    if facts.headcount is not None and facts.headcount >= rubric.TEAM_MIN_HEADCOUNT:
        bumps += 1
        reasons.append(f"команда {facts.headcount} чел. — флекси не закроет визовую квоту")
    # Бюджетом считается только сумма. Модель охотно кладёт в это поле сам вопрос
    # «сколько стоит» — из вопроса о цене горячий лид не следует, скорее наоборот.
    if facts.budget_hint and any(ch.isdigit() for ch in facts.budget_hint):
        bumps += 1
        reasons.append(f"назван бюджет: {facts.budget_hint[:40]}")

    substantive = bumps

    # Язык — довесок, а не самостоятельный повод (см. LANGUAGE_NEEDS_ANOTHER_SIGNAL).
    if facts.language in rubric.TARGET_LANGUAGES:
        if substantive or not rubric.LANGUAGE_NEEDS_ANOTHER_SIGNAL:
            reasons.append(f"язык обращения {facts.language} — основная аудитория")
        else:
            reasons.append(f"язык обращения {facts.language}, но других признаков нет")

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
        reasons.append(f"уверенность извлечения {facts.confidence:.2f} — ниже порога")

    evidence = tuple(Evidence("quote", q) for q in facts.quotes)
    if tier is Tier.HIGH and not evidence:
        violations.append("HIGH без цитаты из обращения")
    if tier is Tier.HIGH and not message.text.strip():
        violations.append("HIGH на пустом тексте обращения")
    if violations:
        tier = Tier.INVALID

    return Score(
        tier=tier,
        address_type=AddressType.UNKNOWN,
        event=Event.NONE,
        reasons=tuple(reasons),
        evidence=evidence,
        violations=tuple(violations),
    )
