"""Детерминированный расчёт приоритета. Модель извлекает факты, приоритет считает код.

Исходов три: годный tier, LOW и INVALID — «не смогли оценить». INVALID
не сворачивается в LOW: это разные вещи для отчёта и для менеджера.

Текста здесь нет и быть не должно — ни у причин, ни у нарушений инвариантов: модуль
называет код и параметры, формулировки на русском и английском живут в
`engine/reasons.py`. `Score.violations` остаётся кортежем строк на русском, но
строки эти собирает отрисовка каталога, а не этот модуль; на английский те же нарушения
отдаёт `reasons.texts_in(score.violations, Language.EN)`.
"""
from __future__ import annotations

from datetime import date
from functools import cache

from leadcentre.engine import rubric
from leadcentre.engine.reasons import (
    Reason,
    ReasonCode,
    Violation,
    ViolationCode,
    reason,
    rendered_all,
    violation,
)
from leadcentre.models import (
    AddressType,
    CityMatch,
    Company,
    EntityStatus,
    Event,
    Evidence,
    InboundMessage,
    LeadFacts,
    Score,
    Tier,
)


def classify_address(company: Company) -> AddressType:
    """Ось A по тексту адреса и органу регистрации."""
    haystack = " ".join(company.address_lines).lower()
    if any(m in haystack for m in rubric.REGISTRAR_ADDRESS_MARKERS):
        return AddressType.REGISTRAR
    if company.registrar_id in rubric.REGISTRAR_AUTHORITY_IDS:
        return AddressType.REGISTRAR
    if any(m in haystack for m in rubric.BUSINESS_CENTRE_MARKERS):
        return AddressType.BUSINESS_CENTRE
    if haystack.strip():
        return AddressType.OWN
    return AddressType.UNKNOWN


def normalize_city(value: str) -> str:
    """Строка города, приведённая к виду, в котором её сравнивают со списками.

    Нижний регистр, одна форма арабского алефа, схлопнутые пробелы. Нормализуются обе
    стороны сравнения — и вход, и маркер из `rubric`, — иначе в списке пришлось бы
    держать по два написания на каждую букву с хамзой («أبو ظبي» и «ابو ظبي»).
    """
    folded = value.strip().lower()
    for form in rubric.CITY_ALEF_FORMS:
        folded = folded.replace(form, rubric.CITY_ALEF_CANONICAL)
    return " ".join(folded.split())


def _city_parts(normalized: str) -> tuple[str, ...]:
    """Части адресной строки города: «jumeirah lakes towers, dubai» -> две части.

    Реестр пишет в поле города и адрес целиком, и город с эмиратом через косую черту.
    Части нужны там, где сравнение идёт по названию целиком (районы): подстрокой
    район искать нельзя, «al ain» нашёлся бы внутри чужого слова.
    """
    parts = [normalized]
    for separator in rubric.CITY_PART_SEPARATORS:
        parts = [chunk for part in parts for chunk in part.split(separator)]
    return tuple(p for p in (" ".join(x.split()) for x in parts) if p)


@cache
def _normalized(markers: tuple[str, ...]) -> frozenset[str]:
    """Маркеры из `rubric`, приведённые тем же нормализатором, что и вход.

    Кэш по самому кортежу, а не заранее посчитанная константа: списки в `rubric` —
    данные, и подмена их в тесте (мутация правила) обязана доезжать до сравнения.
    """
    return frozenset(normalize_city(m) for m in markers)


def classify_city(city: str) -> CityMatch:
    """Целевой ли город, по написанию из реестра. Исходов четыре, и это не два.

    Порядок проверок — само правило, поэтому он здесь, а не размазан по условиям:

    1. Пустая строка — `NOT_SET`: решать не по чему.
    2. Район чужого эмирата (`NON_TARGET_DISTRICTS`) — `OFF_TARGET`. Первым, потому что
       «Al Reem Island» это Абу-Даби, и попасть в «не узнали» он не должен.
    3. Имя чужого эмирата подстрокой — `OFF_TARGET`. Раньше целевых: любой признак
       другого эмирата важнее; поднять до HIGH по догадке дороже, чем не поднять.
    4. Район Дубая целиком (`TARGET_DISTRICTS`) или имя Дубая подстрокой — `TARGET`.
    5. Всё остальное — `UNRECOGNISED`: реестр написал что-то, чего в списках нет.
    """
    normalized = normalize_city(city)
    if not normalized:
        return CityMatch.NOT_SET
    parts = _city_parts(normalized)
    if any(part in _normalized(rubric.NON_TARGET_DISTRICTS) for part in parts):
        return CityMatch.OFF_TARGET
    if any(m in normalized for m in _normalized(rubric.NON_TARGET_CITY_MARKERS)):
        return CityMatch.OFF_TARGET
    if any(part in _normalized(rubric.TARGET_DISTRICTS) for part in parts):
        return CityMatch.TARGET
    if any(m in normalized for m in _normalized(rubric.TARGET_CITY_MARKERS)):
        return CityMatch.TARGET
    return CityMatch.UNRECOGNISED


#: Какую причину печатать на каждый исход по городу. Словарь, а не цепочка if: исходов
#: четыре, и «забыли ветку» здесь превратилось бы в ступень без объяснения.
CITY_REASON: dict[CityMatch, ReasonCode] = {
    CityMatch.OFF_TARGET: ReasonCode.CITY_OFF_TARGET,
    CityMatch.UNRECOGNISED: ReasonCode.CITY_UNRECOGNISED,
    CityMatch.NOT_SET: ReasonCode.CITY_NOT_SET,
}


def normalize_status(value: str) -> str:
    """Статус реестра в виде, в котором его сравнивают со списком.

    Регистр и пробелы снимаются с обеих сторон сравнения: `"lapsed"` и `" LAPSED "` —
    тот же статус, а не «слово, которого мы не знаем». Сам GLEIF пишет верхним
    регистром (ИЗМЕРЕНО 2026-09-11, 120 записей выборки), но `registration_status` —
    поле общей модели, и второй источник в него кладёт что хочет.
    """
    return value.strip().upper()


def status_reason(company: Company) -> Reason | None:
    """Что сказать про статус регистрации, если по нему повода не вышло. Исхода три.

    * статус пустой — `REGISTRATION_STATUS_NOT_SET`: решать не по чему;
    * статус не из перечня реестра — `REGISTRATION_STATUS_UNKNOWN`: «не смогли
      определить», а не «повода нет»;
    * статус известный, но ось B по нему поводов не считает (RETIRED, DUPLICATE,
      ANNULLED, MERGED, PENDING_TRANSFER, PENDING_ARCHIVAL) —
      `REGISTRATION_STATUS_NO_EVENT`: решение принято и названо вслух.

    Ступень эта причина не двигает: повод по оси B от неё не появляется и не исчезает,
    она делает видимым уже принятое решение.
    """
    raw = company.registration_status.strip()
    status = normalize_status(raw)
    if not status:
        return reason(ReasonCode.REGISTRATION_STATUS_NOT_SET)
    if status not in rubric.KNOWN_REGISTRATION_STATUSES:
        return reason(ReasonCode.REGISTRATION_STATUS_UNKNOWN, status=raw[:40])
    if status in (rubric.STATUS_LAPSED, rubric.STATUS_ISSUED):
        return None
    return reason(ReasonCode.REGISTRATION_STATUS_NO_EVENT, status=raw[:40])


def classify_event(company: Company, today: date) -> tuple[Event, tuple[Reason, ...]]:
    """Ось B: самый сильный из поводов плюс причины кодами и параметрами.

    Статус регистрации сравнивается со списком `rubric.KNOWN_REGISTRATION_STATUSES`, а
    не с двумя литералами: раньше любое третье слово реестра молча означало «повода
    нет», и «повода нет» было не отличить от «слова мы не знаем» (см. `status_reason`).
    """
    reasons: list[Reason] = []
    status = normalize_status(company.registration_status)
    if status == rubric.STATUS_LAPSED and company.next_renewal_on:
        overdue = (today - company.next_renewal_on).days
        if 0 <= overdue <= rubric.LAPSED_FRESH_DAYS:
            reasons.append(reason(ReasonCode.LEI_LAPSED_FRESH, days=overdue))
            return Event.LAPSED, tuple(reasons)
        if overdue > rubric.LAPSED_FRESH_DAYS:
            reasons.append(reason(ReasonCode.LEI_LAPSED_LONG_AGO, days=overdue))
            return Event.NONE, tuple(reasons)
    if company.created_on:
        age = (today - company.created_on).days
        if 0 <= age <= rubric.NEW_ENTITY_DAYS:
            reasons.append(reason(ReasonCode.ENTITY_RECENTLY_CREATED, days=age))
            return Event.NEW_ENTITY, tuple(reasons)
    if company.next_renewal_on and status == rubric.STATUS_ISSUED:
        left = (company.next_renewal_on - today).days
        if 0 <= left <= rubric.RENEWAL_SOON_DAYS:
            reasons.append(reason(ReasonCode.LEI_RENEWAL_SOON, days=left))
            return Event.RENEWAL_SOON, tuple(reasons)
    # Повода не вышло — говорим, почему именно: «не смогли прочитать статус» и
    # «статус прочитан, повода по нему нет» здесь расходятся.
    about_status = status_reason(company)
    if about_status is not None:
        reasons.append(about_status)
    return Event.NONE, tuple(reasons)


def collect_evidence(company: Company) -> tuple[Evidence, ...]:
    items = [Evidence("lei", company.external_id)]
    if company.license_no:
        items.append(Evidence("license_no", company.license_no))
    if company.next_renewal_on:
        items.append(Evidence("next_renewal_on", company.next_renewal_on.isoformat()))
    if company.created_on:
        items.append(Evidence("created_on", company.created_on.isoformat()))
    return tuple(items)


def score(company: Company, today: date) -> Score:
    address_type = classify_address(company)
    event, event_reasons = classify_event(company, today)
    tier = rubric.MATRIX[(address_type, event)]
    reasons: list[Reason] = list(event_reasons)
    violations: list[Violation] = []

    # Модификаторы. Город из реестра приходит в четырёх видах написания, поэтому
    # сравнение вынесено в `classify_city`, а его исход выбирает причину по словарю.
    # Ступень понижается на всех исходах, кроме целевого, — но причина у каждого своя:
    # «другой эмират» и «написание не узнали» читаются по-разному и считаются отдельно.
    city_match = classify_city(company.city)
    if tier is Tier.HIGH and city_match is not CityMatch.TARGET:
        tier = Tier.MEDIUM
        code = CITY_REASON[city_match]
        city = company.city.strip()
        reasons.append(
            reason(code) if code is ReasonCode.CITY_NOT_SET else reason(code, city=city)
        )

    # Инварианты. Нарушение — не LOW, а INVALID. Нарушение — такой же код с
    # параметрами, как причина: текст ему собирает каталог, оба языка сразу.
    # Статус юрлица: исхода три, а не два. Раньше здесь стояло `not entity_active`, и
    # всё, что не равно слову ACTIVE, объявлялось неактивным — включая слово NULL,
    # которым реестр сообщает «статус мне не передали» (36 записей по ОАЭ на 2026-09-11).
    # Приговор юрлицу выносит только явное INACTIVE; молчание реестра ступень ограничивает
    # средней — как неизмеренная уверенность в `score_inbound`, — но лид не хоронит.
    if company.entity_status is EntityStatus.INACTIVE:
        tier = Tier.LOW
        reasons.append(reason(ReasonCode.ENTITY_INACTIVE))
    elif company.entity_status is EntityStatus.UNKNOWN:
        tier = min(tier, Tier.MEDIUM, key=rubric.TIER_LADDER.index)
        raw_status = company.entity_status_raw.strip()
        reasons.append(
            reason(ReasonCode.ENTITY_STATUS_UNKNOWN, status=raw_status[:40])
            if raw_status
            else reason(ReasonCode.ENTITY_STATUS_NOT_SET)
        )
    evidence = collect_evidence(company)
    if tier is Tier.HIGH and not evidence:
        violations.append(violation(ViolationCode.HIGH_WITHOUT_EVIDENCE))
    if company.country != "AE":
        violations.append(
            violation(ViolationCode.COMPANY_OUTSIDE_UAE, country=company.country)
        )
    if violations:
        tier = Tier.INVALID

    return Score(
        tier=tier,
        address_type=address_type,
        event=event,
        reason_items=tuple(reasons),
        evidence=evidence,
        violations=rendered_all(tuple(violations)),
    )


# --- входящие обращения: ось C поверх базового уровня ---


def _step(tier: Tier, delta: int) -> Tier:
    """Сдвиг на ступень по лестнице приоритетов, без выхода за края."""
    ladder = rubric.TIER_LADDER
    index = min(max(ladder.index(tier) + delta, 0), len(ladder) - 1)
    return ladder[index]


def score_inbound(message: InboundMessage, facts: LeadFacts) -> Score:
    """Приоритет входящего обращения.

    Модель извлекла факты, дальше решает код: повышают срочность, пакет услуг и язык,
    понижают отсутствие деталей и низкая уверенность извлечения. Спам и обращение без
    единого извлечённого запроса — это LOW, а не «не смогли»: текст прочитан.
    Нарушение инварианта — INVALID, отдельный третий исход.
    """
    reasons: list[Reason] = []
    violations: list[Violation] = []

    if facts.is_spam:
        return Score(
            tier=Tier.LOW,
            address_type=AddressType.UNKNOWN,
            event=Event.NONE,
            reason_items=(reason(ReasonCode.SPAM_OR_OFF_TOPIC),),
            evidence=tuple(Evidence("quote", q) for q in facts.quotes),
        )

    tier = rubric.INBOUND_BASE if facts.request_types else rubric.INBOUND_BASE_NO_REQUEST
    if not facts.request_types:
        reasons.append(reason(ReasonCode.NO_REQUEST_TYPE))

    bumps = 0
    if facts.timeline_days is not None and facts.timeline_days <= rubric.URGENT_TIMELINE_DAYS:
        bumps += 1
        reasons.append(reason(
            ReasonCode.URGENT_TIMELINE,
            days=facts.timeline_days,
            limit=rubric.URGENT_TIMELINE_DAYS,
        ))
    elif facts.urgency_stated:
        # Слово «срочно» — содержательный признак наравне со сроком, но не дата.
        # Раньше здесь стоял выдуманный срок в две недели, и карточка показывала его
        # как извлечённый факт. Признак остался, придуманное число ушло.
        bumps += 1
        reasons.append(reason(ReasonCode.URGENT_STATED))
    if len(facts.request_types) >= rubric.PACKAGE_MIN_REQUEST_TYPES:
        bumps += 1
        reasons.append(reason(ReasonCode.PACKAGE_REQUEST, count=len(facts.request_types)))
    if facts.headcount is not None and facts.headcount >= rubric.TEAM_MIN_HEADCOUNT:
        bumps += 1
        reasons.append(reason(ReasonCode.TEAM_OVER_FLEXI_QUOTA, headcount=facts.headcount))
    # Бюджетом считается только сумма. Модель охотно кладёт в это поле сам вопрос
    # «сколько стоит» — из вопроса о цене горячий лид не следует, скорее наоборот.
    if facts.budget_hint and any(ch.isdigit() for ch in facts.budget_hint):
        bumps += 1
        reasons.append(reason(ReasonCode.BUDGET_NAMED, budget=facts.budget_hint[:40]))

    substantive = bumps

    # Язык — довесок, а не самостоятельный повод (см. LANGUAGE_NEEDS_ANOTHER_SIGNAL).
    if facts.language in rubric.TARGET_LANGUAGES:
        if substantive or not rubric.LANGUAGE_NEEDS_ANOTHER_SIGNAL:
            reasons.append(reason(ReasonCode.TARGET_LANGUAGE, language=facts.language))
        else:
            reasons.append(
                reason(ReasonCode.TARGET_LANGUAGE_ALONE, language=facts.language)
            )

    # Горячим делает только набор содержательных признаков: одного мало, иначе HIGH
    # достаётся половине входящих и перестаёт что-либо значить (см. SIGNALS_FOR_HIGH).
    # Две разные причины подняться на ступень, намеренно не слитые в одно условие:
    # набор признаков делает обращение горячим, а один признак вытаскивает из LOW
    # обращение, где не распознан тип запроса. Слияние через or прячет вторую причину.
    enough_signals = substantive >= rubric.SIGNALS_FOR_HIGH
    rescued_from_low = bool(substantive) and tier is Tier.LOW
    if enough_signals or rescued_from_low:
        tier = _step(tier, 1)

    # Понижающие. Исходов по уверенности три, а не два: измерили и мало, измерили и
    # достаточно, не измеряли вовсе (офлайн-заглушка). Первый и третий одинаково не
    # дают подняться выше среднего — приоритет из ненадёжных фактов дороже пропущенного
    # лида, — но причины у них разные: число, которого не измеряли, нельзя печатать
    # рядом с порогом, как будто его измерили.
    if not facts.confidence_measured:
        tier = min(tier, Tier.MEDIUM, key=rubric.TIER_LADDER.index)
        reasons.append(reason(ReasonCode.CONFIDENCE_NOT_MEASURED))
    elif facts.confidence < rubric.LOW_CONFIDENCE:
        tier = min(tier, Tier.MEDIUM, key=rubric.TIER_LADDER.index)
        reasons.append(reason(
            ReasonCode.LOW_CONFIDENCE,
            confidence=facts.confidence,
            threshold=rubric.LOW_CONFIDENCE,
        ))

    evidence = tuple(Evidence("quote", q) for q in facts.quotes)
    if tier is Tier.HIGH and not evidence:
        violations.append(violation(ViolationCode.HIGH_WITHOUT_QUOTE))
    if tier is Tier.HIGH and not message.text.strip():
        violations.append(violation(ViolationCode.HIGH_ON_EMPTY_TEXT))
    if violations:
        tier = Tier.INVALID

    return Score(
        tier=tier,
        address_type=AddressType.UNKNOWN,
        event=Event.NONE,
        reason_items=tuple(reasons),
        evidence=evidence,
        violations=rendered_all(tuple(violations)),
    )
