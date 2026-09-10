"""Тесты детерминированного извлечения фактов (`leadcentre/engine/facts_rules.py`).

Правило, ради которого файл заведён: слово «лицензия» звучит одинаково в «открыть
компанию и получить лицензию» и в «продлить лицензию», поэтому маркер регистрации
зажигался внутри фразы о продлении. Дефект найден сверкой стенда с ответом модели на
urg-13: маркеры давали `[office, renewal, setup]`, модель — `[office, renewal]`, и права
модель — компанию не регистрируют, лицензию продлевают.

Ожидаемое — литералы: наборы типов перечислены значениями `RequestType`, слова
о лицензии выписаны строками. Из `facts_rules` не импортируется ни `TYPE_MARKERS`,
ни `LICENCE_MARKERS`. Сети и модели не требуется: правила детерминированные.
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

# Слова, из-за которых регистрация зажигалась в фразе о продлении. Литералы, а не
# импорт: тест обязан краснеть, если список в модуле изменится.
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


# --- регистрация снимается: слово о лицензии стоит рядом с продлением -------------


def test_pure_renewal_probe_has_no_setup():
    """Зонд: «продлить лицензию компании» — это продление и только оно."""
    assert _types("нужно продлить лицензию компании, что требуется и сколько стоит?") == {
        RequestType.RENEWAL
    }


def test_urg_13_is_office_and_renewal_without_setup():
    """Живой случай дефекта: «our licence expires in 21 days … move to a bigger unit»."""
    message = _seed_message("urg-13")
    assert "licence" in message.text.lower()  # предпосылка, а не вывод теста
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


# --- регистрация остаётся: живой негативный контроль и раздельные предложения -----


def test_urg_08_keeps_setup_because_freezone_fired():
    """Негативный контроль правила: жадное правило съело бы здесь setup.

    В urg-08 просят и продление лицензии, и «fastest freezone for a fintech
    consultancy» — регистрация настоящая, её снимать нельзя.
    """
    message = _seed_message("urg-08")
    low = message.text.lower()
    assert "licence" in low and "freezone" in low  # предпосылки из текста обращения
    assert set(rules_facts(message).request_types) == {
        RequestType.OFFICE,
        RequestType.SETUP,
        RequestType.RENEWAL,
    }


@pytest.mark.parametrize(
    "text",
    [
        # ЕДИНСТВЕННЫЙ маркер регистрации — слово о лицензии, и стоит оно в предложении
        # без продления. Проверку по предложениям сторожат именно эти входы: если в
        # тексте есть ещё «открыть компанию», правило выходит раньше, до неё.
        "нужна лицензия. а когда продлевать её потом?",
        "лицензия нужна; продление тоже интересует",
        "сколько стоит лицензия? и продление сколько?",
    ],
)
def test_licence_in_a_separate_sentence_keeps_setup(text):
    """Слово о лицензии в предложении без продления — речь всё-таки о регистрации."""
    assert _types(text) == {RequestType.SETUP, RequestType.RENEWAL}


def test_licence_and_setup_word_together_keep_setup():
    """Тот же случай, но с явным «открыть компанию»: правило выходит на шаг раньше."""
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
        ("prc-01", {RequestType.SETUP}),        # «скок стоит фриз зона?» — правило не трогает
        ("gen-20", {RequestType.RENEWAL}),      # продление без слова о лицензии
    ],
)
def test_unrelated_messages_are_unchanged(external_id, expected):
    assert set(rules_facts(_seed_message(external_id)).request_types) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Словоформы, ради которых маркеры продления переписаны основами.
        ("продлеваем лицензию, что нужно?", {RequestType.RENEWAL}),
        ("продлеваете ли вы визы?", {RequestType.VISA, RequestType.RENEWAL}),
        ("надо продлевать?", {RequestType.RENEWAL}),
        ("продление лицензии", {RequestType.RENEWAL}),
        ("продлить лицензию, что нужно?", {RequestType.RENEWAL}),
        ("we are renewing our licence next month", {RequestType.RENEWAL}),
        ("licence renewals", {RequestType.RENEWAL}),
        ("licence expiring soon", {RequestType.RENEWAL}),
        ("лицензия истекла в июле", {RequestType.RENEWAL}),
        # Негативный контроль основ: регистрация от них не зажигается.
        ("хотим открыть компанию во фризоне", {RequestType.SETUP}),
    ],
)
def test_renewal_word_forms_are_covered_by_stems(text, expected):
    """Маркеры продления — основы, а не целые слова: «продлева», «продли», «renew».

    Дыра на «продлеваем» была заперта отдельным тестом с пометкой ИЗМЕРЕНО и закрыта;
    здесь заперты уже покрытые словоформы, чтобы возврат к целым словам был заметен.
    """
    assert _types(text) == expected


def test_setup_without_renewal_is_untouched():
    """Правило включается только при паре setup+renewal, иначе оно вообще не при делах."""
    assert _types("хотим лицензию и открыть компанию") == {RequestType.SETUP}
    assert _types("нужна licence для новой компании") == {RequestType.SETUP}


# --- инвариант по всему набору: ловим беду, а не её случай ------------------------


def _without_licence_words(text: str) -> str:
    """Тот же текст без слов о лицензии: чем ещё держится регистрация, видно сразу."""
    pattern = "|".join(re.escape(word) for word in LICENCE_WORDS)
    return re.sub(pattern, " ", text, flags=re.IGNORECASE)


def test_no_seed_message_gets_setup_only_from_a_licence_word_next_to_renewal():
    """По всем 70 обращениям: если регистрация держится ТОЛЬКО на слове о лицензии,

    а рядом просят продление — такой пары быть не должно. Тест написан про саму беду:
    он переживёт изменение списков маркеров, а перечень «urg-13, urg-08» — не пережил бы.
    ИЗМЕРЕНО 2026-09-09: нарушений 0, при этом пар setup+renewal в наборе 1 (urg-08),
    то есть проверять было что.
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


# --- закрытые дыры: словоформы, которые теперь ловятся ---------------------------
#
# ИЗМЕРЕНО 2026-09-09 после правки маркеров. Заперты, чтобы откат был заметен.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("зарегистрировать компанию", {RequestType.SETUP}),
        ("открытие компании", {RequestType.SETUP}),
        ("incorporation", {RequestType.SETUP}),
        ("рабочее место одно", {RequestType.OFFICE}),
        ("переговорная", {RequestType.OFFICE}),
        ("ведём бухучёт", {RequestType.ACCOUNTING}),
        ("ведем бухучет", {RequestType.ACCOUNTING}),   # и без ё
        ("открыть счёт", {RequestType.BANK}),
        ("открыть счет", {RequestType.BANK}),          # и без ё
        ("открытие счёта", {RequestType.BANK}),
        ("open an account", {RequestType.BANK}),
        ("current account", {RequestType.BANK}),
        ("срок действия истёк", {RequestType.RENEWAL}),
        ("лицензия истекла", {RequestType.RENEWAL}),
    ],
)
def test_closed_marker_gaps_are_covered(text, expected):
    """Формы, которых маркеры раньше не ловили, теперь дают верный тип."""
    assert _types(text) == expected


# --- шум вокруг закрытых дыр: цена, заплаченная за покрытие ----------------------


@pytest.mark.parametrize(
    ("text", "wrong"),
    [
        # «счёт» без банка ловится и там, где речь не о банковском счёте.
        ("открыть счёт в ресторане", RequestType.BANK),
        ("open an account on your website", RequestType.BANK),
        # «переговорн» и «рабочее место» — там, где речь не об аренде.
        ("переговорная комната в отеле", RequestType.OFFICE),
        ("рабочее место дома", RequestType.OFFICE),
        # «зарегистр» ловит любую регистрацию, не только компании.
        ("зарегистрироваться на вебинар", RequestType.SETUP),
        ("зарегистрировать домен", RequestType.SETUP),
    ],
)
def test_noise_introduced_by_closing_the_gaps_is_recorded(text, wrong):
    """Негативный контроль закрытых дыр со стороны шума: ИЗМЕРЕНО 2026-09-09.

    Шесть фраз, которых в канале SORP почти не бывает, теперь получают лишний тип.
    На наборе из 70 обращений это не изменило ни одного приоритета — распределение
    осталось 13 HIGH / 41 MEDIUM / 16 LOW. Заниматься этим не стоит: сузить «счёт»
    до «счёт в банке» значит вернуть исходную дыру, ради которой правка и делалась,
    а лишний тип в карточке менеджер поправит за секунду — пропущенный запрос дороже.
    """
    assert wrong in _types(text)


def test_words_around_the_closed_gaps_that_stay_silent():
    """Не всякое «счёт» зажигает банк — и это тоже измерено, а не предположено."""
    assert _types("закройте счёт, пожалуйста") == set()
    assert _types("счёт на оплату пришлите") == set()
    assert _types("инвойс и счёт-фактура") == set()


# --- известные дыры в маркерах: заперты, а не замолчаны --------------------------
#
# ИЗМЕРЕНО 2026-09-09 прогоном словоформ по всем TYPE_MARKERS. Тесты фиксируют
# ТЕКУЩЕЕ поведение, а не желаемое: пока владелец `facts_rules.py` не решил, что
# чинить, дыра должна быть видна в тестах, а её закрытие — краснить сборку.
# Это тот же приём, что сработал с «продлеваем»: заперто → починено → тест покраснел.


@pytest.mark.parametrize(
    ("text", "missing", "current"),
    [
        # НЕ ЧИНИМ ОСОЗНАННО, причина у каждой строки своя:
        # основа "set up" поймала бы «set up a meeting» и «settings» — шума больше,
        # чем пользы, а по-английски о регистрации чаще пишут «company formation».
        ("company set up", RequestType.SETUP, set()),
        ("setting up a company", RequestType.SETUP, set()),
        # «продлёнка» — это школьная продлёнка, а не продление лицензии.
        ("продлёнка", RequestType.RENEWAL, set()),
        # «открыл бы компанию» — сослагательная форма; основа "открыл" зажигалась бы
        # на «открыл счёт», «открыл дверь», а сам оборот в канале почти не встречается.
        ("открыл бы компанию в оаэ", RequestType.SETUP, set()),
    ],
)
def test_known_marker_gaps_are_recorded(text, missing, current):
    """Словоформа не ловится маркерами — и это решение, а не забывчивость.

    Причина по каждой строке — в комментарии рядом с ней. Тест фиксирует ТЕКУЩЕЕ
    поведение: если кто-то решит дыру закрыть, сборка покраснеет и решение придётся
    принять заново, а не молча.
    """
    assert _types(text) == current
    assert missing not in _types(text)


@pytest.mark.parametrize(
    ("text", "wrong"),
    [
        # Короткие основы ловят чужие слова. Решение владельца от 2026-09-09: оставить,
        # причина — в докстроке теста.
        ("купите телевизор недорого", RequestType.VISA),
        ("приходил ваш визит-менеджер", RequestType.VISA),
        ("процедура банкротства компании", RequestType.BANK),
        ("аудитория подписчиков", RequestType.ACCOUNTING),
        ("my desk job", RequestType.OFFICE),
        ("desktop приложение", RequestType.OFFICE),
    ],
)
def test_known_false_positives_are_recorded(text, wrong):
    """Ложные срабатывания коротких основ — ОСОЗНАННОЕ решение, а не недосмотр.

    Сужать основу не будем: «виз» → «виза» убирает телевизор ценой «визами» и «визы».
    Здесь ложное срабатывание дешевле пропуска — лишний тип в карточке менеджер
    поправит за секунду, а пропущенный запрос про визы стоит лида. То же и с «банк»
    в «банкротстве», «аудит» в «аудитории», «desk» в «desktop».
    Тест фиксирует текущее поведение: сужение основы покраснит сборку и решение
    придётся принять заново.
    """
    assert wrong in _types(text)
