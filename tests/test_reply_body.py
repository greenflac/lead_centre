"""Тесты тела черновика: разметка и неразрывность денежной вставки.

Правило, ради которого файл заведён: арабский черновик `gen-19` начинался строкой
«# وعليكم السلام» — модель дописала markdown-заголовок, чистка его не снимала, линтер
не проверял, и решётка доехала до карточки и до скриншота в репозитории.

Ожидаемое — литералы: маркеры разметки выписаны строками, коды невидимых символов —
шестнадцатеричными значениями. Из `reply` и `lint` не импортируется ни один список
маркеров: сузят его — тест покраснеет, а не поедет следом.
"""
from __future__ import annotations

import pytest

from leadcentre.engine.lint import lint
from leadcentre.engine.reply import Reply, load_prices, price_fragment, strip_markup

# Коды невидимых символов выписаны escape-последовательностями: так их видно в диффе
# и не путает ни редактор, ни линтер.
WORD_JOINER = "\u2060"   # WORD JOINER
NBSP = "\u00a0"         # NO-BREAK SPACE
RLM = "\u200f"          # RIGHT-TO-LEFT MARK


def _reply(body: str, language: str = "ru") -> Reply:
    return Reply(body=body, language=language, used_prices=(), needs_human=False,
                 outcome="draft")


# --- чистка на входе: смысл остаётся, обёртка уходит ---------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("# وعليكم السلام", "وعليكم السلام"),   # тот самый живой дефект
        ("## Здравствуйте", "Здравствуйте"),
        ("###### шесть решёток", "шесть решёток"),
        ("> цитата", "цитата"),
        (">> двойная цитата", "двойная цитата"),
        ("**жирная строка**", "жирная строка"),
        ("_курсив_", "курсив"),
        # Негативный контроль: то, что разметкой не является, обязано уцелеть.
        ("#hashtag без пробела", "#hashtag без пробела"),
        ("обычная строка", "обычная строка"),
        ("Ориентир по рынку: AED 15 000–35 000 в год.", "Ориентир по рынку: AED 15 000–35 000 в год."),
        ("цена 5*7 метров", "цена 5*7 метров"),
    ],
)
def test_markup_is_stripped_but_text_survives(line, expected):
    assert strip_markup(line) == expected


# --- гейт: линтер ловит разметку любого происхождения --------------------------------


@pytest.mark.parametrize(
    "body",
    [
        f"{RLM}# وعليكم السلام\n{RLM}вторая строка",
        "Здравствуйте!\n> Мы посчитаем после звонка.",
        "Здравствуйте!\nЦена **AED 15 000** в год.",
        "Здравствуйте!\nСмотрите `pricelist.yaml`.",
    ],
)
def test_linter_reports_markup_in_the_body(body):
    """Разметка — нарушение, а не «почти нормально»."""
    result = lint(_reply(body))
    assert result.violations, result.summary()
    assert any("разметки" in v for v in result.violations), result.violations


def test_linter_lets_a_clean_letter_through():
    """Негативный контроль: без него проверка зеленела бы и на приборе, всегда кричащем «нет»."""
    body = "Здравствуйте!\nПо офису: у нас собственный бизнес-центр в Дубае."
    result = lint(_reply(body))
    assert not any("разметки" in v for v in result.violations), result.violations


def test_markup_check_is_counted_among_the_checks_done():
    """«Нарушений 0» значит что-то только рядом с «проверено N»."""
    result = lint(_reply("Здравствуйте!\nПо офису: есть мини-офисы."))
    assert "проверено 7" in result.summary(), result.summary()


# --- денежная вставка не рвётся переносом строки -------------------------------------


def test_price_fragment_has_no_break_opportunity():
    """Диапазон, разорванный пополам, читается как одна цена.

    Проверяются все три места, где браузер имел право перенести: после «AED», внутри
    разрядов и после тире между границами.
    """
    fragment = price_fragment(load_prices()["office_mini_year"])
    assert " " not in fragment, f"обычный пробел — точка разрыва: {fragment!r}"
    assert NBSP in fragment, "неразрывный пробел исчез"
    assert f"{WORD_JOINER}–{WORD_JOINER}" in fragment, (
        f"тире диапазона без склеек слов: {fragment!r}"
    )


def test_price_fragment_still_reads_as_a_range():
    """Негативный контроль: склейки невидимы, но текст обязан остаться читаемым."""
    item = load_prices()["office_mini_year"]
    visible = "".join(ch for ch in price_fragment(item) if ch not in "\u2060\u2066\u2069")
    assert visible == f"AED{NBSP}35{NBSP}000–60{NBSP}000", visible
