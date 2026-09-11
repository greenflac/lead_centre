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

    Шесть фраз, которых в клиентском канале почти не бывает, теперь получают лишний тип.
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


# --- размер команды: одно число или «не знаем», но никогда чужое число -------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Обе стороны диапазона и середина: одно число, разные единицы и разное число
        # слов между числом и единицей.
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
    """Одно названное количество людей — берём его; ни одного — «не знаем».

    Единицы перечислены литералами в самих текстах: «человек», «мест», «визы»,
    «visas», «people». Список единиц из модуля не импортируется — сузят его,
    и тест покраснеет, а не поедет следом.
    """
    assert rules_facts(_message(text)).headcount == expected


def test_several_different_counts_give_no_headcount_at_all():
    """Третий исход факта: несколько разных количеств — размер команды неизвестен.

    Живой дефект: на обращении edge-03 («визы: нужно 3 партнёрские + 2 сотрудника,
    потом ещё 4») в карточке стояло `people 2` — первое совпадение, то есть одно из
    слагаемых. Читатель видит рядом текст и ловит расхождение, после чего перестаёт
    верить и остальным фактам. Удобное число хуже честного «не знаем».
    """
    text = "визы: нужно 3 партнёрские + 2 сотрудника, потом ещё 4"
    assert rules_facts(_message(text)).headcount is None


def test_the_same_count_repeated_is_still_one_count():
    """Повтор одного и того же числа — не разногласие, факт остаётся."""
    text = "нужно 5 человек посадить, и визы на тех же 5 сотрудников"
    assert rules_facts(_message(text)).headcount == 5


def test_seed_edge_03_shows_no_headcount():
    """Тот самый вход из репозитория, а не только выдуманная строка."""
    assert rules_facts(_seed_message("edge-03")).headcount is None


def test_headcount_quote_is_absent_when_the_count_is_unknown():
    """Нет числа — нет и цитаты под него: доказательство не выдумывается.

    Проверяется отсутствие именно обрывка под размер команды («2 сотрудника»), а не
    вообще любого упоминания: предложение целиком остаётся законной цитатой под тип
    запроса «визы», и вот оно на карточке уместно.
    """
    facts = rules_facts(_message("визы: нужно 3 партнёрские + 2 сотрудника, потом ещё 4"))
    assert "2 сотрудника" not in facts.quotes
    assert any("визы" in q for q in facts.quotes), "цитата под тип запроса обязана остаться"


# --- цитаты не повторяют друг друга --------------------------------------------------


LONG_ENUMERATION = (
    "вопросы такие: 1) нужен ли офис или хватит flexi desk 2) визы на сотрудников "
    "3) бухгалтерия и аудит 4) банк для нерезидента — всё это в одном предложении "
    "без единой точки, потому что клиент писал в спешке и не расставлял знаки"
)


def test_markers_inside_one_sentence_give_one_quote():
    """Несколько маркеров в одном перечислении — одно доказательство, а не четыре.

    Живой дефект: на обращении edge-03 карточка показывала 12 цитат, из которых первые
    три были окнами вокруг одного и того же места и отличались сдвигом на пару слов.
    Читатель видит три почти одинаковых абзаца и решает, что система пересказывает саму
    себя. Перекрывающееся окно — тот же кусок текста под другим маркером.
    """
    quotes = rules_facts(_message(LONG_ENUMERATION)).quotes
    assert len(quotes) == 1, quotes


def test_markers_in_different_sentences_give_different_quotes():
    """Негативный контроль правила: непересекающиеся места обязаны дать разные цитаты.

    Без этой половины проверка зеленела бы и на правиле «оставлять ровно одну цитату
    всегда», то есть измеряла бы не то.
    """
    text = "нужен офис в Дубае. Отдельным вопросом: визы на сотрудников."
    quotes = rules_facts(_message(text)).quotes
    assert len(quotes) == 2, quotes


def test_seed_edge_03_quotes_do_not_repeat_each_other():
    """Тот самый вход из репозитория: цитат немного и ни одна не повторяет соседнюю."""
    quotes = rules_facts(_seed_message("edge-03")).quotes
    assert len(quotes) <= 5, quotes
    stripped = [q.strip("…").strip() for q in quotes]
    for i, first in enumerate(stripped):
        for second in stripped[i + 1:]:
            assert first not in second and second not in first, (first, second)


def test_hot_requests_keep_at_least_one_quote():
    """Схлопывание цитат не имеет права оставить горячее обращение без доказательства."""
    for external_id in ("urg-01", "urg-13", "edge-03"):
        assert rules_facts(_seed_message(external_id)).quotes, external_id


# --- бюджет цитируется целиком, а не последним числом --------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Оба края и середина: одиночная сумма, диапазон, потолок, нижняя граница.
        ("бюджет 95 тысяч в год", "95 тысяч"),
        ("ориентируемся на 120-150 тысяч дирхам", "120-150 тысяч дирхам"),
        ("бюджет до 180k AED в год", "до 180k AED"),
        ("готовы от 50 тысяч дирхам", "от 50 тысяч дирхам"),
        ("около 200 000 AED на всё", "около 200 000 AED"),
        ("нужен офис, цену пока не знаем", None),
    ],
)
def test_budget_is_quoted_whole(text, expected):
    """Цитата бюджета — обещание дословности, а не последнее число из фразы.

    Живой дефект: «120-150 тысяч дирхам» показывалось на карточке как «150 тысяч».
    Диапазон превращался в точку, потолок — в ориентир, валюта пропадала. Читатель
    видит текст рядом и ловит расхождение.

    Ожидаемое — литералы; ни `MONEY_RE`, ни `MONEY_UNITS` не импортируются.
    """
    assert rules_facts(_message(text)).budget_hint == expected


def test_budget_of_a_price_question_is_still_not_a_budget():
    """Негативный контроль: вопрос о цене без числа бюджетом не становится."""
    assert rules_facts(_message("скок стоит открыть компанию?")).budget_hint is None


def test_seed_edge_03_budget_keeps_both_ends_of_the_range():
    """Тот самый вход из репозитория."""
    assert rules_facts(_seed_message("edge-03")).budget_hint == "120-150 тысяч дирхам"


# --- срок, названный словами -----------------------------------------------------
#
# Дефект, ради которого раздел заведён: слова «в этом месяце», «до пятницы», «на этой
# неделе» лежали в одном списке со словом «срочно», и срок из них не извлекался вовсе.
# Карточка писала «срочность заявлена словами, даты клиент не назвал» на обращении,
# где дата названа (ИЗМЕРЕНО 2026-09-11 на data/inbound_seed.csv: 5 обращений из 7).
#
# Ожидаемое — литералы: числа дней посчитаны по календарю руками, ни `DEADLINE_MARKERS`,
# ни функции правил из `extract` не импортируются. Фикстуры берутся с обоих краёв
# недели (понедельник и пятница) и месяца (первое и последнее число) и из середины.


def _with_date(text: str, received_at: date):
    return InboundMessage(
        external_id="probe", channel="form", text=text, received_at=received_at
    )


@pytest.mark.parametrize(
    ("text", "received_at", "expected"),
    [
        # «в этом месяце» — до последнего дня месяца обращения.
        ("нужен офис в этом месяце", date(2026, 9, 1), 29),    # начало месяца
        ("нужен офис в этом месяце", date(2026, 9, 9), 21),    # середина
        ("нужен офис в этом месяце", date(2026, 9, 30), 0),    # последний день
        ("лицензия нужна до конца месяца", date(2026, 8, 31), 0),
        ("лицензия нужна до конца месяца", date(2026, 2, 1), 27),  # февраль 2026: 28 дней
        ("we need it this month", date(2026, 9, 9), 21),
        # «до пятницы» — ближайшая пятница, считая день обращения.
        ("ответ нужен до пятницы", date(2026, 8, 10), 4),      # понедельник
        ("ответ нужен до пятницы", date(2026, 8, 14), 0),      # сама пятница
        ("ответ нужен до пятницы", date(2026, 8, 15), 6),      # суббота: следующая
        ("ответ нужен до пятницы", date(2026, 8, 8), 6),       # вход urg-05
        # «на этой неделе» — до воскресенья недели обращения.
        ("посмотреть офис на этой неделе", date(2026, 9, 7), 6),   # понедельник
        ("посмотреть офис на этой неделе", date(2026, 9, 5), 1),   # суббота (urg-09)
        ("посмотреть офис на этой неделе", date(2026, 9, 6), 0),   # воскресенье
        # «сегодня» — ноль, а не «скоро».
        ("нужно сегодня", date(2026, 9, 9), 0),
        # Ближайший из названных сроков связывает клиента.
        ("до пятницы, край — в этом месяце", date(2026, 9, 7), 4),
        # Негативный контроль: слов о сроке нет — ничего не выдумываем.
        ("нужен офис на 8 человек", date(2026, 9, 9), None),
        ("срочно нужен офис", date(2026, 9, 9), None),
    ],
)
def test_named_deadline_is_counted_from_the_message_date(text, received_at, expected):
    assert rules_facts(_with_date(text, received_at)).timeline_days == expected


def test_deadline_is_counted_from_the_message_not_from_today():
    """Ловушка из prompts/extract_v4.md: срок считается от даты ОБРАЩЕНИЯ.

    Один и тот же текст, полученный в разные дни, обязан давать разные сроки —
    иначе «на этой неделе» из письма недельной давности означало бы эту неделю.
    """
    text = "нужен офис на этой неделе"
    assert rules_facts(_with_date(text, date(2026, 9, 7))).timeline_days == 6
    assert rules_facts(_with_date(text, date(2026, 9, 9))).timeline_days == 4


@pytest.mark.parametrize(
    ("text", "expected_timeline", "expected_urgency"),
    [
        # Вычислимая дата: срок есть, словесного признака нет.
        ("лицензия нужна до конца месяца", 0, False),
        # Срочность без даты: признак есть, срок не выдуман.
        ("срочно нужен офис", None, True),
        ("we need it asap", None, True),
        ("как можно быстрее", None, True),
        # Оба слова в одном тексте: побеждает названная дата (вход urg-04).
        ("срочно, лицензия нужна до конца месяца, лишь бы быстро", 0, False),
        # Ни того, ни другого.
        ("нужен офис на 8 человек", None, False),
    ],
)
def test_a_message_never_gets_both_a_named_deadline_and_wordless_urgency(
    text, expected_timeline, expected_urgency
):
    """Инвариант разделения: дата и «просто срочно» — разные признаки, не оба сразу."""
    facts = rules_facts(_with_date(text, date(2026, 8, 31)))
    assert facts.timeline_days == expected_timeline
    assert facts.urgency_stated is expected_urgency


def test_the_demo_text_matches_the_live_llm_extraction():
    """Эталон — живое извлечение моделью на том же тексте (координатор, 2026-09-11).

    claude-haiku-4-5 на этом обращении от 2026-09-11 дал `timeline_days: 19` («в этом
    месяце» = до конца сентября). Режим rules обязан давать то же число: расходятся
    здесь не два мнения, а два пути одного продукта.

    Модель при этом поставила и `urgency_stated: True` — это и есть тот дефект, ради
    которого признаки разделены: дата названа, значит словесной срочности нет.
    """
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
        # Дата названа не словами-маркерами, а числом и месяцем: словесный признак
        # обязан молчать и здесь — иначе «даты клиент не назвал» врёт (вход urg-11).
        ("срочно, инвестор просит адрес до конца октября", date(2026, 7, 21), 72),
        ("срочно, нужен офис через 10 дней", date(2026, 9, 9), 10),
    ],
)
def test_any_extracted_deadline_silences_wordless_urgency(text, received_at, expected_timeline):
    facts = rules_facts(_with_date(text, received_at))
    assert facts.timeline_days == expected_timeline
    assert facts.urgency_stated is False


# --- срок, названный числом без предлога ------------------------------------------
#
# Дефект: `NUM_DAYS_RE`/`NUM_WEEKS_RE` требовали предлога «через/in/within», и текст
# «срок - 3 недели» (обращение urg-06 из data/inbound_seed.csv) не давал срока вовсе —
# клиент срок назвал, движок его потерял. ИЗМЕРЕНО 2026-09-11: на 70 обращениях набора
# задето 1 обращение, ложных срабатываний при снятии предлога — 0.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Ровно тот вход, на котором дефект наблюдался (И2).
        ("нам нужен office space asap, 10 seats, + 10 residence visa. срок - 3 недели.", 21),
        # Предлог на месте — поведение прежнее (положительный контроль).
        ("нужен офис через 3 недели", 21),
        ("we need the office in 3 weeks", 21),
        # Края диапазона и середина: один день, три недели, три месяца.
        ("срок - 1 день", 1),
        ("нужно за 10 дней", 10),
        ("срок 90 дней", 90),
        ("timeline 2 weeks", 14),
        ("нужно за 1 неделю", 7),
        # Негативный контроль: число о прошлом сроком не становится.
        ("2 недели назад отправляли заявку, ответа нет", None),
        ("we wrote 10 days ago and got no reply", None),
        # Негативный контроль: числа не о времени срока не дают.
        ("нужен офис на 8 человек", None),
        ("12 рабочих мест, барша хайтс", None),
    ],
)
def test_deadline_in_days_or_weeks_does_not_require_a_preposition(text, expected):
    assert rules_facts(_with_date(text, date(2026, 9, 11))).timeline_days == expected


def test_past_tense_number_does_not_hide_a_real_deadline_later_in_the_text():
    """Отброшен должен быть только рассказ о прошлом, а не весь текст.

    «писали 2 недели назад, а нужно за 10 дней» — срок здесь есть, и он второй.
    """
    text = "писали 2 недели назад, а нужно за 10 дней"
    assert rules_facts(_with_date(text, date(2026, 9, 11))).timeline_days == 10
