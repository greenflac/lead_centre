"""Deadlines worded in the text are counted by code, not by the model.

Expected day counts are literals worked out by calendar; nothing is imported from the
module under test.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from leadcentre.engine.extract import (
    PROMPT_PATH,
    PROMPT_VERSION,
    coded_deadline_wins,
    parse_facts,
    scrub_pii,
)

# Weekday anchors for the dates used below.
SATURDAY = date(2026, 8, 8)
WEDNESDAY = date(2026, 9, 9)
MONDAY_LAST_OF_AUGUST = date(2026, 8, 31)


def _answer(**overrides) -> str:
    """Builds a model answer with every required schema field present."""
    payload = {
        "request_types": ["office"],
        "jurisdiction_hint": None,
        "headcount": None,
        "timeline_days": None,
        "budget_hint": None,
        "language": "ru",
        "is_spam": False,
        "has_contact": False,
        "confidence": 0.8,
        "quotes": [],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


# The rule on its own.


@pytest.mark.parametrize(
    ("text", "received_at", "from_model", "expected"),
    [
        # the exact input the defect was observed on
        ("ответ нужен до пятницы", SATURDAY, 4, 6),
        # code counts; the model number is not consulted even when it agrees
        ("ответ нужен до пятницы", SATURDAY, 6, 6),
        ("ответ нужен до пятницы", SATURDAY, None, 6),
        # week edges
        ("до пятницы", date(2026, 8, 7), 0, 0),
        ("до пятницы", date(2026, 8, 9), 3, 5),
        # month end and month middle
        ("лицензия нужна до конца месяца", MONDAY_LAST_OF_AUGUST, 1, 0),
        ("лицензия нужна в этом месяце", WEDNESDAY, 30, 21),
        # code counts nothing here, so the model number stands
        ("нужно до конца октября", date(2026, 7, 21), 72, 72),
        ("нужно к 15 ноября", WEDNESDAY, 67, 67),
        # nobody named one: None, not zero
        ("нужен офис на 8 человек", WEDNESDAY, None, None),
    ],
)
def test_code_wins_where_it_can_count_and_only_there(text, received_at, from_model, expected):
    assert coded_deadline_wins(text, received_at, from_model) == expected


# The same through parsing a model answer.


def test_parse_facts_replaces_the_models_weekday_arithmetic():
    """Parsing replaces the model's weekday arithmetic with the code's."""
    text = "нужен офис в Дубае, ответ нужен до пятницы"
    scrubbed = scrub_pii(text)
    facts, _dropped, from_model = parse_facts(_answer(timeline_days=4), scrubbed, SATURDAY)
    assert facts.timeline_days == 6
    assert from_model == 4


def test_parse_facts_keeps_the_models_number_where_the_code_counts_nothing():
    """Where code computes nothing, the model number stands."""
    text = "нужен офис, срок - через 3 недели"
    scrubbed = scrub_pii(text)
    facts, _dropped, from_model = parse_facts(_answer(timeline_days=21), scrubbed, WEDNESDAY)
    assert facts.timeline_days == 21
    assert from_model == 21


def test_deadline_found_by_code_silences_wordless_urgency_in_the_llm_path():
    text = "срочно, ответ нужен до пятницы"
    scrubbed = scrub_pii(text)
    facts, _dropped, from_model = parse_facts(_answer(timeline_days=None), scrubbed, SATURDAY)
    assert facts.timeline_days == 6
    assert facts.urgency_stated is False
    assert from_model is None


def test_the_two_extraction_modes_agree_on_the_defect_input():
    from leadcentre.engine.facts_rules import rules_facts
    from leadcentre.models import InboundMessage

    text = "нужен офис в Дубае, ответ нужен до пятницы"
    message = InboundMessage(
        external_id="urg-05-like", channel="jivo", text=text, received_at=SATURDAY
    )
    by_rules = rules_facts(message).timeline_days
    by_llm = parse_facts(_answer(timeline_days=4), scrub_pii(text), SATURDAY)[0].timeline_days
    assert by_rules == by_llm == 6


# Prompt version: the name and the file.


def test_prompt_version_points_at_a_file_that_exists():
    assert PROMPT_VERSION == "extract_v5"
    assert PROMPT_PATH == Path(__file__).resolve().parents[1] / "prompts" / "extract_v5.md"
    assert PROMPT_PATH.is_file()
    assert PROMPT_PATH.read_text(encoding="utf-8").strip()
