"""Проверка черновика перед показом менеджеру. Правила — данные, вердиктов — три.

Три исхода (Р1): OK / VIOLATIONS (список нарушений) / UNVERIFIABLE («не смогли проверить»).
Третий не сворачивается ни в первый, ни во второй: если прайс не прочитан, проверка цифр
не отработала, и «нарушений нет» здесь означало бы «мы не смотрели». Поэтому вердикт
UNVERIFIABLE выносится, даже когда остальные проверки чистые, а найденные нарушения всё
равно печатаются рядом — вместе с тремя числами: проверено N, нарушений M, не смогли K (Р2).

Прайс читается функцией из reply.py — одно знание в одном месте (Е1): линтер и генератор
обязаны видеть один и тот же диапазон, иначе проверка проверяет не то, что напечатано.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from leadcentre.engine.reply import (
    DEFAULT_PRICELIST,
    OUTCOME_SPAM_SKIPPED,
    PriceItem,
    PriceListError,
    Reply,
    load_prices,
)

# --- пороги и словари как данные ---

MAX_LINES = 6  # ВЫБРАНО (автор, 2026-09-09): договор «4-6 строк» из ТЗ. Мутация красит тест (Т1).

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
)

# Обещание срока государственной процедуры: «лицензия за 3 дня», "visa within 5 days".
# Сроки держит не SORP, а орган; обещание такого срока — обещание за чужой счёт.
GOV_SUBJECT_RU = r"(лиценз\w*|виз\w*|разрешен\w*|регистрац\w*|emirates\s*id|вид на жительство)"
GOV_SUBJECT_EN = r"(licen[cs]\w*|visa\w*|permit\w*|registration|emirates\s*id|residency)"
GOV_PERIOD_RU = r"(за|через)\s+\d+\s*(рабоч\w*\s+)?(дн\w*|недел\w*|час\w*|мес\w*)"
GOV_PERIOD_EN = r"(in|within)\s+\d+\s*(business\s+|working\s+)?(day|week|hour|month)s?"
DEADLINE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("ru-предмет-срок", rf"{GOV_SUBJECT_RU}[^.\n]{{0,40}}{GOV_PERIOD_RU}"),
    ("ru-срок-предмет", rf"{GOV_PERIOD_RU}[^.\n]{{0,40}}{GOV_SUBJECT_RU}"),
    ("en-предмет-срок", rf"{GOV_SUBJECT_EN}[^.\n]{{0,40}}{GOV_PERIOD_EN}"),
    ("en-срок-предмет", rf"{GOV_PERIOD_EN}[^.\n]{{0,40}}{GOV_SUBJECT_EN}"),
)

# Число: либо с разрядами («35 000», «1,500»), либо простое. Десятичная точка — точка.
_NUM = r"\d{1,3}(?:[   ,]\d{3})+|\d+(?:\.\d+)?"
_DASH = r"[-–—]"
AED_PATTERNS: tuple[str, ...] = (
    rf"AED\s*({_NUM})(?:\s*{_DASH}\s*({_NUM}))?",
    rf"({_NUM})(?:\s*{_DASH}\s*({_NUM}))?\s*(?:AED\b|дирхам\w*|dirham\w*)",
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
    return float(raw.replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ""))


def extract_aed_amounts(text: str) -> tuple[float, ...]:
    """Числа, стоящие рядом с AED/дирхамами. Прочие числа (люди, дни) сюда не попадают."""
    found: list[float] = []
    for pattern in AED_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
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
    low = body.lower()
    for phrase in SLOP_PHRASES:
        if phrase in low:
            violations.append(f"{CHECK_SLOP}: «{phrase}»")


def _check_deadlines(body: str, violations: list[str]) -> None:
    low = body.lower()
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
    if reply.outcome == OUTCOME_SPAM_SKIPPED or not reply.body.strip():
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
