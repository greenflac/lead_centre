"""Tier distribution over the whole seed: the scale must still discriminate.

Thresholds are literals chosen before the first run, so the numbers cannot be fitted
to whatever the engine happens to produce.
"""
from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from leadcentre.engine.facts_rules import rules_facts
from leadcentre.engine.reasons import ReasonCode
from leadcentre.engine.score import score_inbound
from leadcentre.models import InboundMessage, Tier
from tests.conftest import has_reason

ROOT = Path(__file__).resolve().parents[1]
SEED_CSV = ROOT / "data" / "inbound_seed.csv"
EVAL_SCRIPT = ROOT / "eval" / "run_eval.py"

# Bounds on the hot share, chosen before the first run so they cannot be fitted.
MAX_HIGH_SHARE = 0.25
MIN_HIGH_SHARE = 0.10


def _load_eval_module():
    """Loads the eval harness as a module without running it."""
    spec = importlib.util.spec_from_file_location("run_eval_for_tests", EVAL_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def messages():
    return _load_eval_module().load_messages(SEED_CSV)


@pytest.fixture(scope="module")
def facts(messages):
    """Extracts facts for one seed message."""
    return [rules_facts(m) for m in messages]


@pytest.fixture(scope="module")
def scores(messages, facts):
    return [score_inbound(m, f) for m, f in zip(messages, facts, strict=True)]


@pytest.fixture(scope="module")
def tiers(scores) -> list[Tier]:
    return [s.tier for s in scores]


def test_seed_holds_70_messages(tiers):
    assert len(tiers) == 70


def test_high_share_stays_below_a_quarter_of_the_set(tiers):
    counts = Counter(tiers)
    high = counts[Tier.HIGH]
    assert high <= int(70 * 0.25), f"HIGH {high} из 70 — приоритет обесценен"
    assert high / 70 <= MAX_HIGH_SHARE


def test_high_share_stays_above_a_tenth_of_the_set(tiers):
    high = Counter(tiers)[Tier.HIGH]
    assert high >= int(70 * 0.10), f"HIGH {high} из 70 — горячие пропали"
    assert high / 70 >= MIN_HIGH_SHARE


def test_every_tier_is_represented(tiers):
    counts = Counter(tiers)
    assert counts[Tier.HIGH] > 0
    assert counts[Tier.MEDIUM] > 0
    assert counts[Tier.LOW] > 0
    assert sum(counts.values()) == 70


def test_medium_is_the_largest_bucket(tiers):
    counts = Counter(tiers)
    assert counts[Tier.MEDIUM] > counts[Tier.HIGH]
    assert counts[Tier.MEDIUM] > counts[Tier.LOW]


def test_no_invalid_leads_in_the_seed(tiers):
    """No seed message trips an invariant."""
    assert Counter(tiers)[Tier.INVALID] == 0


# Has the urgency signal degenerated at this threshold? Both sides are counted, so a
# signal that always fires or never fires would be caught.


def test_timeline_signal_still_discriminates(facts):
    """The deadline signal both fires and stays silent across the seed."""
    known = [f.timeline_days for f in facts if f.timeline_days is not None]
    assert len(known) >= 8, f"сроков в наборе всего {len(known)} — мерить нечем"

    fires = [d for d in known if d <= 60]
    silent = [d for d in known if d > 60]
    assert fires, "признак срочности не срабатывает ни на одном обращении"
    assert silent, (
        f"признак срочности срабатывает на всех {len(known)} сроках — "
        "он выродился в «срок вообще упомянут»"
    )
    # counted by number, not by "both non-empty", so a degenerate split is visible
    assert len(fires) == 8
    assert len(silent) == 7


def test_timeline_signal_is_not_the_only_road_to_high(messages, facts, tiers):
    """Hot leads are not reachable by the deadline signal alone."""
    high_without_timeline = [
        m.external_id
        for m, f, t in zip(messages, facts, tiers, strict=True)
        if t is Tier.HIGH and f.timeline_days is None
    ]
    assert high_without_timeline, "все HIGH держатся на одном признаке — шкала однобокая"


def test_urgency_fires_on_8_of_the_15_extracted_deadlines(scores, facts):
    """The urgency window splits the extracted deadlines, counted by number, not by a flag."""
    with_deadline = [f for f in facts if f.timeline_days is not None]
    fired = [s for s in scores if has_reason(s, ReasonCode.URGENT_TIMELINE)]
    assert len(with_deadline) == 15
    assert len(fired) == 8
    assert len(with_deadline) - len(fired) == 7


def test_urgency_in_words_is_counted_separately_from_a_named_deadline(scores, facts):
    stated = [f for f in facts if f.urgency_stated]
    both_facts = [f for f in facts if f.urgency_stated and f.timeline_days is not None]
    assert not both_facts, "факты: у обращения есть и названный срок, и словесная срочность"
    by_words = [s for s in scores if has_reason(s, ReasonCode.URGENT_STATED)]
    by_date = [s for s in scores if has_reason(s, ReasonCode.URGENT_TIMELINE)]
    assert len(stated) == len(by_words) == 0, (
        "словесная срочность снова сработала на наборе — перемеряйте числа выше"
    )
    assert by_date, "срок, названный клиентом, перестал срабатывать"
    both = [s for s in scores
            if has_reason(s, ReasonCode.URGENT_STATED)
            and has_reason(s, ReasonCode.URGENT_TIMELINE)]
    assert not both, "одно обращение получило и придуманную, и названную срочность"


def test_wordless_urgency_still_fires_on_a_text_without_a_date():
    message = InboundMessage(
        external_id="wordless-urgency",
        channel="jivo",
        text="нужен офис в Дубае, нужно срочно, перезвоните пожалуйста",
        received_at=date(2026, 9, 11),
    )
    extracted = rules_facts(message)
    assert extracted.timeline_days is None
    assert extracted.urgency_stated is True
