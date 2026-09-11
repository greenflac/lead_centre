"""Which quote proves which reason, on both paths that render a card.

The defect this guards: the live backend returned no links at all, so a card showed
"no quote" under reasons whose evidence was printed one column to the left.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from fastapi.testclient import TestClient

from leadcentre import api
from leadcentre.api import app
from leadcentre.engine.evidence import link_reasons, reason_links_payload
from leadcentre.engine.facts_rules import rules_facts
from leadcentre.engine.score import score_inbound
from leadcentre.models import InboundMessage, facts_from_dict

DEFECT_TEXT = (
    "our licence expires in 21 days and we must move to a bigger unit at the same time. "
    "14 staff. urgent, who can call me today"
)
RECEIVED_ON = date(2026, 9, 11)


def _extraction_of(facts):
    """The extraction result shape, carrying facts the rules path produced."""
    return SimpleNamespace(
        facts=facts, model="rules", provider="offline", offline=True, elapsed_s=0.0,
        input_tokens=0, output_tokens=0, dropped_quotes=0, timeline_from_model=None,
        scrubbed=SimpleNamespace(summary=dict, text=DEFECT_TEXT),
    )


def _scored(text: str, received_on: date = RECEIVED_ON):
    message = InboundMessage(
        external_id="x", channel="whatsapp", text=text, received_at=received_on
    )
    facts = rules_facts(message)
    return message, facts, score_inbound(message, facts)


def test_each_reason_points_at_the_sentence_it_follows_from():
    """Live defect: these three reasons all rendered "no quote" on the dashboard card."""
    message, facts, result = _scored(DEFECT_TEXT)
    links = dict(link_reasons(result.reason_items, facts.quotes, facts, message.received_at))
    # Literals, not values read back out of the engine (T2).
    assert links["urgent_timeline"] == (0,)
    assert links["package_request"] == (0,)
    assert links["team_over_flexi_quota"] == (1,)


def test_a_reason_about_the_whole_request_is_linked_to_nothing():
    """Negative control: without it the test above would pass on "link everything"."""
    _, facts, result = _scored("нужно открыть компанию, бюджет 150 тысяч AED")
    links = dict(link_reasons(result.reason_items, facts.quotes, facts, RECEIVED_ON))
    assert any(quotes == () for quotes in links.values()), (
        f"every reason got a quote, so the link proves nothing: {links}"
    )


def test_a_budget_quote_must_name_the_same_amount_as_the_reason():
    """A sentence naming a different amount is not evidence for this budget.

    Accepting any money-looking quote once put a sentence about turnover under a reason
    naming a much smaller figure, so the card showed evidence contradicting its own claim.
    The office sentence below names 300 thousand while the budget reason names 150.
    """
    _, facts, result = _scored(
        "бюджет 150 тысяч AED на регистрацию компании. "
        "офис снимаем за 300 тысяч AED в год, это отдельные деньги"
    )
    links = dict(link_reasons(result.reason_items, facts.quotes, facts, RECEIVED_ON))
    assert facts.budget_hint == "150 тысяч AED"
    assert facts.quotes[0].startswith("офис снимаем за 300")
    assert 0 not in links["budget_named"]
    # Negative control: the rule must still accept the sentence that does name 150.
    assert 1 in links["budget_named"]


def test_no_quotes_means_no_links_rather_than_a_crash():
    """Third outcome: nothing to link is an empty list, never an invented index."""
    assert reason_links_payload((), (), rules_facts(
        InboundMessage(external_id="x", channel="form", text="hi", received_at=RECEIVED_ON)
    ), RECEIVED_ON) == []


def test_the_posted_lead_carries_one_link_per_reason(monkeypatch):
    """The API path: the card reads links by position, so the counts must match.

    Extraction is replaced by the deterministic rules path rather than a model call --
    a test must not reach the network (T4), and this is about the payload, not the model.
    """
    _, facts, _ = _scored(DEFECT_TEXT)
    monkeypatch.setattr(api, "extract_detailed", lambda _msg: _extraction_of(facts))
    client = TestClient(app)
    answer = client.post("/leads", json={"text": DEFECT_TEXT, "channel": "whatsapp"})
    assert answer.status_code == 200
    score = answer.json()["score"]
    assert len(score["reason_links"]) == len(score["reasons"])
    assert any(link["quotes"] for link in score["reason_links"])


def test_a_card_without_quotes_omits_the_field_instead_of_sending_an_empty_list(monkeypatch):
    """Third outcome: the dashboard renders a missing field and an empty one differently.

    An empty list means "every reason is unquoted" and leaves the card with no reasons at
    all; the absent key means "could not link" and lets the card fall back.
    """
    monkeypatch.setenv("OFFLINE", "1")
    client = TestClient(app)
    answer = client.post("/leads", json={"text": DEFECT_TEXT, "channel": "whatsapp"})
    score = answer.json()["score"]
    assert score["reasons"]
    assert "reason_links" not in score


def test_links_rebuilt_from_stored_facts_match_the_ones_built_live():
    """A stored card and a fresh one must not disagree about their own evidence."""
    message, facts, result = _scored(DEFECT_TEXT)
    live = link_reasons(result.reason_items, facts.quotes, facts, message.received_at)
    stored = facts_from_dict({
        "request_types": [t.value for t in facts.request_types],
        "headcount": facts.headcount,
        "timeline_days": facts.timeline_days,
        "urgency_stated": facts.urgency_stated,
        "budget_hint": facts.budget_hint,
        "language": facts.language,
        "is_spam": facts.is_spam,
        "has_contact": facts.has_contact,
        "confidence": facts.confidence,
        "quotes": list(facts.quotes),
    })
    assert link_reasons(result.reason_items, stored.quotes, stored, message.received_at) == live
