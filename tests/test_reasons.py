"""Тесты каталога причин: полнота, согласование числительных, негативные контроли.

Перенесено из приёмочного прибора автора модуля (scratchpad/check_reasons.py): там оно
печатало таблицу и считало сработавшие контроли, здесь каждый контроль — отдельный тест,
который краснеет сам. Печатать «ГОДНО» рядом с зелёным прогоном больше не требуется:
вердикт выносит раннер, а не тот, кто делал.

Что здесь сторожится:

* **Полнота каталога** (`test_every_code_has_a_text_in_every_language` и соседи): у
  каждого кода обязан быть текст на КАЖДОМ языке `Language`, и текст обязан подставлять
  ровно объявленные параметры. Тест идёт по всем членам обоих перечислений, поэтому
  новый код или новый язык разбудят его сами — тот же приём, что поймал `RequestType.RENEWAL`
  в tests/test_enum_coverage.py.
* **Числительные**: «1 день / 2 дня / 5 дней», отдельно край 11–14 («11 дней», не
  «11 день») — правило, которое ломается тише всего.
* **Негативные контроли**: отсутствующий параметр, лишний параметр, дробное число,
  неизвестный язык, пустой и отсутствующий текст, потерянный placeholder, единица без
  форм. Рядом с каждым — положительный контроль: на годном входе прибор обязан
  шевельнуться, иначе «ошибка есть всегда» читалось бы как успех.

Ожидаемое — литералы: русские и английские строки выписаны руками, а не собраны
тем же кодом, который проверяется.
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

# Показательные параметры каждой причины: ими каталог отрисовывается целиком.
# Числа подобраны так, чтобы в русском была видна форма, отличная от «дней».
# Код без записи здесь роняет `test_sample_params_cover_every_code` — иначе новая
# причина осталась бы неотрисованной ни разу.
SAMPLE_PARAMS: dict[R.ReasonCode, dict[str, object]] = {
    R.ReasonCode.LEI_LAPSED_FRESH: {"days": 41},
    R.ReasonCode.LEI_LAPSED_LONG_AGO: {"days": 122},
    R.ReasonCode.ENTITY_RECENTLY_CREATED: {"days": 12},
    R.ReasonCode.LEI_RENEWAL_SOON: {"days": 30},
    R.ReasonCode.CITY_OFF_TARGET: {"city": "Абу-Даби"},
    R.ReasonCode.CITY_NOT_SET: {},
    R.ReasonCode.ENTITY_INACTIVE: {},
    R.ReasonCode.SPAM_OR_OFF_TOPIC: {},
    R.ReasonCode.NO_REQUEST_TYPE: {},
    R.ReasonCode.URGENT_TIMELINE: {"days": 14, "limit": 60},
    R.ReasonCode.PACKAGE_REQUEST: {"count": 2},
    R.ReasonCode.TEAM_OVER_FLEXI_QUOTA: {"headcount": 8},
    R.ReasonCode.BUDGET_NAMED: {"budget": "20 000 AED в год"},
    R.ReasonCode.TARGET_LANGUAGE: {"language": "ru"},
    R.ReasonCode.TARGET_LANGUAGE_ALONE: {"language": "ru"},
    R.ReasonCode.LOW_CONFIDENCE: {"confidence": 0.30, "threshold": 0.5},
}

ALL_CODES = list(R.ReasonCode)
ALL_LANGUAGES = list(R.Language)


def _placeholders(template: str) -> set[str]:
    """Имена параметров шаблона. Разбор здесь свой, а не импорт `R._placeholders`:
    импортированное ожидание поедет вместе с кодом и промолчит."""
    import string

    return {
        name.split(".")[0].split("[")[0]
        for _, name, _, _ in string.Formatter().parse(template)
        if name
    }


# --- полнота каталога ------------------------------------------------------------


def test_sample_params_cover_every_code():
    """Прибор измеряет весь каталог, а не его половину («проверено N»)."""
    missing = sorted(set(R.ReasonCode) - set(SAMPLE_PARAMS), key=lambda c: c.value)
    assert missing == [], f"нет показательных параметров для {[c.value for c in missing]}"
    assert len(SAMPLE_PARAMS) == len(ALL_CODES) == 16


def test_catalogue_declares_every_code():
    """Код без записи в каталоге — дыра: `render` упал бы на живой карточке."""
    assert set(R.CATALOGUE) == set(R.ReasonCode)


@pytest.mark.parametrize("code", ALL_CODES, ids=lambda c: c.value)
@pytest.mark.parametrize("language", ALL_LANGUAGES, ids=lambda language: language.value)
def test_every_code_has_a_text_in_every_language(code, language):
    """Каталог не бывает переведённым наполовину: текст обязан быть на каждом языке.

    Именно этот тест не даст выпустить причину, у которой есть русский и нет
    английского, — на английском экране такая карточка была бы пустым местом.
    """
    template = R.CATALOGUE[code].texts.get(language)
    assert template, f"нет текста {language.value} у причины {code.value}"
    assert template.strip(), f"пустой текст {language.value} у причины {code.value}"


@pytest.mark.parametrize("code", ALL_CODES, ids=lambda c: c.value)
@pytest.mark.parametrize("language", ALL_LANGUAGES, ids=lambda language: language.value)
def test_every_text_uses_exactly_the_declared_params(code, language):
    """Текст подставляет ровно объявленные параметры — ни больше, ни меньше.

    Лишний placeholder — `ReasonRenderError` на живом входе, потерянный — молча
    съеденное число («срочность» без срока), и второе хуже.
    """
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
    """Отрисовка проходит на каждом коде и языке и даёт непустой текст."""
    text = R.reason(code, **SAMPLE_PARAMS[code]).text(language)
    assert text.strip()
    assert "{" not in text, f"неподставленный placeholder в {code.value}/{language.value}"


@pytest.mark.parametrize("language", ALL_LANGUAGES, ids=lambda language: language.value)
def test_every_language_has_plural_forms_and_a_rule(language):
    """Новый язык без форм числительных и без правила выбора формы — IndexError в проде."""
    assert language in R.PLURAL_RULES, f"нет правила числительных для {language.value}"
    assert language in R.PLURAL_FORMS, f"нет форм числительных для {language.value}"
    # Единицы совпадают во всех языках: шаблон ссылается на единицу, а не на язык.
    assert set(R.PLURAL_FORMS[language]) == {"day", "person", "service", "char"}


def test_validate_catalogue_reports_how_much_it_checked():
    """«Проверено N» вместо голого «нарушений нет»: 16 кодов x 2 языка."""
    assert R.validate_catalogue() == 32


def test_catalogue_has_no_two_codes_with_the_same_russian_text():
    """Два кода с одинаковым текстом — признак того, что различие потерялось."""
    texts = [spec.texts[RU] for spec in R.CATALOGUE.values()]
    assert len(set(texts)) == len(texts), "русские тексты причин повторяются"


# --- числительные ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (0, "0 дней"),
        (1, "1 день"),
        (2, "2 дня"),
        (4, "4 дня"),
        (5, "5 дней"),
        (11, "11 дней"),   # край guard 11-14: не «11 день»
        (12, "12 дней"),   # не «12 дня»
        (14, "14 дней"),
        (15, "15 дней"),
        (21, "21 день"),
        (22, "22 дня"),
        (25, "25 дней"),
        (41, "41 день"),
        (101, "101 день"),
        (102, "102 дня"),
        (105, "105 дней"),
        (111, "111 дней"),  # тот же guard на второй сотне
        (112, "112 дней"),
    ],
)
def test_russian_numerals_agree_with_the_noun(number, expected):
    """Три формы русского, с обоими краями и серединой.

    11-14 — исключение из правила «оканчивается на 1 — форма один»: без guard
    получится «11 день», и это ровно та ошибка, которую не видно на глаз в тесте,
    где числа только однозначные.
    """
    assert R.plural_phrase(RU, number, "day") == expected


@pytest.mark.parametrize(
    ("number", "expected"),
    [(0, "0 people"), (1, "1 person"), (2, "2 people"), (11, "11 people")],
)
def test_english_numerals_have_two_forms(number, expected):
    assert R.plural_phrase(EN, number, "person") == expected


def test_russian_and_english_forms_differ_on_the_same_number():
    """Негативный контроль прибора: если бы отрисовка не знала про язык, тексты
    совпали бы, и все проверки выше проходили бы, ничего не измеряя."""
    assert R.plural_phrase(RU, 5, "day") != R.plural_phrase(EN, 5, "day")


def test_numeral_text_reaches_the_rendered_reason():
    """Согласование видно в самой причине, а не только в помощнике."""
    assert R.reason(R.ReasonCode.LEI_RENEWAL_SOON, days=11).text(RU) == (
        "продление LEI через 11 дней"
    )
    assert R.reason(R.ReasonCode.LEI_RENEWAL_SOON, days=21).text(RU) == (
        "продление LEI через 21 день"
    )
    assert R.reason(R.ReasonCode.LEI_RENEWAL_SOON, days=1).text(EN) == (
        "LEI renewal due in 1 day"
    )


# --- негативные контроли: сборка причины ------------------------------------------


def test_reason_without_params_is_an_error():
    """Контроль 1: код, у которого объявлен параметр, без параметра не собирается."""
    with pytest.raises(R.ReasonError):
        R.reason(R.ReasonCode.TEAM_OVER_FLEXI_QUOTA)


def test_reason_without_the_second_param_is_an_error():
    """Контроль 2: половина параметров — тоже «не смогли», а не «сойдёт»."""
    with pytest.raises(R.ReasonError):
        R.reason(R.ReasonCode.URGENT_TIMELINE, days=14)


def test_reason_with_an_extra_param_is_an_error():
    """Контроль 3: лишний параметр — признак того, что автор перепутал код."""
    with pytest.raises(R.ReasonError):
        R.reason(R.ReasonCode.ENTITY_INACTIVE, days=3)


def test_reason_with_params_stripped_behind_the_factory_is_an_error():
    """Контроль 4: параметры выпилены в обход фабрики — падение всё равно.

    Обход намеренный: фабрика проверяет вход, но причина может приехать из хранилища
    или из `replace`, и там некому вставить проверку, кроме самой причины.
    """
    good = R.reason(R.ReasonCode.LEI_RENEWAL_SOON, days=5)
    with pytest.raises(R.ReasonError):
        R.render(replace(good, params=()), RU)


def test_reason_with_a_duplicate_param_is_an_error():
    """Тот же обход, но параметр задан дважды: молча победившее значение — дефект."""
    with pytest.raises(R.ReasonError):
        R.Reason(R.ReasonCode.LEI_RENEWAL_SOON, (("days", 5), ("days", 9)))


def test_the_same_reason_with_params_renders():
    """Положительный контроль к контролям 1-4: годный вход обязан пройти."""
    assert R.reason(R.ReasonCode.TEAM_OVER_FLEXI_QUOTA, headcount=8).text(EN) == (
        "team of 8 — flexi desk will not cover the visa quota"
    )


# --- негативные контроли: согласование числа --------------------------------------


def test_fractional_number_cannot_be_agreed():
    """Контроль 5: «2.5 день» напечатать нельзя, и молчать об этом тоже нельзя."""
    with pytest.raises(R.ReasonError):
        R.plural_phrase(RU, 2.5, "day")


@pytest.mark.parametrize("value", ["5", None, True, 3.0])
def test_non_integer_number_cannot_be_agreed(value):
    """Тот же контроль шире: строка, None, bool и целое-как-float — не число для формы.

    `True` отдельным случаем: в Python это `int`, и без явной проверки вышло бы
    «True день».
    """
    with pytest.raises(R.ReasonError):
        R.plural_phrase(RU, value, "day")


def test_unknown_noun_has_no_forms():
    """Единица, форм которой нет, — ошибка, а не «14 week»."""
    with pytest.raises(R.ReasonError):
        R.plural_phrase(RU, 14, "week")


def test_integer_number_is_agreed():
    """Положительный контроль к контролям про число."""
    assert R.plural_phrase(RU, 2, "day") == "2 дня"


# --- негативные контроли: дырявый каталог -----------------------------------------


def _catalogue_with(code: R.ReasonCode, spec: R.ReasonSpec) -> dict:
    """Копия каталога продукта с одной подменённой причиной.

    Подменяется копия, а не `R.CATALOGUE`: тест, портящий глобальный каталог,
    покрасил бы соседей и вердикт был бы не про то.
    """
    holed = dict(R.CATALOGUE)
    holed[code] = spec
    return holed


def test_catalogue_without_the_english_text_is_rejected():
    """Контроль 6: причина с параметром, у которой пропал английский текст."""
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
    """Контроль 7: текст из пробелов.

    Причина без параметров выбрана намеренно: пустоту тут ловит только правило
    пустоты — сверка placeholder'ов подстраховать его не может, и контроль мерит
    ровно то, что называет.
    """
    blank = _catalogue_with(
        R.ReasonCode.ENTITY_INACTIVE,
        R.ReasonSpec(params=(), texts={RU: "юрлицо неактивно", EN: "   "}),
    )
    with pytest.raises(R.ReasonCatalogueError):
        R.validate_catalogue(blank)


def test_catalogue_without_the_english_text_of_a_paramless_reason_is_rejected():
    """Контроль 8: то же отсутствие, но у причины без параметров."""
    absent = _catalogue_with(
        R.ReasonCode.SPAM_OR_OFF_TOPIC,
        R.ReasonSpec(params=(), texts={RU: "обращение помечено как спам или не по теме"}),
    )
    with pytest.raises(R.ReasonCatalogueError):
        R.validate_catalogue(absent)


def test_catalogue_with_a_lost_placeholder_is_rejected():
    """Контроль 9: английский перевод потерял `{city}` — город из карточки пропал бы."""
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
    """Обратная сторона контроля 9: в тексте есть то, чего не объявляли."""
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
    """Контроль 10: `{days:plural:week}` — форм недели нет ни в одном языке."""
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
    """Дыра со стороны перечисления: код есть, записи в каталоге нет."""
    incomplete = dict(R.CATALOGUE)
    del incomplete[R.ReasonCode.BUDGET_NAMED]
    with pytest.raises(R.ReasonCatalogueError):
        R.validate_catalogue(incomplete)


def test_the_product_catalogue_passes():
    """Положительный контроль к контролям 6-10: каталог продукта проходит целиком.

    Без него все проверки выше зеленели бы и на приборе, который всегда говорит «нет».
    """
    assert R.validate_catalogue(R.CATALOGUE) == 32


# --- негативные контроли: язык отрисовки ------------------------------------------


@pytest.mark.parametrize("language", ["de", "ru", "", None, 0])
def test_render_on_an_unknown_language_is_an_error(language):
    """Контроль 11: язык — член перечисления, а не строка. «ru» строкой тоже нельзя:
    иначе опечатка в вызове тихо вернула бы текст не того языка."""
    with pytest.raises(R.ReasonError):
        R.render(R.reason(R.ReasonCode.ENTITY_INACTIVE), language)


def test_render_on_a_known_language_works():
    """Положительный контроль к контролю 11."""
    assert R.render(R.reason(R.ReasonCode.ENTITY_INACTIVE), EN) == "entity is not active"


# --- негативные контроли: Score как носитель причин -------------------------------


def test_score_with_two_sources_of_text_is_rejected():
    """Контроль 12: причины заданы дважды — строками и кодами.

    Два источника текста — это тот дефект, ради которого каталог и заведён;
    `Score` не даёт завести его заново.
    """
    with pytest.raises(ValueError):
        Score(
            tier=Tier.LOW,
            address_type=AddressType.UNKNOWN,
            event=Event.NONE,
            reasons=("юрлицо неактивно",),
            reason_items=(R.reason(R.ReasonCode.ENTITY_INACTIVE),),
        )


def _stored_card() -> Score:
    """Карточка, поднятая из хранилища: там сохранены только русские строки."""
    return Score(
        tier=Tier.LOW,
        address_type=AddressType.UNKNOWN,
        event=Event.NONE,
        reasons=("регистрация LEI просрочена на 10 дней",),
    )


def test_stored_card_cannot_be_rendered_in_english():
    """Контроль 13: строк без кодов не хватает на второй язык — это «не смогли»,
    а не молчаливая подмена русским текстом на английском экране."""
    with pytest.raises(R.ReasonError):
        _stored_card().reasons_in(EN)


def test_stored_card_still_gives_russian():
    """Положительный контроль к контролю 13: язык хранения по-прежнему доступен."""
    assert _stored_card().reasons_in(RU) == ("регистрация LEI просрочена на 10 дней",)


def test_card_without_reasons_at_all_is_an_empty_tuple():
    """Третий исход `reasons_in`: причин нет вовсе — пусто, и это не ошибка."""
    empty = Score(tier=Tier.LOW, address_type=AddressType.UNKNOWN, event=Event.NONE)
    assert empty.reasons_in(EN) == ()
    assert empty.reasons_in(RU) == ()


def test_score_renders_russian_from_the_items_it_was_given():
    """`reasons` — это отрисовка `reason_items`, а не отдельное знание."""
    card = Score(
        tier=Tier.MEDIUM,
        address_type=AddressType.UNKNOWN,
        event=Event.NONE,
        reason_items=(R.reason(R.ReasonCode.URGENT_TIMELINE, days=2, limit=60),),
    )
    assert card.reasons == ("срок 2 дня — внутри горячего окна в 60 дней",)
    assert card.reasons_in(RU) == card.reasons
    assert card.reasons_in(EN) == ("needed in 2 days — inside the hot window of 60 days",)


# --- прогон движка: причины обоих языков на живых входах ---------------------------


@pytest.mark.parametrize("external_id", ["urg-01", "prc-01", "gen-20", "edge-02"])
def test_inbound_seed_rows_render_in_both_languages(external_id):
    """Прогон по обращениям из посева: каждая причина рисуется на обоих языках.

    Сеть не нужна — CSV лежит в репозитории.
    """
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
    """Тот же прогон со стороны реестра: офлайн-кэш GLEIF, причин хотя бы у одной."""
    fetched = GleifAdapter(mode="lapsed", offline=True).fetch(60)
    scored = [score(company, TODAY) for company in fetched.companies]
    with_reasons = [result for result in scored if result.reason_items]
    assert with_reasons, "ни одна компания из кэша не дала причин — прогон не состоялся"
    for result in with_reasons:
        assert len(result.reasons_in(EN)) == len(result.reason_items)
        assert all(text.strip() for text in result.reasons_in(EN))


def test_engine_reason_reaches_the_card_in_english():
    """Точечно и литералом: просрочка на 2 дня доезжает до английской карточки."""
    result = score(lapsed_company(2), TODAY)
    assert result.reasons == ("регистрация LEI просрочена на 2 дня",)
    assert result.reasons_in(EN) == ("LEI registration lapsed 2 days ago",)
