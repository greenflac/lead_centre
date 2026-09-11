"""Which quote proves which reason.

A reason is only as good as the sentence behind it, and the link is not written by hand:
every quote is run back through the same rules extractor that produced the facts, and it
counts as evidence for a reason only when the same fact follows from that quote alone.

A reason that cannot have a quote -- one about the request as a whole, such as the
language it was written in -- gets an empty list. "Not quotable" is its own outcome and
is never rendered as "no quote was found".
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from leadcentre.engine.reasons import ReasonCode
from leadcentre.models import InboundMessage, LeadFacts


def _facts_of(fragment: str, received_on: date) -> LeadFacts:
    """Extracts facts from one quote alone, so that the link is derived, not asserted."""
    from leadcentre.engine.facts_rules import rules_facts

    return rules_facts(InboundMessage(
        external_id="quote", channel="form", text=fragment, received_at=received_on,
    ))


def _tests(facts: LeadFacts) -> dict[ReasonCode, Callable[[LeadFacts], bool]]:
    """Per reason code, what a quote must itself yield to count as its evidence."""
    return {
        ReasonCode.URGENT_TIMELINE: lambda f: f.timeline_days == facts.timeline_days,
        ReasonCode.URGENT_STATED: lambda f: f.urgency_stated,
        ReasonCode.PACKAGE_REQUEST: lambda f: bool(f.request_types),
        ReasonCode.TEAM_OVER_FLEXI_QUOTA: lambda f: f.headcount == facts.headcount,
        # A quote proves a budget only when the same amount follows from it. Accepting any
        # money-looking quote once put a sentence about 4m AED turnover under a reason
        # naming 150k -- the evidence stated a different number than the reason.
        ReasonCode.BUDGET_NAMED: lambda f: f.budget_hint == facts.budget_hint,
        ReasonCode.SPAM_OR_OFF_TOPIC: lambda f: f.is_spam,
    }


def link_reasons(
    reason_items: tuple[Any, ...],
    quotes: tuple[str, ...] | list[str],
    facts: LeadFacts,
    received_on: date,
) -> tuple[tuple[str, tuple[int, ...]], ...]:
    """Returns, per reason, its code and the indices of the quotes it follows from.

    Matched on the reason code, not its wording: the wording lives in engine/reasons.py
    and gets rewritten, the code is the contract.
    """
    per_quote = [_facts_of(quote, received_on) for quote in quotes]
    tests = _tests(facts)
    linked: list[tuple[str, tuple[int, ...]]] = []
    for item in reason_items:
        test = tests.get(item.code)
        chosen = tuple(i for i, f in enumerate(per_quote) if test(f)) if test else ()
        linked.append((item.code.value, chosen))
    return tuple(linked)


def reason_links_payload(
    reason_items: tuple[Any, ...],
    quotes: tuple[str, ...] | list[str],
    facts: LeadFacts,
    received_on: date,
) -> list[dict[str, Any]]:
    """The same links shaped for JSON, which is what the dashboard card reads."""
    return [
        {"code": code, "quotes": list(indices)}
        for code, indices in link_reasons(reason_items, quotes, facts, received_on)
    ]
