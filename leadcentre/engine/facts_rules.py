"""Deterministic fact extraction without a model: the `rules` mode used by the eval
harness and the demo set. Here `confidence` means "markers matched", not a probability."""
from __future__ import annotations

import re
from datetime import date

from leadcentre.engine import extract as extract_mod
from leadcentre.models import InboundMessage, LeadFacts, RequestType

SPAM_MARKERS = (
    "seo agency", "rank your website", "ищу работу", "резюме вышлю", "cv attached",
    "looking for job", "job opportunity", "арбитраж", "депозит от", "business database",
    "сдаю квартиру", "без комиссии", "buy verified",
)

TYPE_MARKERS: dict[RequestType, tuple[str, ...]] = {
    RequestType.OFFICE: (
        "офис", "office", "кабинет", "рабочих мест", "рабочие места", "рабочее место",
        "seats", "desk", "переговорн",
        "флекси", "flexi", "помещение", "кв.м", "sqm", "переговорк", "unit at",
    ),
    RequestType.SETUP: (
        "лицензи", "licence", "license", "регистрац", "регистрируем", "зарегистр", "register",
        "открыть компанию", "открытие компании", "открываем", "open company",
        "company formation", "incorporation", "фризон",
        "фриз зона", "freezone", "free zone", "mainland", "мейнленд", "юрлиц", "филиал",
        "холдинг", "تأسيس",
    ),
    RequestType.VISA: ("виз", "visa", "emirates id", "резидентств", "residence", "golden"),
    RequestType.ACCOUNTING: (
        "бухгалт", "бухучёт", "бухучет", "accounting", "bookkeeping", "аудит", "audit",
        "налог", " tax", "vat",
        "отчётност", "отчетност",
    ),
    RequestType.RENEWAL: (
        # Why stems: one stem covers every inflected form, including both ё and е.
        "продлен", "продли", "продлева", "renew", "истекает", "истекл", "истёк", "истек",
        "заканчивается", "expires", "expiry", "expiring", "ежегодн", "annual fee",
    ),
    RequestType.BANK: (
        # Why action markers too: an account is often opened without naming a bank.
        "банк", "bank", "счёт в банке", "счет в банке", "открыть счёт", "открыть счет",
        "открытие счёта", "открытие счета", "open an account", "current account",
        "платёжный шлюз", "payment gateway",
    ),
}

BUDGET_MARKERS = (
    "бюджет", "budget", "aed", "дирхам", "готовы подписать", "договор на", "цена устроит",
    "approved",
)

# Why degenerate: a literal was found or it was not; this is not a model probability.
RULES_CONFIDENCE_MATCHED = 1.0
RULES_CONFIDENCE_EMPTY = 0.0


MONTHS = {
    "январ": 1, "феврал": 2, "марта": 3, "апрел": 4, "мая": 5, "июн": 6, "июл": 7,
    "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
    "january": 1, "february": 2, "april": 4, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12, "nov": 11, "dec": 12,
}

# Why optional: a bare number states a deadline. The nominative day form is listed
# separately, because the stem used for the other forms does not cover it.
NUM_DAYS_RE = re.compile(
    r"(?:(?:через|in|within)\s+)?(\d+)\s*(?:дн|дней|день|day|days)", re.IGNORECASE
)
NUM_WEEKS_RE = re.compile(r"(?:(?:через|in|within)\s+)?(\d+)\s*(?:недел|week)", re.IGNORECASE)
# Why a tail guard: a bare number also matches the past, and a past deadline is no deadline.
PAST_TAIL_MARKERS = ("назад", "ago")
PAST_TAIL_CHARS = 12
EXPIRES_RE = re.compile(
    r"(?:expires?|истека\w*|заканчива\w*|слетает)\D{0,25}(\d+)\s*(дн|day|week|недел)", re.IGNORECASE
)
# Why words may sit between number and unit: that is how counts and team sizes are written.
HEADCOUNT_RE = re.compile(
    r"(\d+)\s*(?:[а-яёa-z]+\s+){0,2}?"
    r"(?:человек|чел\b|людей|people|ppl|persons|seats|мест\b|сотрудник\w*|staff"
    r"|партнёр\w*|партнер\w*|виз\w*|visas?)",
    re.IGNORECASE,
)
# Why the whole amount: a trimmed range becomes a point and a ceiling becomes an estimate.
MONEY_QUALIFIERS = r"(?:до|от|около|примерно|порядка|up\s+to|around|about)\s+"
MONEY_UNITS = r"(?:aed|дирхам\w*|тысяч\w*|k\b)"
MONEY_RE = re.compile(
    rf"(?:{MONEY_QUALIFIERS})?"
    rf"\d[\d\s.,]*(?:\s*[-–—]\s*\d[\d\s.,]*)?\s*{MONEY_UNITS}"
    rf"(?:\s+(?:aed|дирхам\w*))?",
    re.IGNORECASE,
)


def _future_match(pattern: re.Pattern[str], low: str) -> re.Match[str] | None:
    """Returns the first match not followed by a word about the past, else None."""
    for match in pattern.finditer(low):
        tail = low[match.end(): match.end() + PAST_TAIL_CHARS]
        if any(marker in tail for marker in PAST_TAIL_MARKERS):
            continue
        return match
    return None


def _timeline_days(text: str, received_at: date) -> int | None:
    """Returns the deadline in days, or None when none is stated (unknown is not urgent)."""
    low = text.lower()
    m = _future_match(NUM_DAYS_RE, low)
    if m:
        return int(m.group(1))
    m = _future_match(NUM_WEEKS_RE, low)
    if m:
        return int(m.group(1)) * 7
    m = EXPIRES_RE.search(low)
    if m:
        value = int(m.group(1))
        return value * 7 if m.group(2).lower().startswith(("недел", "week")) else value
    # Why before month names: a worded deadline beats a month mentioned in passing.
    named = extract_mod.deadline_days(text, received_at)
    if named is not None:
        return named
    best: int | None = None
    for marker, month in MONTHS.items():
        if marker not in low:
            continue
        year = received_at.year + (1 if month < received_at.month else 0)
        delta = (date(year, month, 1) - received_at).days
        if delta >= 0 and (best is None or delta < best):
            best = delta
    if best is not None:
        return best
    # Why nothing is invented: urgency words carry no date, and travel as their own fact.
    return None


SENTENCE_BOUNDARIES = ".!?\n;"
MAX_QUOTE_CHARS = 160


def _sentence_span(text: str, start: int, end: int) -> tuple[str, int, int]:
    """Returns the sentence around a match plus its span, which identifies duplicate quotes."""
    left = max((text.rfind(ch, 0, start) for ch in SENTENCE_BOUNDARIES), default=-1)
    right_candidates = [pos for pos in (text.find(ch, end) for ch in SENTENCE_BOUNDARIES) if pos >= 0]
    right = min(right_candidates) if right_candidates else len(text)
    fragment = text[left + 1: right].strip()
    if len(fragment) <= MAX_QUOTE_CHARS:
        return fragment, left + 1, right

    # Why trimmed on word boundaries: a quote starting mid-word reads as garbage.
    head = max(left + 1, start - MAX_QUOTE_CHARS // 2)
    tail = min(right, head + MAX_QUOTE_CHARS)
    cut = text[head:tail]
    if head > left + 1:
        space = cut.find(" ")
        cut = cut[space + 1:] if space >= 0 else cut
    if tail < right:
        space = cut.rfind(" ")
        cut = cut[:space] if space >= 0 else cut
    cut = cut.strip()
    prefix = "…" if head > left + 1 else ""
    suffix = "…" if tail < right else ""
    return f"{prefix}{cut}{suffix}", head, tail


def _sentence_around(text: str, start: int, end: int) -> str:
    """Returns the sentence containing the match."""
    return _sentence_span(text, start, end)[0]


# Why separate: a licence word sounds the same for a registration and for a renewal.
LICENCE_MARKERS = ("лицензи", "licence", "license")


def _drop_setup_inside_renewal(
    low: str, types: list[RequestType], fired: dict[RequestType, list[str]]
) -> list[RequestType]:
    """Drops SETUP when it fired only on a licence word standing next to a renewal."""
    if RequestType.SETUP not in types or RequestType.RENEWAL not in types:
        return types
    setup_markers = fired.get(RequestType.SETUP, ())
    if any(marker not in LICENCE_MARKERS for marker in setup_markers):
        return types
    renewal_markers = fired.get(RequestType.RENEWAL, ())
    for sentence in re.split(f"[{re.escape(SENTENCE_BOUNDARIES)}]", low):
        has_licence = any(marker in sentence for marker in setup_markers)
        has_renewal = any(marker in sentence for marker in renewal_markers)
        if has_licence and not has_renewal:
            return types  # a licence is discussed apart from any renewal, so SETUP stands
    return [kind for kind in types if kind is not RequestType.SETUP]


def rules_facts(message: InboundMessage) -> LeadFacts:
    """Extracts facts from text; a field that was not found is None, never zero."""
    low = message.text.lower()
    scrubbed = extract_mod.scrub_pii(message.text)
    quotes: list[str] = []
    # Why spans are tracked: an overlapping window is the same text, not new evidence.
    spans: list[tuple[int, int]] = []

    def hit(marker: str) -> bool:
        """Looks a marker up and quotes the sentence around it, since a stem proves nothing."""
        index = low.find(marker)
        if index < 0:
            return False
        fragment, start, end = _sentence_span(message.text, index, index + len(marker))
        overlaps = any(start < taken_end and taken_start < end for taken_start, taken_end in spans)
        if fragment and not overlaps and fragment not in quotes:
            quotes.append(fragment)
            spans.append((start, end))
        return True

    types: list[RequestType] = []
    fired: dict[RequestType, list[str]] = {}
    for kind, markers in TYPE_MARKERS.items():
        # Why every marker, not the first: all matching quotes are needed.
        matched = False
        for marker in markers:
            if marker in low:
                fired.setdefault(kind, []).append(marker)
            matched = hit(marker) or matched
        if matched:
            types.append(kind)
    types = _drop_setup_inside_renewal(low, types, fired)
    # Why several different counts yield nothing: each is an addend, not the team size.
    headcount = None
    matches = list(HEADCOUNT_RE.finditer(low))
    distinct = {int(m.group(1)) for m in matches}
    if len(distinct) == 1:
        found = matches[0]
        headcount = int(found.group(1))
        quotes.append(message.text[found.start(): found.end()])
    # Why a fragment: scoring reads this field for an amount, which is what marks a budget.
    money = MONEY_RE.search(low)
    if money:
        budget = message.text[money.start(): money.end()].strip()
        quotes.append(budget)
    else:
        budget = None
        for marker in BUDGET_MARKERS:
            if hit(marker):
                budget = message.text[low.find(marker): low.find(marker) + len(marker)]
                break
    timeline_days = _timeline_days(message.text, message.received_at)
    is_spam = False
    for marker in SPAM_MARKERS:
        is_spam = hit(marker) or is_spam
    return LeadFacts(
        request_types=tuple(types),
        jurisdiction_hint=None,
        headcount=headcount,
        timeline_days=timeline_days,
        # Why the shared helper: both extraction paths must compute this identically.
        urgency_stated=extract_mod.wordless_urgency(message.text, timeline_days),
        budget_hint=budget,
        language=extract_mod.detect_language(message.text),
        is_spam=is_spam,
        has_contact=scrubbed.has_contact,
        confidence=RULES_CONFIDENCE_MATCHED if quotes else RULES_CONFIDENCE_EMPTY,
        quotes=tuple(dict.fromkeys(quotes)),
    )


