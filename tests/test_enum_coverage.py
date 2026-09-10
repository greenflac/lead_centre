"""Тесты полноты покрытия перечислений: беда вместо случая.

Дефект, ради которого файл заведён: в `RequestType` появился `RENEWAL`, а в словарях
`reply.py` его не завели — черновик падал с `KeyError` на обращении о продлении.
Покейсовые тесты этого не заметили: они перечисляют типы поимённо, и добавление члена
в перечисление никого не разбудило.

Приём тот же, что уже сработал дважды (состав выдачи поймал инфляцию HIGH, инвариант
про арабские названия — беду вместо случая): тест идёт по ВСЕМ членам перечисления,
поэтому следующий новый тип разбудит его сам, без правки теста.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from leadcentre.engine import reply
from leadcentre.engine.facts_rules import TYPE_MARKERS
from leadcentre.engine.rubric import MATRIX
from leadcentre.models import (
    AddressType,
    Event,
    InboundMessage,
    LeadFacts,
    RequestType,
    Tier,
)

ROOT = Path(__file__).resolve().parents[1]
PRICELIST = ROOT / "data" / "pricelist_demo.yaml"

# Языки, на которых движок обязан отвечать шаблонами. Литералы (Т2): список в модуле
# может измениться, и тест должен это заметить, а не поехать следом.
TEMPLATE_LANGUAGES = ("ru", "en")

# Исходы, объявленные автором reply.py. Падение исключением исходом не является (Р1).
DECLARED_OUTCOMES = ("draft", "questions", "spam_skipped", "no_draft_needs_human")


# Язык определяется по письменности самого обращения, поэтому текст берётся под язык:
# иначе тест проверял бы не полноту шаблонов, а работу определителя языка.
MESSAGE_TEXT = {
    "ru": "нужно продлить лицензию, 8 человек, бюджет 40000 AED",
    "en": "we need to renew the licence, 8 people, budget 40000 AED",
}


def _message(text: str = MESSAGE_TEXT["ru"]):
    from datetime import date

    return InboundMessage(
        external_id="cov", channel="form", text=text, received_at=date(2026, 9, 9)
    )


def _rich_facts(kind: RequestType, language: str) -> LeadFacts:
    """Факты, при которых генератор идёт в ветку черновика, а не в вопросы."""
    return LeadFacts(
        request_types=(kind, RequestType.OFFICE) if kind is not RequestType.OFFICE else (kind,),
        headcount=8,
        timeline_days=10,
        budget_hint="40000 AED",
        language=language,
        has_contact=True,
        confidence=0.9,
        quotes=("продлить лицензию", "8 человек"),
    )


# --- полнота: draft отрабатывает на каждом члене RequestType ---------------------


@pytest.mark.parametrize("kind", list(RequestType), ids=lambda k: k.value)
@pytest.mark.parametrize("language", TEMPLATE_LANGUAGES)
def test_draft_survives_every_request_type(kind, language):
    """Ни один тип запроса не обрушивает карточку — проверяются ВСЕ члены перечисления.

    Именно этот тест поймал бы `RENEWAL`, добавленный в `RequestType` без правки
    словарей `reply.py`: черновик падал там с `KeyError`.
    """
    message = _message(MESSAGE_TEXT[language])
    result = reply.draft(message, _rich_facts(kind, language), Tier.HIGH)
    assert result.outcome in DECLARED_OUTCOMES
    assert result.language == language


@pytest.mark.parametrize("kind", list(RequestType), ids=lambda k: k.value)
def test_draft_survives_a_lonely_request_type(kind):
    """Тот же перебор, но тип в обращении один: вторая ветка сборки текста."""
    facts = LeadFacts(
        request_types=(kind,),
        headcount=8,
        timeline_days=10,
        budget_hint="40000 AED",
        language="ru",
        has_contact=True,
        confidence=0.9,
        quotes=("продлить лицензию",),
    )
    assert reply.draft(_message(), facts, Tier.HIGH).outcome in DECLARED_OUTCOMES


@pytest.mark.parametrize("kind", list(RequestType), ids=lambda k: k.value)
def test_reply_dictionaries_cover_every_request_type(kind):
    """Словари шаблонов и прайс-ключей заведены на каждый член перечисления."""
    assert kind in reply.SUBSTANCE, f"нет шаблона для {kind.value}"
    assert kind in reply.PRICE_KEY_BY_REQUEST, f"нет прайс-ключей для {kind.value}"


@pytest.mark.parametrize("kind", list(RequestType), ids=lambda k: k.value)
@pytest.mark.parametrize("language", TEMPLATE_LANGUAGES)
def test_substance_text_exists_for_every_language(kind, language):
    assert reply.SUBSTANCE[kind].get(language), f"нет текста {language} для {kind.value}"


# --- полнота со стороны прайса ---------------------------------------------------


def _pricelist_keys() -> set[str]:
    """Ключи позиций из YAML. Разбор простой: файл плоский, ключи на одном отступе."""
    text = PRICELIST.read_text(encoding="utf-8")
    items = text.split("items:", 1)[1]
    return set(re.findall(r"^  ([a-z0-9_]+):", items, flags=re.MULTILINE))


def test_pricelist_has_an_item_for_every_declared_price_key():
    """Каждый ключ из `PRICE_KEY_BY_REQUEST` обязан быть позицией в прайсе.

    Иначе черновик назовёт услугу, а линтер не сможет сверить число: получится цена,
    за которой ничего не стоит.
    """
    declared = {key for keys in reply.PRICE_KEY_BY_REQUEST.values() for key in keys}
    missing = sorted(declared - _pricelist_keys())
    assert missing == [], f"ключи без позиции в прайсе: {missing}"
    assert len(declared) >= 5, "объявленных ключей подозрительно мало — проверять нечего"


def test_loaded_prices_match_the_declared_keys():
    """Тот же контроль, но через загрузчик: YAML читается, ключи совпадают."""
    prices = reply.load_prices(PRICELIST)
    declared = {key for keys in reply.PRICE_KEY_BY_REQUEST.values() for key in keys}
    assert declared <= set(prices), sorted(declared - set(prices))


@pytest.mark.parametrize("kind", list(RequestType), ids=lambda k: k.value)
def test_types_with_price_keys_can_be_priced(kind):
    """Если для типа объявлены ключи — все они грузятся из прайса (или ключей нет вовсе)."""
    prices = reply.load_prices(PRICELIST)
    for key in reply.PRICE_KEY_BY_REQUEST[kind]:
        assert key in prices, f"{kind.value}: ключ {key} отсутствует в прайсе"


# --- негативный контроль: типа нет в шаблонах вовсе ------------------------------


def test_missing_template_is_a_declared_outcome_not_an_exception(monkeypatch):
    """Негативный контроль (И5/Р1): если шаблона для типа нет, это исход, а не падение.

    Тип выбрасывается из словарей на время теста — так воспроизводится ровно то
    состояние, в котором `RENEWAL` обрушивал карточку.
    """
    substance = dict(reply.SUBSTANCE)
    price_keys = dict(reply.PRICE_KEY_BY_REQUEST)
    substance.pop(RequestType.OFFICE)
    price_keys.pop(RequestType.OFFICE)
    monkeypatch.setattr(reply, "SUBSTANCE", substance)
    monkeypatch.setattr(reply, "PRICE_KEY_BY_REQUEST", price_keys)

    result = reply.draft(_message(), _rich_facts(RequestType.OFFICE, "ru"), Tier.HIGH)
    # Заявленный автором исход: говорить не о чем — карточку берёт человек (Р1),
    # а не KeyError и не молчаливый пустой черновик.
    assert result.outcome == "no_draft_needs_human"
    assert result.needs_human is True
    assert result.body == ""


def test_unknown_request_type_value_does_not_crash(monkeypatch):
    """Ещё жёстче: в фактах тип, которого в шаблонах нет ни под каким видом."""

    class FakeType(str):
        value = "quantum_consulting"

    facts = LeadFacts(
        request_types=(FakeType("quantum_consulting"),),
        headcount=8,
        language="ru",
        has_contact=True,
        confidence=0.9,
        quotes=("тест",),
    )
    result = reply.draft(_message(), facts, Tier.HIGH)
    assert result.outcome == "no_draft_needs_human"
    assert result.needs_human is True


def test_unknown_type_alongside_a_known_one_is_skipped_with_a_notice(monkeypatch):
    """Один незнакомый тип не отменяет черновик: он пропускается, и пропуск виден (Е3).

    Проверяется вместе со знакомым типом — иначе исход был бы NO_DRAFT и мы бы
    не увидели ни черновика, ни пометки.
    """
    substance = dict(reply.SUBSTANCE)
    substance.pop(RequestType.VISA)
    monkeypatch.setattr(reply, "SUBSTANCE", substance)

    facts = LeadFacts(
        request_types=(RequestType.OFFICE, RequestType.VISA),
        headcount=8,
        timeline_days=10,
        budget_hint="40000 AED",
        language="ru",
        has_contact=True,
        confidence=0.9,
        quotes=("8 человек",),
    )
    result = reply.draft(_message(), facts, Tier.HIGH)
    assert result.outcome == "draft"
    assert result.body  # черновик собрался на знакомом типе
    assert "visa" in result.notice, result.notice


# --- полнота прочих словарей по перечислениям ------------------------------------


def test_matrix_covers_every_address_and_event_combination():
    """Рубрика A x B: 4 x 4 = 16 ячеек, дырок нет — иначе скоринг упал бы с KeyError."""
    combinations = {(a, e) for a in AddressType for e in Event}
    assert set(MATRIX) == combinations
    assert len(MATRIX) == 16


def test_type_markers_cover_every_request_type_except_other():
    """`TYPE_MARKERS` покрывает все типы, кроме OTHER, и это осознанное исключение.

    OTHER — запасной тип «ничего не распознали», у него не может быть своих маркеров.
    Если появится новый тип без маркеров, тест разбудит: список исключений — литерал.
    """
    covered = set(TYPE_MARKERS)
    expected = set(RequestType) - {RequestType.OTHER}
    assert covered == expected, {
        "нет маркеров": sorted(k.value for k in expected - covered),
        "лишние": sorted(k.value for k in covered - expected),
    }


# --- одно знание — одно место (Е1) -----------------------------------------------


GEN_MOCK = ROOT / "web" / "scripts" / "gen_mock.py"


def test_mock_generator_uses_the_shared_facts_rules():
    """Генератор демо-данных обязан звать общую реализацию, а не свою копию.

    Своя копия правил уже разошлась однажды: дашборд показывал 13/37/20, а стенд
    считал 12/41/17. Тест сторожит, чтобы копия не завелась заново.
    """
    source = GEN_MOCK.read_text(encoding="utf-8")
    assert "from leadcentre.engine.facts_rules import rules_facts" in source
    assert "rules_facts(" in source


def test_mock_generator_keeps_no_private_copy_of_the_rules():
    """Генератор демонстрационных данных не заводит свою копию правил.

    Раньше в gen_mock.py лежал словарь REQUEST_MARKERS — остаток прежней копии эвристики,
    из-за которой дашборд показывал 13/37/20, а измерительный стенд считал 12/41/17.
    Словарь удалён вместе с самой копией; тест сторожит от повторного форка: генератор
    обязан звать общую rules_facts и не должен заводить своих словарей маркеров.
    """
    source = GEN_MOCK.read_text(encoding="utf-8")
    assert "rules_facts" in source, "генератор перестал звать общую реализацию правил"
    for forked in ("REQUEST_MARKERS", "TYPE_MARKERS", "BUDGET_MARKERS", "SPAM_MARKERS"):
        assert forked not in source, f"в генераторе снова заведён свой словарь {forked}"
