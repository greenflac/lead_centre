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

from leadcentre.engine.score import score_inbound
from leadcentre.models import Tier

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
def tiers() -> list[Tier]:
    run_eval = _load_eval_module()
    messages = run_eval.load_messages(SEED_CSV)
    return [score_inbound(m, run_eval.rules_facts(m)).tier for m in messages]


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
