"""Shared fixtures: companies per axis, inbound messages and reason helpers."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from leadcentre.models import Company, EntityStatus

# A fixed "today" for every test, so runs do not depend on the calendar.
TODAY = date(2026, 9, 9)

# Input addresses per axis A. Literals taken from live registry output,
# not from the rubric marker lists.
ADDR_REGISTRAR = ("Meydan Grandstand, 6th floor, Meydan Road, Nad Al Sheba",)
ADDR_BUSINESS_CENTRE = ("Emirates Towers, Business Centre, Office 12",)
ADDR_OWN = ("Plot 12, Al Quoz Industrial Area 3",)
ADDR_EMPTY: tuple[str, ...] = ()


def make_company(**overrides) -> Company:
    """Builds a company, with every field overridable."""
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
        "entity_status": EntityStatus.ACTIVE,
        "registration_status": "ISSUED",
        "next_renewal_on": TODAY + timedelta(days=300),
        "is_synthetic": True,
    }
    base.update(overrides)
    return Company(**base)


def lapsed_company(overdue_days: int, **overrides) -> Company:
    """A company whose registration lapsed recently."""
    return make_company(
        registration_status="LAPSED",
        next_renewal_on=TODAY - timedelta(days=overdue_days),
        **overrides,
    )


def new_company(age_days: int, **overrides) -> Company:
    """A recently created company."""
    return make_company(
        created_on=TODAY - timedelta(days=age_days),
        registration_status="ISSUED",
        next_renewal_on=None,
        **overrides,
    )


def renewal_company(days_left: int, **overrides) -> Company:
    """A company whose renewal is approaching."""
    return make_company(
        created_on=TODAY - timedelta(days=1000),
        registration_status="ISSUED",
        next_renewal_on=TODAY + timedelta(days=days_left),
        **overrides,
    )


@pytest.fixture
def today() -> date:
    return TODAY


# Axis C: inbound requests.


def make_message(text: str = "Нужен офис и виза для новой компании", **overrides):
    """Builds an inbound request; the text is non-empty, as empty is its own control."""
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
    """Builds LeadFacts with the given fields set."""
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
        "confidence_measured": True,
        "quotes": ("нужен офис",),
    }
    base.update(overrides)
    return LeadFacts(**base)


# Reasons are matched by code, never by wording.
# A reason is data (code plus parameters), so a test checks the rule that fired,
# not the phrasing: wording is edited, a code is migrated. Rendering itself is
# covered separately in tests/test_reasons.py.



def reason_items(result) -> tuple:
    """Returns the structural reasons of a score."""
    return tuple(getattr(result, "reason_items", result))


def reason_codes(result) -> list:
    """Returns the reason codes of a score."""
    return [item.code for item in reason_items(result)]


def find_reason(result, code):
    """Returns the reason with the given code, or None."""
    found = [item for item in reason_items(result) if item.code is code]
    assert found, (
        f"нет причины {code.value}; сработали: "
        f"{[c.value for c in reason_codes(result)]}"
    )
    assert len(found) == 1, f"причина {code.value} сработала {len(found)} раз(а)"
    return found[0]


def reason_params(result, code) -> dict:
    """Returns the parameters of the reason with the given code."""
    return find_reason(result, code).values()


def has_reason(result, code) -> bool:
    """Reports whether a score carries the given reason code."""
    return any(item.code is code for item in reason_items(result))
