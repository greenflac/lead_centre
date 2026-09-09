"""Детерминированное извлечение фактов из текста — без модели.

Одно знание — одно место (Е1). Раньше эта логика существовала дважды: своя в
измерительном стенде и своя в генераторе данных дашборда, и они расходились —
13/37/20 против 12/41/17 на одном и том же наборе. Демонстрация показывала одну
картину, замер мерял другую, и обе назывались «движок».

Это не извлечение, а заглушка: confidence здесь означает «нашлись ли маркеры»,
а не уверенность модели. Настоящее извлечение — engine/extract.py.
"""
from __future__ import annotations

import re
from datetime import date

from leadcentre.engine import extract as extract_mod
from leadcentre.models import InboundMessage, LeadFacts, RequestType

# Порог «срочно» по умолчанию, когда в тексте есть слова срочности без числа.
URGENT_DEFAULT_DAYS = 14

# --- факты без модели: режим rules ---

SPAM_MARKERS = (
    "seo agency", "rank your website", "ищу работу", "резюме вышлю", "cv attached",
    "looking for job", "job opportunity", "арбитраж", "депозит от", "business database",
    "сдаю квартиру", "без комиссии", "buy verified",
)

TYPE_MARKERS: dict[RequestType, tuple[str, ...]] = {
    RequestType.OFFICE: (
        "офис", "office", "кабинет", "рабочих мест", "рабочие места", "seats", "desk",
        "флекси", "flexi", "помещение", "кв.м", "sqm", "переговорк", "unit at",
    ),
    RequestType.SETUP: (
        "лицензи", "licence", "license", "регистрац", "регистрируем", "register",
        "открыть компанию", "открываем", "open company", "company formation", "фризон",
        "фриз зона", "freezone", "free zone", "mainland", "мейнленд", "юрлиц", "филиал",
        "холдинг", "تأسيس",
    ),
    RequestType.VISA: ("виз", "visa", "emirates id", "резидентств", "residence", "golden"),
    RequestType.ACCOUNTING: (
        "бухгалт", "accounting", "bookkeeping", "аудит", "audit", "налог", " tax", "vat",
        "отчётност", "отчетност",
    ),
    RequestType.BANK: (
        "банк", "bank", "счёт в банке", "счет в банке", "платёжный шлюз", "payment gateway",
    ),
}

BUDGET_MARKERS = (
    "бюджет", "budget", "aed", "дирхам", "готовы подписать", "договор на", "цена устроит",
    "approved",
)

# ВЫБРАНО: детерминированный извлекатель уверен в том, что нашёл, — он нашёл литерал,
# а не предположил. Это НЕ вероятность модели; в режиме llm confidence приходит из ответа.
RULES_CONFIDENCE_MATCHED = 1.0
RULES_CONFIDENCE_EMPTY = 0.0   # ничего не нашли — «моделью не смотрено», честный ноль

URGENT_MARKERS = (
    "срочно", "urgent", "asap", "в этом месяце", "this month", "до конца месяца",
    "до пятницы", "сегодня", "today", "как можно быстрее", "лишь бы быстро",
    "на этой неделе",
)

MONTHS = {
    "январ": 1, "феврал": 2, "марта": 3, "апрел": 4, "мая": 5, "июн": 6, "июл": 7,
    "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
    "january": 1, "february": 2, "april": 4, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12, "nov": 11, "dec": 12,
}

NUM_DAYS_RE = re.compile(r"(?:через|in|within)\s+(\d+)\s*(?:дн|дней|day|days)", re.IGNORECASE)
NUM_WEEKS_RE = re.compile(r"(?:через|in|within)\s+(\d+)\s*(?:недел|week)", re.IGNORECASE)
EXPIRES_RE = re.compile(
    r"(?:expires?|истека\w*|заканчива\w*|слетает)\D{0,25}(\d+)\s*(дн|day|week|недел)", re.IGNORECASE
)
# Одно необязательное слово между числом и единицей: «12 рабочих мест», «6 employment
# visas» пишут именно так; без него прибор молча терял размер команды.
HEADCOUNT_RE = re.compile(
    r"(\d+)\s*(?:[а-яёa-z]+\s+)?"
    r"(?:человек|чел\b|людей|people|ppl|persons|seats|мест\b|сотрудник\w*|staff)",
    re.IGNORECASE,
)
MONEY_RE = re.compile(r"\d[\d\s.,]*\s*(?:aed|дирхам|тысяч|k\b)", re.IGNORECASE)


def _timeline_days(text: str, received_at: date) -> int | None:
    """Срок в днях, детерминированно. Не нашли — None, а не ноль (неизвестно != срочно)."""
    low = text.lower()
    m = NUM_DAYS_RE.search(low)
    if m:
        return int(m.group(1))
    m = NUM_WEEKS_RE.search(low)
    if m:
        return int(m.group(1)) * 7
    m = EXPIRES_RE.search(low)
    if m:
        value = int(m.group(1))
        return value * 7 if m.group(2).lower().startswith(("недел", "week")) else value
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
    if any(marker in low for marker in URGENT_MARKERS):
        return URGENT_DEFAULT_DAYS
    return None


def rules_facts(message: InboundMessage) -> LeadFacts:
    """Факты из текста без модели: заглушка стенда, а не извлечение (confidence=0.0).

    Живёт в eval, а не в движке, сознательно: это прибор, которым меряют движок, и он
    обязан быть дешёвым и детерминированным. Качество извлечения меряется режимом llm.
    """
    low = message.text.lower()
    scrubbed = extract_mod.scrub_pii(message.text)
    quotes: list[str] = []

    def hit(marker: str) -> bool:
        """Нашли маркер — кладём в цитаты ровно тот кусок текста, который его вызвал (Е2)."""
        index = low.find(marker)
        if index < 0:
            return False
        fragment = message.text[index: index + len(marker)]
        if fragment not in quotes:
            quotes.append(fragment)
        return True

    types: list[RequestType] = []
    for kind, markers in TYPE_MARKERS.items():
        # перебираем все маркеры, а не до первого: цитаты нужны все, что сработали
        matched = False
        for marker in markers:
            matched = hit(marker) or matched
        if matched:
            types.append(kind)
    headcount = None
    found = HEADCOUNT_RE.search(low)
    if found:
        headcount = int(found.group(1))
        quotes.append(message.text[found.start(): found.end()])
    # budget_hint — кусок текста, а не наш пересказ (Е2): движок смотрит на содержимое
    # поля (в нём должна быть сумма), и подмена пересказом молча съела бы сигнал.
    budget_parts: list[str] = []
    money = MONEY_RE.search(low)
    if money:
        fragment = message.text[money.start(): money.end()].strip()
        budget_parts.append(fragment)
        quotes.append(fragment)
    for marker in BUDGET_MARKERS:
        if hit(marker):
            budget_parts.append(message.text[low.find(marker): low.find(marker) + len(marker)])
    budget = "; ".join(dict.fromkeys(budget_parts)) or None
    is_spam = False
    for marker in SPAM_MARKERS:
        is_spam = hit(marker) or is_spam
    return LeadFacts(
        request_types=tuple(types),
        jurisdiction_hint=None,
        headcount=headcount,
        timeline_days=_timeline_days(message.text, message.received_at),
        budget_hint=budget,
        language=extract_mod.detect_language(message.text),
        is_spam=is_spam,
        has_contact=scrubbed.has_contact,
        confidence=RULES_CONFIDENCE_MATCHED if quotes else RULES_CONFIDENCE_EMPTY,
        quotes=tuple(dict.fromkeys(quotes)),
    )


