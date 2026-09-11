"""Тесты рубрики и скоринга.

Ожидаемое — литералами: пороги записаны числами (90 / 60 / 90), ступени —
членами `Tier`, ни одного обращения к `rubric.MATRIX` или `rubric.*_DAYS`.
Фикстуры берутся с обоих краёв диапазона и из середины.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from leadcentre.engine.reasons import ReasonCode
from leadcentre.engine.score import (
    classify_address,
    classify_city,
    classify_event,
    score,
)
from leadcentre.models import AddressType, CityMatch, EntityStatus, Event, Tier
from tests.conftest import (
    ADDR_BUSINESS_CENTRE,
    ADDR_EMPTY,
    ADDR_OWN,
    ADDR_REGISTRAR,
    TODAY,
    has_reason,
    lapsed_company,
    make_company,
    new_company,
    reason_codes,
    reason_params,
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


# --- ось B: пороги. Края и середина каждого диапазона ------------------------


@pytest.mark.parametrize(
    ("overdue_days", "expected", "expected_code"),
    [
        (0, Event.LAPSED, ReasonCode.LEI_LAPSED_FRESH),    # ровно сегодня истёк
        (1, Event.LAPSED, ReasonCode.LEI_LAPSED_FRESH),
        (45, Event.LAPSED, ReasonCode.LEI_LAPSED_FRESH),   # середина
        # ровно порог LAPSED_FRESH_DAYS — ещё повод
        (90, Event.LAPSED, ReasonCode.LEI_LAPSED_FRESH),
        # на день дальше — повода нет, и причина уже другая
        (91, Event.NONE, ReasonCode.LEI_LAPSED_LONG_AGO),
        (400, Event.NONE, ReasonCode.LEI_LAPSED_LONG_AGO),
    ],
)
def test_lapsed_threshold_is_90_days(overdue_days, expected, expected_code):
    """Порог 90 наблюдаем и в поводе, и в коде причины.

    Причина сверяется кодом и параметром, а не текстом: формулировка «просрочена на N
    дней» — дело каталога (tests/test_reasons.py), а здесь проверяется, какое правило
    сработало и с каким числом.
    """
    event, reasons = classify_event(lapsed_company(overdue_days), TODAY)
    assert event is expected
    assert reason_codes(reasons) == [expected_code], (
        "причина обязана быть названа в обоих исходах просрочки"
    )
    assert reason_params(reasons, expected_code) == {"days": overdue_days}


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
    assert reason_params(result, ReasonCode.CITY_OFF_TARGET) == {"city": "Sharjah"}
    assert not has_reason(result, ReasonCode.CITY_NOT_SET)


def test_high_stays_high_in_dubai_case_insensitive():
    company = lapsed_company(10, city="DUBAI", address_lines=ADDR_REGISTRAR)
    assert score(company, TODAY).tier is Tier.HIGH


# --- город: реестр пишет его четырьмя способами ----------------------------------
#
# Все строки — литералы из выдачи GLEIF (data/gleif_ae_*_sample.json), ни один список
# написаний из `rubric` сюда не импортируется: тест обязан покраснеть, когда список
# поедет. Фикстуры с обоих краёв и из середины: целевой город в трёх написаниях,
# район без слова «Дубай», адрес с запятой, нецелевой эмират в двух написаниях,
# район чужого эмирата, пустая строка и написание, которого в списках нет.


@pytest.mark.parametrize(
    ("city", "expected"),
    [
        ("Dubai", CityMatch.TARGET),
        ("DUBAI", CityMatch.TARGET),
        ("دبي", CityMatch.TARGET),
        ("Nad Al Sheba", CityMatch.TARGET),
        ("Dubai Silicon Oasis", CityMatch.TARGET),
        ("Jumeirah Lakes Towers, Dubai", CityMatch.TARGET),
        ("  dubai  ", CityMatch.TARGET),
        ("Abu Dhabi", CityMatch.OFF_TARGET),
        ("ABU DHABI", CityMatch.OFF_TARGET),
        ("أبو ظبي", CityMatch.OFF_TARGET),
        ("أبوظبي", CityMatch.OFF_TARGET),
        ("ابوظبي", CityMatch.OFF_TARGET),
        ("Al Reem Island", CityMatch.OFF_TARGET),   # район Абу-Даби, не Дубая
        ("جزيرة الريم", CityMatch.OFF_TARGET),
        ("Sharjah", CityMatch.OFF_TARGET),
        ("الشارقة", CityMatch.OFF_TARGET),
        ("Ajman", CityMatch.OFF_TARGET),
        ("منطقة عجمان الحرة", CityMatch.OFF_TARGET),
        ("Ras Al Khaimah/ رأس الخيمة", CityMatch.OFF_TARGET),
        ("RAS AL-KHAIMAH", CityMatch.OFF_TARGET),
        ("", CityMatch.NOT_SET),
        ("   ", CityMatch.NOT_SET),
        ("Hatta", CityMatch.UNRECOGNISED),
        ("Al Reem", CityMatch.UNRECOGNISED),
    ],
)
def test_classify_city(city, expected):
    assert classify_city(city) is expected


def test_district_of_dubai_keeps_high():
    """Дефект, ради которого правило переписано: район Дубая снижал ступень.

    ИЗМЕРЕНО 2026-09-11 на data/gleif_ae_lapsed_sample.json: четыре компании с
    region=AE-DU были снижены с HIGH до MEDIUM, две из них — с городом «Nad Al Sheba».
    """
    company = lapsed_company(10, city="Nad Al Sheba", address_lines=ADDR_REGISTRAR)
    result = score(company, TODAY)
    assert result.tier is Tier.HIGH
    assert not has_reason(result, ReasonCode.CITY_OFF_TARGET)
    assert not has_reason(result, ReasonCode.CITY_UNRECOGNISED)


def test_arabic_dubai_keeps_high():
    company = lapsed_company(10, city="دبي", address_lines=ADDR_REGISTRAR)
    assert score(company, TODAY).tier is Tier.HIGH


def test_address_with_a_comma_keeps_high():
    company = lapsed_company(
        10, city="Jumeirah Lakes Towers, Dubai", address_lines=ADDR_REGISTRAR
    )
    assert score(company, TODAY).tier is Tier.HIGH


@pytest.mark.parametrize("city", ["Abu Dhabi", "أبو ظبي", "Al Reem Island", "Ajman"])
def test_other_emirates_stay_off_target(city):
    """Негативный контроль правила: расширение списков не должно втащить соседей.

    Если бы «Дубай» стал означать «любой город ОАЭ», этот тест покраснел бы первым.
    """
    company = lapsed_company(10, city=city, address_lines=ADDR_REGISTRAR)
    result = score(company, TODAY)
    assert result.tier is Tier.MEDIUM
    assert reason_params(result, ReasonCode.CITY_OFF_TARGET) == {"city": city}


def test_unknown_spelling_is_its_own_outcome_not_a_silent_off_target():
    """Третий исход: написание не узнали — это не «другой эмират».

    Ступень понижается так же, но причина другая: по ней видно, что списки написаний
    отстали от реестра, а не что компания сидит в Шардже.
    """
    company = lapsed_company(10, city="Hatta", address_lines=ADDR_REGISTRAR)
    result = score(company, TODAY)
    assert result.tier is Tier.MEDIUM
    assert reason_params(result, ReasonCode.CITY_UNRECOGNISED) == {"city": "Hatta"}
    assert not has_reason(result, ReasonCode.CITY_OFF_TARGET)
    assert not has_reason(result, ReasonCode.CITY_NOT_SET)


def test_empty_city_is_not_set_and_not_unrecognised():
    company = lapsed_company(10, city="   ", address_lines=ADDR_REGISTRAR)
    result = score(company, TODAY)
    assert result.tier is Tier.MEDIUM
    assert has_reason(result, ReasonCode.CITY_NOT_SET)
    assert not has_reason(result, ReasonCode.CITY_UNRECOGNISED)
    assert not has_reason(result, ReasonCode.CITY_OFF_TARGET)


def test_city_does_not_raise_a_low_lead():
    """Город — только понижающий модификатор: он не делает MEDIUM горячим.

    Негативный контроль в другую сторону: если бы правило стало повышать, «Dubai»
    в строке города вытаскивал бы наверх любую компанию.
    """
    company = lapsed_company(10, city="Dubai", address_lines=ADDR_OWN)
    assert score(company, TODAY).tier is Tier.MEDIUM


def test_inactive_entity_is_low():
    """Негативный контроль: неактивное юрлицо — LOW, даже если ячейка матрицы HIGH."""
    company = lapsed_company(10, address_lines=ADDR_REGISTRAR, entity_status=EntityStatus.INACTIVE)
    result = score(company, TODAY)
    assert result.tier is Tier.LOW
    assert has_reason(result, ReasonCode.ENTITY_INACTIVE)
    assert result.violations == ()


def test_non_ae_country_is_invalid_with_violation():
    """Негативный контроль: компания не из ОАЭ — INVALID и непустые violations."""
    company = lapsed_company(10, country="SA", address_lines=ADDR_REGISTRAR)
    result = score(company, TODAY)
    assert result.tier is Tier.INVALID
    assert result.violations == ("компания вне ОАЭ: SA",)


def test_invalid_wins_over_inactive_low():
    """INVALID не сворачивается в LOW: «не смогли оценить» ≠ «оценили низко»."""
    company = lapsed_company(10, country="SA", entity_status=EntityStatus.INACTIVE)
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


# --- статус регистрации: «повода нет» и «статуса не знаем» — разные исходы ---------
#
# Дефект: сравнение с двумя литералами («LAPSED», «ISSUED»), и любое третье слово
# реестра молча означало «повода для разговора нет». Перечисление статусов отдал сам
# API GLEIF (ИЗМЕРЕНО 2026-09-11: ISSUED, LAPSED, ANNULLED, PENDING_TRANSFER,
# PENDING_ARCHIVAL, DUPLICATE, RETIRED, MERGED; по ОАЭ 132 записи из 9369 — не ISSUED
# и не LAPSED). Ожидаемое ниже — литералы кодов, а не импорт списка из rubric.


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        # Оба статуса, по которым ось B считает повод: отдельной причины про статус нет.
        ("ISSUED", None),
        ("LAPSED", None),
        # Известные реестру статусы, по которым повода нет: решение названо вслух.
        ("RETIRED", ReasonCode.REGISTRATION_STATUS_NO_EVENT),
        ("DUPLICATE", ReasonCode.REGISTRATION_STATUS_NO_EVENT),
        ("ANNULLED", ReasonCode.REGISTRATION_STATUS_NO_EVENT),
        ("MERGED", ReasonCode.REGISTRATION_STATUS_NO_EVENT),
        ("PENDING_TRANSFER", ReasonCode.REGISTRATION_STATUS_NO_EVENT),
        ("PENDING_ARCHIVAL", ReasonCode.REGISTRATION_STATUS_NO_EVENT),
        # Слово не из перечня реестра: «не смогли определить», третий исход.
        ("SUSPENDED", ReasonCode.REGISTRATION_STATUS_UNKNOWN),
        ("ISSUED_AND_THEN_SOME", ReasonCode.REGISTRATION_STATUS_UNKNOWN),
        # Поля нет вовсе.
        ("", ReasonCode.REGISTRATION_STATUS_NOT_SET),
        ("   ", ReasonCode.REGISTRATION_STATUS_NOT_SET),
    ],
)
def test_registration_status_outcome_is_named_on_the_card(status, expected_code):
    company = make_company(
        registration_status=status,
        created_on=TODAY - timedelta(days=1000),
        next_renewal_on=TODAY + timedelta(days=300),
    )
    result = score(company, TODAY)
    about_status = [
        item.code for item in result.reason_items
        if item.code in (
            ReasonCode.REGISTRATION_STATUS_NO_EVENT,
            ReasonCode.REGISTRATION_STATUS_UNKNOWN,
            ReasonCode.REGISTRATION_STATUS_NOT_SET,
        )
    ]
    assert about_status == ([expected_code] if expected_code else [])


def test_unknown_registration_status_prints_the_word_the_registry_sent():
    """Слово реестра едет в причину: без него «не смогли» нечем проверить руками."""
    company = make_company(registration_status="SUSPENDED")
    result = score(company, TODAY)
    assert "SUSPENDED" in " ".join(result.reasons)


@pytest.mark.parametrize("status", ["lapsed", " LAPSED ", "Lapsed"])
def test_lapsed_status_is_recognised_whatever_the_case(status):
    """Регистр слова статуса — не новость о компании: повод обязан сработать."""
    company = make_company(
        registration_status=status,
        next_renewal_on=TODAY - timedelta(days=10),
        created_on=TODAY - timedelta(days=1000),
    )
    event, _ = classify_event(company, TODAY)
    assert event is Event.LAPSED


@pytest.mark.parametrize("status", ["issued", "ISSUED"])
def test_issued_status_is_recognised_whatever_the_case(status):
    company = make_company(
        registration_status=status,
        next_renewal_on=TODAY + timedelta(days=10),
        created_on=TODAY - timedelta(days=1000),
    )
    event, _ = classify_event(company, TODAY)
    assert event is Event.RENEWAL_SOON


def test_registration_status_reason_does_not_move_the_tier():
    """Причина про статус объясняет молчание оси B, а не наказывает за него.

    Та же компания с RETIRED и с выдуманным SUSPENDED обязана получить одну ступень:
    двигает ступень повод, а не то, знаем ли мы слово.
    """
    retired = make_company(registration_status="RETIRED", address_lines=ADDR_REGISTRAR)
    unknown = make_company(registration_status="SUSPENDED", address_lines=ADDR_REGISTRAR)
    assert score(retired, TODAY).tier is score(unknown, TODAY).tier


# --- статус юрлица: «неактивно» и «реестр промолчал» — разные исходы ---------------


def test_unknown_entity_status_is_capped_at_medium_not_dropped_to_low():
    """Слово NULL реестра — не приговор: ступень ограничена средней, а не LOW.

    Ячейка матрицы у этой компании HIGH (адрес регистратора плюс свежая просрочка);
    при INACTIVE она даёт LOW (тест выше), при UNKNOWN обязана дать MEDIUM.
    """
    company = lapsed_company(
        10, address_lines=ADDR_REGISTRAR, entity_status=EntityStatus.UNKNOWN,
        entity_status_raw="NULL",
    )
    result = score(company, TODAY)
    assert result.tier is Tier.MEDIUM
    assert has_reason(result, ReasonCode.ENTITY_STATUS_UNKNOWN)
    assert not has_reason(result, ReasonCode.ENTITY_INACTIVE)
    assert "NULL" in " ".join(result.reasons)


def test_missing_entity_status_has_its_own_reason():
    """Поля нет вовсе — отдельный код, а не слово-заглушка внутри score.py."""
    company = lapsed_company(
        10, address_lines=ADDR_REGISTRAR, entity_status=EntityStatus.UNKNOWN,
        entity_status_raw="",
    )
    result = score(company, TODAY)
    assert result.tier is Tier.MEDIUM
    assert has_reason(result, ReasonCode.ENTITY_STATUS_NOT_SET)
    assert not has_reason(result, ReasonCode.ENTITY_STATUS_UNKNOWN)


def test_unknown_entity_status_does_not_raise_a_low_card():
    """Ограничение работает в одну сторону: MEDIUM — потолок, а не пол.

    Компания без повода (ячейка LOW) с несообщённым статусом остаётся LOW.
    """
    company = make_company(
        entity_status=EntityStatus.UNKNOWN, entity_status_raw="NULL",
        address_lines=ADDR_OWN, registration_status="RETIRED",
    )
    result = score(company, TODAY)
    assert result.tier is Tier.LOW
    assert has_reason(result, ReasonCode.ENTITY_STATUS_UNKNOWN)


def test_active_entity_gets_no_status_reason_at_all():
    """Положительный контроль: у обычной активной компании ни одной причины про статус."""
    company = lapsed_company(10, address_lines=ADDR_REGISTRAR)
    result = score(company, TODAY)
    assert not has_reason(result, ReasonCode.ENTITY_INACTIVE)
    assert not has_reason(result, ReasonCode.ENTITY_STATUS_UNKNOWN)
    assert not has_reason(result, ReasonCode.ENTITY_STATUS_NOT_SET)
    assert result.tier is Tier.HIGH
