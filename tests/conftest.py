"""Общие фикстуры тестов движка.

Правило файла: ожидаемые значения в тестах — литералы (Т2). Отсюда экспортируются
только *входные* данные; ни один порог и ни одна ячейка матрицы из `rubric` не
импортируются, иначе тест поедет вместе с кодом и промолчит.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from leadcentre.models import Company

# Опорная «сегодня» для всех тестов: фиксирована, чтобы прогон не зависел от календаря.
TODAY = date(2026, 9, 9)

# Входные адреса под каждую ось A. Литералы взяты из реальной выдачи GLEIF
# (data/gleif_ae_*_sample.json), а не из списков маркеров rubric.
ADDR_REGISTRAR = ("Meydan Grandstand, 6th floor, Meydan Road, Nad Al Sheba",)
ADDR_BUSINESS_CENTRE = ("Emirates Towers, Business Centre, Office 12",)
ADDR_OWN = ("Plot 12, Al Quoz Industrial Area 3",)
ADDR_EMPTY: tuple[str, ...] = ()


def make_company(**overrides) -> Company:
    """Компания по умолчанию: ОАЭ, Дубай, активна, без повода, свой офис."""
    base = {
        "source": "test",
        "external_id": "TESTLEI0000000000001",
        "name": "Test Trading LLC",
        "city": "Dubai",
        "country": "AE",
        "address_lines": ADDR_OWN,
        "registrar_id": "RA999999",
        "license_no": "LIC-1",
        "created_on": TODAY - timedelta(days=1000),
        "entity_active": True,
        "registration_status": "ISSUED",
        "next_renewal_on": TODAY + timedelta(days=300),
        "is_synthetic": True,
    }
    base.update(overrides)
    return Company(**base)


def lapsed_company(overdue_days: int, **overrides) -> Company:
    """Просрочка ровно на `overdue_days` дней относительно TODAY."""
    return make_company(
        registration_status="LAPSED",
        next_renewal_on=TODAY - timedelta(days=overdue_days),
        **overrides,
    )


def new_company(age_days: int, **overrides) -> Company:
    """Юрлицо возрастом ровно `age_days` дней; повода по продлению нет."""
    return make_company(
        created_on=TODAY - timedelta(days=age_days),
        registration_status="ISSUED",
        next_renewal_on=None,
        **overrides,
    )


def renewal_company(days_left: int, **overrides) -> Company:
    """Продление через `days_left` дней; юрлицо старое, чтобы не сработал B2."""
    return make_company(
        created_on=TODAY - timedelta(days=1000),
        registration_status="ISSUED",
        next_renewal_on=TODAY + timedelta(days=days_left),
        **overrides,
    )


@pytest.fixture
def today() -> date:
    return TODAY


# --- ось C: входящие обращения ---------------------------------------------------


def make_message(text: str = "Нужен офис и виза для новой компании", **overrides):
    """Обращение из канала SORP. Текст непустой: пустой — отдельный негативный контроль."""
    from leadcentre.models import InboundMessage

    base = {
        "external_id": "MSG-1",
        "channel": "jivo",
        "text": text,
        "received_at": TODAY,
        "is_synthetic": True,
    }
    base.update(overrides)
    return InboundMessage(**base)


def make_facts(**overrides):
    """Факты по умолчанию: один тип запроса, язык не целевой, уверенность высокая,
    цитата есть. Каждый модификатор оси C включается в тесте явно."""
    from leadcentre.models import LeadFacts, RequestType

    base = {
        "request_types": (RequestType.OFFICE,),
        "jurisdiction_hint": None,
        "headcount": None,
        "timeline_days": None,
        "budget_hint": None,
        "language": "en",
        "is_spam": False,
        "has_contact": True,
        "confidence": 0.9,
        "quotes": ("нужен офис",),
    }
    base.update(overrides)
    return LeadFacts(**base)


# --- причины: сверка по коду, а не по тексту -------------------------------------
# Причина стала данными (код плюс параметры), и тест обязан проверять сработавшее
# правило, а не формулировку: текст правится редактором, код — миграцией. Раньше здесь
# матчились подстроки («срок 10 дн.»), и переезд на каталог покрасил 15 тестов,
# ни одно правило при этом не сломав. Рендеринг проверяется отдельно — tests/test_reasons.py.


def reason_items(result) -> tuple:
    """Причины из `Score` или прямо из кортежа, который вернул `classify_event`.

    Два входа намеренно: `classify_event` отдаёт причины кортежем, `score` — внутри
    `Score`, и заводить два набора помощников ради этого не за что.
    """
    return tuple(getattr(result, "reason_items", result))


def reason_codes(result) -> list:
    """Коды сработавших причин в порядке, в котором их выдал движок."""
    return [item.code for item in reason_items(result)]


def find_reason(result, code):
    """Единственная причина с кодом `code`; нет её или их две — AssertionError.

    Две причины одним кодом — это дефект движка (правило сработало дважды), а не
    «хотя бы одна подошла», поэтому единственность проверяется здесь, а не в тесте.
    """
    found = [item for item in reason_items(result) if item.code is code]
    assert found, (
        f"нет причины {code.value}; сработали: "
        f"{[c.value for c in reason_codes(result)]}"
    )
    assert len(found) == 1, f"причина {code.value} сработала {len(found)} раз(а)"
    return found[0]


def reason_params(result, code) -> dict:
    """Параметры сработавшей причины: `{'days': 10, 'limit': 60}`."""
    return find_reason(result, code).values()


def has_reason(result, code) -> bool:
    """Сработала ли причина с таким кодом. Для негативных контролей."""
    return any(item.code is code for item in reason_items(result))
