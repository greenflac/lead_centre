"""Deterministic fact extraction: request types, headcount, quotes, budget and deadlines.

Expected values are literals; no marker list is imported from the module under test, so
narrowing one reddens these tests instead of moving them along.
"""
from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

import pytest

from leadcentre.engine.facts_rules import rules_facts
from leadcentre.models import InboundMessage, RequestType

SEED_CSV = Path(__file__).resolve().parents[1] / "data" / "inbound_seed.csv"

# Literals, not an import: this test must redden if the module's list changes.
LICENCE_WORDS = ("лицензи", "licence", "license")


def _message(text: str, external_id: str = "probe") -> InboundMessage:
    return InboundMessage(
        external_id=external_id, channel="form", text=text, received_at=date(2026, 9, 9)
    )


def _types(text: str) -> set[RequestType]:
    return set(rules_facts(_message(text)).request_types)


def _seed_rows() -> list[dict[str, str]]:
    with SEED_CSV.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _seed_message(external_id: str) -> InboundMessage:
    row = next(r for r in _seed_rows() if r["external_id"] == external_id)
    return InboundMessage(
        external_id=external_id,
        channel=row["channel"],
        text=row["text"],
        received_at=date.fromisoformat(row["received_at"]),
    )


# Registration is dropped: a licence word stands next to a renewal.


def test_pure_renewal_probe_has_no_setup():
    """A pure renewal probe yields renewal and nothing else."""
    assert _types("нужно продлить лицензию компании, что требуется и сколько стоит?") == {
        RequestType.RENEWAL
    }


def test_urg_13_is_office_and_renewal_without_setup():
    """A live request about an expiring licence plus a bigger unit stays office and renewal."""
    message = _seed_message("urg-13")
    assert "licence" in message.text.lower()  # a precondition, not the conclusion
    assert set(rules_facts(message).request_types) == {
        RequestType.OFFICE,
        RequestType.RENEWAL,
    }


@pytest.mark.parametrize(
    "text",
    [
        "продлить лицензию, что нужно?",
        "надо продлить license, сроки?",
        "our trade licence expires next month, what is the renewal fee?",
        "licence renewal please",
    ],
)
def test_licence_next_to_renewal_never_yields_setup(text):
    assert RequestType.SETUP not in _types(text)
    assert RequestType.RENEWAL in _types(text)


# Registration stands: live negative control and separate sentences.


def test_urg_08_keeps_setup_because_freezone_fired():
    """Negative control: a greedy rule would drop a genuine registration here."""
    message = _seed_message("urg-08")
    low = message.text.lower()
    assert "licence" in low and "freezone" in low  # preconditions taken from the request text
    assert set(rules_facts(message).request_types) == {
        RequestType.OFFICE,
        RequestType.SETUP,
        RequestType.RENEWAL,
    }


@pytest.mark.parametrize(
    "text",
    [
        # These inputs guard the per-sentence check: the only registration marker is
        # a licence word, and it sits in a sentence that says nothing about renewal.
        "нужна лицензия. а когда продлевать её потом?",
        "лицензия нужна; продление тоже интересует",
        "сколько стоит лицензия? и продление сколько?",
    ],
)
def test_licence_in_a_separate_sentence_keeps_setup(text):
    """A licence word in a sentence without a renewal still means registration."""
    assert _types(text) == {RequestType.SETUP, RequestType.RENEWAL}


def test_licence_and_setup_word_together_keep_setup():
    """With an explicit setup word the rule exits a step earlier."""
    text = "хотим открыть компанию, лицензия нужна. и ещё, когда продлевать?"
    assert _types(text) == {RequestType.SETUP, RequestType.RENEWAL}


@pytest.mark.parametrize(
    "text",
    [
        "нужна лицензия во фризоне, и когда её продлевать потом?",
        "открываем компанию, лицензия нужна; продление тоже интересует",
        "mainland licence, а продлевать через год?",
    ],
)
def test_another_setup_marker_keeps_setup_next_to_renewal(text):
    assert RequestType.SETUP in _types(text)
    assert RequestType.RENEWAL in _types(text)


@pytest.mark.parametrize(
    ("external_id", "expected"),
    [
        ("prc-01", {RequestType.SETUP}),        # a bare price question: the rule does not touch it
        ("gen-20", {RequestType.RENEWAL}),      # a renewal with no licence word
    ],
)
def test_unrelated_messages_are_unchanged(external_id, expected):
    assert set(rules_facts(_seed_message(external_id)).request_types) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Word forms the renewal stems were introduced for.
        ("продлеваем лицензию, что нужно?", {RequestType.RENEWAL}),
        ("продлеваете ли вы визы?", {RequestType.VISA, RequestType.RENEWAL}),
        ("надо продлевать?", {RequestType.RENEWAL}),
        ("продление лицензии", {RequestType.RENEWAL}),
        ("продлить лицензию, что нужно?", {RequestType.RENEWAL}),
        ("we are renewing our licence next month", {RequestType.RENEWAL}),
        ("licence renewals", {RequestType.RENEWAL}),
        ("licence expiring soon", {RequestType.RENEWAL}),
        ("лицензия истекла в июле", {RequestType.RENEWAL}),
        # Negative control: these stems do not light registration.
        ("хотим открыть компанию во фризоне", {RequestType.SETUP}),
    ],
)
def test_renewal_word_forms_are_covered_by_stems(text, expected):
    """Renewal markers are stems, so a return to whole words would be visible here."""
    assert _types(text) == expected


def test_setup_without_renewal_is_untouched():
    """The rule engages only on a setup and renewal pair."""
    assert _types("хотим лицензию и открыть компанию") == {RequestType.SETUP}
    assert _types("нужна licence для новой компании") == {RequestType.SETUP}


# Invariant over the whole seed: the fault itself, not one instance of it.


def _without_licence_words(text: str) -> str:
    """Returns the same text without licence words, showing what else holds registration."""
    pattern = "|".join(re.escape(word) for word in LICENCE_WORDS)
    return re.sub(pattern, " ", text, flags=re.IGNORECASE)


def test_no_seed_message_gets_setup_only_from_a_licence_word_next_to_renewal():
    """Across the whole seed: registration never rests on a licence word beside a renewal.

    Written about the fault itself, so it survives a change to the marker lists.
    """
    rows = _seed_rows()
    assert len(rows) == 70

    violations: list[str] = []
    pairs = 0
    for row in rows:
        message = _seed_message(row["external_id"])
        types = set(rules_facts(message).request_types)
        if not {RequestType.SETUP, RequestType.RENEWAL} <= types:
            continue
        pairs += 1
        stripped = _types(_without_licence_words(message.text))
        if RequestType.SETUP not in stripped:
            violations.append(row["external_id"])

    assert pairs >= 1, "в наборе нет ни одной пары setup+renewal — проверять нечего"
    assert violations == [], (
        f"регистрация держится только на слове о лицензии: {violations}"
    )


# Closed gaps, locked in so a regression is visible.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("зарегистрировать компанию", {RequestType.SETUP}),
        ("открытие компании", {RequestType.SETUP}),
        ("incorporation", {RequestType.SETUP}),
        ("рабочее место одно", {RequestType.OFFICE}),
        ("переговорная", {RequestType.OFFICE}),
        ("ведём бухучёт", {RequestType.ACCOUNTING}),
        ("ведем бухучет", {RequestType.ACCOUNTING}),   # and the same spelled without yo
        ("открыть счёт", {RequestType.BANK}),
        ("открыть счет", {RequestType.BANK}),          # and the same spelled without yo
        ("открытие счёта", {RequestType.BANK}),
        ("open an account", {RequestType.BANK}),
        ("current account", {RequestType.BANK}),
        ("срок действия истёк", {RequestType.RENEWAL}),
        ("лицензия истекла", {RequestType.RENEWAL}),
    ],
)
def test_closed_marker_gaps_are_covered(text, expected):
    """Word forms the markers used to miss now yield the right type."""
    assert _types(text) == expected


# The noise around those closed gaps: the price paid for the coverage.


@pytest.mark.parametrize(
    ("text", "wrong"),
    [
        # an account stem also fires where no bank account is meant
        ("открыть счёт в ресторане", RequestType.BANK),
        ("open an account on your website", RequestType.BANK),
        # meeting-room and desk stems fire outside renting
        ("переговорная комната в отеле", RequestType.OFFICE),
        ("рабочее место дома", RequestType.OFFICE),
        # the registration stem catches any registration, not only a company
        ("зарегистрироваться на вебинар", RequestType.SETUP),
        ("зарегистрировать домен", RequestType.SETUP),
    ],
)
def test_noise_introduced_by_closing_the_gaps_is_recorded(text, wrong):
    """The noise the closed gaps introduced, locked in deliberately: an extra type costs
    a manager a second, while a missed request costs a lead."""
    assert wrong in _types(text)


def test_words_around_the_closed_gaps_that_stay_silent():
    assert _types("закройте счёт, пожалуйста") == set()
    assert _types("счёт на оплату пришлите") == set()
    assert _types("инвойс и счёт-фактура") == set()


# Known marker gaps, locked in rather than left unsaid: these tests fix CURRENT
# behaviour, so closing a gap reddens the build and the decision is retaken.


@pytest.mark.parametrize(
    ("text", "missing", "current"),
    [
        # left open on purpose, each line for its own reason:
        # a "set up" stem would catch "set up a meeting" and "settings"
        ("company set up", RequestType.SETUP, set()),
        ("setting up a company", RequestType.SETUP, set()),
        # this word is an after-school club, not a licence renewal
        ("продлёнка", RequestType.RENEWAL, set()),
        # a subjunctive form; the stem would fire on unrelated phrases
        ("открыл бы компанию в оаэ", RequestType.SETUP, set()),
    ],
)
def test_known_marker_gaps_are_recorded(text, missing, current):
    """Marker gaps left open on purpose; closing one reddens the build so the decision
    is taken again rather than silently."""
    assert _types(text) == current
    assert missing not in _types(text)


@pytest.mark.parametrize(
    ("text", "wrong"),
    [
        # short stems catch unrelated words; kept on purpose, see the docstring
        ("купите телевизор недорого", RequestType.VISA),
        ("приходил ваш визит-менеджер", RequestType.VISA),
        ("процедура банкротства компании", RequestType.BANK),
        ("аудитория подписчиков", RequestType.ACCOUNTING),
        ("my desk job", RequestType.OFFICE),
        ("desktop приложение", RequestType.OFFICE),
    ],
)
def test_known_false_positives_are_recorded(text, wrong):
    """False positives of short stems, accepted on purpose: narrowing a stem would cost
    more real matches than the noise it removes."""
    assert wrong in _types(text)


# Team size: one number or "unknown", but never somebody else's number.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # both ends of the range and the middle: units and spacing vary
        ("переезжаем командой 8 человек, нужен офис", 8),
        ("нужно 12 рабочих мест с 1 октября", 12),
        ("Нужно оформить 3 рабочие визы для сотрудников", 3),
        ("we need 6 employment visas", 6),
        ("team of 25 people relocating", 25),
        ("нас двое, нужен флекси", None),
        ("нужен офис, что по срокам?", None),
    ],
)
def test_headcount_is_read_when_the_text_names_exactly_one(text, expected):
    """One stated count is taken; units are literals here, never imported from the module."""
    assert rules_facts(_message(text)).headcount == expected


def test_several_different_counts_give_no_headcount_at_all():
    """Several different counts leave the team size unknown: an addend is not a total."""
    text = "визы: нужно 3 партнёрские + 2 сотрудника, потом ещё 4"
    assert rules_facts(_message(text)).headcount is None


def test_the_same_count_repeated_is_still_one_count():
    text = "нужно 5 человек посадить, и визы на тех же 5 сотрудников"
    assert rules_facts(_message(text)).headcount == 5


def test_seed_edge_03_shows_no_headcount():
    """The real seed input, not only a synthetic string."""
    assert rules_facts(_seed_message("edge-03")).headcount is None


def test_headcount_quote_is_absent_when_the_count_is_unknown():
    """No count means no quote under it; the surrounding sentence still quotes its own type."""
    facts = rules_facts(_message("визы: нужно 3 партнёрские + 2 сотрудника, потом ещё 4"))
    assert "2 сотрудника" not in facts.quotes
    assert any("визы" in q for q in facts.quotes), "цитата под тип запроса обязана остаться"


# Quotes do not repeat one another.


LONG_ENUMERATION = (
    "вопросы такие: 1) нужен ли офис или хватит flexi desk 2) визы на сотрудников "
    "3) бухгалтерия и аудит 4) банк для нерезидента — всё это в одном предложении "
    "без единой точки, потому что клиент писал в спешке и не расставлял знаки"
)


def test_markers_inside_one_sentence_give_one_quote():
    """Several markers in one listing are one piece of evidence, not four."""
    quotes = rules_facts(_message(LONG_ENUMERATION)).quotes
    assert len(quotes) == 1, quotes


def test_markers_in_different_sentences_give_different_quotes():
    """Negative control: without it the check would also pass on "always keep one quote"."""
    text = "нужен офис в Дубае. Отдельным вопросом: визы на сотрудников."
    quotes = rules_facts(_message(text)).quotes
    assert len(quotes) == 2, quotes


def test_seed_edge_03_quotes_do_not_repeat_each_other():
    """On the real seed input no quote repeats its neighbour."""
    quotes = rules_facts(_seed_message("edge-03")).quotes
    assert len(quotes) <= 5, quotes
    stripped = [q.strip("…").strip() for q in quotes]
    for i, first in enumerate(stripped):
        for second in stripped[i + 1:]:
            assert first not in second and second not in first, (first, second)


def test_hot_requests_keep_at_least_one_quote():
    """Collapsing quotes may never leave a hot request without evidence."""
    for external_id in ("urg-01", "urg-13", "edge-03"):
        assert rules_facts(_seed_message(external_id)).quotes, external_id


# A budget is quoted whole, not by its last number.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # both ends and the middle: single amount, range, ceiling, floor
        ("бюджет 95 тысяч в год", "95 тысяч"),
        ("ориентируемся на 120-150 тысяч дирхам", "120-150 тысяч дирхам"),
        ("бюджет до 180k AED в год", "до 180k AED"),
        ("готовы от 50 тысяч дирхам", "от 50 тысяч дирхам"),
        ("около 200 000 AED на всё", "около 200 000 AED"),
        ("нужен офис, цену пока не знаем", None),
    ],
)
def test_budget_is_quoted_whole(text, expected):
    """A budget quote is verbatim, not the last number in the phrase.

    Expected values are literals; no money pattern is imported from the module.
    """
    assert rules_facts(_message(text)).budget_hint == expected


def test_budget_of_a_price_question_is_still_not_a_budget():
    assert rules_facts(_message("скок стоит открыть компанию?")).budget_hint is None


def test_seed_edge_03_budget_keeps_both_ends_of_the_range():
    """The real seed input keeps both ends of the range."""
    assert rules_facts(_seed_message("edge-03")).budget_hint == "120-150 тысяч дирхам"


# Deadlines named in words. Expected day counts are literals worked out by calendar,
# with no marker table imported. Fixtures take both ends of the week and month, plus the middle.


def _with_date(text: str, received_at: date):
    return InboundMessage(
        external_id="probe", channel="form", text=text, received_at=received_at
    )


@pytest.mark.parametrize(
    ("text", "received_at", "expected"),
    [
        # "this month" runs to the last day of the request month
        ("нужен офис в этом месяце", date(2026, 9, 1), 29),    # start of the month
        ("нужен офис в этом месяце", date(2026, 9, 9), 21),    # middle
        ("нужен офис в этом месяце", date(2026, 9, 30), 0),    # last day
        ("лицензия нужна до конца месяца", date(2026, 8, 31), 0),
        ("лицензия нужна до конца месяца", date(2026, 2, 1), 27),  # a short month
        ("we need it this month", date(2026, 9, 9), 21),
        # "by friday" is the next Friday, counting the request day
        ("ответ нужен до пятницы", date(2026, 8, 10), 4),      # Monday
        ("ответ нужен до пятницы", date(2026, 8, 14), 0),      # Friday itself
        ("ответ нужен до пятницы", date(2026, 8, 15), 6),      # Saturday: the next one
        ("ответ нужен до пятницы", date(2026, 8, 8), 6),       # a seed input
        # "this week" runs to the Sunday of the request week
        ("посмотреть офис на этой неделе", date(2026, 9, 7), 6),   # Monday
        ("посмотреть офис на этой неделе", date(2026, 9, 5), 1),   # Saturday, a seed input
        ("посмотреть офис на этой неделе", date(2026, 9, 6), 0),   # Sunday
        # "today" states no deadline for the service: measured on the 70-request set it
        # occurs once, in "who can call me today" — a call, not a deadline. It reads as
        # worded urgency instead, and the guard for that lives in the test below.
        ("нужно сегодня", date(2026, 9, 9), None),
        # the nearest named deadline binds the client
        ("до пятницы, край — в этом месяце", date(2026, 9, 7), 4),
        # negative control: no deadline words, so nothing is invented
        ("нужен офис на 8 человек", date(2026, 9, 9), None),
        ("срочно нужен офис", date(2026, 9, 9), None),
    ],
)
def test_named_deadline_is_counted_from_the_message_date(text, received_at, expected):
    assert rules_facts(_with_date(text, received_at)).timeline_days == expected


def test_deadline_is_counted_from_the_message_not_from_today():
    """A worded deadline counts from the request date: the same text on two days differs."""
    text = "нужен офис на этой неделе"
    assert rules_facts(_with_date(text, date(2026, 9, 7))).timeline_days == 6
    assert rules_facts(_with_date(text, date(2026, 9, 9))).timeline_days == 4


@pytest.mark.parametrize(
    ("text", "expected_timeline", "expected_urgency"),
    [
        # computable date: a deadline, and no worded signal
        ("лицензия нужна до конца месяца", 0, False),
        # urgency without a date: the signal, and no invented deadline
        ("срочно нужен офис", None, True),
        ("we need it asap", None, True),
        ("как можно быстрее", None, True),
        # both in one text: the named date wins
        ("срочно, лицензия нужна до конца месяца, лишь бы быстро", 0, False),
        # neither
        ("нужен офис на 8 человек", None, False),
    ],
)
def test_a_message_never_gets_both_a_named_deadline_and_wordless_urgency(
    text, expected_timeline, expected_urgency
):
    facts = rules_facts(_with_date(text, date(2026, 8, 31)))
    assert facts.timeline_days == expected_timeline
    assert facts.urgency_stated is expected_urgency


def test_the_demo_text_matches_the_live_llm_extraction():
    """Rules mode must match the live model on this text: two paths of one product."""
    text = (
        "Переезжаем командой 9 человек в Дубай, нужен офис и визы на всех, лицензию "
        "тоже оформляем. Срочно, хотим закрыть в этом месяце. Бюджет есть."
    )
    facts = rules_facts(_with_date(text, date(2026, 9, 11)))
    assert facts.timeline_days == 19
    assert facts.urgency_stated is False


@pytest.mark.parametrize(
    ("text", "received_at", "expected_timeline"),
    [
        # a date named by number and month must also silence the worded signal
        ("срочно, инвестор просит адрес до конца октября", date(2026, 7, 21), 72),
        ("срочно, нужен офис через 10 дней", date(2026, 9, 9), 10),
    ],
)
def test_any_extracted_deadline_silences_wordless_urgency(text, received_at, expected_timeline):
    facts = rules_facts(_with_date(text, received_at))
    assert facts.timeline_days == expected_timeline
    assert facts.urgency_stated is False


# Deadlines named by a bare number, with no preposition in front of it.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # the exact input the defect was observed on
        ("нам нужен office space asap, 10 seats, + 10 residence visa. срок - 3 недели.", 21),
        # positive control: with the preposition the behaviour is unchanged
        ("нужен офис через 3 недели", 21),
        ("we need the office in 3 weeks", 21),
        # both ends and the middle: one day, three weeks, three months
        ("срок - 1 день", 1),
        ("нужно за 10 дней", 10),
        ("срок 90 дней", 90),
        ("timeline 2 weeks", 14),
        ("нужно за 1 неделю", 7),
        # negative control: a number about the past is not a deadline
        ("2 недели назад отправляли заявку, ответа нет", None),
        ("we wrote 10 days ago and got no reply", None),
        # negative control: numbers that are not about time give no deadline
        ("нужен офис на 8 человек", None),
        ("12 рабочих мест, барша хайтс", None),
    ],
)
def test_deadline_in_days_or_weeks_does_not_require_a_preposition(text, expected):
    assert rules_facts(_with_date(text, date(2026, 9, 11))).timeline_days == expected


def test_past_tense_number_does_not_hide_a_real_deadline_later_in_the_text():
    """Only the talk about the past is discarded; a real deadline later in the text stands."""
    text = "писали 2 недели назад, а нужно за 10 дней"
    assert rules_facts(_with_date(text, date(2026, 9, 11))).timeline_days == 10


def test_today_is_worded_urgency_not_a_deadline():
    """A request naming a real deadline keeps it when "today" also appears.

    Live defect: "our licence expires in 21 days ... who can call me today" was scored
    with `timeline 0 days`. The word "today" was read as a deadline for the service, and
    zero days outranked the twenty-one the same message states.
    """
    text = ("our licence expires in 21 days and we must move to a bigger unit "
            "at the same time. 14 staff. urgent, who can call me today")
    facts = rules_facts(_message(text))
    assert facts.timeline_days == 21, facts.timeline_days
    assert facts.urgency_stated is False, "a named deadline cancels worded urgency"


def test_today_alone_is_urgency_without_a_date():
    """With no other deadline in the text, "today" says urgent and names no date."""
    facts = rules_facts(_message("нужно сегодня, очень ждём"))
    assert facts.timeline_days is None
    assert facts.urgency_stated is True
