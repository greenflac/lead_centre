"""Проверка черновика перед показом менеджеру. Правила — данные, вердиктов — три.

Три исхода (Р1): OK / VIOLATIONS (список нарушений) / UNVERIFIABLE («не смогли проверить»).
Третий не сворачивается ни в первый, ни во второй: если прайс не прочитан, проверка цифр
не отработала, и «нарушений нет» здесь означало бы «мы не смотрели». Поэтому вердикт
UNVERIFIABLE выносится, даже когда остальные проверки чистые, а найденные нарушения всё
равно печатаются рядом — вместе с тремя числами: проверено N, нарушений M, не смогли K (Р2).

Прайс читается функцией из reply.py — одно знание в одном месте (Е1): линтер и генератор
обязаны видеть один и тот же диапазон, иначе проверка проверяет не то, что напечатано.

Арабский. Линтер обязан работать на нём так же, как на ru/en: арабский черновик пишет
модель, и линтер — единственный шлюз между её текстом и клиентом. ИЗМЕРЕНО (2026-09-09,
scratchpad/check_arabic.py): до нормализации проверка чисел пропускала цену вне диапазона
в шести записях из девяти — арабский разделитель разрядов U+066C резал «٩٠٠٬٠٠٠» до 900,
изолят между AED и числом прятал сумму целиком, а валюта «د.إ» или «درهم» не опознавалась
вовсе. Поэтому перед проверками текст нормализуется: снимаются метки направления,
арабо-индийские и персидские цифры переводятся в ASCII, арабские разделители — в пробел
и точку. Молчаливый пропуск здесь опаснее ложного срабатывания.
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

# --- пороги и словари как данные ---

MAX_LINES = 6  # ВЫБРАНО (автор, 2026-09-09): договор «4-6 строк» из ТЗ. Мутация красит тест (Т1).

# --- нормализация текста перед проверками ---

# Невидимая разметка направления: для проверок шум, для вёрстки смысл (см. reply.py).
BIDI_CONTROLS = "\u200e\u200f\u061c\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"

# Цифры: арабо-индийские (U+0660..) и персидские (U+06F0..) — в ASCII.
DIGIT_MAP = {
    **{chr(0x0660 + i): str(i) for i in range(10)},
    **{chr(0x06F0 + i): str(i) for i in range(10)},
}
# Арабские разделители: U+066C — разряды, U+066B — десятичный.
SEPARATOR_MAP = {"\u066c": " ", "\u066b": "."}
_NORMALIZE_TABLE = str.maketrans({
    **dict.fromkeys(BIDI_CONTROLS, ""),
    **DIGIT_MAP,
    **SEPARATOR_MAP,
})

# Арабское письмо: огласовки и татвиль на смысл не влияют, а совпадение ломают.
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
    """Текст в форму, на которой сравнение осмысленно. Длину по нему НЕ считаем."""
    folded = unicodedata.normalize("NFKC", text)
    return folded.translate(_NORMALIZE_TABLE).translate(_ARABIC_LETTER_MAP).lower()


# Слоп-фразы: пустая вежливость, по которой письмо читается как рассылка.
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
    # Арабские аналоги тех же клише. Сравнение идёт по normalize(), поэтому огласовки
    # и написание أ/ا/ة роли не играют.
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

# Обещание срока государственной процедуры: «лицензия за 3 дня», "visa within 5 days".
# Сроки держит не SORP, а орган; обещание такого срока — обещание за чужой счёт.
GOV_SUBJECT_RU = r"(лиценз\w*|виз\w*|разрешен\w*|регистрац\w*|emirates\s*id|вид на жительство)"
GOV_SUBJECT_EN = r"(licen[cs]\w*|visa\w*|permit\w*|registration|emirates\s*id|residency)"
GOV_PERIOD_RU = r"(за|через)\s+\d+\s*(рабоч\w*\s+)?(дн\w*|недел\w*|час\w*|мес\w*)"
GOV_PERIOD_EN = r"(in|within)\s+\d+\s*(business\s+|working\s+)?(day|week|hour|month)s?"
# Арабские шаблоны — в нормализованной форме (أ/إ -> ا, ة -> ه, без огласовок).
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

# Число: либо с разрядами («35 000», «1,500»), либо простое. Десятичная точка — точка.
_NUM = r"\d{1,3}(?:[   ,]\d{3})+|\d+(?:\.\d+)?"
_DASH = r"[-–—]"
# Как в тексте может быть записана валюта: латиницей, кириллицей и по-арабски.
_CURRENCY = r"AED|د\.?\s?ا|درهم\w*|دراهم|dirham\w*|дирхам\w*"
AED_PATTERNS: tuple[str, ...] = (
    rf"(?:{_CURRENCY})\s*({_NUM})(?:\s*{_DASH}\s*({_NUM}))?",
    rf"({_NUM})(?:\s*{_DASH}\s*({_NUM}))?\s*(?:{_CURRENCY})",
)

# Названия проверок — они же ключи в счётчиках «проверено» и «не смогли».
CHECK_LENGTH = "длина"
CHECK_SLOP = "слоп-фразы"
CHECK_DEADLINE = "обещания сроков"
CHECK_PRICES = "числа AED в диапазоне прайса"

STATUS_OK = "OK"
STATUS_VIOLATIONS = "VIOLATIONS"
STATUS_UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True)
class LintResult:
    """Вердикт линтера. Годно — только когда отработали все проверки и нарушений нет."""

    status: str
    violations: tuple[str, ...] = ()
    checks_done: tuple[str, ...] = ()
    checks_failed: tuple[str, ...] = field(default_factory=tuple)
    numbers_checked: tuple[float, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def summary(self) -> str:
        """Строка для отчёта: три числа рядом, без агрегатного булева (Р2, Е3)."""
        return (
            f"проверено {len(self.checks_done)}, нарушений {len(self.violations)}, "
            f"не смогли {len(self.checks_failed)}"
        )


def _parse_amount(raw: str) -> float:
    for space in (" ", "\u00a0", "\u202f", ","):
        raw = raw.replace(space, "")
    return float(raw)


def extract_aed_amounts(text: str) -> tuple[float, ...]:
    """Числа рядом с AED/дирхамами. Прочие числа (люди, дни) сюда не попадают.

    Текст нормализуется: без этого арабская запись суммы проходит мимо проверки молча.
    """
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
        # П1: канал воздействия без счётчика — цена «использована», но в текст не доехала.
        violations.append(
            f"{CHECK_PRICES}: заявлены цены {reply.used_prices}, а чисел в тексте нет"
        )
    return amounts


def lint(
    reply: Reply,
    prices: dict[str, PriceItem] | None = None,
    pricelist_path: Path | str = DEFAULT_PRICELIST,
) -> LintResult:
    """Проверить черновик. Прайс можно передать готовым — иначе читается с диска."""
    violations: list[str] = []
    done: list[str] = []
    failed: list[str] = []

    # Спама здесь быть не должно: черновика нет, проверять нечего — и это не «годно».
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
