"""Тесты рубрики и скоринга.

Ожидаемое — литералами (Т2): пороги записаны числами (90 / 60 / 90), ступени —
членами `Tier`, ни одного обращения к `rubric.MATRIX` или `rubric.*_DAYS`.
Фикстуры берутся с обоих краёв диапазона и из середины (Т3).
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from leadcentre.engine.score import classify_address, classify_event, score
from leadcentre.models import AddressType, Event, Tier
from tests.conftest import (
    ADDR_BUSINESS_CENTRE,
    ADDR_EMPTY,
    ADDR_OWN,
    ADDR_REGISTRAR,
    TODAY,
    lapsed_company,
    make_company,
    new_company,
    renewal_company,
)

# --- ось A: классификатор адреса -------------------------------------------------


@pytest.mark.parametrize(
    ("address_lines", "registrar_id", "expected"),
    [
        (ADDR_REGISTRAR, "RA999999", AddressType.REGISTRAR),
        (("Business Centre,Sharjah Publishing City Free Zone",), "RA888888", AddressType.REGISTRAR),
        (ADDR_OWN, "RA000752", AddressType.REGISTRAR),  # по RA-коду органа регистрации
        (ADDR_BUSINESS_CENTRE, "RA999999", AddressType.BUSINESS_CENTRE),
        (("Regus, Level 3, Boulevard Plaza",), "RA999999", AddressType.BUSINESS_CENTRE),
        (ADDR_OWN, "RA999999", AddressType.OWN),
        (ADDR_EMPTY, "RA999999", AddressType.UNKNOWN),
        (("", "   "), "RA999999", AddressType.UNKNOWN),  # негативный контроль: пустой адрес
        (("MEYDAN GRANDSTAND, 6TH FLOOR",), "RA999999", AddressType.REGISTRAR),  # регистр
    ],
)
def test_classify_address(address_lines, registrar_id, expected):
    company = make_company(address_lines=address_lines, registrar_id=registrar_id)
    assert classify_address(company) is expected


def test_registrar_marker_wins_over_business_centre():
    """Приоритет осей A1 > A2: адрес зоны сильнее слова «business centre» в той же строке."""
    company = make_company(
        address_lines=("Business Centre, Meydan Grandstand, 6th floor",), registrar_id="RA999999"
    )
    assert classify_address(company) is AddressType.REGISTRAR


# --- ось B: пороги. Края и середина каждого диапазона (Т3) ------------------------


@pytest.mark.parametrize(
    ("overdue_days", "expected"),
    [
        (0, Event.LAPSED),    # ровно сегодня истёк
        (1, Event.LAPSED),
        (45, Event.LAPSED),   # середина
        (90, Event.LAPSED),   # ровно порог LAPSED_FRESH_DAYS — ещё повод
        (91, Event.NONE),     # на день дальше — повода нет
        (400, Event.NONE),
    ],
)
def test_lapsed_threshold_is_90_days(overdue_days, expected):
    event, reasons = classify_event(lapsed_company(overdue_days), TODAY)
    assert event is expected
    assert reasons, "причина обязана быть напечатана в обоих исходах просрочки"
    assert str(overdue_days) in reasons[0]


def test_lapsed_in_future_is_not_an_event():
    """Негативный контроль: дата продления в будущем при статусе LAPSED — не повод B1."""
    company = make_company(
        registration_status="LAPSED", next_renewal_on=TODAY + timedelta(days=10)
    )
    event, _ = classify_event(company, TODAY)
    assert event is Event.NONE


@pytest.mark.parametrize(
    ("age_days", "expected"),
    [
        (0, Event.NEW_ENTITY),   # создана сегодня
        (1, Event.NEW_ENTITY),
        (45, Event.NEW_ENTITY),  # середина
        (90, Event.NEW_ENTITY),  # ровно порог NEW_ENTITY_DAYS
        (91, Event.NONE),
        (1000, Event.NONE),
    ],
)
def test_new_entity_threshold_is_90_days(age_days, expected):
    event, _ = classify_event(new_company(age_days), TODAY)
    assert event is expected


@pytest.mark.parametrize(
    ("days_left", "expected"),
    [
        (0, Event.RENEWAL_SOON),   # продление сегодня
        (1, Event.RENEWAL_SOON),
        (30, Event.RENEWAL_SOON),  # середина
        (60, Event.RENEWAL_SOON),  # ровно порог RENEWAL_SOON_DAYS
        (61, Event.NONE),
        (300, Event.NONE),
    ],
)
def test_renewal_soon_threshold_is_60_days(days_left, expected):
    event, _ = classify_event(renewal_company(days_left), TODAY)
    assert event is expected


def test_lapsed_wins_over_new_entity():
    """Приоритет поводов: свежая просрочка сильнее свежего юрлица."""
    company = lapsed_company(10, created_on=TODAY - timedelta(days=10))
    event, _ = classify_event(company, TODAY)
    assert event is Event.LAPSED


def test_renewal_soon_requires_issued_status():
    """Негативный контроль: у не-ISSUED регистрации повода «продление» нет."""
    company = make_company(
        registration_status="RETIRED",
        created_on=TODAY - timedelta(days=1000),
        next_renewal_on=TODAY + timedelta(days=10),
    )
    event, _ = classify_event(company, TODAY)
    assert event is Event.NONE


# --- матрица A x B: все 16 ячеек литералами --------------------------------------

_ADDRESS_INPUT = {
    AddressType.REGISTRAR: {"address_lines": ADDR_REGISTRAR, "registrar_id": "RA999999"},
    AddressType.BUSINESS_CENTRE: {
        "address_lines": ADDR_BUSINESS_CENTRE,
        "registrar_id": "RA999999",
    },
    AddressType.OWN: {"address_lines": ADDR_OWN, "registrar_id": "RA999999"},
    AddressType.UNKNOWN: {"address_lines": ADDR_EMPTY, "registrar_id": "RA999999"},
}


def _company_for(address: AddressType, event: Event):
    extra = _ADDRESS_INPUT[address]
    if event is Event.LAPSED:
        return lapsed_company(10, **extra)
    if event is Event.NEW_ENTITY:
        return new_company(10, **extra)
    if event is Event.RENEWAL_SOON:
        return renewal_company(30, **extra)
    return renewal_company(300, **extra)


@pytest.mark.parametrize(
    ("address", "event", "expected_tier"),
    [
        (AddressType.REGISTRAR, Event.LAPSED, Tier.HIGH),
        (AddressType.REGISTRAR, Event.NEW_ENTITY, Tier.HIGH),
        (AddressType.REGISTRAR, Event.RENEWAL_SOON, Tier.MEDIUM),
        (AddressType.REGISTRAR, Event.NONE, Tier.LOW),
        (AddressType.BUSINESS_CENTRE, Event.LAPSED, Tier.MEDIUM),
        (AddressType.BUSINESS_CENTRE, Event.NEW_ENTITY, Tier.MEDIUM),
        (AddressType.BUSINESS_CENTRE, Event.RENEWAL_SOON, Tier.MEDIUM),
        (AddressType.BUSINESS_CENTRE, Event.NONE, Tier.LOW),
        (AddressType.OWN, Event.LAPSED, Tier.MEDIUM),
        (AddressType.OWN, Event.NEW_ENTITY, Tier.LOW),
        (AddressType.OWN, Event.RENEWAL_SOON, Tier.LOW),
        (AddressType.OWN, Event.NONE, Tier.LOW),
        (AddressType.UNKNOWN, Event.LAPSED, Tier.MEDIUM),
        (AddressType.UNKNOWN, Event.NEW_ENTITY, Tier.MEDIUM),
        (AddressType.UNKNOWN, Event.RENEWAL_SOON, Tier.LOW),
        (AddressType.UNKNOWN, Event.NONE, Tier.LOW),
    ],
)
def test_matrix_cell(address, event, expected_tier):
    result = score(_company_for(address, event), TODAY)
    assert (result.address_type, result.event) == (address, event)
    assert result.tier is expected_tier
    assert result.violations == ()


# --- модификаторы и инварианты ---------------------------------------------------


def test_high_outside_dubai_is_downgraded_to_medium():
    company = lapsed_company(10, city="Sharjah", address_lines=ADDR_REGISTRAR)
    result = score(company, TODAY)
    assert result.tier is Tier.MEDIUM
    assert any("город вне целевых" in r for r in result.reasons)


def test_high_stays_high_in_dubai_case_insensitive():
    company = lapsed_company(10, city="DUBAI", address_lines=ADDR_REGISTRAR)
    assert score(company, TODAY).tier is Tier.HIGH


def test_inactive_entity_is_low():
    """Негативный контроль: неактивное юрлицо — LOW, даже если ячейка матрицы HIGH."""
    company = lapsed_company(10, address_lines=ADDR_REGISTRAR, entity_active=False)
    result = score(company, TODAY)
    assert result.tier is Tier.LOW
    assert "юрлицо неактивно" in result.reasons
    assert result.violations == ()


def test_non_ae_country_is_invalid_with_violation():
    """Негативный контроль: компания не из ОАЭ — INVALID и непустые violations (Р1)."""
    company = lapsed_company(10, country="SA", address_lines=ADDR_REGISTRAR)
    result = score(company, TODAY)
    assert result.tier is Tier.INVALID
    assert result.violations == ("компания вне ОАЭ: SA",)


def test_invalid_wins_over_inactive_low():
    """INVALID не сворачивается в LOW: «не смогли оценить» ≠ «оценили низко»."""
    company = lapsed_company(10, country="SA", entity_active=False)
    result = score(company, TODAY)
    assert result.tier is Tier.INVALID
    assert len(result.violations) == 1


def test_empty_country_is_invalid():
    result = score(make_company(country=""), TODAY)
    assert result.tier is Tier.INVALID
    assert result.violations == ("компания вне ОАЭ: ",)


def test_evidence_carries_lei_license_and_dates():
    company = lapsed_company(10, license_no="LIC-777", created_on=TODAY - timedelta(days=1000))
    result = score(company, TODAY)
    assert [(e.kind, e.value) for e in result.evidence] == [
        ("lei", "TESTLEI0000000000001"),
        ("license_no", "LIC-777"),
        ("next_renewal_on", "2026-08-30"),
        ("created_on", "2023-12-14"),
    ]


def test_evidence_without_optional_fields():
    company = make_company(license_no=None, created_on=None, next_renewal_on=None)
    result = score(company, TODAY)
    assert [e.kind for e in result.evidence] == ["lei"]
