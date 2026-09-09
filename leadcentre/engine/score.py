"""Детерминированный расчёт приоритета. Модель извлекает факты, приоритет считает код.

Три исхода вместо двух (Р1): годный tier, LOW и INVALID — «не смогли оценить». INVALID
не сворачивается в LOW: это разные вещи для отчёта и для менеджера.
"""
from __future__ import annotations

from datetime import date

from leadcentre.engine import rubric
from leadcentre.models import AddressType, Company, Evidence, Event, Score, Tier


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
