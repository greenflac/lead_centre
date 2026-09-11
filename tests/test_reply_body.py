"""The letter body carries no markdown, and a money run never breaks across lines.

Invisible characters are written as escapes so they are visible in a diff.
"""
from __future__ import annotations

import pytest

from leadcentre.engine.lint import lint
from leadcentre.engine.reply import Reply, load_prices, price_fragment, strip_markup

# Invisible characters are written as escapes so they show up in a diff
# and confuse neither an editor nor the linter.
WORD_JOINER = "\u2060"   # WORD JOINER
NBSP = "\u00a0"         # NO-BREAK SPACE
RLM = "\u200f"          # RIGHT-TO-LEFT MARK


def _reply(body: str, language: str = "ru") -> Reply:
    return Reply(body=body, language=language, used_prices=(), needs_human=False,
                 outcome="draft")


# Cleanup on input: the meaning stays, the wrapping goes.


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("# وعليكم السلام", "وعليكم السلام"),   # the live defect itself
        ("## Здравствуйте", "Здравствуйте"),
        ("###### шесть решёток", "шесть решёток"),
        ("> цитата", "цитата"),
        (">> двойная цитата", "двойная цитата"),
        ("**жирная строка**", "жирная строка"),
        ("_курсив_", "курсив"),
        # negative control: what is not markup must survive
        ("#hashtag без пробела", "#hashtag без пробела"),
        ("обычная строка", "обычная строка"),
        ("Ориентир по рынку: AED 15 000–35 000 в год.", "Ориентир по рынку: AED 15 000–35 000 в год."),
        ("цена 5*7 метров", "цена 5*7 метров"),
    ],
)
def test_markup_is_stripped_but_text_survives(line, expected):
    assert strip_markup(line) == expected


# The gate: the linter catches markup of any origin.


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
    result = lint(_reply(body))
    assert result.violations, result.summary()
    assert any("разметки" in v for v in result.violations), result.violations


def test_linter_lets_a_clean_letter_through():
    """A clean letter passes."""
    body = "Здравствуйте!\nПо офису: у нас собственный бизнес-центр в Дубае."
    result = lint(_reply(body))
    assert not any("разметки" in v for v in result.violations), result.violations


def test_markup_check_is_counted_among_the_checks_done():
    result = lint(_reply("Здравствуйте!\nПо офису: есть мини-офисы."))
    assert "проверено 7" in result.summary(), result.summary()


# A money run never breaks across lines.


def test_price_fragment_has_no_break_opportunity():
    """A money run offers no line-break opportunity anywhere inside it."""
    fragment = price_fragment(load_prices()["office_mini_year"])
    assert " " not in fragment, f"обычный пробел — точка разрыва: {fragment!r}"
    assert NBSP in fragment, "неразрывный пробел исчез"
    assert f"{WORD_JOINER}–{WORD_JOINER}" in fragment, (
        f"тире диапазона без склеек слов: {fragment!r}"
    )


def test_price_fragment_still_reads_as_a_range():
    """The money run still reads as a range once the invisible marks are stripped."""
    item = load_prices()["office_mini_year"]
    visible = "".join(ch for ch in price_fragment(item) if ch not in "\u2060\u2066\u2069")
    assert visible == f"AED{NBSP}35{NBSP}000–60{NBSP}000", visible
