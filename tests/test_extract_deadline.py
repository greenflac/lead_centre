"""Срок, названный словами, считает код, а не модель (`engine/extract.py`).

Дефект, ради которого файл заведён, вскрыт координатором живым замером 2026-09-11
(claude-haiku-4-5, промпт extract_v5): на обращении urg-05 («до пятницы», получено
в субботу 2026-08-08) модель дважды подряд ответила `timeline_days: 4` вместо 6 —
при том, что ровно этот случай разобран в промпте примером с готовым ответом.
Арифметику конца месяца и конца недели модель делает верно, арифметику дней недели —
нет; промптом это не лечится. Код считает тот же срок детерминированно, поэтому число
модели им заменяется, а не сверяется с ним.

Ожидаемое — литералы: числа дней выписаны руками по календарю 2026 года, ни одно не
импортируется из проверяемого модуля. Сеть не нужна: `parse_facts` разбирает готовую
строку ответа.
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

# 2026: 8 августа — суббота, 9 сентября — среда, 31 августа — понедельник.
SATURDAY = date(2026, 8, 8)
WEDNESDAY = date(2026, 9, 9)
MONDAY_LAST_OF_AUGUST = date(2026, 8, 31)


def _answer(**overrides) -> str:
    """Минимальный валидный ответ модели: обязательные поля схемы на месте."""
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


# --- правило в чистом виде --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "received_at", "from_model", "expected"),
    [
        # Тот самый вход, на котором дефект наблюдался: суббота + «до пятницы».
        ("ответ нужен до пятницы", SATURDAY, 4, 6),
        # Код считает — число модели не спрашивают, даже если оно совпало.
        ("ответ нужен до пятницы", SATURDAY, 6, 6),
        ("ответ нужен до пятницы", SATURDAY, None, 6),
        # Края недели: пятница — сегодня, воскресенье — пять дней.
        ("до пятницы", date(2026, 8, 7), 0, 0),
        ("до пятницы", date(2026, 8, 9), 3, 5),
        # Конец месяца: 31-е — ноль, середина месяца — остаток месяца.
        ("лицензия нужна до конца месяца", MONDAY_LAST_OF_AUGUST, 1, 0),
        ("лицензия нужна в этом месяце", WEDNESDAY, 30, 21),
        # Код НЕ считает: число модели остаётся — эти формулировки за ней.
        ("нужно до конца октября", date(2026, 7, 21), 72, 72),
        ("нужно к 15 ноября", WEDNESDAY, 67, 67),
        # Не назвал никто — None, а не ноль.
        ("нужен офис на 8 человек", WEDNESDAY, None, None),
    ],
)
def test_code_wins_where_it_can_count_and_only_there(text, received_at, from_model, expected):
    assert coded_deadline_wins(text, received_at, from_model) == expected


# --- то же сквозь разбор ответа модели --------------------------------------------


def test_parse_facts_replaces_the_models_weekday_arithmetic():
    """Сквозь `parse_facts`: в фактах срок кода, а слово модели осталось приборным полем."""
    text = "нужен офис в Дубае, ответ нужен до пятницы"
    scrubbed = scrub_pii(text)
    facts, _dropped, from_model = parse_facts(_answer(timeline_days=4), scrubbed, SATURDAY)
    assert facts.timeline_days == 6
    assert from_model == 4


def test_parse_facts_keeps_the_models_number_where_the_code_counts_nothing():
    """Негативный контроль: без словесного маркера правило молчит и число модели живёт."""
    text = "нужен офис, срок - через 3 недели"
    scrubbed = scrub_pii(text)
    facts, _dropped, from_model = parse_facts(_answer(timeline_days=21), scrubbed, WEDNESDAY)
    assert facts.timeline_days == 21
    assert from_model == 21


def test_deadline_found_by_code_silences_wordless_urgency_in_the_llm_path():
    """Признаки не расходятся между путями: найденный код-сроком день гасит «срочно».

    Модель здесь срока не назвала вовсе, а текст его содержит: если бы замена стояла
    после расчёта `urgency_stated`, карточка получила бы и срок, и «даты клиент не назвал».
    """
    text = "срочно, ответ нужен до пятницы"
    scrubbed = scrub_pii(text)
    facts, _dropped, from_model = parse_facts(_answer(timeline_days=None), scrubbed, SATURDAY)
    assert facts.timeline_days == 6
    assert facts.urgency_stated is False
    assert from_model is None


def test_the_two_extraction_modes_agree_on_the_defect_input():
    """Оба пути на одном тексте дают одно число — ради этого правило и заведено."""
    from leadcentre.engine.facts_rules import rules_facts
    from leadcentre.models import InboundMessage

    text = "нужен офис в Дубае, ответ нужен до пятницы"
    message = InboundMessage(
        external_id="urg-05-like", channel="jivo", text=text, received_at=SATURDAY
    )
    by_rules = rules_facts(message).timeline_days
    by_llm = parse_facts(_answer(timeline_days=4), scrub_pii(text), SATURDAY)[0].timeline_days
    assert by_rules == by_llm == 6


# --- версия промпта: имя и файл ---------------------------------------------------


def test_prompt_version_points_at_a_file_that_exists():
    """Висящий артефакт ловится здесь: имя версии и файл обязаны существовать вместе."""
    assert PROMPT_VERSION == "extract_v5"
    assert PROMPT_PATH == Path(__file__).resolve().parents[1] / "prompts" / "extract_v5.md"
    assert PROMPT_PATH.is_file()
    assert PROMPT_PATH.read_text(encoding="utf-8").strip()
