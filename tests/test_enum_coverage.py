"""Completeness against enumerations: every member must be served, not only the ones we thought of.

Expected members are listed as literals, so a changed enumeration reddens the test
instead of moving along with it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from leadcentre.engine import reply
from leadcentre.engine.facts_rules import TYPE_MARKERS
from leadcentre.engine.rubric import MATRIX
from leadcentre.models import (
    AddressType,
    Event,
    InboundMessage,
    LeadFacts,
    RequestType,
    Tier,
)

ROOT = Path(__file__).resolve().parents[1]
PRICELIST = ROOT / "data" / "pricelist_demo.yaml"

# Literals: the module's list may change, and this test must notice rather than follow.
TEMPLATE_LANGUAGES = ("ru", "en")

# Outcomes declared by the reply module; raising is not one of them.
DECLARED_OUTCOMES = ("draft", "questions", "spam_skipped", "no_draft_needs_human")


# Text is chosen to match the language, or this would test language detection instead.
MESSAGE_TEXT = {
    "ru": "нужно продлить лицензию, 8 человек, бюджет 40000 AED",
    "en": "we need to renew the licence, 8 people, budget 40000 AED",
}


def _message(text: str = MESSAGE_TEXT["ru"]):
    from datetime import date

    return InboundMessage(
        external_id="cov", channel="form", text=text, received_at=date(2026, 9, 9)
    )


def _rich_facts(kind: RequestType, language: str) -> LeadFacts:
    """Builds facts rich enough for a draft of any request type."""
    return LeadFacts(
        request_types=(kind, RequestType.OFFICE) if kind is not RequestType.OFFICE else (kind,),
        headcount=8,
        timeline_days=10,
        budget_hint="40000 AED",
        language=language,
        has_contact=True,
        confidence=0.9,
        quotes=("продлить лицензию", "8 человек"),
    )




@pytest.mark.parametrize("kind", list(RequestType), ids=lambda k: k.value)
@pytest.mark.parametrize("language", TEMPLATE_LANGUAGES)
def test_draft_survives_every_request_type(kind, language):
    """Drafting works for every member of the request-type enumeration."""
    message = _message(MESSAGE_TEXT[language])
    result = reply.draft(message, _rich_facts(kind, language), Tier.HIGH)
    assert result.outcome in DECLARED_OUTCOMES
    assert result.language == language


@pytest.mark.parametrize("kind", list(RequestType), ids=lambda k: k.value)
def test_draft_survives_a_lonely_request_type(kind):
    """Drafting works when a request carries exactly one type."""
    facts = LeadFacts(
        request_types=(kind,),
        headcount=8,
        timeline_days=10,
        budget_hint="40000 AED",
        language="ru",
        has_contact=True,
        confidence=0.9,
        quotes=("продлить лицензию",),
    )
    assert reply.draft(_message(), facts, Tier.HIGH).outcome in DECLARED_OUTCOMES


@pytest.mark.parametrize("kind", list(RequestType), ids=lambda k: k.value)
def test_reply_dictionaries_cover_every_request_type(kind):
    assert kind in reply.SUBSTANCE, f"нет шаблона для {kind.value}"
    assert kind in reply.PRICE_KEY_BY_REQUEST, f"нет прайс-ключей для {kind.value}"


@pytest.mark.parametrize("kind", list(RequestType), ids=lambda k: k.value)
@pytest.mark.parametrize("language", TEMPLATE_LANGUAGES)
def test_substance_text_exists_for_every_language(kind, language):
    assert reply.SUBSTANCE[kind].get(language), f"нет текста {language} для {kind.value}"




def _pricelist_keys() -> set[str]:
    """Returns the price keys declared in the reply module."""
    text = PRICELIST.read_text(encoding="utf-8")
    items = text.split("items:", 1)[1]
    return set(re.findall(r"^  ([a-z0-9_]+):", items, flags=re.MULTILINE))


def test_pricelist_has_an_item_for_every_declared_price_key():
    declared = {key for keys in reply.PRICE_KEY_BY_REQUEST.values() for key in keys}
    missing = sorted(declared - _pricelist_keys())
    assert missing == [], f"ключи без позиции в прайсе: {missing}"
    assert len(declared) >= 5, "объявленных ключей подозрительно мало — проверять нечего"


def test_loaded_prices_match_the_declared_keys():
    prices = reply.load_prices(PRICELIST)
    declared = {key for keys in reply.PRICE_KEY_BY_REQUEST.values() for key in keys}
    assert declared <= set(prices), sorted(declared - set(prices))


@pytest.mark.parametrize("kind", list(RequestType), ids=lambda k: k.value)
def test_types_with_price_keys_can_be_priced(kind):
    """Every type with a price key can actually be priced."""
    prices = reply.load_prices(PRICELIST)
    for key in reply.PRICE_KEY_BY_REQUEST[kind]:
        assert key in prices, f"{kind.value}: ключ {key} отсутствует в прайсе"


# Negative control: a type with no template at all.


def test_missing_template_is_a_declared_outcome_not_an_exception(monkeypatch):
    substance = dict(reply.SUBSTANCE)
    price_keys = dict(reply.PRICE_KEY_BY_REQUEST)
    substance.pop(RequestType.OFFICE)
    price_keys.pop(RequestType.OFFICE)
    monkeypatch.setattr(reply, "SUBSTANCE", substance)
    monkeypatch.setattr(reply, "PRICE_KEY_BY_REQUEST", price_keys)

    result = reply.draft(_message(), _rich_facts(RequestType.OFFICE, "ru"), Tier.HIGH)
    # the declared outcome: a human takes the card, not a KeyError or an empty draft
    assert result.outcome == "no_draft_needs_human"
    assert result.needs_human is True
    assert result.body == ""


def test_unknown_request_type_value_does_not_crash(monkeypatch):

    class FakeType(str):
        value = "quantum_consulting"

    facts = LeadFacts(
        request_types=(FakeType("quantum_consulting"),),
        headcount=8,
        language="ru",
        has_contact=True,
        confidence=0.9,
        quotes=("тест",),
    )
    result = reply.draft(_message(), facts, Tier.HIGH)
    assert result.outcome == "no_draft_needs_human"
    assert result.needs_human is True


def test_unknown_type_alongside_a_known_one_is_skipped_with_a_notice(monkeypatch):
    substance = dict(reply.SUBSTANCE)
    substance.pop(RequestType.VISA)
    monkeypatch.setattr(reply, "SUBSTANCE", substance)

    facts = LeadFacts(
        request_types=(RequestType.OFFICE, RequestType.VISA),
        headcount=8,
        timeline_days=10,
        budget_hint="40000 AED",
        language="ru",
        has_contact=True,
        confidence=0.9,
        quotes=("8 человек",),
    )
    result = reply.draft(_message(), facts, Tier.HIGH)
    assert result.outcome == "draft"
    assert result.body  # the draft was built from the known type
    assert "visa" in result.notice, result.notice


# Completeness of the remaining tables against their enumerations.


def test_matrix_covers_every_address_and_event_combination():
    combinations = {(a, e) for a in AddressType for e in Event}
    assert set(MATRIX) == combinations
    assert len(MATRIX) == 16


def test_type_markers_cover_every_request_type_except_other():
    covered = set(TYPE_MARKERS)
    expected = set(RequestType) - {RequestType.OTHER}
    assert covered == expected, {
        "нет маркеров": sorted(k.value for k in expected - covered),
        "лишние": sorted(k.value for k in covered - expected),
    }




GEN_MOCK = ROOT / "web" / "scripts" / "gen_mock.py"


def test_mock_generator_uses_the_shared_facts_rules():
    source = GEN_MOCK.read_text(encoding="utf-8")
    assert "from leadcentre.engine.facts_rules import rules_facts" in source
    assert "rules_facts(" in source


def test_mock_generator_keeps_no_private_copy_of_the_rules():
    source = GEN_MOCK.read_text(encoding="utf-8")
    assert "rules_facts" in source, "генератор перестал звать общую реализацию правил"
    for forked in ("REQUEST_MARKERS", "TYPE_MARKERS", "BUDGET_MARKERS", "SPAM_MARKERS"):
        assert forked not in source, f"в генераторе снова заведён свой словарь {forked}"
