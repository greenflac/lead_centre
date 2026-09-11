"""Draft linting before a manager sees the text.

Three verdicts, and UNVERIFIABLE never collapses into the others: an unread price list
means the number check did not run. Arabic is normalised first, since otherwise a
separator or a bidi mark hides a sum from every check.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from leadcentre.engine.reply import (
    DEFAULT_PRICELIST,
    OUTCOME_NO_DRAFT,
    OUTCOME_SPAM_SKIPPED,
    PriceItem,
    PriceListError,
    Reply,
    load_prices,
)

MAX_LINES = 6  # Why 6: the agreed draft length is 4-6 lines.

BIDI_CONTROLS = "\u200e\u200f\u061c\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"

DIGIT_MAP = {
    **{chr(0x0660 + i): str(i) for i in range(10)},
    **{chr(0x06F0 + i): str(i) for i in range(10)},
}
SEPARATOR_MAP = {"\u066c": " ", "\u066b": "."}
_NORMALIZE_TABLE = str.maketrans({
    **dict.fromkeys(BIDI_CONTROLS, ""),
    **DIGIT_MAP,
    **SEPARATOR_MAP,
})

# Why stripped: diacritics and tatweel do not change meaning but break matching.
ARABIC_DIACRITICS = "".join(chr(c) for c in range(0x064B, 0x0653)) + "\u0640\u0670"
_ARABIC_LETTER_MAP = str.maketrans({
    **dict.fromkeys(ARABIC_DIACRITICS, ""),
    "\u0623": "\u0627",   # أ -> ا
    "\u0625": "\u0627",   # إ -> ا
    "\u0622": "\u0627",   # آ -> ا
    "\u0649": "\u064a",   # ى -> ي
    "\u0629": "\u0647",   # ة -> ه
})


def normalize(text: str) -> str:
    """Folds text into a comparable form; line length is never measured on it."""
    folded = unicodedata.normalize("NFKC", text)
    return folded.translate(_NORMALIZE_TABLE).translate(_ARABIC_LETTER_MAP).lower()


#: Slop phrases: empty politeness that makes a letter read as a mailshot.
SLOP_PHRASES: tuple[str, ...] = (
    "мы рады сообщить",
    "рады сообщить",
    "не стесняйтесь обращаться",
    "в кратчайшие сроки",
    "команда профессионалов",
    "индивидуальный подход",
    "широкий спектр услуг",
    "we are pleased to inform",
    "we are delighted to inform",
    "do not hesitate to contact",
    "don't hesitate to contact",
    "feel free to reach out",
    "as soon as possible",
    "team of professionals",
    "wide range of services",
    # Arabic equivalents; compared after normalize(), so spelling variants do not matter.
    "يسعدنا ان نعلمكم",
    "يسرنا ان نبلغكم",
    "لا تترددوا في التواصل",
    "لا تتردد في التواصل",
    "في اقرب وقت ممكن",
    "في اسرع وقت ممكن",
    "فريق من المحترفين",
    "فريق محترفين",
    "مجموعه واسعه من الخدمات",
)

# Why forbidden: a government body owns these timelines, not us.
GOV_SUBJECT_RU = r"(лиценз\w*|виз\w*|разрешен\w*|регистрац\w*|emirates\s*id|вид на жительство)"
GOV_SUBJECT_EN = r"(licen[cs]\w*|visa\w*|permit\w*|registration|emirates\s*id|residency)"
GOV_PERIOD_RU = r"(за|через)\s+\d+\s*(рабоч\w*\s+)?(дн\w*|недел\w*|час\w*|мес\w*)"
GOV_PERIOD_EN = r"(in|within)\s+\d+\s*(business\s+|working\s+)?(day|week|hour|month)s?"
GOV_SUBJECT_AR = r"(رخص\w*|تاشير\w*|تصريح\w*|اقام\w*|تسجيل\w*|الهويه)"
GOV_PERIOD_AR = (
    r"(خلال|في|بعد|في غضون)\s+\d+\s*(يوم\w*|ايام|اسبوع\w*|اسابيع|شهر\w*|اشهر|ساع\w*)"
)
DEADLINE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("ru-предмет-срок", rf"{GOV_SUBJECT_RU}[^.\n]{{0,40}}{GOV_PERIOD_RU}"),
    ("ru-срок-предмет", rf"{GOV_PERIOD_RU}[^.\n]{{0,40}}{GOV_SUBJECT_RU}"),
    ("en-предмет-срок", rf"{GOV_SUBJECT_EN}[^.\n]{{0,40}}{GOV_PERIOD_EN}"),
    ("en-срок-предмет", rf"{GOV_PERIOD_EN}[^.\n]{{0,40}}{GOV_SUBJECT_EN}"),
    ("ar-предмет-срок", rf"{GOV_SUBJECT_AR}[^.\n]{{0,40}}{GOV_PERIOD_AR}"),
    ("ar-срок-предмет", rf"{GOV_PERIOD_AR}[^.\n]{{0,40}}{GOV_SUBJECT_AR}"),
)

_NUM = r"\d{1,3}(?:[   ,]\d{3})+|\d+(?:\.\d+)?"
_DASH = r"[-–—]"
_CURRENCY = r"AED|د\.?\s?ا|درهم\w*|دراهم|dirham\w*|дирхам\w*"
AED_PATTERNS: tuple[str, ...] = (
    rf"(?:{_CURRENCY})\s*({_NUM})(?:\s*{_DASH}\s*({_NUM}))?",
    rf"({_NUM})(?:\s*{_DASH}\s*({_NUM}))?\s*(?:{_CURRENCY})",
)

CHECK_LENGTH = "длина"
CHECK_SLOP = "слоп-фразы"
CHECK_DEADLINE = "обещания сроков"
CHECK_PRICES = "числа AED в диапазоне прайса"
CHECK_MONEY_VERBATIM = "денежная вставка не переписана"
CHECK_ASCII_DIGITS = "цифры в сумме европейские"
CHECK_NO_MARKUP = "в письме нет разметки"

# Why checked on the raw body: normalize() erases these before they can be noticed.
NON_ASCII_DIGITS = "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹"

STATUS_OK = "OK"
STATUS_VIOLATIONS = "VIOLATIONS"
STATUS_UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True)
class LintResult:
    """A lint verdict; OK only when every check ran and none failed."""

    status: str
    violations: tuple[str, ...] = ()
    checks_done: tuple[str, ...] = ()
    checks_failed: tuple[str, ...] = field(default_factory=tuple)
    numbers_checked: tuple[float, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def summary(self) -> str:
        """Returns the report line: three counts, with no aggregate boolean."""
        return (
            f"проверено {len(self.checks_done)}, нарушений {len(self.violations)}, "
            f"не смогли {len(self.checks_failed)}"
        )


def _parse_amount(raw: str) -> float:
    for space in (" ", "\u00a0", "\u202f", ","):
        raw = raw.replace(space, "")
    return float(raw)


def extract_aed_amounts(text: str) -> tuple[float, ...]:
    """Returns amounts written next to a currency; other numbers are ignored."""
    found: list[float] = []
    for pattern in AED_PATTERNS:
        for match in re.finditer(pattern, normalize(text), flags=re.IGNORECASE):
            for group in match.groups():
                if group:
                    found.append(_parse_amount(group))
    return tuple(found)


def _in_any_range(amount: float, prices: dict[str, PriceItem]) -> bool:
    return any(item.min <= amount <= item.max for item in prices.values())


def _check_length(body: str, violations: list[str]) -> None:
    lines = [line for line in body.splitlines() if line.strip()]
    if len(lines) > MAX_LINES:
        violations.append(f"{CHECK_LENGTH}: строк {len(lines)}, максимум {MAX_LINES}")


def _check_slop(body: str, violations: list[str]) -> None:
    low = normalize(body)
    for phrase in SLOP_PHRASES:
        if normalize(phrase) in low:
            violations.append(f"{CHECK_SLOP}: «{phrase}»")


def _check_deadlines(body: str, violations: list[str]) -> None:
    low = normalize(body)
    for name, pattern in DEADLINE_PATTERNS:
        match = re.search(pattern, low)
        if match:
            fragment = " ".join(match.group(0).split())
            violations.append(f"{CHECK_DEADLINE} [{name}]: «{fragment}»")


def _check_prices(
    reply: Reply,
    prices: dict[str, PriceItem],
    violations: list[str],
) -> tuple[float, ...]:
    amounts = extract_aed_amounts(reply.body)
    for amount in amounts:
        if not _in_any_range(amount, prices):
            violations.append(
                f"{CHECK_PRICES}: AED {amount:g} не попадает ни в один диапазон прайса"
            )
    if reply.used_prices and not amounts:
        # Why counted: otherwise a price counts as used without reaching the text.
        violations.append(
            f"{CHECK_PRICES}: заявлены цены {reply.used_prices}, а чисел в тексте нет"
        )
    return amounts


def _check_money_verbatim(reply: Reply, prices: dict[str, PriceItem], violations: list[str]) -> None:
    """Checks the money run character for character; the Latin currency is the only anchor
    holding a number inside an Arabic line."""
    from leadcentre.engine.reply import price_fragment

    for key in reply.used_prices:
        item = prices.get(key)
        if item is None:
            continue
        if price_fragment(item) not in reply.body:
            violations.append(
                f"{CHECK_MONEY_VERBATIM}: вставка для «{key}» переписана — "
                "без латинского AED и меток направления диапазон перевернётся"
            )


def _check_ascii_digits(body: str, violations: list[str]) -> None:
    """Checks that amounts carry no Arabic-Indic digits, as the price list uses ASCII."""
    found = sorted({ch for ch in body if ch in NON_ASCII_DIGITS})
    if found:
        violations.append(
            f"{CHECK_ASCII_DIGITS}: в тексте арабо-индийские цифры {''.join(found)}"
        )


#: Markdown in a customer letter; checked here because the linter guards any origin.
MARKUP_LINE_RE = re.compile(r"^[\u200f\u200e\s]*(?:#{1,6}|>+)(?:\s+|$)", re.MULTILINE)
MARKUP_INLINE_RE = re.compile(r"(?<!\*)\*{2,3}[^*\n]+\*{2,3}(?!\*)|`{1,3}[^`\n]+`{1,3}")


def _check_markup(body: str, violations: list[str]) -> None:
    """Checks that no markdown appears at the start of a line or inside one."""
    line = MARKUP_LINE_RE.search(body)
    if line:
        violations.append(f"{CHECK_NO_MARKUP}: строка начинается с «{line.group(0).strip()}»")
    inline = MARKUP_INLINE_RE.search(body)
    if inline:
        violations.append(f"{CHECK_NO_MARKUP}: внутри строки «{inline.group(0)[:40]}»")


def lint(
    reply: Reply,
    prices: dict[str, PriceItem] | None = None,
    pricelist_path: Path | str = DEFAULT_PRICELIST,
) -> LintResult:
    """Lints a draft; the price list may be passed in, otherwise it is read from disk."""
    violations: list[str] = []
    done: list[str] = []
    failed: list[str] = []

    # Why not OK: there is no draft here, so nothing was checked.
    if reply.outcome in (OUTCOME_SPAM_SKIPPED, OUTCOME_NO_DRAFT) or not reply.body.strip():
        return LintResult(
            status=STATUS_UNVERIFIABLE,
            violations=(),
            checks_done=(),
            checks_failed=(f"черновик не сгенерирован (исход {reply.outcome})",),
        )

    _check_length(reply.body, violations)
    done.append(CHECK_LENGTH)
    _check_slop(reply.body, violations)
    done.append(CHECK_SLOP)
    _check_deadlines(reply.body, violations)
    done.append(CHECK_DEADLINE)
    _check_markup(reply.body, violations)
    done.append(CHECK_NO_MARKUP)

    amounts: tuple[float, ...] = ()
    if prices is None:
        try:
            prices = load_prices(pricelist_path)
        except PriceListError as exc:
            failed.append(f"{CHECK_PRICES}: {exc}")
            prices = None
    if prices is not None:
        amounts = _check_prices(reply, prices, violations)
        done.append(CHECK_PRICES)
        _check_money_verbatim(reply, prices, violations)
        done.append(CHECK_MONEY_VERBATIM)
    _check_ascii_digits(reply.body, violations)
    done.append(CHECK_ASCII_DIGITS)

    if failed:
        status = STATUS_UNVERIFIABLE
    elif violations:
        status = STATUS_VIOLATIONS
    else:
        status = STATUS_OK
    return LintResult(
        status=status,
        violations=tuple(violations),
        checks_done=tuple(done),
        checks_failed=tuple(failed),
        numbers_checked=amounts,
    )
