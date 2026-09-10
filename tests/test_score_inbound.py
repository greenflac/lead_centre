"""Тесты оси C — скоринг входящих обращений (`score_inbound`).

Ожидаемое — литералы (Т2): пороги записаны числами и строками (30 дней, 2 типа услуг,
5 человек, 0.5 уверенности, язык "ru", 2 содержательных признака для HIGH), ступени —
членами `Tier`. Из `rubric` не импортируется ничего: ни `URGENT_TIMELINE_DAYS`,
ни `SIGNALS_FOR_HIGH`, ни `TIER_LADDER`, ни базовая ступень.

Правило подъёма (SIGNALS_FOR_HIGH = 2): считаются только СОДЕРЖАТЕЛЬНЫЕ признаки —
срочность, пакет услуг, размер команды, названный бюджет. Язык в счёт не идёт вовсе.
Поэтому почти в каждом тесте есть «спутник» — второй признак, без которого подъём до
HIGH не наблюдается: `headcount=10` или `timeline_days=10`.
Края и середина по каждому порогу (Т3), негативные контроли на спам и пустой текст (И5).
"""
from __future__ import annotations

import pytest

from leadcentre.engine.reasons import ReasonCode
from leadcentre.engine.score import score_inbound
from leadcentre.models import AddressType, Event, RequestType, Tier
from tests.conftest import (
    has_reason,
    make_facts,
    make_message,
    reason_codes,
    reason_params,
)

# Причина проверяется кодом и параметром, а не текстом карточки: формулировки живут
# в каталоге `engine/reasons.py` и проверяются в tests/test_reasons.py. Раньше здесь
# матчились подстроки («срок 10 дн.»), и правка текста красила девять тестов,
# ни одного правила при этом не сломав.

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
    assert reason_codes(result) == [ReasonCode.NO_REQUEST_TYPE]


def test_no_request_types_with_one_bump_reaches_only_medium():
    """Понижение базы наблюдаемо: с одним повышением обращение без запроса — MEDIUM, не HIGH."""
    result = score_inbound(make_message(), make_facts(request_types=(), timeline_days=5))
    assert result.tier is Tier.MEDIUM


# --- порог срочности: 30 дней ----------------------------------------------------


@pytest.mark.parametrize(
    ("timeline_days", "expected"),
    [
        (0, Tier.HIGH),    # «нужно вчера»
        (1, Tier.HIGH),
        (30, Tier.HIGH),   # месяц — прежний порог, теперь середина диапазона
        (45, Tier.HIGH),   # urg-02: «с 1 октября», получено 17 августа
        (59, Tier.HIGH),
        (60, Tier.HIGH),   # ровно порог URGENT_TIMELINE_DAYS — ещё срочно
        (61, Tier.MEDIUM),  # на день дальше — признак не сработал
        (90, Tier.MEDIUM),
        (365, Tier.MEDIUM),
        (None, Tier.MEDIUM),  # срок не извлечён
    ],
)
def test_urgent_timeline_threshold_is_60_days(timeline_days, expected):
    """Спутник — команда 10 человек: срок становится вторым признаком и даёт HIGH.

    Порог 60, а не 30: решение об офисе и регистрации принимают за месяц-два, и на
    пороге 30 негативный контроль urg-02 (45 дней) уезжал в MEDIUM против разметки.
    """
    facts = make_facts(timeline_days=timeline_days, headcount=10)
    assert score_inbound(make_message(), facts).tier is expected


@pytest.mark.parametrize("timeline_days", [61, 90, 365])
def test_timeline_beyond_the_threshold_leaves_no_reason(timeline_days):
    """Негативный контроль: не сработавший признак не пишет причину в карточку."""
    result = score_inbound(make_message(), make_facts(timeline_days=timeline_days))
    assert not has_reason(result, ReasonCode.URGENT_TIMELINE)


def test_urgent_timeline_alone_is_only_one_signal():
    """Один содержательный признак от базы MEDIUM оставляет MEDIUM (SIGNALS_FOR_HIGH = 2)."""
    result = score_inbound(make_message(), make_facts(timeline_days=1))
    assert result.tier is Tier.MEDIUM
    # 60 — порог срочности литералом (Т2): причина обязана назвать и срок, и границу
    assert reason_params(result, ReasonCode.URGENT_TIMELINE) == {"days": 1, "limit": 60}


def test_urgent_timeline_reason_names_the_number():
    result = score_inbound(make_message(), make_facts(timeline_days=10))
    assert reason_params(result, ReasonCode.URGENT_TIMELINE) == {"days": 10, "limit": 60}


# --- порог пакета: 2 типа запроса ------------------------------------------------


@pytest.mark.parametrize(
    ("request_types", "expected"),
    [
        ((), Tier.MEDIUM),  # база LOW, спутник поднимает на ступень
        ((RequestType.OFFICE,), Tier.MEDIUM),  # один запрос — пакета нет, признак один
        ((RequestType.OFFICE, RequestType.VISA), Tier.HIGH),  # ровно порог: два типа
        ((RequestType.OFFICE, RequestType.VISA, RequestType.BANK), Tier.HIGH),
    ],
)
def test_package_threshold_is_2_request_types(request_types, expected):
    """Спутник — команда 10 человек; пакет услуг становится вторым признаком."""
    facts = make_facts(request_types=request_types, headcount=10)
    assert score_inbound(make_message(), facts).tier is expected


def test_package_alone_is_only_one_signal():
    facts = make_facts(request_types=(RequestType.OFFICE, RequestType.VISA))
    result = score_inbound(make_message(), facts)
    assert result.tier is Tier.MEDIUM
    assert reason_params(result, ReasonCode.PACKAGE_REQUEST) == {"count": 2}


def test_package_reason_names_the_count():
    facts = make_facts(request_types=(RequestType.OFFICE, RequestType.VISA))
    result = score_inbound(make_message(), facts)
    assert reason_params(result, ReasonCode.PACKAGE_REQUEST) == {"count": 2}


# --- порог команды: 5 человек ----------------------------------------------------


@pytest.mark.parametrize(
    ("headcount", "expected"),
    [
        (0, Tier.MEDIUM),     # край снизу: команды нет
        (1, Tier.MEDIUM),
        (4, Tier.MEDIUM),     # на человека меньше порога — повышения нет
        (5, Tier.HIGH),       # ровно порог TEAM_MIN_HEADCOUNT
        (6, Tier.HIGH),
        (12, Tier.HIGH),      # середина реального диапазона
        (200, Tier.HIGH),
        (None, Tier.MEDIUM),  # численность не извлечена
    ],
)
def test_team_headcount_threshold_is_5(headcount, expected):
    """Спутник — срок 10 дней; размер команды становится вторым признаком."""
    result = score_inbound(make_message(), make_facts(headcount=headcount, timeline_days=10))
    assert result.tier is expected
    assert result.violations == ()


def test_team_headcount_alone_is_only_one_signal():
    result = score_inbound(make_message(), make_facts(headcount=40))
    assert result.tier is Tier.MEDIUM
    assert reason_params(result, ReasonCode.TEAM_OVER_FLEXI_QUOTA) == {"headcount": 40}


def test_team_headcount_reason_names_the_number():
    result = score_inbound(make_message(), make_facts(headcount=7))
    assert reason_params(result, ReasonCode.TEAM_OVER_FLEXI_QUOTA) == {"headcount": 7}


def test_headcount_below_threshold_leaves_no_reason():
    """Негативный контроль: не сработавший признак не пишет причину в карточку."""
    result = score_inbound(make_message(), make_facts(headcount=4))
    assert not has_reason(result, ReasonCode.TEAM_OVER_FLEXI_QUOTA)


# --- бюджет: засчитывается только сумма, а не вопрос о цене ----------------------


@pytest.mark.parametrize(
    "budget_hint",
    [
        "скок стоит",          # ровно тот вход, что давал ложный HIGH на prc-01
        "сколько стоит?",
        "бюджет есть",
        "готовы обсуждать бюджет",
        "недорого",
        "",
        None,
    ],
)
def test_budget_without_a_digit_does_not_raise_the_tier(budget_hint):
    """Вопрос о цене — не бюджет: из «скок стоит» горячий лид не следует (И2, живой дефект).

    Спутник (команда 10 человек) есть, поэтому засчитайся бюджет — вышло бы HIGH.
    """
    result = score_inbound(make_message(), make_facts(budget_hint=budget_hint, headcount=10))
    assert result.tier is Tier.MEDIUM
    assert not has_reason(result, ReasonCode.BUDGET_NAMED)


@pytest.mark.parametrize(
    "budget_hint",
    [
        "120-150 тысяч дирхам",
        "AED 60k",
        "до 50000 в год",
        "бюджет 30 тыс.",
        "5",  # край: одна цифра — уже сумма
    ],
)
def test_budget_with_a_digit_raises_the_tier(budget_hint):
    """Спутник — команда 10 человек; названный бюджет становится вторым признаком."""
    result = score_inbound(make_message(), make_facts(budget_hint=budget_hint, headcount=10))
    assert result.tier is Tier.HIGH
    assert reason_params(result, ReasonCode.BUDGET_NAMED) == {"budget": budget_hint}


def test_budget_reason_is_trimmed_to_40_characters():
    long_hint = "1" + "я" * 80
    result = score_inbound(make_message(), make_facts(budget_hint=long_hint, headcount=10))
    # обрезка — свойство параметра, а не текста: 40 символов литералом (Т2)
    assert reason_params(result, ReasonCode.BUDGET_NAMED) == {"budget": long_hint[:40]}
    assert len(long_hint[:40]) == 40


# --- регрессии живых прогонов ----------------------------------------------------


def test_live_lead_is_high_for_substantive_reasons_not_for_the_language():
    """Живой лид: «нужно 12 рабочих мест с 1 октября, бюджет есть, готовы подписать».

    Он обязан быть HIGH, но причина в карточке — команда и срок, а не «написано по-русски».
    Заперто по дефекту: раньше HIGH выдавался с единственной причиной про язык.
    """
    facts = make_facts(
        request_types=(RequestType.OFFICE,),
        headcount=12,
        timeline_days=22,
        budget_hint="бюджет есть",  # цифр нет — как бюджет не засчитывается
        language="ru",
        confidence=0.9,
        quotes=("нужно 12 рабочих мест с 1 октября",),
    )
    result = score_inbound(make_message("нужно 12 рабочих мест с 1 октября"), facts)
    assert result.tier is Tier.HIGH
    assert reason_params(result, ReasonCode.TEAM_OVER_FLEXI_QUOTA) == {"headcount": 12}
    assert reason_params(result, ReasonCode.URGENT_TIMELINE) == {"days": 22, "limit": 60}
    assert not has_reason(result, ReasonCode.BUDGET_NAMED)
    # содержательных причин минимум две — карточка не держится на языке
    language_codes = {ReasonCode.TARGET_LANGUAGE, ReasonCode.TARGET_LANGUAGE_ALONE}
    assert len([c for c in reason_codes(result) if c not in language_codes]) >= 2


def test_prc_01_price_question_in_russian_is_not_high():
    """Живой дефект prc-01: «скок стоит» по-русски давало HIGH. Теперь MEDIUM."""
    facts = make_facts(
        request_types=(RequestType.OFFICE,),
        budget_hint="скок стоит",
        language="ru",
        quotes=("скок стоит",),
    )
    result = score_inbound(make_message("скок стоит офис?"), facts)
    assert result.tier is Tier.MEDIUM
    assert reason_params(result, ReasonCode.TARGET_LANGUAGE_ALONE) == {"language": "ru"}


# --- язык: довесок, а не самостоятельный повод ------------------------------------


@pytest.mark.parametrize("language", ["ru", "en", "ar", "mixed", "RU"])
def test_language_alone_never_raises_the_tier(language):
    """Дефект, ради которого правило и введено: «HIGH, потому что по-русски» не бывает."""
    result = score_inbound(make_message(), make_facts(language=language))
    assert result.tier is Tier.MEDIUM


def test_russian_alone_says_in_reasons_that_other_signals_are_missing():
    result = score_inbound(make_message(), make_facts(language="ru"))
    assert result.tier is Tier.MEDIUM
    assert reason_params(result, ReasonCode.TARGET_LANGUAGE_ALONE) == {"language": "ru"}
    assert not has_reason(result, ReasonCode.TARGET_LANGUAGE)


@pytest.mark.parametrize(
    ("signal", "expected_code", "expected_params"),
    [
        ({"timeline_days": 10}, ReasonCode.URGENT_TIMELINE, {"days": 10, "limit": 60}),
        ({"headcount": 12}, ReasonCode.TEAM_OVER_FLEXI_QUOTA, {"headcount": 12}),
        (
            {"budget_hint": "120-150 тысяч дирхам"},
            ReasonCode.BUDGET_NAMED,
            {"budget": "120-150 тысяч дирхам"},
        ),
        (
            {"request_types": (RequestType.OFFICE, RequestType.VISA)},
            ReasonCode.PACKAGE_REQUEST,
            {"count": 2},
        ),
    ],
)
@pytest.mark.parametrize("language", ["ru", "en"])
def test_language_never_changes_the_tier_only_the_reason(
    signal, expected_code, expected_params, language
):
    """Язык не входит в счётчик признаков: при любом языке ступень одна и та же.

    Меняется только причина в карточке — ради неё правило и оставлено. Русский даёт
    TARGET_LANGUAGE (есть содержательный признак), нецелевой язык — ни одной языковой
    причины вовсе.
    """
    overrides = {"request_types": (RequestType.OFFICE,), "language": language, **signal}
    facts = make_facts(**overrides)
    result = score_inbound(make_message(), facts)
    assert result.tier is Tier.MEDIUM  # один содержательный признак — подъёма нет
    assert reason_params(result, expected_code) == expected_params
    if language == "ru":
        assert reason_params(result, ReasonCode.TARGET_LANGUAGE) == {"language": "ru"}
        assert not has_reason(result, ReasonCode.TARGET_LANGUAGE_ALONE)
    else:
        assert not has_reason(result, ReasonCode.TARGET_LANGUAGE)
        assert not has_reason(result, ReasonCode.TARGET_LANGUAGE_ALONE)


def test_language_does_not_complete_a_pair_of_signals():
    """Дефект, ради которого правило и введено: русский не заменяет второй признак."""
    russian = score_inbound(make_message(), make_facts(language="ru", headcount=10))
    english = score_inbound(make_message(), make_facts(language="en", headcount=10))
    assert russian.tier is Tier.MEDIUM
    assert russian.tier is english.tier


def test_two_substantive_signals_give_high_in_any_language():
    for language in ("ru", "en", "ar", "mixed"):
        facts = make_facts(language=language, headcount=10, timeline_days=10)
        assert score_inbound(make_message(), facts).tier is Tier.HIGH, language


@pytest.mark.parametrize("language", ["en", "ar", "mixed", "RU"])
def test_non_target_language_adds_nothing_even_with_a_signal(language):
    result = score_inbound(
        make_message(), make_facts(request_types=(), language=language, headcount=10)
    )
    assert result.tier is Tier.MEDIUM
    assert not has_reason(result, ReasonCode.TARGET_LANGUAGE)
    assert not has_reason(result, ReasonCode.TARGET_LANGUAGE_ALONE)


def test_russian_alone_from_low_base_stays_low():
    """Ни одного содержательного признака: язык не вытягивает даже на ступень вверх."""
    result = score_inbound(make_message(), make_facts(request_types=(), language="ru"))
    assert result.tier is Tier.LOW
    assert reason_params(result, ReasonCode.TARGET_LANGUAGE_ALONE) == {"language": "ru"}


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
    """Два содержательных признака есть всегда; решает только уверенность."""
    facts = make_facts(timeline_days=7, headcount=10, confidence=confidence)
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
    # 0.5 — порог доверия литералом (Т2)
    assert reason_params(result, ReasonCode.LOW_CONFIDENCE) == {
        "confidence": 0.42,
        "threshold": 0.5,
    }


# --- сколько признаков делает лид горячим: ровно 2 -------------------------------


_SIGNALS = {
    "timeline": {"timeline_days": 10},
    "package": {"request_types": (RequestType.OFFICE, RequestType.VISA)},
    "headcount": {"headcount": 10},
    "budget": {"budget_hint": "120000 AED"},
}


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ((), Tier.MEDIUM),                                    # ноль признаков
        (("timeline",), Tier.MEDIUM),                         # один — мало
        (("headcount",), Tier.MEDIUM),
        (("budget",), Tier.MEDIUM),
        (("package",), Tier.MEDIUM),
        (("timeline", "headcount"), Tier.HIGH),               # ровно два — порог
        (("package", "budget"), Tier.HIGH),
        (("timeline", "package", "headcount"), Tier.HIGH),    # три
        (("timeline", "package", "headcount", "budget"), Tier.HIGH),  # все четыре
    ],
)
def test_two_substantive_signals_are_required_for_high(names, expected):
    """SIGNALS_FOR_HIGH = 2: один признак — MEDIUM, два и больше — HIGH (края и середина)."""
    overrides = {}
    for name in names:
        overrides.update(_SIGNALS[name])
    result = score_inbound(make_message(), make_facts(**overrides))
    assert result.tier is expected
    assert result.violations == ()


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


def test_all_five_signals_still_cap_at_high():
    """Потолок лестницы при новом счётчике: пять признаков сразу — всё равно ровно HIGH."""
    facts = make_facts(
        request_types=(RequestType.OFFICE, RequestType.VISA, RequestType.SETUP),
        timeline_days=3,
        headcount=40,
        budget_hint="300000 AED",
        language="ru",
        confidence=1.0,
        quotes=("сорок человек с ноября", "бюджет 300000 AED"),
    )
    result = score_inbound(make_message(), facts)
    assert result.tier is Tier.HIGH
    assert result.violations == ()
    assert len(result.reasons) == 5


def test_low_base_reaches_only_medium_even_with_two_signals():
    """Подъём — ровно на одну ступень: от базы LOW до HIGH не добраться никаким набором."""
    facts = make_facts(request_types=(), timeline_days=5, headcount=40, language="ru")
    result = score_inbound(make_message(), facts)
    assert result.tier is Tier.MEDIUM
    assert has_reason(result, ReasonCode.NO_REQUEST_TYPE)


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
    assert reason_codes(result) == [ReasonCode.SPAM_OR_OFF_TOPIC]
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
        request_types=(RequestType.OFFICE, RequestType.VISA), headcount=10, quotes=()
    )
    result = score_inbound(make_message(), facts)
    assert result.tier is Tier.INVALID
    assert result.violations == ("HIGH без цитаты из обращения",)


def test_both_violations_are_reported_not_collapsed():
    facts = make_facts(
        request_types=(RequestType.OFFICE, RequestType.VISA), headcount=10, quotes=()
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
