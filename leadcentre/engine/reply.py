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

Языки ответа: ru, en, ar. **НЕПРОВЕРЕНО (Ц4): арабские формулировки в этом файле и в
data/pricelist_demo.yaml составлены не носителем языка и носителем не вычитаны.** До
показа реальному клиенту их обязан просмотреть человек, владеющий арабским; до тех пор
арабский черновик — материал для правки, а не готовое письмо.

Направление письма: арабская строка идёт справа налево, а вставки вида «AED 35 000-60 000»
внутри неё — слева направо. Без разметки такая вставка визуально распадается (валюта,
число и тире переставляются местами). Поэтому каждая латинско-цифровая вставка
заворачивается в изолят LRI…PDI (U+2066…U+2069), а строка начинается с RLM (U+200F),
задающего направление абзаца.

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

# Поддерживаемые языки ответа. Всё, что не опознано по тексту, уходит в en.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("ru", "en", "ar")
FALLBACK_LANGUAGE = "en"
RTL_LANGUAGES: tuple[str, ...] = ("ar",)

# Юникод-разметка направления (см. докстринг модуля).
RLM = "\u200f"   # RIGHT-TO-LEFT MARK: задаёт направление строки
LRI = "\u2066"   # LEFT-TO-RIGHT ISOLATE: начало латинско-цифровой вставки
PDI = "\u2069"   # POP DIRECTIONAL ISOLATE: конец вставки

# Диапазоны письменностей — свидетельство языка обращения (Е2). Порядок = приоритет.
SCRIPT_RANGES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("ar", (("\u0600", "\u06ff"), ("\u0750", "\u077f"), ("\ufb50", "\ufdff"), ("\ufe70", "\ufeff"))),
    ("ru", (("\u0400", "\u04ff"),)),
)

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
        "ar": "بخصوص المكتب: لدينا مركز أعمال خاص بنا في دبي، فيه مكاتب صغيرة "
              "ومكاتب مرنة تغطي حصة التأشيرات.",
    },
    RequestType.SETUP: {
        "ru": "По регистрации: считаем оба варианта — mainland и фризона, "
              "выбор зависит от вида деятельности и того, нужны ли визы.",
        "en": "On the setup: we compare both routes — mainland and free zone; "
              "the choice depends on your activity and how many visas you need.",
        "ar": "بخصوص التأسيس: نحسب المسارين معًا — البر الرئيسي والمنطقة الحرة، "
              "والاختيار يعتمد على النشاط وعدد التأشيرات المطلوبة.",
    },
    RequestType.VISA: {
        "ru": "По визам: оформляем рабочие визы под ключ — медкомиссия, Emirates ID, "
              "штамп; квота считается от площади офиса.",
        "en": "On visas: we handle employment visas end to end — medical, Emirates ID, "
              "stamping; the quota depends on your office space.",
        "ar": "بخصوص التأشيرات: نتولى تأشيرات العمل من البداية إلى النهاية — الفحص الطبي "
              "والهوية الإماراتية والختم، والحصة تعتمد على مساحة المكتب.",
    },
    RequestType.ACCOUNTING: {
        "ru": "По бухгалтерии: ведём учёт, VAT и корпоративный налог, "
              "объём работ зависит от числа операций в месяц.",
        "en": "On accounting: we cover bookkeeping, VAT and corporate tax; "
              "the scope depends on your monthly transaction volume.",
        "ar": "بخصوص المحاسبة: نتولى الدفاتر وضريبة القيمة المضافة وضريبة الشركات، "
              "وحجم العمل يعتمد على عدد العمليات شهريًا.",
    },
    RequestType.BANK: {
        "ru": "По счёту: сопровождаем открытие в местных банках, "
              "решение принимает банк, мы готовим комплект и защищаем заявку.",
        "en": "On banking: we support account opening with local banks — the bank "
              "decides, we prepare and defend the application.",
        "ar": "بخصوص الحساب البنكي: ندعم فتح الحساب لدى البنوك المحلية — القرار للبنك، "
              "ونحن نجهّز الملف وندافع عن الطلب.",
    },
    RequestType.OTHER: {
        "ru": "Спасибо за обращение — разберём вашу задачу по шагам.",
        "en": "Thanks for reaching out — let us take your case step by step.",
        "ar": "شكرًا لتواصلكم — سنراجع طلبكم خطوة بخطوة.",
    },
}

GREETING = {"ru": "Здравствуйте!", "en": "Hello,", "ar": "مرحبًا،"}

# Оговорка вместо точной цены: обещать точную цифру черновику запрещено.
DISCLAIMER = {
    "ru": "Это рыночный диапазон, итог зависит от вида деятельности и числа виз — "
          "посчитаем точно после короткого разговора.",
    "en": "This is a market range; the final figure depends on your activity and visa "
          "count — we will price it exactly after a short call.",
    "ar": "هذا مدى سوقي، والمبلغ النهائي يعتمد على النشاط وعدد التأشيرات — "
          "نحسبه بدقة بعد مكالمة قصيرة.",
}

CLOSER_URGENT = {
    "ru": "Вижу, что сроки сжатые: возьмём в работу сегодня — во сколько удобно созвониться?",
    "en": "Your timeline looks tight: we can start today — what time suits a call?",
    "ar": "أرى أن الوقت ضيق: نبدأ اليوم — ما الوقت المناسب لمكالمة؟",
}
CLOSER_MEETING = {
    "ru": "Удобно встретиться в нашем офисе в Дубае на этой неделе или созвониться?",
    "en": "Would a meeting at our Dubai office this week work, or a call instead?",
    "ar": "هل يناسبكم لقاء في مكتبنا بدبي هذا الأسبوع، أم مكالمة؟",
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
        "ar": "كم عدد الموظفين الذين تخططون لتوظيفهم في السنة الأولى؟",
    },
    "timeline_days": {
        "ru": "К какому сроку нужно, чтобы всё было готово?",
        "en": "By when do you need everything up and running?",
        "ar": "ما الموعد الذي تحتاجون أن يكون فيه كل شيء جاهزًا؟",
    },
    "jurisdiction_hint": {
        "ru": "Смотрите mainland или фризону — или как раз хотите сравнить два варианта?",
        "en": "Are you leaning towards mainland or a free zone — or would you compare both?",
        "ar": "هل تميلون إلى البر الرئيسي أم إلى منطقة حرة، أم نقارن بين الخيارين؟",
    },
    "has_contact": {
        "ru": "Оставьте номер WhatsApp — пришлём расчёт туда и не потеряем ваш вопрос.",
        "en": "Share a WhatsApp number and we will send the numbers there.",
        "ar": "شاركونا رقم واتساب وسنرسل الأرقام عليه.",
    },
}

# Опора на известное для случая, когда спрашивать больше нечего.
GROUNDING = {
    "headcount": {
        "ru": "вас {value} человек",
        "en": "there are {value} of you",
        "ar": "عددكم {value}",
    },
    "timeline_days": {
        "ru": "срок {value} дн.",
        "en": "your timeline is {value} days",
        "ar": "المدة {value} يومًا",
    },
    "jurisdiction_hint": {
        "ru": "формат {value}",
        "en": "you are looking at {value}",
        "ar": "الخيار {value}",
    },
}
GROUNDED_MEETING_TAIL = {
    "ru": "предлагаю созвон сегодня или встречу в нашем офисе в Дубае.",
    "en": "let us do a call today or meet at our Dubai office.",
    "ar": "أقترح مكالمة اليوم أو لقاءً في مكتبنا بدبي.",
}
# Чем склеиваются две опоры в строке-предложении встречи.
GROUNDING_JOINER = {"ru": " и ", "en": " and ", "ar": "، و"}

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
    "ar": (
        "ما الذي تحتاجونه أولًا — تأسيس شركة أم مكتب أم تأشيرات أم محاسبة؟",
        "هل تنظرون إلى البر الرئيسي أم إلى منطقة حرة، وكم تأشيرة تحتاجون؟",
        "ما الموعد النهائي الذي تحتاجون القرار قبله؟",
    ),
}
QUESTIONS_INTRO = {
    "ru": "Здравствуйте! Чтобы ответить по делу и без лишних цифр, уточните пару вещей.",
    "en": "Hello, to answer precisely and without guessing numbers, a couple of questions.",
    "ar": "مرحبًا! لكي نجيب بدقة ومن دون أرقام تخمينية، نحتاج توضيح نقطتين.",
}
QUESTIONS_CLOSER = {
    "ru": "Ответьте одной строкой — подготовим расчёт и вышлем в течение дня.",
    "en": "One line back is enough — we will prepare the numbers and send them the same day.",
    "ar": "يكفي سطر واحد في ردكم — سنجهّز الحساب ونرسله في اليوم نفسه.",
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
    label_ar: str
    unit_ru: str
    unit_en: str
    unit_ar: str
    min: int
    max: int
    origin: str

    def label(self, language: str) -> str:
        return {"ru": self.label_ru, "ar": self.label_ar}.get(language, self.label_en)

    def unit(self, language: str) -> str:
        return {"ru": self.unit_ru, "ar": self.unit_ar}.get(language, self.unit_en)


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
                label_ar=str(body["label_ar"]),
                unit_ru=str(body["unit_ru"]),
                unit_en=str(body["unit_en"]),
                unit_ar=str(body["unit_ar"]),
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


def detect_script_language(text: str) -> str | None:
    """Язык по письменности текста. None — письменность ничего не сказала (латиница)."""
    for language, ranges in SCRIPT_RANGES:
        if any(low <= ch <= high for ch in text for low, high in ranges):
            return language
    return None


def resolve_language(message: InboundMessage, facts: LeadFacts) -> str:
    """Язык ответа = язык обращения. При расхождении флага и текста верим тексту (Е2).

    facts.language заполняет модель, и она ошибается; письменность обращения —
    свидетельство: арабская вязь и кириллица опознаются по диапазонам юникода. Латиница
    сама по себе языка не доказывает, поэтому там решает флаг, а его неизвестные значения
    (mixed, пустое, язык без шаблонов) сводятся к английскому — молча писать не на том
    языке хуже, чем писать на общем.
    """
    by_script = detect_script_language(message.text)
    if by_script is not None:
        return by_script
    flag = (facts.language or "").lower()
    return flag if flag in SUPPORTED_LANGUAGES else FALLBACK_LANGUAGE


def format_amount(value: int) -> str:
    """Число в тексте письма: разряды через неразрывный пробел, как пишут люди."""
    return f"{value:,}".replace(",", " ")


def ltr_run(text: str) -> str:
    """Латинско-цифровая вставка внутри строки: изолируется, чтобы не рассыпаться в RTL.

    Вставку изолируем всегда, а не только в арабском: в ru/en изолят ничего не меняет
    визуально, зато не приходится держать две ветки форматирования (Е1).
    """
    return f"{LRI}{text}{PDI}"


def price_line(item: PriceItem, language: str) -> str:
    amount = ltr_run(f"AED {format_amount(item.min)}–{format_amount(item.max)}")
    if language == "ru":
        return f"Ориентир по рынку: {item.label('ru')} — {amount} {item.unit('ru')}."
    if language == "ar":
        # RLM в начале: строка начинается с арабского, направление абзаца — RTL.
        return f"{RLM}المدى السوقي: {item.label('ar')} — {amount} {item.unit('ar')}."
    return f"Market range: {item.label('en')} — {amount} {item.unit('en')}."


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
        parts.append(template[language].format(value=ltr_run(str(getattr(facts, key)))))
        if len(parts) == 2:  # две опоры — предел: строка должна остаться читаемой
            break
    if not parts:
        return CLOSER_MEETING[language]
    joiner = GROUNDING_JOINER[language]
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
    if language in RTL_LANGUAGES:
        lines = [RLM + line for line in lines]
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

    if language in RTL_LANGUAGES:
        lines = [line if line.startswith(RLM) else RLM + line for line in lines]

    return Reply(
        body="\n".join(lines),
        language=language,
        used_prices=keys,
        needs_human=needs_human,
        outcome=OUTCOME_DRAFT,
    )
