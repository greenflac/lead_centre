"""Fact extraction from request text by a language model: the model proposes, code decides.

The provider is swappable (`LLM_PROVIDER`); everything shared — PII scrubbing, response
validation and LeadFacts assembly — lives in the pipeline, not in the provider.
Three outcomes, and the third never collapses into the first two: extracted facts,
the deterministic OFFLINE stub, or an exception. An empty LeadFacts is never returned
in place of an error. Reason wording lives in engine/reasons.py.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

from leadcentre.engine.reasons import (
    DEFAULT_LANGUAGE,
    Language,
    RouteReason,
    RouteReasonCode,
    rendered,
    route_reason,
    text_in,
)
from leadcentre.models import InboundMessage, LeadFacts, RequestType

DEFAULT_PROVIDER = "anthropic"
# Why the small model by default: extraction from a short message is simple work and the
# chat channel promises an answer in seconds.
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_EFFORT = "low"
ANTHROPIC_THINKING = "off"
# Why 600: the small model loses relative dates ("с 20 числа") on long walls of text,
# which start well past the median request length. Exactly 600 still counts as short.
LONG_MESSAGE_CHARS = 600
LONG_MODEL = "claude-opus-5"
# Why a list: other models answer 400 to `output_config.effort` and adaptive thinking.
EFFORT_MODELS = ("claude-opus-5", "claude-opus-4-", "claude-sonnet-5", "claude-sonnet-4-6",
                 "claude-fable-")
POLLINATIONS_MODEL = "openai"            # gateway alias for GPT-OSS 20B
POLLINATIONS_URL = "https://text.pollinations.ai/openai"
USER_AGENT = "leadcentre/0.1 (+Lead Centre)"
MAX_TOKENS = 4096
TIMEOUT_S = 60.0
MAX_RETRIES = 2
HEADCOUNT_MIN = 1                        # Why 1: "zero people" is noise, not a fact.
TIMELINE_MIN = 0                         # Why 0: a deadline in the past was invented.
MIN_PHONE_DIGITS = 9                     # Why 9: shorter runs are counts or dates.
# Why a share, not any occurrence: below this it is a Latin proper noun inside a Russian
# phrase (TECOM, IFZA), not a mixed-language request.
MIXED_SHARE = 0.2

# Why versioned by filename: earlier prompts stay alongside for comparison.
PROMPT_VERSION = "extract_v5"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / f"{PROMPT_VERSION}.md"

PHONE_MASK = "[phone]"
EMAIL_MASK = "[email]"

# Why email first: otherwise a digit tail inside an address is masked as a phone.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w])\+?\d[\d\-\s().]{5,}\d(?![\w])")
FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


class ExtractionError(RuntimeError):
    """Extraction failed; never collapsed into an empty LeadFacts."""


class ProviderBudgetError(ExtractionError):
    """The provider refused on billing limits; retrying will not help."""


@dataclass(frozen=True)
class Scrubbed:
    """Text with personal data removed, plus counts of what was removed."""

    text: str
    phones: int
    emails: int

    @property
    def has_contact(self) -> bool:
        """True when a phone or an email was actually cut out of the text."""
        return bool(self.phones or self.emails)

    def summary(self) -> str:
        """Returns a one-line summary of what was removed."""
        return f"вырезано: телефонов {self.phones}, адресов почты {self.emails}"


@dataclass(frozen=True)
class Completion:
    """A provider response in the common shape the pipeline works with."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0     # 0 for providers without a cache
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class Extraction:
    """Facts plus how they were obtained: for reports, logs and the token bill."""

    facts: LeadFacts
    provider: str
    model: str
    elapsed_s: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    offline: bool
    scrubbed: Scrubbed
    dropped_quotes: int      # quotes absent from the request: the model invented them
    route_reason: str = ""
    #: What the model said about `timeline_days` before code recomputed it. Without this
    #: "code replaced the model number" is indistinguishable from "the model said so".
    timeline_from_model: int | None = None

    def route_reason_in(self, language: Language = DEFAULT_LANGUAGE) -> str:
        """Returns the route reason in the given language; a code-less string raises."""
        return text_in(self.route_reason, language)

    def served_by(self) -> str:
        """Returns one line naming what actually served the lead, per the API response."""
        cache = ""
        if self.cache_read_tokens or self.cache_write_tokens:
            cache = (f", кэш: прочитано {self.cache_read_tokens}, "
                     f"записано {self.cache_write_tokens}")
        return (f"обслужено: {self.provider}/{self.model} за {self.elapsed_s:.2f} с "
                f"({self.route_reason}; токены {self.input_tokens}/{self.output_tokens}"
                f"{cache})")


def _mask_phone(match: re.Match[str]) -> str:
    """Masks a match only when it holds enough digits to be a phone number."""
    digits = sum(c.isdigit() for c in match.group(0))
    if digits < MIN_PHONE_DIGITS:
        return match.group(0)
    # Why the edges are restored: without them neighbouring words run together.
    head = match.group(0)[: len(match.group(0)) - len(match.group(0).lstrip())]
    tail = match.group(0)[len(match.group(0).rstrip()):]
    return f"{head}{PHONE_MASK}{tail}"


def scrub_pii(text: str) -> Scrubbed:
    """Removes phones and emails; everything sent to any model passes through here."""
    without_email, emails = EMAIL_RE.subn(EMAIL_MASK, text)
    phones = 0

    def replace(match: re.Match[str]) -> str:
        """Masks one phone match and counts it."""
        nonlocal phones
        masked = _mask_phone(match)
        if PHONE_MASK in masked:
            phones += 1
        return masked

    without_phone = PHONE_RE.sub(replace, without_email)
    return Scrubbed(text=without_phone, phones=phones, emails=emails)


def _schema() -> dict:
    """Returns the JSON schema of the model response; its fields mirror LeadFacts."""
    return {
        "type": "object",
        "properties": {
            "request_types": {
                "type": "array",
                "items": {"type": "string", "enum": [t.value for t in RequestType]},
            },
            "jurisdiction_hint": {"type": ["string", "null"]},
            # Why no bounds here: structured outputs answers 400 to minimum/maximum, so
            # parse_facts enforces HEADCOUNT_MIN / TIMELINE_MIN after parsing.
            "headcount": {"type": ["integer", "null"]},
            "timeline_days": {"type": ["integer", "null"]},
            "budget_hint": {"type": ["string", "null"]},
            "language": {"type": "string", "enum": ["ru", "en", "ar", "mixed"]},
            "is_spam": {"type": "boolean"},
            "has_contact": {"type": "boolean"},
            "confidence": {"type": "number"},
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
    """Reads the prompt file; unreadable raises ExtractionError."""
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtractionError(f"не читается промпт {PROMPT_PATH}: {exc}") from exc


def user_content(message: InboundMessage, scrubbed: Scrubbed) -> str:
    """Builds the user half of the request; the text arrives here only after scrub_pii."""
    # Why the date is spelled out: without an anchor the model counts "с 20 числа" from an
    # arbitrary day and timeline_days stops being reproducible.
    return (
        f"Обращение получено: {message.received_at.isoformat()}\n"
        f"Канал: {message.channel}\n"
        f"Текст обращения:\n{scrubbed.text}"
    )


@dataclass(frozen=True)
class Route:
    """Where this lead is sent; decided from the text length, before any network call."""

    model: str
    effort: str          # "" means: do not send the parameter at all
    thinking: str        # "" means: do not send the parameter at all
    reason: str          # rendered text that remembers its own code
    reason_item: RouteReason | None = None


def _route(model: str, effort: str, thinking: str, item: RouteReason) -> Route:
    """Builds a route; only the reasons.py catalogue renders the reason text."""
    return Route(model, effort, thinking, rendered(item), item)


def route(message: InboundMessage) -> Route:
    """Routes short requests to the cheap model, long ones to the stable model.

    `LLM_MODEL` disables routing so a whole set can be run on one model.
    """
    forced = os.environ.get("LLM_MODEL")
    if forced:
        return _route(forced, os.environ.get("LLM_EFFORT", ""),
                      os.environ.get("LLM_THINKING", ""),
                      route_reason(RouteReasonCode.MODEL_FORCED))
    length = len(message.text)
    if length > LONG_MESSAGE_CHARS:
        return _route(LONG_MODEL, "low", "off", route_reason(
            RouteReasonCode.LONG_MESSAGE, length=length, limit=LONG_MESSAGE_CHARS))
    return _route(ANTHROPIC_MODEL, ANTHROPIC_EFFORT, ANTHROPIC_THINKING, route_reason(
        RouteReasonCode.SHORT_MESSAGE, length=length, limit=LONG_MESSAGE_CHARS))


class Provider(Protocol):
    """Swappable model adapter: request body and response parsing only."""

    name: str

    def model(self) -> str: ...

    def build_body(self, system: str, content: str) -> dict: ...

    def complete(self, body: dict) -> Completion: ...


def anthropic_client():
    """Returns an Anthropic client; the key is read here and only here.

    Public because translit.py shares it: two ways to obtain the key would drift apart.
    """
    api_key = os.environ.get("CLAUDE_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ExtractionError("нет ключа: ни CLAUDE_KEY, ни ANTHROPIC_API_KEY не заданы")
    try:
        import anthropic
    except ImportError as exc:  # caught before any network call
        raise ExtractionError("нет пакета anthropic: pip install anthropic") from exc
    return anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_S, max_retries=MAX_RETRIES)


def anthropic_error(exc: Exception) -> ExtractionError:
    """Converts an API error into our type, keeping billing limits apart from network."""
    text = str(exc)
    # Why matched on text: a low credit balance arrives as an ordinary BadRequestError,
    # indistinguishable by status code from a typo in the body.
    if "credit balance" in text or "billing" in text.lower():
        return ProviderBudgetError(
            "anthropic: кончились деньги на аккаунте — API отвечает 400 "
            "«credit balance is too low». Что делать: пополнить баланс в Console → "
            "Plans & Billing либо переключиться: LLM_PROVIDER=pollinations. "
            f"Ответ API как есть: {text}"
        )
    return ExtractionError(f"anthropic: запрос не удался: {type(exc).__name__}: {text}")


class AnthropicProvider:
    """Claude through the official SDK; the API itself enforces the schema."""

    name = "anthropic"

    def __init__(self, route_: Route | None = None) -> None:
        """Builds the provider, falling back to the environment when no route is given."""
        self.route = route_ or Route(
            os.environ.get("LLM_MODEL") or ANTHROPIC_MODEL,
            os.environ.get("LLM_EFFORT", ANTHROPIC_EFFORT),
            os.environ.get("LLM_THINKING", ANTHROPIC_THINKING),
            "маршрут не задан: значения по умолчанию",
        )

    def model(self) -> str:
        """Returns the routed model name."""
        return self.route.model

    def build_body(self, system: str, content: str) -> dict:
        """Builds an Anthropic request body with structured outputs."""
        model = self.model()
        supports_effort = model.startswith(EFFORT_MODELS)
        body = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": content}],
            # Why cached: system prompt and schema are identical per lead, while the
            # volatile parts follow in messages. Whether it engaged is visible in the
            # cache_read/cache_write counters on Extraction.
            "cache_control": {"type": "ephemeral"},
            "output_config": {"format": {"type": "json_schema", "schema": _schema()}},
        }
        # Why the empty value is dropped rather than sent: models that reject the
        # parameter must not see it in the body at all.
        effort = self.route.effort.strip().lower()
        if effort not in ("", "none") and supports_effort:
            body["output_config"]["effort"] = effort
        thinking = self.route.thinking.strip().lower()
        if thinking == "adaptive" and supports_effort:
            body["thinking"] = {"type": "adaptive"}
        elif thinking in ("off", "disabled") and supports_effort:
            body["thinking"] = {"type": "disabled"}
        return body

    def _client(self):
        return anthropic_client()

    def complete(self, body: dict) -> Completion:
        """Calls the SDK and returns the response in the common shape."""
        client = self._client()
        try:
            response = client.messages.create(**body)
        except Exception as exc:
            raise anthropic_error(exc) from exc

        if response.stop_reason not in ("end_turn", "stop_sequence"):
            raise ExtractionError(f"anthropic: модель не договорила: {response.stop_reason}")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise ExtractionError("anthropic: в ответе нет текстового блока")
        usage = response.usage
        return Completion(
            text=text,
            model=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )


class OpenAICompatibleProvider:
    """An OpenAI chat-completions gateway; the schema rides in the system prompt."""

    name = "pollinations"

    def model(self) -> str:
        """Returns the gateway model name."""
        return os.environ.get("LLM_MODEL") or POLLINATIONS_MODEL

    def build_body(self, system: str, content: str) -> dict:
        """Builds a chat-completions body with the schema appended to the system prompt."""
        schema_note = (
            "\n\n## Формат ответа\n\n"
            "Верни РОВНО один JSON-объект по этой JSON-схеме, без пояснений, "
            "без markdown-ограждения, без текста до и после:\n\n"
            f"{json.dumps(_schema(), ensure_ascii=False, indent=2)}"
        )
        return {
            "model": self.model(),
            "max_tokens": MAX_TOKENS,
            "temperature": 0,     # Why 0: variety in fact extraction is harmful.
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system + schema_note},
                {"role": "user", "content": content},
            ],
        }

    def complete(self, body: dict) -> Completion:
        """Posts the body to the gateway and returns the response in the common shape."""
        api_key = os.environ.get("POLLINATIONS_API_KEY")
        if not api_key:
            raise ExtractionError("нет ключа POLLINATIONS_API_KEY")
        request = urllib.request.Request(
            POLLINATIONS_URL,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                # Why required: the gateway sits behind Cloudflare and answers 403 to
                # the default python-urllib agent.
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if exc.code == 402 or "KEY_BUDGET_EXHAUSTED" in detail:
                raise ProviderBudgetError(
                    "pollinations: исчерпан бюджет ключа — шлюз отвечает 402 "
                    "KEY_BUDGET_EXHAUSTED. Что делать: поднять лимит ключа на "
                    "enter.pollinations.ai/keys (пополнение кошелька лимит НЕ поднимает) "
                    "либо переключиться на другого провайдера: LLM_PROVIDER=anthropic. "
                    f"Ответ шлюза как есть: HTTP {exc.code} {detail}"
                ) from exc
            raise ExtractionError(
                f"pollinations: HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ExtractionError(
                f"pollinations: запрос не удался: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            choice = payload["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ExtractionError(f"pollinations: неожиданная форма ответа: {payload}") from exc
        if choice.get("finish_reason") not in (None, "stop"):
            raise ExtractionError(
                f"pollinations: модель не договорила: finish_reason={choice['finish_reason']}"
            )
        if not text:
            raise ExtractionError("pollinations: пустой content в ответе")
        usage = payload.get("usage") or {}
        return Completion(
            text=text,
            model=payload.get("model") or body["model"],
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )


PROVIDERS: dict[str, type] = {
    AnthropicProvider.name: AnthropicProvider,
    OpenAICompatibleProvider.name: OpenAICompatibleProvider,
}


def _default_is_anthropic() -> bool:
    key = (os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    return key == AnthropicProvider.name


def get_provider(name: str | None = None) -> Provider:
    """Returns a provider by name or by `LLM_PROVIDER`; an unknown name raises."""
    key = (name or os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if key not in PROVIDERS:
        raise ExtractionError(
            f"неизвестный LLM_PROVIDER={key!r}; известны: {', '.join(sorted(PROVIDERS))}"
        )
    return PROVIDERS[key]()


def build_request_body(
    message: InboundMessage, provider: Provider | None = None
) -> tuple[dict, Scrubbed]:
    """Builds the request body and returns it with the Scrubbed text.

    Scrubbing happens here, so unscrubbed text physically cannot reach a provider.
    """
    provider = provider or get_provider()
    scrubbed = scrub_pii(message.text)
    body = provider.build_body(load_prompt(), user_content(message, scrubbed))
    return body, scrubbed


def parse_facts(
    text: str, scrubbed: Scrubbed, received_at: date
) -> tuple[LeadFacts, int, int | None]:
    """Converts any provider response into facts, dropped-quote count and model deadline.

    Quotes absent from the sent text were invented and are dropped. `received_at` has no
    default: worded deadlines count from the request date. An invalid response raises.
    """
    stripped = FENCE_RE.sub("", text.strip())
    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ExtractionError(
            f"ответ модели не разбирается как JSON: {exc}; было: {text!r}"
        ) from exc
    if not isinstance(raw, dict):
        raise ExtractionError(f"ответ модели — не объект, а {type(raw).__name__}")

    missing = [f for f in _schema()["required"] if f not in raw]
    if missing:
        raise ExtractionError(f"в ответе модели нет обязательных полей: {', '.join(missing)}")

    types: list[RequestType] = []
    for value in raw.get("request_types") or []:
        try:
            kind = RequestType(value)
        except ValueError as exc:
            raise ExtractionError(f"неизвестный request_type: {value!r}") from exc
        if kind not in types:
            types.append(kind)

    language = raw.get("language") or "en"
    if language not in ("ru", "en", "ar", "mixed"):
        raise ExtractionError(f"неизвестный language: {language!r}")

    # Why here: the API rejects these bounds in the schema, and the limit must not be
    # lost along with the schema key.
    headcount = _optional_int(raw.get("headcount"), "headcount", HEADCOUNT_MIN)
    timeline_from_model = _optional_int(
        raw.get("timeline_days"), "timeline_days", TIMELINE_MIN
    )
    timeline_days = coded_deadline_wins(scrubbed.text, received_at, timeline_from_model)

    quotes = [q for q in (raw.get("quotes") or []) if q and q in scrubbed.text]
    dropped = len(raw.get("quotes") or []) - len(quotes)

    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError) as exc:
        raise ExtractionError(f"confidence не число: {raw.get('confidence')!r}") from exc
    if not 0.0 <= confidence <= 1.0:
        raise ExtractionError(f"confidence вне диапазона: {confidence}")

    facts = LeadFacts(
        request_types=tuple(types),
        jurisdiction_hint=_optional_str(raw.get("jurisdiction_hint"), "jurisdiction_hint"),
        headcount=headcount,
        timeline_days=timeline_days,
        # Why the scrubbed text: masking contacts leaves urgency words untouched, and
        # both extraction modes must compute this identically.
        urgency_stated=wordless_urgency(scrubbed.text, timeline_days),
        budget_hint=_optional_str(raw.get("budget_hint"), "budget_hint"),
        language=language,
        is_spam=bool(raw.get("is_spam")),
        # Why not from the model: it never sees contacts, so code decides from what was cut.
        has_contact=scrubbed.has_contact,
        confidence=confidence,
        quotes=tuple(quotes),
    )
    return facts, dropped, timeline_from_model


def _optional_int(value: object, field: str, minimum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExtractionError(f"{field} не целое число: {value!r}")
    if value < minimum:
        raise ExtractionError(f"{field}={value} меньше допустимого {minimum}")
    return value


def _optional_str(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExtractionError(f"{field} не строка: {value!r}")
    return value.strip() or None


# Why two separate marker lists: "в этом месяце" states a deadline computable from the
# request date, while "срочно" states urgency with no date at all. Mixing them either
# loses a stated deadline or invents one.


def _days_to_end_of_month(received_at: date) -> int:
    """Returns days to the last day of the request month."""
    first_of_next = (received_at.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (first_of_next - timedelta(days=1) - received_at).days


def _days_to_end_of_week(received_at: date) -> int:
    """Returns days to the end of the request week (ISO week, chosen)."""
    return 6 - received_at.weekday()


def _days_to_next_friday(received_at: date) -> int:
    """Returns days to the next Friday, counting the request day itself (chosen)."""
    return (FRIDAY - received_at.weekday()) % 7


def _same_day(received_at: date) -> int:
    """Returns zero: "today" is zero days, not "soon"."""
    return 0


FRIDAY = 4  # index in date.weekday(), where Monday is 0

#: Worded deadline -> how to count it from the REQUEST date, never from today: a week-old
#: request saying "на этой неделе" means that week, not this one.
DEADLINE_MARKERS: dict[str, Callable[[date], int]] = {
    "в этом месяце": _days_to_end_of_month,
    "до конца месяца": _days_to_end_of_month,
    "до конца этого месяца": _days_to_end_of_month,
    "this month": _days_to_end_of_month,          # chosen, not observed in the set
    "на этой неделе": _days_to_end_of_week,
    "this week": _days_to_end_of_week,            # chosen, not observed in the set
    "до пятницы": _days_to_next_friday,
    "by friday": _days_to_next_friday,            # chosen, not observed in the set
    "сегодня": _same_day,                         # chosen, not observed in the set
    "today": _same_day,                           # chosen, not observed in the set
}

#: Urgency without a date: no deadline is ever derived from these.
VAGUE_URGENCY_MARKERS = (
    "срочно", "urgent", "asap", "как можно быстрее", "лишь бы быстро",
)


def deadline_days(text: str, received_at: date) -> int | None:
    """Returns the worded deadline in days, nearest marker winning, else None."""
    low = text.lower()
    found = [rule(received_at) for marker, rule in DEADLINE_MARKERS.items() if marker in low]
    return min(found) if found else None


def urgency_stated(text: str) -> bool:
    """Reports urgency worded without a date; a computable phrase cancels it."""
    low = text.lower()
    if any(marker in low for marker in DEADLINE_MARKERS):
        return False
    return any(marker in low for marker in VAGUE_URGENCY_MARKERS)


def coded_deadline_wins(
    text: str, received_at: date, from_model: int | None
) -> int | None:
    """Lets code own the deadlines it can compute; elsewhere the model number stands.

    Calendar arithmetic in code is deterministic, and the model gets it wrong even when
    the prompt spells the case out.
    """
    named = deadline_days(text, received_at)
    return named if named is not None else from_model


def wordless_urgency(text: str, timeline_days: int | None) -> bool:
    """Reports worded urgency with no date; any extracted deadline cancels it.

    Both extraction modes call this, so the signal cannot drift between them.
    """
    return timeline_days is None and urgency_stated(text)


def detect_language(text: str) -> str:
    """Detects the language by script; `mixed` needs a real share of a second script."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    cyrillic = sum(1 for c in letters if "Ѐ" <= c <= "ӿ") / len(letters)
    # Why Arabic is counted alongside Cyrillic: without this branch an Arabic request is
    # declared English and the draft goes out in the wrong language.
    arabic = sum(1 for c in letters if "\u0600" <= c <= "\u06ff" or "\ufb50" <= c <= "\ufeff")
    arabic /= len(letters)
    if arabic >= 1.0 - MIXED_SHARE:
        return "ar"
    if cyrillic >= 1.0 - MIXED_SHARE:
        return "ru"
    if arabic > MIXED_SHARE or cyrillic > MIXED_SHARE:
        return "mixed"
    return "en"


def _offline_extraction(message: InboundMessage) -> Extraction:
    """Returns the offline stub, marked so it cannot pass for a real extraction."""
    scrubbed = scrub_pii(message.text)
    facts = LeadFacts(
        request_types=(RequestType.OTHER,),
        language=detect_language(message.text),
        urgency_stated=wordless_urgency(message.text, None),
        has_contact=scrubbed.has_contact,
        # Why the flag next to the zero: an unmeasured zero and a measured zero look the
        # same in JSON and mean different things.
        confidence=0.0,
        confidence_measured=False,
    )
    return Extraction(
        facts=facts,
        provider="offline",
        model="offline-stub",
        elapsed_s=0.0,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        offline=True,
        scrubbed=scrubbed,
        dropped_quotes=0,
        route_reason=rendered(route_reason(RouteReasonCode.OFFLINE_NO_CALL)),
    )


def is_offline() -> bool:
    """Reports whether the OFFLINE environment switch is on."""
    return os.environ.get("OFFLINE", "") not in ("", "0")


def extract_detailed(message: InboundMessage, provider: Provider | None = None) -> Extraction:
    """Extracts facts and reports how: provider, timing, tokens, what was cut and dropped."""
    if is_offline():
        return _offline_extraction(message)

    if not any(c.isalnum() for c in message.text):
        # Why no call is made: a model answer about empty text would be invention.
        raise ExtractionError(
            f"обращение {message.external_id!r} пустое (ни букв, ни цифр) — "
            "извлекать нечего, запрос к модели не отправлялся"
        )
    chosen = route(message)
    provider = provider or (AnthropicProvider(chosen) if _default_is_anthropic()
                            else get_provider())
    body, scrubbed = build_request_body(message, provider)
    started = time.monotonic()
    completion = provider.complete(body)
    elapsed = time.monotonic() - started

    facts, dropped, timeline_from_model = parse_facts(
        completion.text, scrubbed, message.received_at
    )
    return Extraction(
        facts=facts,
        timeline_from_model=timeline_from_model,
        provider=provider.name,
        model=completion.model,
        elapsed_s=elapsed,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        cache_read_tokens=completion.cache_read_tokens,
        cache_write_tokens=completion.cache_write_tokens,
        offline=False,
        scrubbed=scrubbed,
        dropped_quotes=dropped,
        route_reason=chosen.reason if isinstance(provider, AnthropicProvider) else "",
    )


def extract(message: InboundMessage) -> LeadFacts:
    """Returns the facts of a request; failure raises rather than returning empty facts."""
    return extract_detailed(message).facts
