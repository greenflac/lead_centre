"""Reply language is decided by the script of the request, with the flag as fallback."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from leadcentre.engine.reply import detect_script_language, resolve_language
from leadcentre.models import InboundMessage, LeadFacts

SEED_CSV = Path(__file__).resolve().parents[1] / "data" / "inbound_seed.csv"

RU = "нам нужен офис в Дубае на восемь человек и визы на всех сотрудников"
AR = "نحتاج مكتبًا في دبي لفريق من ستة أشخاص مع تأشيرات عمل ونود معرفة التكلفة"
EN = "we need an office in Dubai for eight people and employment visas for the team"


def _facts(language: str | None) -> LeadFacts:
    return LeadFacts(request_types=(), language=language)


def _message(text: str) -> InboundMessage:
    return InboundMessage(
        external_id="probe", channel="form", text=text, received_at=date(2026, 9, 9)
    )


def _seed_text(external_id: str) -> str:
    with SEED_CSV.open(encoding="utf-8", newline="") as fh:
        return next(r["text"] for r in csv.DictReader(fh) if r["external_id"] == external_id)




@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (RU, "ru"),
        (AR, "ar"),
        (EN, None),          # Latin proves no language: the third outcome
        ("", None),          # nothing to measure
        ("+971 55 000 0000", None),
    ],
)
def test_script_names_the_language_only_when_it_dominates(text, expected):
    assert detect_script_language(text) == expected


def test_one_foreign_phrase_does_not_change_the_language():
    text = (RU + ". ") * 8 + "سنكون في دبي في نهاية أكتوبر"
    assert detect_script_language(text) == "ru"


def test_seed_edge_03_is_russian_not_arabic():
    """A seed request with a few Arabic words stays in its dominant language."""
    assert detect_script_language(_seed_text("edge-03")) == "ru"


def test_seed_arabic_request_stays_arabic():
    assert detect_script_language(_seed_text("gen-19")) == "ar"




def test_half_and_half_is_the_third_outcome():
    text = "офис в Дубае " + "مكتب في دبي"
    assert detect_script_language(text) is None


@pytest.mark.parametrize(
    ("arabic_letters", "russian_letters", "expected"),
    [
        # both edges and the middle of the script share
        (100, 0, "ar"),      # 1.00
        (85, 15, "ar"),      # above the threshold
        (80, 20, "ar"),      # exactly at the threshold: still named
        (79, 21, None),      # just below: no longer
        (50, 50, None),      # evenly matched
        (20, 80, "ru"),      # mirrored
    ],
)
def test_the_share_threshold_is_0_8(arabic_letters, russian_letters, expected):
    """The dominance threshold is checked at both edges and in the middle."""
    text = "ا" * arabic_letters + "я" * russian_letters
    assert detect_script_language(text) == expected




def test_script_beats_the_flag_when_they_disagree():
    assert resolve_language(_message(RU), _facts("ar")) == "ru"


def test_flag_decides_when_the_script_is_silent():
    assert resolve_language(_message(EN), _facts("en")) == "en"


@pytest.mark.parametrize("flag", ["mixed", "", None, "de"])
def test_unknown_flag_falls_back_to_english(flag):
    assert resolve_language(_message(EN), _facts(flag)) == "en"
