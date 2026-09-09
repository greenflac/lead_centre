"""Извлечение фактов из текста обращения моделью Claude. Модель предлагает — код решает.

Три исхода вместо двух (Р1):
  * `LeadFacts` — извлекли, факты годные (в т.ч. `is_spam=True` — это тоже результат);
  * `LeadFacts` из режима OFFLINE — детерминированная заглушка, `confidence=0.0`;
  * `ExtractionError` — не смогли извлечь (нет ключа, сеть, невалидный ответ).
Пустой `LeadFacts` вместо ошибки не возвращается никогда: «модель ничего не нашла» и
«мы не доехали до модели» — разные вещи для отчёта и для менеджера.

Персональные данные (телефон, почта) вырезаются из текста ДО отправки в модель
(`scrub_pii`), а `has_contact` считает код по факту вырезанного, а не модель (Е2):
модель контактов не видит и видеть не должна.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from leadcentre.models import InboundMessage, LeadFacts, RequestType

# --- константы-решения ---

DEFAULT_MODEL = "claude-opus-5"          # ВЫБРАНО: значение по умолчанию, переопределяется LLM_MODEL
MAX_TOKENS = 4096                        # ВЫБРАНО: ответ — один JSON-объект, с запасом
EFFORT = "low"                           # ВЫБРАНО: извлечение из абзаца текста — простая задача
TIMEOUT_S = 60.0                         # ВЫБРАНО: чат-канал, дольше ждать смысла нет
MAX_RETRIES = 2                          # ВЫБРАНО: столько же, сколько по умолчанию у SDK
MIN_PHONE_DIGITS = 9                     # ВЫБРАНО: короче — это не телефон, а «8 человек» или дата
MIXED_SHARE = 0.2                        # ВЫБРАНО: доля второго алфавита, ниже которой это не «mixed»,
                                         # а имя собственное латиницей внутри русской фразы (TECOM, IFZA)

PROMPT_VERSION = "extract_v1"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / f"{PROMPT_VERSION}.md"

PHONE_MASK = "[phone]"
EMAIL_MASK = "[email]"

# Порядок: почта раньше телефона, иначе хвост номера внутри адреса маскируется как телефон.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w])\+?\d[\d\-\s().]{5,}\d(?![\w])")


class ExtractionError(RuntimeError):
    """Третий исход: извлечь не смогли. Не сворачивается в пустой LeadFacts (Р1)."""


@dataclass(frozen=True)
class Scrubbed:
    """Текст без персональных данных плюс числа, а не флаг (Е3/Р2)."""

    text: str
    phones: int
    emails: int

    @property
    def has_contact(self) -> bool:
        return bool(self.phones or self.emails)

    def summary(self) -> str:
        return f"вырезано: телефонов {self.phones}, адресов почты {self.emails}"


@dataclass(frozen=True)
class Extraction:
    """Факты плюс то, чем они получены: для отчёта, логов и счёта за токены."""

    facts: LeadFacts
    model: str
    elapsed_s: float
    input_tokens: int
    output_tokens: int
    offline: bool
    scrubbed: Scrubbed
    dropped_quotes: int      # цитат, которых в обращении нет: модель их придумала


# --- минимизация персональных данных ---


def _mask_phone(match: re.Match[str]) -> str:
    """Маскируем только то, где хватает цифр на номер: «8 человек» и «2026-09» — не телефон."""
    digits = sum(c.isdigit() for c in match.group(0))
    if digits < MIN_PHONE_DIGITS:
        return match.group(0)
    # Пробелы по краям захвата возвращаем на место, иначе слова слипнутся.
    head = match.group(0)[: len(match.group(0)) - len(match.group(0).lstrip())]
    tail = match.group(0)[len(match.group(0).rstrip()):]
    return f"{head}{PHONE_MASK}{tail}"


def scrub_pii(text: str) -> Scrubbed:
    """Вырезает телефоны и адреса почты. Всё, что уходит в модель, проходит через это."""
    without_email, emails = EMAIL_RE.subn(EMAIL_MASK, text)
    phones = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal phones
        masked = _mask_phone(match)
        if PHONE_MASK in masked:
            phones += 1
        return masked

    without_phone = PHONE_RE.sub(replace, without_email)
    return Scrubbed(text=without_phone, phones=phones, emails=emails)


# --- схема ответа: поле в поле с LeadFacts (Е1) ---


def _schema() -> dict:
    """JSON-схема ответа модели. Одно знание — одно место: поля берутся из LeadFacts."""
    return {
        "type": "object",
        "properties": {
            "request_types": {
                "type": "array",
                "items": {"type": "string", "enum": [t.value for t in RequestType]},
            },
            "jurisdiction_hint": {"type": ["string", "null"]},
            "headcount": {"type": ["integer", "null"], "minimum": 1},
            "timeline_days": {"type": ["integer", "null"], "minimum": 0},
            "budget_hint": {"type": ["string", "null"]},
            "language": {"type": "string", "enum": ["ru", "en", "ar", "mixed"]},
            "is_spam": {"type": "boolean"},
            "has_contact": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "quotes": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "request_types",
            "jurisdiction_hint",
            "headcount",
            "timeline_days",
            "budget_hint",
            "language",
            "is_spam",
            "has_contact",
            "confidence",
            "quotes",
        ],
        "additionalProperties": False,
    }


def load_prompt() -> str:
    """Промпт живёт файлом с версией в имени; в коде — только загрузка (Е1)."""
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtractionError(f"не читается промпт {PROMPT_PATH}: {exc}") from exc


def build_request_body(message: InboundMessage) -> tuple[dict, Scrubbed]:
    """Тело запроса к модели. Отдельной функцией — чтобы негативный контроль по
    телефону проверял ровно то, что уходит в сеть, а не его пересказ (И5)."""
    scrubbed = scrub_pii(message.text)
    body = {
        "model": os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
        "max_tokens": MAX_TOKENS,
        "system": load_prompt(),
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Канал: {message.channel}. Дата: {message.received_at.isoformat()}.\n"
                    f"Текст обращения:\n{scrubbed.text}"
                ),
            }
        ],
        "output_config": {"effort": EFFORT, "format": {"type": "json_schema", "schema": _schema()}},
    }
    return body, scrubbed


# --- разбор ответа ---


def _to_facts(raw: dict, scrubbed: Scrubbed) -> tuple[LeadFacts, int]:
    """Валидированный схемой JSON → LeadFacts. Цитаты сличаются с текстом (Е2)."""
    types: list[RequestType] = []
    for value in raw.get("request_types") or []:
        try:
            kind = RequestType(value)
        except ValueError as exc:
            raise ExtractionError(f"неизвестный request_type: {value!r}") from exc
        if kind not in types:
            types.append(kind)

    # Цитата — доказательство; недословную выбрасываем и считаем, а не молчим.
    quotes = [q for q in (raw.get("quotes") or []) if q and q in scrubbed.text]
    dropped = len(raw.get("quotes") or []) - len(quotes)

    confidence = float(raw.get("confidence") or 0.0)
    if not 0.0 <= confidence <= 1.0:
        raise ExtractionError(f"confidence вне диапазона: {confidence}")

    facts = LeadFacts(
        request_types=tuple(types),
        jurisdiction_hint=raw.get("jurisdiction_hint"),
        headcount=raw.get("headcount"),
        timeline_days=raw.get("timeline_days"),
        budget_hint=raw.get("budget_hint"),
        language=raw.get("language") or "en",
        is_spam=bool(raw.get("is_spam")),
        # has_contact — по свидетельству вырезанного, а не по слову модели (Е2).
        has_contact=scrubbed.has_contact,
        confidence=confidence,
        quotes=tuple(quotes),
    )
    return facts, dropped


# --- режим OFFLINE ---


def detect_language(text: str) -> str:
    """Язык по алфавиту: дешёвая детерминированная оценка для OFFLINE и для сверки с моделью.

    `mixed` — только когда второго алфавита заметно много (`MIXED_SHARE`): «офис в TECOM»
    остаётся `ru`, потому что латиницей там одно имя собственное.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    share = sum(1 for c in letters if "\u0400" <= c <= "\u04ff") / len(letters)
    if share >= 1.0 - MIXED_SHARE:
        return "ru"
    if share <= MIXED_SHARE:
        return "en"
    return "mixed"


def _offline_extraction(message: InboundMessage) -> Extraction:
    """Сеть не трогаем: детерминированная заглушка для тестов и CI.

    `confidence=0.0` и `request_types=(OTHER,)` — честная метка «моделью не смотрено»:
    заглушку нельзя спутать с извлечением (И5).
    """
    scrubbed = scrub_pii(message.text)
    facts = LeadFacts(
        request_types=(RequestType.OTHER,),
        language=detect_language(message.text),
        has_contact=scrubbed.has_contact,
        confidence=0.0,
    )
    return Extraction(
        facts=facts,
        model="offline-stub",
        elapsed_s=0.0,
        input_tokens=0,
        output_tokens=0,
        offline=True,
        scrubbed=scrubbed,
        dropped_quotes=0,
    )


def is_offline() -> bool:
    return os.environ.get("OFFLINE", "") not in ("", "0")


# --- вызов модели ---


def _client():
    """Ключ — из CLAUDE_KEY, запасной вариант ANTHROPIC_API_KEY (в этой среде первый)."""
    api_key = os.environ.get("CLAUDE_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ExtractionError("нет ключа: ни CLAUDE_KEY, ни ANTHROPIC_API_KEY не заданы")
    try:
        import anthropic
    except ImportError as exc:  # П2: дешёвая проверка раньше сетевой
        raise ExtractionError("нет пакета anthropic: pip install anthropic") from exc
    return anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_S, max_retries=MAX_RETRIES)


def extract_detailed(message: InboundMessage) -> Extraction:
    """Извлечение с приборными данными: время, токены, что вырезано, что выброшено."""
    if is_offline():
        return _offline_extraction(message)

    client = _client()
    body, scrubbed = build_request_body(message)
    started = time.monotonic()
    try:
        response = client.messages.create(**body)
    except Exception as exc:  # сеть, 4xx, 5xx — всё это «не смогли», а не пустые факты
        raise ExtractionError(f"запрос к модели не удался: {type(exc).__name__}: {exc}") from exc
    elapsed = time.monotonic() - started

    if response.stop_reason not in ("end_turn", "stop_sequence"):
        raise ExtractionError(f"модель не договорила: stop_reason={response.stop_reason}")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ExtractionError("в ответе модели нет текстового блока")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"ответ модели не разбирается как JSON: {exc}") from exc

    facts, dropped = _to_facts(raw, scrubbed)
    return Extraction(
        facts=facts,
        model=response.model,
        elapsed_s=elapsed,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        offline=False,
        scrubbed=scrubbed,
        dropped_quotes=dropped,
    )


def extract(message: InboundMessage) -> LeadFacts:
    """Факты из обращения. Не смогли — `ExtractionError`, а не пустой LeadFacts (Р1)."""
    return extract_detailed(message).facts
