"""Reason catalogue: completeness, plural agreement and negative controls.

Every code must carry a text in every language and substitute exactly the declared
parameters; the tests walk both enumerations, so a new code or language wakes them.
Each negative control has a positive one beside it, so "always says no" cannot pass.
Expected strings are literals, not assembled by the code under test.
"""
from __future__ import annotations

import csv
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from leadcentre.engine import reasons as R
from leadcentre.engine.facts_rules import rules_facts
from leadcentre.engine.score import score, score_inbound
from leadcentre.models import AddressType, Event, InboundMessage, Score, Tier
from leadcentre.sources.gleif import GleifAdapter
from tests.conftest import TODAY, lapsed_company

RU, EN = R.Language.RU, R.Language.EN

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# Sample parameters per code, chosen so plural agreement is visible. A code missing
# here reddens its own test, or a new reason would never be rendered at all.
SAMPLE_PARAMS: dict[R.ReasonCode, dict[str, object]] = {
    R.ReasonCode.LEI_LAPSED_FRESH: {"days": 41},
    R.ReasonCode.LEI_LAPSED_LONG_AGO: {"days": 122},
    R.ReasonCode.ENTITY_RECENTLY_CREATED: {"days": 12},
    R.ReasonCode.LEI_RENEWAL_SOON: {"days": 30},
    R.ReasonCode.CITY_OFF_TARGET: {"city": "Абу-Даби"},
    R.ReasonCode.CITY_NOT_SET: {},
    R.ReasonCode.CITY_UNRECOGNISED: {"city": "Nad Al Sheba"},
    R.ReasonCode.ENTITY_INACTIVE: {},
    R.ReasonCode.ENTITY_STATUS_UNKNOWN: {"status": "NULL"},
    R.ReasonCode.ENTITY_STATUS_NOT_SET: {},
    R.ReasonCode.REGISTRATION_STATUS_NO_EVENT: {"status": "RETIRED"},
    R.ReasonCode.REGISTRATION_STATUS_UNKNOWN: {"status": "SUSPENDED"},
    R.ReasonCode.REGISTRATION_STATUS_NOT_SET: {},
    R.ReasonCode.SPAM_OR_OFF_TOPIC: {},
    R.ReasonCode.NO_REQUEST_TYPE: {},
    R.ReasonCode.URGENT_TIMELINE: {"days": 14, "limit": 60},
    R.ReasonCode.URGENT_STATED: {},
    R.ReasonCode.PACKAGE_REQUEST: {"count": 2},
    R.ReasonCode.TEAM_OVER_FLEXI_QUOTA: {"headcount": 8},
    R.ReasonCode.BUDGET_NAMED: {"budget": "20 000 AED в год"},
    R.ReasonCode.TARGET_LANGUAGE: {"language": "ru"},
    R.ReasonCode.TARGET_LANGUAGE_ALONE: {"language": "ru"},
    R.ReasonCode.LOW_CONFIDENCE: {"confidence": 0.30, "threshold": 0.5},
    R.ReasonCode.CONFIDENCE_NOT_MEASURED: {},
}

ALL_CODES = list(R.ReasonCode)
ALL_LANGUAGES = list(R.Language)


def _placeholders(template: str) -> set[str]:
    """Returns a template's parameter names; parsed here, not imported, so it cannot drift."""
    import string

    return {
        name.split(".")[0].split("[")[0]
        for _, name, _, _ in string.Formatter().parse(template)
        if name
    }




def test_sample_params_cover_every_code():
    """The sample covers every code, so the instrument measures the whole catalogue."""
    missing = sorted(set(R.ReasonCode) - set(SAMPLE_PARAMS), key=lambda c: c.value)
    assert missing == [], f"нет показательных параметров для {[c.value for c in missing]}"
    assert len(SAMPLE_PARAMS) == len(ALL_CODES) == 24


def test_catalogue_declares_every_code():
    """A code with no catalogue entry would raise on a live card."""
    assert set(R.CATALOGUE) == set(R.ReasonCode)


@pytest.mark.parametrize("code", ALL_CODES, ids=lambda c: c.value)
@pytest.mark.parametrize("language", ALL_LANGUAGES, ids=lambda language: language.value)
def test_every_code_has_a_text_in_every_language(code, language):
    """No entry is half-translated: a text exists in every language."""
    template = R.CATALOGUE[code].texts.get(language)
    assert template, f"нет текста {language.value} у причины {code.value}"
    assert template.strip(), f"пустой текст {language.value} у причины {code.value}"


@pytest.mark.parametrize("code", ALL_CODES, ids=lambda c: c.value)
@pytest.mark.parametrize("language", ALL_LANGUAGES, ids=lambda language: language.value)
def test_every_text_uses_exactly_the_declared_params(code, language):
    """Each text substitutes exactly the declared parameters, no more and no fewer."""
    spec = R.CATALOGUE[code]
    used = _placeholders(spec.texts[language])
    assert used == set(spec.params), {
        "код": code.value,
        "язык": language.value,
        "в тексте": sorted(used),
        "объявлено": sorted(spec.params),
    }


@pytest.mark.parametrize("code", ALL_CODES, ids=lambda c: c.value)
@pytest.mark.parametrize("language", ALL_LANGUAGES, ids=lambda language: language.value)
def test_every_code_renders_on_every_language(code, language):
    text = R.reason(code, **SAMPLE_PARAMS[code]).text(language)
    assert text.strip()
    assert "{" not in text, f"неподставленный placeholder в {code.value}/{language.value}"


@pytest.mark.parametrize("language", ALL_LANGUAGES, ids=lambda language: language.value)
def test_every_language_has_plural_forms_and_a_rule(language):
    assert language in R.PLURAL_RULES, f"нет правила числительных для {language.value}"
    assert language in R.PLURAL_FORMS, f"нет форм числительных для {language.value}"
    # units match across languages: a template names a unit, not a language
    assert set(R.PLURAL_FORMS[language]) == {"day", "person", "service", "char"}


def test_validate_catalogue_reports_how_much_it_checked():
    assert R.validate_catalogue() == 48


def test_catalogue_has_no_two_codes_with_the_same_russian_text():
    """Two codes sharing one text mean a distinction was lost."""
    texts = [spec.texts[RU] for spec in R.CATALOGUE.values()]
    assert len(set(texts)) == len(texts), "русские тексты причин повторяются"




@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (0, "0 дней"),
        (1, "1 день"),
        (2, "2 дня"),
        (4, "4 дня"),
        (5, "5 дней"),
        (11, "11 дней"),   # the 11-14 guard edge
        (12, "12 дней"),   # and its neighbour
        (14, "14 дней"),
        (15, "15 дней"),
        (21, "21 день"),
        (22, "22 дня"),
        (25, "25 дней"),
        (41, "41 день"),
        (101, "101 день"),
        (102, "102 дня"),
        (105, "105 дней"),
        (111, "111 дней"),  # the same guard in the next hundred
        (112, "112 дней"),
    ],
)
def test_russian_numerals_agree_with_the_noun(number, expected):
    """Russian plural agreement, including the 11-14 exception that breaks most quietly."""
    assert R.plural_phrase(RU, number, "day") == expected


@pytest.mark.parametrize(
    ("number", "expected"),
    [(0, "0 people"), (1, "1 person"), (2, "2 people"), (11, "11 people")],
)
def test_english_numerals_have_two_forms(number, expected):
    assert R.plural_phrase(EN, number, "person") == expected


def test_russian_and_english_forms_differ_on_the_same_number():
    """Negative control: identical output would mean rendering ignores the language."""
    assert R.plural_phrase(RU, 5, "day") != R.plural_phrase(EN, 5, "day")


def test_numeral_text_reaches_the_rendered_reason():
    """Agreement is visible in the rendered reason, not only in the helper."""
    assert R.reason(R.ReasonCode.LEI_RENEWAL_SOON, days=11).text(RU) == (
        "продление LEI через 11 дней"
    )
    assert R.reason(R.ReasonCode.LEI_RENEWAL_SOON, days=21).text(RU) == (
        "продление LEI через 21 день"
    )
    assert R.reason(R.ReasonCode.LEI_RENEWAL_SOON, days=1).text(EN) == (
        "LEI renewal due in 1 day"
    )




def test_reason_without_params_is_an_error():
    """A code declaring a parameter cannot be built without it."""
    with pytest.raises(R.ReasonError):
        R.reason(R.ReasonCode.TEAM_OVER_FLEXI_QUOTA)


def test_reason_without_the_second_param_is_an_error():
    """Half the parameters is also "could not", not "good enough"."""
    with pytest.raises(R.ReasonError):
        R.reason(R.ReasonCode.URGENT_TIMELINE, days=14)


def test_reason_with_an_extra_param_is_an_error():
    """An extra parameter means the author confused the code."""
    with pytest.raises(R.ReasonError):
        R.reason(R.ReasonCode.ENTITY_INACTIVE, days=3)


def test_reason_with_params_stripped_behind_the_factory_is_an_error():
    """Stripping parameters behind the factory still raises: a reason may arrive from storage."""
    good = R.reason(R.ReasonCode.LEI_RENEWAL_SOON, days=5)
    with pytest.raises(R.ReasonError):
        R.render(replace(good, params=()), RU)


def test_reason_with_a_duplicate_param_is_an_error():
    """A parameter given twice raises rather than letting one value win silently."""
    with pytest.raises(R.ReasonError):
        R.Reason(R.ReasonCode.LEI_RENEWAL_SOON, (("days", 5), ("days", 9)))


def test_the_same_reason_with_params_renders():
    """Positive control: a valid reason renders."""
    assert R.reason(R.ReasonCode.TEAM_OVER_FLEXI_QUOTA, headcount=8).text(EN) == (
        "team of 8 — flexi desk will not cover the visa quota"
    )




def test_fractional_number_cannot_be_agreed():
    with pytest.raises(R.ReasonError):
        R.plural_phrase(RU, 2.5, "day")


@pytest.mark.parametrize("value", ["5", None, True, 3.0])
def test_non_integer_number_cannot_be_agreed(value):
    """Strings, None, bool and int-as-float are not numbers for agreement."""
    with pytest.raises(R.ReasonError):
        R.plural_phrase(RU, value, "day")


def test_unknown_noun_has_no_forms():
    """A unit with no forms is an error, not a bare fallback."""
    with pytest.raises(R.ReasonError):
        R.plural_phrase(RU, 14, "week")


def test_integer_number_is_agreed():
    """Positive control for the number checks."""
    assert R.plural_phrase(RU, 2, "day") == "2 дня"


# Negative controls: a catalogue with a hole in it.


def _catalogue_with(code: R.ReasonCode, spec: R.ReasonSpec) -> dict:
    """Returns a copy of the catalogue with one entry replaced, leaving the global intact."""
    holed = dict(R.CATALOGUE)
    holed[code] = spec
    return holed


def test_catalogue_without_the_english_text_is_rejected():
    """An entry missing a language is rejected."""
    holed = _catalogue_with(
        R.ReasonCode.PACKAGE_REQUEST,
        R.ReasonSpec(
            params=("count",),
            texts={RU: "запрошено услуг: {count} — нужен пакет"},
        ),
    )
    with pytest.raises(R.ReasonCatalogueError):
        R.validate_catalogue(holed)


def test_catalogue_with_a_blank_english_text_is_rejected():
    """A whitespace-only text is rejected; a paramless code isolates the blankness rule."""
    blank = _catalogue_with(
        R.ReasonCode.ENTITY_INACTIVE,
        R.ReasonSpec(params=(), texts={RU: "юрлицо неактивно", EN: "   "}),
    )
    with pytest.raises(R.ReasonCatalogueError):
        R.validate_catalogue(blank)


def test_catalogue_without_the_english_text_of_a_paramless_reason_is_rejected():
    """The same absence on a reason without parameters is also rejected."""
    absent = _catalogue_with(
        R.ReasonCode.SPAM_OR_OFF_TOPIC,
        R.ReasonSpec(params=(), texts={RU: "обращение помечено как спам или не по теме"}),
    )
    with pytest.raises(R.ReasonCatalogueError):
        R.validate_catalogue(absent)


def test_catalogue_with_a_lost_placeholder_is_rejected():
    skewed = _catalogue_with(
        R.ReasonCode.CITY_OFF_TARGET,
        R.ReasonSpec(
            params=("city",),
            texts={
                RU: "город вне целевых ({city}) — ступень понижена",
                EN: "city outside the target list — tier lowered",
            },
        ),
    )
    with pytest.raises(R.ReasonCatalogueError):
        R.validate_catalogue(skewed)


def test_catalogue_with_an_extra_placeholder_is_rejected():
    """A text carrying a placeholder that was never declared is rejected."""
    skewed = _catalogue_with(
        R.ReasonCode.ENTITY_INACTIVE,
        R.ReasonSpec(
            params=(),
            texts={RU: "юрлицо {name} неактивно", EN: "entity {name} is not active"},
        ),
    )
    with pytest.raises(R.ReasonCatalogueError):
        R.validate_catalogue(skewed)


def test_catalogue_referring_to_a_unit_without_forms_is_rejected():
    """A template naming a unit with no plural forms is rejected."""
    unknown_noun = _catalogue_with(
        R.ReasonCode.LEI_RENEWAL_SOON,
        R.ReasonSpec(
            params=("days",),
            texts={
                RU: "продление LEI через {days:plural:week}",
                EN: "LEI renewal due in {days:plural:week}",
            },
        ),
    )
    with pytest.raises(R.ReasonCatalogueError):
        R.validate_catalogue(unknown_noun)


def test_catalogue_without_a_code_is_rejected():
    """A gap on the enumeration side: the code exists, the entry does not."""
    incomplete = dict(R.CATALOGUE)
    del incomplete[R.ReasonCode.BUDGET_NAMED]
    with pytest.raises(R.ReasonCatalogueError):
        R.validate_catalogue(incomplete)


def test_the_product_catalogue_passes():
    """Positive control: without it the checks above would pass on an always-no instrument."""
    assert R.validate_catalogue(R.CATALOGUE) == 48




@pytest.mark.parametrize("language", ["de", "ru", "", None, 0])
def test_render_on_an_unknown_language_is_an_error(language):
    """The language is an enum member, never a string, so a typo cannot pick another one."""
    with pytest.raises(R.ReasonError):
        R.render(R.reason(R.ReasonCode.ENTITY_INACTIVE), language)


def test_render_on_a_known_language_works():
    """Positive control for the language check."""
    assert R.render(R.reason(R.ReasonCode.ENTITY_INACTIVE), EN) == "entity is not active"


# Negative controls: Score as the carrier of reasons.


def test_score_with_two_sources_of_text_is_rejected():
    """Reasons given twice, as strings and as codes, are rejected."""
    with pytest.raises(ValueError):
        Score(
            tier=Tier.LOW,
            address_type=AddressType.UNKNOWN,
            event=Event.NONE,
            reasons=("юрлицо неактивно",),
            reason_items=(R.reason(R.ReasonCode.ENTITY_INACTIVE),),
        )


def _stored_card() -> Score:
    """Returns a card restored from storage, where only rendered strings were saved."""
    return Score(
        tier=Tier.LOW,
        address_type=AddressType.UNKNOWN,
        event=Event.NONE,
        reasons=("регистрация LEI просрочена на 10 дней",),
    )


def test_stored_card_cannot_be_rendered_in_english():
    """Strings without codes cannot reach a second language: that is "could not"."""
    with pytest.raises(R.ReasonError):
        _stored_card().reasons_in(EN)


def test_stored_card_still_gives_russian():
    """Positive control: the stored language is still available."""
    assert _stored_card().reasons_in(RU) == ("регистрация LEI просрочена на 10 дней",)


def test_card_without_reasons_at_all_is_an_empty_tuple():
    empty = Score(tier=Tier.LOW, address_type=AddressType.UNKNOWN, event=Event.NONE)
    assert empty.reasons_in(EN) == ()
    assert empty.reasons_in(RU) == ()


def test_score_renders_russian_from_the_items_it_was_given():
    """`reasons` is a rendering of `reason_items`, not separate knowledge."""
    card = Score(
        tier=Tier.MEDIUM,
        address_type=AddressType.UNKNOWN,
        event=Event.NONE,
        reason_items=(R.reason(R.ReasonCode.URGENT_TIMELINE, days=2, limit=60),),
    )
    assert card.reasons == ("срок 2 дня — внутри горячего окна в 60 дней",)
    assert card.reasons_in(RU) == card.reasons
    assert card.reasons_in(EN) == ("needed in 2 days — inside the hot window of 60 days",)


# Engine run: reasons in both languages over live inputs.


@pytest.mark.parametrize("external_id", ["urg-01", "prc-01", "gen-20", "edge-02"])
def test_inbound_seed_rows_render_in_both_languages(external_id):
    """Every reason from the seed renders in both languages; no network needed."""
    rows = {
        row["external_id"]: row
        for row in csv.DictReader((DATA_DIR / "inbound_seed.csv").open(encoding="utf-8"))
    }
    row = rows[external_id]
    message = InboundMessage(
        external_id=external_id,
        channel=row["channel"],
        text=row["text"],
        received_at=date.fromisoformat(row["received_at"]),
        is_synthetic=row["is_synthetic"].strip().lower() == "true",
    )
    result = score_inbound(message, rules_facts(message))
    ru_texts, en_texts = result.reasons_in(RU), result.reasons_in(EN)
    assert ru_texts == result.reasons
    assert len(ru_texts) == len(en_texts) == len(result.reason_items)
    for ru_text, en_text in zip(ru_texts, en_texts, strict=True):
        assert ru_text.strip() and en_text.strip()


def test_registry_companies_render_in_both_languages():
    """The same run from the registry side, over the offline cache."""
    fetched = GleifAdapter(mode="lapsed", offline=True).fetch(60)
    scored = [score(company, TODAY) for company in fetched.companies]
    with_reasons = [result for result in scored if result.reason_items]
    assert with_reasons, "ни одна компания из кэша не дала причин — прогон не состоялся"
    for result in with_reasons:
        assert len(result.reasons_in(EN)) == len(result.reason_items)
        assert all(text.strip() for text in result.reasons_in(EN))


def test_engine_reason_reaches_the_card_in_english():
    result = score(lapsed_company(2), TODAY)
    assert result.reasons == ("регистрация LEI просрочена на 2 дня",)
    assert result.reasons_in(EN) == ("LEI registration lapsed 2 days ago",)
