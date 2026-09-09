"""Тесты оси C — скоринг входящих обращений (`score_inbound`).

Ожидаемое — литералы (Т2): пороги записаны числами и строками (30 дней, 2 типа,
0.5 уверенности, язык "ru"), ступени — членами `Tier`. Из `rubric` не импортируется
ничего: ни `URGENT_TIMELINE_DAYS`, ни `TIER_LADDER`, ни базовая ступень.
Края и середина по каждому порогу (Т3), негативные контроли на спам и пустой текст (И5).
"""
from __future__ import annotations

import pytest

from leadcentre.engine.score import score_inbound
from leadcentre.models import AddressType, Event, RequestType, Tier
from tests.conftest import make_facts, make_message

# --- база: с типом запроса и без ------------------------------------------------


def test_base_tier_with_one_request_type_is_medium():
    """База MEDIUM: человек написал сам и хотя бы один запрос из текста извлечён."""
    result = score_inbound(make_message(), make_facts())
    assert result.tier is Tier.MEDIUM
    assert result.reasons == ()
    assert result.violations == ()
    # ось C не про компанию: оси A и B остаются пустыми
    assert result.address_type is AddressType.UNKNOWN
    assert result.event is Event.NONE


def test_base_tier_without_request_types_is_low():
    result = score_inbound(make_message(), make_facts(request_types=()))
    assert result.tier is Tier.LOW
    assert "из текста не извлечён ни один тип запроса" in result.reasons


def test_no_request_types_with_one_bump_reaches_only_medium():
    """Понижение базы наблюдаемо: с одним повышением обращение без запроса — MEDIUM, не HIGH."""
    result = score_inbound(make_message(), make_facts(request_types=(), language="ru"))
    assert result.tier is Tier.MEDIUM


# --- порог срочности: 30 дней ----------------------------------------------------


@pytest.mark.parametrize(
    ("timeline_days", "expected"),
    [
        (0, Tier.HIGH),    # «нужно вчера»
        (1, Tier.HIGH),
        (15, Tier.HIGH),   # середина
        (29, Tier.HIGH),
        (30, Tier.HIGH),   # ровно порог URGENT_TIMELINE_DAYS — ещё срочно
        (31, Tier.MEDIUM),  # на день дальше — повышения нет
        (90, Tier.MEDIUM),
        (None, Tier.MEDIUM),  # срок не извлечён
    ],
)
def test_urgent_timeline_threshold_is_30_days(timeline_days, expected):
    result = score_inbound(make_message(), make_facts(timeline_days=timeline_days))
    assert result.tier is expected
    assert result.violations == ()


def test_urgent_timeline_reason_names_the_number():
    result = score_inbound(make_message(), make_facts(timeline_days=10))
    assert any("срок 10 дн." in r for r in result.reasons)


# --- порог пакета: 2 типа запроса ------------------------------------------------


@pytest.mark.parametrize(
    ("request_types", "expected"),
    [
        ((), Tier.LOW),
        ((RequestType.OFFICE,), Tier.MEDIUM),  # один запрос — повышения нет
        ((RequestType.OFFICE, RequestType.VISA), Tier.HIGH),  # ровно порог: два
        ((RequestType.OFFICE, RequestType.VISA, RequestType.BANK), Tier.HIGH),
    ],
)
def test_package_threshold_is_2_request_types(request_types, expected):
    result = score_inbound(make_message(), make_facts(request_types=request_types))
    assert result.tier is expected


def test_package_reason_names_the_count():
    facts = make_facts(request_types=(RequestType.OFFICE, RequestType.VISA))
    assert any("запрошено услуг: 2" in r for r in score_inbound(make_message(), facts).reasons)


# --- язык обращения --------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("ru", Tier.HIGH),   # целевой язык
        ("en", Tier.MEDIUM),
        ("ar", Tier.MEDIUM),
        ("mixed", Tier.MEDIUM),
        ("RU", Tier.MEDIUM),  # сравнение точное, регистр не нормализуется
    ],
)
def test_only_russian_raises_the_tier(language, expected):
    result = score_inbound(make_message(), make_facts(language=language))
    assert result.tier is expected


# --- порог уверенности: 0.5 ------------------------------------------------------


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (0.0, Tier.MEDIUM),   # край: ничего не поняли — выше среднего не пускаем
        (0.49, Tier.MEDIUM),
        (0.5, Tier.HIGH),     # ровно порог LOW_CONFIDENCE — уже доверяем
        (0.51, Tier.HIGH),
        (0.75, Tier.HIGH),    # середина верхней половины
        (1.0, Tier.HIGH),
    ],
)
def test_low_confidence_caps_the_tier_at_medium(confidence, expected):
    """Повышение по языку есть всегда; решает только уверенность."""
    facts = make_facts(language="ru", confidence=confidence)
    result = score_inbound(make_message(), facts)
    assert result.tier is expected


def test_low_confidence_does_not_lower_below_the_base():
    """Понижение — это потолок MEDIUM, а не шаг вниз: LOW не становится ниже, MEDIUM остаётся."""
    medium = score_inbound(make_message(), make_facts(confidence=0.1))
    assert medium.tier is Tier.MEDIUM
    low = score_inbound(make_message(), make_facts(request_types=(), confidence=0.1))
    assert low.tier is Tier.LOW


def test_low_confidence_reason_prints_the_number():
    result = score_inbound(make_message(), make_facts(confidence=0.42))
    assert any("уверенность извлечения 0.42" in r for r in result.reasons)


# --- потолок лестницы ------------------------------------------------------------


def test_three_bumps_do_not_go_above_high():
    """Негативный контроль лестницы: три повышения подряд упираются в HIGH."""
    facts = make_facts(
        request_types=(RequestType.OFFICE, RequestType.VISA, RequestType.SETUP),
        timeline_days=7,
        language="ru",
        confidence=0.95,
        quotes=("нужен офис и визы", "запускаемся через неделю"),
    )
    result = score_inbound(make_message(), facts)
    assert result.tier is Tier.HIGH
    assert result.violations == ()
    assert len(result.reasons) == 3  # все три повышения названы поимённо


def test_two_bumps_from_low_base_reach_high():
    """Лестница считается от базы: без типов запроса два повышения дают ровно HIGH."""
    facts = make_facts(request_types=(), timeline_days=5, language="ru")
    assert score_inbound(make_message(), facts).tier is Tier.HIGH


# --- негативные контроли: спам и пустой текст ------------------------------------


def test_spam_is_low_and_ignores_every_bump():
    """Спам — LOW ранним возвратом: ни срок, ни язык, ни пакет его не поднимают."""
    facts = make_facts(
        is_spam=True,
        request_types=(RequestType.OFFICE, RequestType.VISA, RequestType.BANK),
        timeline_days=1,
        language="ru",
        confidence=1.0,
        quotes=("куплю базу клиентов",),
    )
    result = score_inbound(make_message(), facts)
    assert result.tier is Tier.LOW
    assert result.reasons == ("обращение помечено как спам или не по теме",)
    assert result.violations == ()


def test_spam_without_request_types_is_still_low():
    facts = make_facts(is_spam=True, request_types=(), language="ru", timeline_days=1)
    assert score_inbound(make_message(), facts).tier is Tier.LOW


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_empty_text_cannot_produce_high(text):
    """Негативный контроль: высокая уверенность и цитаты не спасают пустой текст (Р1)."""
    facts = make_facts(
        request_types=(RequestType.OFFICE, RequestType.VISA),
        timeline_days=3,
        language="ru",
        confidence=1.0,
        quotes=("цитата из ниоткуда",),
    )
    result = score_inbound(make_message(text=text), facts)
    assert result.tier is Tier.INVALID
    assert result.violations == ("HIGH на пустом тексте обращения",)


def test_high_without_quotes_is_invalid():
    facts = make_facts(
        request_types=(RequestType.OFFICE, RequestType.VISA), language="ru", quotes=()
    )
    result = score_inbound(make_message(), facts)
    assert result.tier is Tier.INVALID
    assert result.violations == ("HIGH без цитаты из обращения",)


def test_both_violations_are_reported_not_collapsed():
    facts = make_facts(
        request_types=(RequestType.OFFICE, RequestType.VISA), language="ru", quotes=()
    )
    result = score_inbound(make_message(text=""), facts)
    assert result.tier is Tier.INVALID
    assert result.violations == (
        "HIGH без цитаты из обращения",
        "HIGH на пустом тексте обращения",
    )


def test_medium_without_quotes_is_not_invalid():
    """Инвариант держит только HIGH: MEDIUM без цитат — обычный результат, не «не смогли»."""
    result = score_inbound(make_message(), make_facts(quotes=()))
    assert result.tier is Tier.MEDIUM
    assert result.violations == ()
    assert result.evidence == ()


def test_medium_on_empty_text_is_not_invalid():
    result = score_inbound(make_message(text=""), make_facts())
    assert result.tier is Tier.MEDIUM
    assert result.violations == ()


# --- доказательства --------------------------------------------------------------


def test_quotes_become_evidence_in_order():
    facts = make_facts(quotes=("нужен офис на 5 человек", "бюджет до 60 тысяч"))
    result = score_inbound(make_message(), facts)
    assert [(e.kind, e.value) for e in result.evidence] == [
        ("quote", "нужен офис на 5 человек"),
        ("quote", "бюджет до 60 тысяч"),
    ]


def test_spam_keeps_quotes_as_evidence():
    result = score_inbound(make_message(), make_facts(is_spam=True, quotes=("куплю базу",)))
    assert [e.value for e in result.evidence] == ["куплю базу"]
