"""Тест на СОСТАВ выдачи, а не на отдельные кейсы.

Дефект, ради которого тест написан, был найден глазами на дашборде (П3): каждый
отдельный лид оценивался правильно, а картина целиком была плохой — HIGH получали
30 обращений из 70. Приоритет, который выдаётся почти половине входящих, менеджер
перестаёт читать как приоритет. Ни один покейсовый тест этого поймать не мог.

Прибор: 70 обращений из `data/inbound_seed.csv` плюс детерминированные факты из
`eval/run_eval.py` в режиме rules — те же, что у стенда (Е1: правила извлечения
живут в одном месте). Сети и денег не требуется, модель не вызывается.

Границы — литералы (Т2), и обе стороны обязательны: верхняя ловит инфляцию HIGH,
нижняя — рубрику, которая перестала выдавать горячих вовсе (И5).
"""
from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path

import pytest

from leadcentre.engine.facts_rules import rules_facts
from leadcentre.engine.reasons import ReasonCode
from leadcentre.engine.score import score_inbound
from leadcentre.models import Tier
from tests.conftest import has_reason

ROOT = Path(__file__).resolve().parents[1]
SEED_CSV = ROOT / "data" / "inbound_seed.csv"
EVAL_SCRIPT = ROOT / "eval" / "run_eval.py"

# Доля HIGH, при которой шкала ещё что-то значит. ВЫБРАНО: верхняя граница — четверть
# набора (на 70 обращениях это 17), нижняя — десятая часть (7). ИЗМЕРЕНО 2026-09-09
# при SIGNALS_FOR_HIGH = 2: 12 HIGH / 41 MEDIUM / 17 LOW, то есть 17.1% горячих.
MAX_HIGH_SHARE = 0.25
MIN_HIGH_SHARE = 0.10


def _load_eval_module():
    """`eval/` — не пакет, поэтому модуль грузится по пути; в сеть он не ходит."""
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
    """Те же детерминированные факты, что у стенда: одна реализация на всех (Е1)."""
    return [rules_facts(m) for m in messages]


@pytest.fixture(scope="module")
def scores(messages, facts):
    return [score_inbound(m, f) for m, f in zip(messages, facts, strict=True)]


@pytest.fixture(scope="module")
def tiers(scores) -> list[Tier]:
    return [s.tier for s in scores]


def test_seed_holds_70_messages(tiers):
    """Негативный контроль прибора: на пустом или урезанном наборе доли ничего не значат."""
    assert len(tiers) == 70


def test_high_share_stays_below_a_quarter_of_the_set(tiers):
    """Верхняя граница: горячих не больше четверти набора — иначе HIGH не приоритет."""
    counts = Counter(tiers)
    high = counts[Tier.HIGH]
    assert high <= int(70 * 0.25), f"HIGH {high} из 70 — приоритет обесценен"
    assert high / 70 <= MAX_HIGH_SHARE


def test_high_share_stays_above_a_tenth_of_the_set(tiers):
    """Нижняя граница: рубрика, не выдающая горячих, тоже сломана — просто молча."""
    high = Counter(tiers)[Tier.HIGH]
    assert high >= int(70 * 0.10), f"HIGH {high} из 70 — горячие пропали"
    assert high / 70 >= MIN_HIGH_SHARE


def test_every_tier_is_represented(tiers):
    """Вырождение в одну ступень — тоже дефект состава, и числами оно видно (Е3)."""
    counts = Counter(tiers)
    assert counts[Tier.HIGH] > 0
    assert counts[Tier.MEDIUM] > 0
    assert counts[Tier.LOW] > 0
    assert sum(counts.values()) == 70


def test_medium_is_the_largest_bucket(tiers):
    """Форма распределения: середина шире краёв — очередь разбирается сверху вниз."""
    counts = Counter(tiers)
    assert counts[Tier.MEDIUM] > counts[Tier.HIGH]
    assert counts[Tier.MEDIUM] > counts[Tier.LOW]


def test_no_invalid_leads_in_the_seed(tiers):
    """INVALID на синтетическом наборе означал бы сломанный инвариант, а не лид (Р1)."""
    assert Counter(tiers)[Tier.INVALID] == 0


# --- не выродился ли признак срочности при пороге 60 -----------------------------
#
# ИЗМЕРЕНО 2026-09-09 на 70 обращениях: срок извлечён у 15, из них 8 укладываются в
# 60 дней (признак срабатывает) и 7 не укладываются (не срабатывает). При прежнем
# пороге 30 было 7 и 8. Признак продолжает делить набор, а не помечать всех подряд.


def test_timeline_signal_still_discriminates(facts):
    """Если бы признак срабатывал на КАЖДОМ извлечённом сроке, он означал бы

    «срок вообще упомянут» и перестал бы что-либо отбирать. Обе группы обязаны быть
    непустыми — это негативный контроль самого признака (И5).
    """
    known = [f.timeline_days for f in facts if f.timeline_days is not None]
    assert len(known) >= 10, f"сроков в наборе всего {len(known)} — мерить нечем"

    fires = [d for d in known if d <= 60]
    silent = [d for d in known if d > 60]
    assert fires, "признак срочности не срабатывает ни на одном обращении"
    assert silent, (
        f"признак срочности срабатывает на всех {len(known)} сроках — "
        "он выродился в «срок вообще упомянут»"
    )
    # обе группы заметные, а не «один случай для галочки»
    assert len(fires) >= 3
    assert len(silent) >= 3


def test_timeline_signal_is_not_the_only_road_to_high(messages, facts, tiers):
    """Горячие не сводятся к «есть срок»: HIGH выдаётся и без извлечённого срока."""
    high_without_timeline = [
        m.external_id
        for m, f, t in zip(messages, facts, tiers, strict=True)
        if t is Tier.HIGH and f.timeline_days is None
    ]
    assert high_without_timeline, "все HIGH держатся на одном признаке — шкала однобокая"


def test_urgency_fires_on_8_of_the_15_extracted_deadlines(scores, facts):
    """Сколько раз признак срочности реально сработал — числом, а не «работает» (Е3).

    Считается по коду причины в карточке, а не по порогу из `rubric` и не по началу
    строки: тест видит поведение, а не константу, и переживает правку формулировки.
    ИЗМЕРЕНО 2026-09-09 при пороге 60: 15 сроков извлечено, 8 сработали, 7 нет.
    """
    with_deadline = [f for f in facts if f.timeline_days is not None]
    fired = [s for s in scores if has_reason(s, ReasonCode.URGENT_TIMELINE)]
    assert len(with_deadline) == 15
    assert len(fired) == 8
    assert len(with_deadline) - len(fired) == 7
