"""Детерминированное извлечение фактов из текста — без модели.

Единственная реализация режима `rules`: ею меряют движок в eval и ею же наполняется
демонстрационный набор дашборда, поэтому замер и демонстрация не могут разойтись.

confidence здесь означает «нашлись ли маркеры», а не уверенность модели.
Настоящее извлечение — engine/extract.py.
"""
from __future__ import annotations

import re
from datetime import date

from leadcentre.engine import extract as extract_mod
from leadcentre.models import InboundMessage, LeadFacts, RequestType

# --- факты без модели: режим rules ---

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
        # Основы, а не целые слова: «продлева» покрывает продлеваем/продлеваете/продлевает,
        # «renew» — renewal/renewing/renewals. «истёк» пишут и через ё, и через е,
        # поэтому нужны обе формы.
        "продлен", "продли", "продлева", "renew", "истекает", "истекл", "истёк", "истек",
        "заканчивается", "expires", "expiry", "expiring", "ежегодн", "annual fee",
    ),
    RequestType.BANK: (
        # Счёт часто открывают, не называя банк, — нужны маркеры на само действие.
        "банк", "bank", "счёт в банке", "счет в банке", "открыть счёт", "открыть счет",
        "открытие счёта", "открытие счета", "open an account", "current account",
        "платёжный шлюз", "payment gateway",
    ),
}

BUDGET_MARKERS = (
    "бюджет", "budget", "aed", "дирхам", "готовы подписать", "договор на", "цена устроит",
    "approved",
)

# Детерминированный извлекатель нашёл литерал, а не предположил, поэтому уверенность
# вырожденная. Это не вероятность модели: в режиме llm confidence приходит из её ответа.
RULES_CONFIDENCE_MATCHED = 1.0
RULES_CONFIDENCE_EMPTY = 0.0   # ничего не нашли — честный ноль, а не догадка


MONTHS = {
    "январ": 1, "феврал": 2, "марта": 3, "апрел": 4, "мая": 5, "июн": 6, "июл": 7,
    "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
    "january": 1, "february": 2, "april": 4, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12, "nov": 11, "dec": 12,
}

# Предлог необязателен: «срок - 3 недели» и «нужно за 10 дней» — такой же названный
# клиентом срок, как «через 3 недели». Раньше предлог требовался, и обращение urg-06
# из data/inbound_seed.csv теряло срок целиком (ИЗМЕРЕНО 2026-09-11: 1 обращение из 70).
# «день» в списке единиц отдельно: основа «дн» покрывает дня/дней/дн., но не
# именительный падеж — «срок - 1 день» не извлекался и с предлогом (тот же класс
# дефекта, найден тестом на краю диапазона).
NUM_DAYS_RE = re.compile(
    r"(?:(?:через|in|within)\s+)?(\d+)\s*(?:дн|дней|день|day|days)", re.IGNORECASE
)
NUM_WEEKS_RE = re.compile(r"(?:(?:через|in|within)\s+)?(\d+)\s*(?:недел|week)", re.IGNORECASE)
# Число без предлога ловит и рассказ о прошлом («две недели назад писали»), поэтому
# совпадение с хвостом из этого списка не считается сроком: срок в прошлом — не срок.
# Смотрим ровно на хвост, а не на всё предложение: «3 недели назад» и «через 3 недели,
# а месяц назад…» — разные вещи.
PAST_TAIL_MARKERS = ("назад", "ago")
PAST_TAIL_CHARS = 12
EXPIRES_RE = re.compile(
    r"(?:expires?|истека\w*|заканчива\w*|слетает)\D{0,25}(\d+)\s*(дн|day|week|недел)", re.IGNORECASE
)
# До двух необязательных слов между числом и единицей: «12 рабочих мест», «3 рабочие визы
# для сотрудников», «6 employment visas» пишут именно так. Виза и партнёр в списке единиц
# потому, что размер команды в обращении чаще называют через них, чем словом «человек».
HEADCOUNT_RE = re.compile(
    r"(\d+)\s*(?:[а-яёa-z]+\s+){0,2}?"
    r"(?:человек|чел\b|людей|people|ppl|persons|seats|мест\b|сотрудник\w*|staff"
    r"|партнёр\w*|партнер\w*|виз\w*|visas?)",
    re.IGNORECASE,
)
# Сумма берётся целиком, а не одним последним числом. Цитата — обещание дословности:
# «120-150 тысяч дирхам», урезанные до «150 тысяч», превращают диапазон в точку, а «до
# 180k AED» без «до» — потолок в ориентир. И то и другое читатель ловит по тексту рядом.
MONEY_QUALIFIERS = r"(?:до|от|около|примерно|порядка|up\s+to|around|about)\s+"
MONEY_UNITS = r"(?:aed|дирхам\w*|тысяч\w*|k\b)"
MONEY_RE = re.compile(
    rf"(?:{MONEY_QUALIFIERS})?"
    rf"\d[\d\s.,]*(?:\s*[-–—]\s*\d[\d\s.,]*)?\s*{MONEY_UNITS}"
    rf"(?:\s+(?:aed|дирхам\w*))?",
    re.IGNORECASE,
)


def _future_match(pattern: re.Pattern[str], low: str) -> re.Match[str] | None:
    """Первое совпадение, за которым не стоит слово о прошлом.

    Три исхода у самого поиска нет — есть «нашли срок» и «нет»; но совпадение,
    за которым идёт «назад»/«ago», это не срок, а рассказ о прошлом, и считать его
    сроком хуже, чем не найти ничего.
    """
    for match in pattern.finditer(low):
        tail = low[match.end(): match.end() + PAST_TAIL_CHARS]
        if any(marker in tail for marker in PAST_TAIL_MARKERS):
            continue
        return match
    return None


def _timeline_days(text: str, received_at: date) -> int | None:
    """Срок в днях, детерминированно. Не нашли — None, а не ноль (неизвестно != срочно)."""
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
    # Срок, названный словами («до пятницы», «в этом месяце»), — такой же названный
    # клиентом срок, как «через 3 недели»; считается от даты обращения. Раньше эти слова
    # лежали в списке маркеров срочности и срок из них не извлекался вовсе.
    # Раньше названия месяца: «в этом месяце» точнее, чем случайно упомянутый месяц.
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
    # Слово «срочно» — не дата. Раньше здесь подставлялись две недели, и карточка
    # показывала «timeline 14 days» на тексте, где никакого числа не было: выдуманное
    # число, поданное как извлечённый факт. Сама срочность не теряется — она уходит
    # отдельным фактом `urgency_stated`, у которого есть цитата и нет придуманной даты.
    # Вычислимые словами сроки сюда не попадают: их забрал `deadline_days` выше.
    return None


# Границы предложения в свободном тексте чата: перевод строки считается концом наравне
# с точкой — в чате пишут строками, а не абзацами.
SENTENCE_BOUNDARIES = ".!?\n;"
MAX_QUOTE_CHARS = 160


def _sentence_span(text: str, start: int, end: int) -> tuple[str, int, int]:
    """Предложение вокруг найденного куска и границы, которые оно заняло в тексте.

    Границы возвращаются вместе с текстом, потому что по ним отсеиваются цитаты-двойники:
    в длинном перечислении без точек несколько маркеров попадают в одно и то же место, и
    без сравнения границ карточка показывает три почти одинаковых окна как три разных
    доказательства.
    """
    left = max((text.rfind(ch, 0, start) for ch in SENTENCE_BOUNDARIES), default=-1)
    right_candidates = [pos for pos in (text.find(ch, end) for ch in SENTENCE_BOUNDARIES) if pos >= 0]
    right = min(right_candidates) if right_candidates else len(text)
    fragment = text[left + 1: right].strip()
    if len(fragment) <= MAX_QUOTE_CHARS:
        return fragment, left + 1, right

    # Длинное предложение подрезается вокруг совпадения по границам слов с обеих сторон:
    # цитата, начатая посреди слова, читается как мусор и обесценивает остальные
    # доказательства в карточке.
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
    """Предложение, внутри которого лежит найденный кусок."""
    return _sentence_span(text, start, end)[0]


# Слово о лицензии одинаково звучит при первичной регистрации и при продлении: «нужна
# лицензия» и «нужно продлить лицензию». Само по себе оно не доказывает регистрацию:
# истекающую лицензию продлевают, а не оформляют заново.
LICENCE_MARKERS = ("лицензи", "licence", "license")


def _drop_setup_inside_renewal(
    low: str, types: list[RequestType], fired: dict[RequestType, list[str]]
) -> list[RequestType]:
    """Убирает регистрацию, если она зажглась только словом о лицензии рядом с продлением.

    Настоящая регистрация рядом с продлением остаётся: если сработал хоть один маркер,
    кроме слова о лицензии («открыть компанию», «фризона», «mainland»), речь и правда
    о новой компании — одно обращение может нести оба типа сразу.
    """
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
            return types  # где-то о лицензии говорят отдельно от продления — оставляем
    return [kind for kind in types if kind is not RequestType.SETUP]


def rules_facts(message: InboundMessage) -> LeadFacts:
    """Факты из текста без модели: детерминированная заглушка режима `rules`.

    LeadFacts возвращается всегда; ненайденное поле — None (неизвестно), а не ноль.
    confidence вырожденная: 1.0, если сработал хоть один маркер, иначе 0.0.
    """
    low = message.text.lower()
    scrubbed = extract_mod.scrub_pii(message.text)
    quotes: list[str] = []
    # Границы уже занятых цитат: окно, пересекающееся с занятым, — тот же кусок текста
    # под другим маркером, а не второе доказательство.
    spans: list[tuple[int, int]] = []

    def hit(marker: str) -> bool:
        """Ищет маркер; найденный добавляет в цитаты предложением целиком.

        В цитату идёт текст обращения, а не сам маркер: обрубок основы («виз», «офис»)
        ничего не доказывает. Длинное предложение подрезается по границам слов.
        """
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
        # перебираем все маркеры, а не до первого: цитаты нужны все, что сработали
        matched = False
        for marker in markers:
            if marker in low:
                fired.setdefault(kind, []).append(marker)
            matched = hit(marker) or matched
        if matched:
            types.append(kind)
    types = _drop_setup_inside_renewal(low, types, fired)
    # Третий исход для факта: если в тексте названо несколько разных количеств людей
    # («3 партнёрские + 2 сотрудника, потом ещё 4»), размер команды из него не следует.
    # Первое совпадение в таком тексте — не размер команды, а одно из слагаемых, и на
    # карточке оно противоречит тексту, который читатель видит рядом. Ничего не знаем —
    # так и говорим, а не берём удобное число.
    headcount = None
    matches = list(HEADCOUNT_RE.finditer(low))
    distinct = {int(m.group(1)) for m in matches}
    if len(distinct) == 1:
        found = matches[0]
        headcount = int(found.group(1))
        quotes.append(message.text[found.start(): found.end()])
    # budget_hint — фрагмент обращения, а не пересказ: скоринг читает содержимое поля и
    # ищет в нём сумму. Названная сумма вытесняет всё остальное — именно она отличает
    # объявленный бюджет от разговоров о бюджете; склейка сработавших маркеров сюда не идёт.
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
        # Словесная срочность — только когда срока нет вовсе: один и тот же помощник
        # на обоих путях извлечения, иначе признак разъедется между rules и llm.
        urgency_stated=extract_mod.wordless_urgency(message.text, timeline_days),
        budget_hint=budget,
        language=extract_mod.detect_language(message.text),
        is_spam=is_spam,
        has_contact=scrubbed.has_contact,
        confidence=RULES_CONFIDENCE_MATCHED if quotes else RULES_CONFIDENCE_EMPTY,
        quotes=tuple(dict.fromkeys(quotes)),
    )


