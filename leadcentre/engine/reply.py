"""Черновик ответа клиенту: шаблоны плюс диапазон из прайса, без обращения к LLM.

Почему без LLM (осознанное решение, не экономия):
- черновик читает и отправляет человек, и он должен быть воспроизводим: один и тот же
  вход обязан давать один и тот же текст, иначе линтер (lint.py) проверяет не то, что
  уйдёт клиенту, а один из вариантов;
- цена в тексте берётся из data/pricelist_demo.yaml и только оттуда (Е1); генеративная
  модель придумывает числа, и поймать это можно лишь постфактум;
- красота текста здесь дешевле детерминированности: менеджер правит формулировку за
  секунды, а выдуманная цена стоит сделки.
LLM в этом контуре работает раньше — в extract.py, где из текста достаются факты.

Три исхода вместо двух (Р1): DRAFT (есть о чём говорить), QUESTIONS (фактов мало —
уточняем, цен не называем), SPAM_SKIPPED (черновик не создаётся вовсе). Спам — именно
отдельный исход, а не пустая строка: пустую строку невозможно отличить от сбоя.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from leadcentre.models import InboundMessage, LeadFacts, RequestType, Tier

# --- пороги и правила как данные (стиль rubric.py) ---

# Ниже этой уверенности извлечения цены не называем — только уточняющие вопросы.
# ВЫБРАНО (автор, 2026-09-09): половина шкалы 0..1. Мутация обязана красить тест (Т1).
MIN_CONFIDENCE_FOR_PRICE = 0.5

# Срочность, при которой черновик отдаётся человеку. ВЫБРАНО (автор, 2026-09-09): неделя.
URGENT_TIMELINE_DAYS = 7

DEFAULT_PRICELIST = Path(__file__).resolve().parents[2] / "data" / "pricelist_demo.yaml"

# Какой пункт прайса отвечает какому типу запроса. Порядок важен: он же порядок строк.
PRICE_KEY_BY_REQUEST: dict[RequestType, tuple[str, ...]] = {
    RequestType.OFFICE: ("office_mini_year", "flexi_desk_year"),
    RequestType.SETUP: ("setup_mainland_package", "setup_freezone_package"),
    RequestType.VISA: ("visa_employment",),
    RequestType.ACCOUNTING: ("accounting_month",),
    RequestType.BANK: (),
    RequestType.OTHER: (),
}

# Ответ по существу — первая строка письма, до всяких цен.
SUBSTANCE: dict[RequestType, dict[str, str]] = {
    RequestType.OFFICE: {
        "ru": "По офису: у нас собственный бизнес-центр в Дубае, есть мини-офисы "
              "и флекси-десками закрываем визовую квоту.",
        "en": "On the office: we run our own business centre in Dubai, with small "
              "private offices and flexi-desks that cover the visa quota.",
    },
    RequestType.SETUP: {
        "ru": "По регистрации: считаем оба варианта — mainland и фризона, "
              "выбор зависит от вида деятельности и того, нужны ли визы.",
        "en": "On the setup: we compare both routes — mainland and free zone; "
              "the choice depends on your activity and how many visas you need.",
    },
    RequestType.VISA: {
        "ru": "По визам: оформляем рабочие визы под ключ — медкомиссия, Emirates ID, "
              "штамп; квота считается от площади офиса.",
        "en": "On visas: we handle employment visas end to end — medical, Emirates ID, "
              "stamping; the quota depends on your office space.",
    },
    RequestType.ACCOUNTING: {
        "ru": "По бухгалтерии: ведём учёт, VAT и корпоративный налог, "
              "объём работ зависит от числа операций в месяц.",
        "en": "On accounting: we cover bookkeeping, VAT and corporate tax; "
              "the scope depends on your monthly transaction volume.",
    },
    RequestType.BANK: {
        "ru": "По счёту: сопровождаем открытие в местных банках, "
              "решение принимает банк, мы готовим комплект и защищаем заявку.",
        "en": "On banking: we support account opening with local banks — the bank "
              "decides, we prepare and defend the application.",
    },
    RequestType.OTHER: {
        "ru": "Спасибо за обращение — разберём вашу задачу по шагам.",
        "en": "Thanks for reaching out — let us take your case step by step.",
    },
}

GREETING = {"ru": "Здравствуйте!", "en": "Hello,"}

# Оговорка вместо точной цены: обещать точную цифру черновику запрещено.
DISCLAIMER = {
    "ru": "Это рыночный диапазон, итог зависит от вида деятельности и числа виз — "
          "посчитаем точно после короткого разговора.",
    "en": "This is a market range; the final figure depends on your activity and visa "
          "count — we will price it exactly after a short call.",
}

CLOSER_URGENT = {
    "ru": "Вижу, что сроки сжатые: возьмём в работу сегодня — во сколько удобно созвониться?",
    "en": "Your timeline looks tight: we can start today — what time suits a call?",
}
CLOSER_MEETING = {
    "ru": "Удобно встретиться в нашем офисе в Дубае на этой неделе или созвониться?",
    "en": "Would a meeting at our Dubai office this week work, or a call instead?",
}

# Замыкающий вопрос спрашивает только то, чего в фактах НЕТ. Спросить про уже сказанное —
# показать клиенту, что обращение не прочитали; это дороже любой неспрошенной детали.
# Порядок кандидатов — данные, а не ветвление в тексте: меняется здесь, и мутация порядка
# обязана менять последнюю строку черновика (Т1).
CLOSER_QUESTION_ORDER: tuple[str, ...] = (
    "headcount",
    "timeline_days",
    "jurisdiction_hint",
    "has_contact",
)

# Чем проверяется «факт известен». Ключ тот же, что в LeadFacts.
FACT_IS_KNOWN = {
    "headcount": lambda f: f.headcount is not None,
    "timeline_days": lambda f: f.timeline_days is not None,
    "jurisdiction_hint": lambda f: bool(f.jurisdiction_hint),
    "has_contact": lambda f: f.has_contact,
}

CLOSER_QUESTION = {
    "headcount": {
        "ru": "Подскажите, сколько человек планируете нанять в первый год?",
        "en": "Could you tell us how many people you plan to hire in the first year?",
    },
    "timeline_days": {
        "ru": "К какому сроку нужно, чтобы всё было готово?",
        "en": "By when do you need everything up and running?",
    },
    "jurisdiction_hint": {
        "ru": "Смотрите mainland или фризону — или как раз хотите сравнить два варианта?",
        "en": "Are you leaning towards mainland or a free zone — or would you compare both?",
    },
    "has_contact": {
        "ru": "Оставьте номер WhatsApp — пришлём расчёт туда и не потеряем ваш вопрос.",
        "en": "Share a WhatsApp number and we will send the numbers there.",
    },
}

# Опора на известное для случая, когда спрашивать больше нечего.
GROUNDING = {
    "headcount": {"ru": "вас {value} человек", "en": "there are {value} of you"},
    "timeline_days": {"ru": "срок {value} дн.", "en": "your timeline is {value} days"},
    "jurisdiction_hint": {"ru": "формат {value}", "en": "you are looking at {value}"},
}
GROUNDED_MEETING_TAIL = {
    "ru": "предлагаю созвон сегодня или встречу в нашем офисе в Дубае.",
    "en": "let us do a call today or meet at our Dubai office.",
}

# Уточняющие вопросы, когда фактов мало. Порядок — от самого важного.
QUESTIONS = {
    "ru": (
        "Что именно нужно в первую очередь — регистрация компании, офис, визы или бухгалтерия?",
        "Планируете mainland или фризону и сколько виз потребуется?",
        "К какому сроку нужно решение?",
    ),
    "en": (
        "What do you need first — company setup, an office, visas or accounting?",
        "Are you looking at mainland or a free zone, and how many visas do you need?",
        "By when do you need this done?",
    ),
}
QUESTIONS_INTRO = {
    "ru": "Здравствуйте! Чтобы ответить по делу и без лишних цифр, уточните пару вещей.",
    "en": "Hello, to answer precisely and without guessing numbers, a couple of questions.",
}
QUESTIONS_CLOSER = {
    "ru": "Ответьте одной строкой — подготовим расчёт и вышлем в течение дня.",
    "en": "One line back is enough — we will prepare the numbers and send them the same day.",
}

# Исходы черновика (Р1).
OUTCOME_DRAFT = "draft"
OUTCOME_QUESTIONS = "questions"
OUTCOME_SPAM_SKIPPED = "spam_skipped"


@dataclass(frozen=True)
class PriceItem:
    """Пункт демо-прайса. Диапазон, а не цена: точной цены у движка нет и быть не должно."""

    key: str
    label_ru: str
    label_en: str
    unit_ru: str
    unit_en: str
    min: int
    max: int
    origin: str

    def label(self, language: str) -> str:
        return self.label_ru if language == "ru" else self.label_en

    def unit(self, language: str) -> str:
        return self.unit_ru if language == "ru" else self.unit_en


@dataclass(frozen=True)
class Reply:
    """Черновик ответа. Пустой body осмыслен только вместе с outcome."""

    body: str
    language: str
    used_prices: tuple[str, ...]
    needs_human: bool
    outcome: str = OUTCOME_DRAFT


class PriceListError(RuntimeError):
    """Прайс не прочитан. Наверх идёт исключением, чтобы линтер сказал «не смогли» (Р1)."""


# --- чтение прайса ---


def _parse_scalar(raw: str) -> str | int | float:
    """Скаляр YAML в том подмножестве, которым записан прайс."""
    text = raw.strip()
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1]
    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        return text[1:-1]
    # У незакавыченного скаляра комментарий отрезается; у закавыченного — нет.
    if " #" in text:
        text = text.split(" #", 1)[0].strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _parse_simple_yaml(text: str) -> dict[str, object]:
    """Мини-разбор YAML: вложенные словари по отступам, скаляры, комментарии.

    Внешних зависимостей у пакета нет (pyproject: dependencies = []), а прайс — это
    два уровня вложенности без списков и якорей. Списки и многострочные скаляры
    сознательно НЕ поддержаны: встретив их, разбор обязан упасть, а не угадать.
    """
    root: dict[str, object] = {}
    stack: list[tuple[int, dict[str, object]]] = [(-1, root)]
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if stripped.startswith("- "):
            raise PriceListError(f"строка {lineno}: списки в прайсе не поддержаны")
        if ":" not in stripped:
            raise PriceListError(f"строка {lineno}: ожидалась пара ключ: значение")
        key, _, raw_value = stripped.partition(":")
        key = key.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise PriceListError(f"строка {lineno}: сломан отступ")
        parent = stack[-1][1]
        if raw_value.strip() == "":
            child: dict[str, object] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(raw_value)
    return root


def load_prices(path: Path | str = DEFAULT_PRICELIST) -> dict[str, PriceItem]:
    """Прайс из YAML. Единственная точка чтения цен для reply.py и lint.py (Е1)."""
    file_path = Path(path)
    try:
        raw_text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PriceListError(f"прайс не прочитан: {file_path}: {exc}") from exc
    data = _parse_simple_yaml(raw_text)
    items = data.get("items")
    if not isinstance(items, dict) or not items:
        raise PriceListError(f"в прайсе нет раздела items: {file_path}")
    result: dict[str, PriceItem] = {}
    for key, body in items.items():
        if not isinstance(body, dict):
            raise PriceListError(f"пункт {key} не словарь")
        try:
            low = int(body["min"])  # type: ignore[arg-type]
            high = int(body["max"])  # type: ignore[arg-type]
            item = PriceItem(
                key=key,
                label_ru=str(body["label_ru"]),
                label_en=str(body["label_en"]),
                unit_ru=str(body["unit_ru"]),
                unit_en=str(body["unit_en"]),
                min=low,
                max=high,
                origin=str(body["origin"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PriceListError(f"пункт {key}: неполный или битый: {exc}") from exc
        if item.min > item.max:
            raise PriceListError(f"пункт {key}: min больше max")
        result[key] = item
    return result


# --- сборка черновика ---


def _has_cyrillic(text: str) -> bool:
    return any("Ѐ" <= ch <= "ӿ" for ch in text)


def resolve_language(message: InboundMessage, facts: LeadFacts) -> str:
    """Язык ответа = язык обращения. При расхождении флага и текста верим тексту (Е2).

    facts.language заполняет модель, и она ошибается; кириллица в тексте — свидетельство.
    """
    text_is_ru = _has_cyrillic(message.text)
    flag = (facts.language or "").lower()
    if flag == "ru" and not text_is_ru:
        return "en"  # флаг сказал «ru», кириллицы нет — верим тексту
    if text_is_ru:
        return "ru"
    return "ru" if flag == "ru" else "en"


def format_amount(value: int) -> str:
    """Число в тексте письма: разряды через неразрывный пробел, как пишут люди."""
    return f"{value:,}".replace(",", " ")


def price_line(item: PriceItem, language: str) -> str:
    low, high = format_amount(item.min), format_amount(item.max)
    if language == "ru":
        return f"Ориентир по рынку: {item.label('ru')} — AED {low}–{high} {item.unit('ru')}."
    return f"Market range: {item.label('en')} — AED {low}–{high} {item.unit('en')}."


def _pick_price_keys(facts: LeadFacts, prices: dict[str, PriceItem]) -> tuple[str, ...]:
    """Не больше двух диапазонов: письмо в 6 строк третий не вмещает."""
    keys: list[str] = []
    for request in facts.request_types:
        for key in PRICE_KEY_BY_REQUEST.get(request, ()):
            if key in prices and key not in keys:
                keys.append(key)
    # Один тип запроса — показываем оба его варианта; несколько типов — по одному на тип.
    if len(facts.request_types) > 1:
        keys = []
        for request in facts.request_types:
            for key in PRICE_KEY_BY_REQUEST.get(request, ()):
                if key in prices and key not in keys:
                    keys.append(key)
                    break
    return tuple(keys[:2])


def _needs_human(facts: LeadFacts, tier: Tier) -> bool:
    """Кого звать человеком: горячих, срочных и тех, кого не смогли оценить."""
    if tier in (Tier.HIGH, Tier.INVALID):
        return True
    return facts.timeline_days is not None and facts.timeline_days <= URGENT_TIMELINE_DAYS


def _grounded_meeting(facts: LeadFacts, language: str) -> str:
    """Спрашивать нечего — значит, опираемся на сказанное клиентом, а не переспрашиваем."""
    parts: list[str] = []
    for key in CLOSER_QUESTION_ORDER:
        template = GROUNDING.get(key)
        if template is None or not FACT_IS_KNOWN[key](facts):
            continue
        parts.append(template[language].format(value=getattr(facts, key)))
        if len(parts) == 2:  # две опоры — предел: строка должна остаться читаемой
            break
    if not parts:
        return CLOSER_MEETING[language]
    joiner = " и " if language == "ru" else " and "
    lead = joiner.join(parts)
    return f"{lead[0].upper()}{lead[1:]} — {GROUNDED_MEETING_TAIL[language]}"


def _closer(facts: LeadFacts, tier: Tier, language: str) -> str:
    """Последняя строка: срочность, затем первый НЕизвестный факт, затем опора на известное."""
    if facts.timeline_days is not None and facts.timeline_days <= URGENT_TIMELINE_DAYS:
        return CLOSER_URGENT[language]
    for key in CLOSER_QUESTION_ORDER:
        if not FACT_IS_KNOWN[key](facts):
            return CLOSER_QUESTION[key][language]
    return _grounded_meeting(facts, language)


def _questions_draft(language: str, needs_human: bool) -> Reply:
    lines = [QUESTIONS_INTRO[language], *QUESTIONS[language], QUESTIONS_CLOSER[language]]
    return Reply(
        body="\n".join(lines),
        language=language,
        used_prices=(),
        needs_human=needs_human,
        outcome=OUTCOME_QUESTIONS,
    )


def draft(
    message: InboundMessage,
    facts: LeadFacts,
    tier: Tier,
    prices: dict[str, PriceItem] | None = None,
) -> Reply:
    """Черновик ответа. Цены — только из прайса, точных обещаний — ни одного."""
    language = resolve_language(message, facts)

    # Спам: отдельный исход. Черновика нет, и человека дёргать не за чем.
    if facts.is_spam:
        return Reply(
            body="",
            language=language,
            used_prices=(),
            needs_human=False,
            outcome=OUTCOME_SPAM_SKIPPED,
        )

    if prices is None:
        prices = load_prices()
    needs_human = _needs_human(facts, tier)

    # Фактов мало — спрашиваем, а не считаем. Цена по домыслу дороже лишнего вопроса.
    if not facts.request_types or facts.confidence < MIN_CONFIDENCE_FOR_PRICE:
        return _questions_draft(language, needs_human)

    keys = _pick_price_keys(facts, prices)
    if not keys:
        # Тип запроса есть (например, банк), а цены для него в прайсе нет — не выдумываем.
        return _questions_draft(language, needs_human)

    lines = [GREETING[language]]
    for request in facts.request_types[:2]:
        lines.append(SUBSTANCE[request][language])
    lines.extend(price_line(prices[key], language) for key in keys)
    lines.append(DISCLAIMER[language])
    lines.append(_closer(facts, tier, language))

    # 4-6 строк: если типов и диапазонов набралось много, режем середину, а не концовку —
    # ответ по существу и вопрос в конце нужнее второго диапазона.
    while len(lines) > 6:
        del lines[2]

    return Reply(
        body="\n".join(lines),
        language=language,
        used_prices=keys,
        needs_human=needs_human,
        outcome=OUTCOME_DRAFT,
    )
