"""Извлечение фактов из текста обращения языковой моделью. Модель предлагает — код решает.

Провайдер сменный, как источник компаний в `sources/base.py`: движок про провайдера знает
только то, что тот вернёт текст. `LLM_PROVIDER` = `anthropic` (structured outputs) или
`pollinations` (OpenAI-совместимый шлюз). Демо не должно останавливаться из-за того, у кого
из провайдеров кончились деньги.

Исходы ровно три, и третий не сворачивается в первые два:
  * `LeadFacts` — извлекли, факты годные (в том числе `is_spam=True` — это тоже результат);
  * `LeadFacts` из режима OFFLINE — детерминированная заглушка, `confidence=0.0`;
  * исключение — извлечь не смогли; `ProviderBudgetError` («кончились деньги/бюджет»)
    отделён от прочих `ExtractionError`, потому что чинится он не кодом, а кошельком.
Пустой `LeadFacts` вместо ошибки не возвращается никогда.

Общее для всех провайдеров лежит в конвейере, а не в провайдере:
  * вырезание телефонов и почты (`scrub_pii`) — свойство конвейера, не провайдера;
  * валидация ответа и сборка `LeadFacts` (`parse_facts`) — одна на всех провайдеров;
  * `has_contact` определяет код по факту вырезанного, а не модель: контактов она
    не видит и видеть не должна.

Причина маршрута (`Route.reason`, `Extraction.route_reason`) текстом здесь не собирается:
модуль называет код и параметры, формулировки на обоих языках живут в `engine/reasons.py`.
Строка остаётся русской, как её читают отчёты и хранилище, но помнит свой код, поэтому
интерфейс берёт английский через `Extraction.route_reason_in(Language.EN)`, а не держит
собственный перевод.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
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

# --- константы-решения ---

DEFAULT_PROVIDER = "anthropic"           # переопределяется LLM_PROVIDER
# Извлечение фактов из короткого сообщения — простая работа, а чат-канал обещает ответ
# за секунды: по времени и цене на лид выигрывает младшая модель. Старшая включается
# переменной LLM_MODEL, а не правкой кода.
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_EFFORT = "low"                 # задача простая; переопределяется LLM_EFFORT
ANTHROPIC_THINKING = "off"               # рассуждать не над чем; переопределяется LLM_THINKING
# Длина текста, начиная с которой обращение уходит на старшую модель; ровно
# LONG_MESSAGE_CHARS — ещё короткое. Медиана длины обращения в наборе — около 100 символов,
# а младшая модель путается в относительных датах («с 20 числа») именно на длинных
# простынях, которые начинаются заметно дальше медианы.
LONG_MESSAGE_CHARS = 600
LONG_MODEL = "claude-opus-5"             # на длинных обращениях даёт воспроизводимый разбор
# `output_config.effort` и adaptive-мышление принимают только перечисленные модели,
# остальные отвечают на эти параметры 400. Список ведётся по факту проверки запросом.
EFFORT_MODELS = ("claude-opus-5", "claude-opus-4-", "claude-sonnet-5", "claude-sonnet-4-6",
                 "claude-fable-")
POLLINATIONS_MODEL = "openai"            # алиас GPT-OSS 20B в выдаче GET /models шлюза
POLLINATIONS_URL = "https://text.pollinations.ai/openai"
USER_AGENT = "leadcentre/0.1 (+SORP Lead Centre)"
MAX_TOKENS = 4096                        # ответ — один JSON-объект, взято с запасом
TIMEOUT_S = 60.0                         # чат-канал, дольше ждать смысла нет
MAX_RETRIES = 2                          # столько же, сколько по умолчанию у SDK
HEADCOUNT_MIN = 1                        # «ноль человек» — не факт, а мусор в ответе
TIMELINE_MIN = 0                         # срок в прошлом модель придумала
MIN_PHONE_DIGITS = 9                     # короче — не телефон, а «8 человек» или дата
# Доля второго алфавита, ниже которой это не «mixed», а имя собственное латиницей
# внутри русской фразы (TECOM, IFZA).
MIXED_SHARE = 0.2

# Версия в имени файла: прежние промпты лежат рядом и остаются доступны для сравнения.
PROMPT_VERSION = "extract_v4"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / f"{PROMPT_VERSION}.md"

PHONE_MASK = "[phone]"
EMAIL_MASK = "[email]"

# Порядок: почта раньше телефона, иначе хвост номера внутри адреса маскируется как телефон.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w])\+?\d[\d\-\s().]{5,}\d(?![\w])")
FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


class ExtractionError(RuntimeError):
    """Извлечь не смогли. В пустой LeadFacts не сворачивается: это отдельный исход."""


class ProviderBudgetError(ExtractionError):
    """Провайдер недоступен по лимитам: деньги/бюджет ключа, а не сеть и не код.

    Отдельный тип, потому что и лечится отдельно: кодом это не чинится, повторять запрос
    бессмысленно, а сообщение должно говорить человеку, куда идти.
    """


@dataclass(frozen=True)
class Scrubbed:
    """Текст без персональных данных и счётчики вырезанного — числами, а не флагом."""

    text: str
    phones: int
    emails: int

    @property
    def has_contact(self) -> bool:
        return bool(self.phones or self.emails)

    def summary(self) -> str:
        return f"вырезано: телефонов {self.phones}, адресов почты {self.emails}"


@dataclass(frozen=True)
class Completion:
    """Ответ провайдера, приведённый к общему виду. Дальше конвейер про провайдера не знает."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0     # из кэша (дёшево); 0 у провайдеров без кэша
    cache_write_tokens: int = 0    # записано в кэш (дороже обычного входа)


@dataclass(frozen=True)
class Extraction:
    """Факты плюс то, чем они получены: для отчёта, логов и счёта за токены."""

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
    dropped_quotes: int      # цитат, которых в обращении нет: модель их придумала
    route_reason: str = ""   # почему выбрана эта модель — в карточку, для демонстрации

    def route_reason_in(self, language: Language = DEFAULT_LANGUAGE) -> str:
        """Причина маршрута на нужном языке — это берёт интерфейс.

        Исходов три: строка помнит свой код — отрисовывается на любом языке; причины нет
        вовсе (провайдер не anthropic) — пусто; строка пришла без кода (поднята из
        хранилища) — на другой язык её не отрисовать, и это `ReasonRenderError`, а не
        молчаливая подмена русским текстом.
        """
        return text_in(self.route_reason, language)

    def served_by(self) -> str:
        """Одной строкой: чем фактически обслужен лид.

        Имя модели берётся из ответа API, а не из намерения: подмену модели на стороне
        провайдера видно в отчёте.
        """
        cache = ""
        if self.cache_read_tokens or self.cache_write_tokens:
            cache = (f", кэш: прочитано {self.cache_read_tokens}, "
                     f"записано {self.cache_write_tokens}")
        return (f"обслужено: {self.provider}/{self.model} за {self.elapsed_s:.2f} с "
                f"({self.route_reason}; токены {self.input_tokens}/{self.output_tokens}"
                f"{cache})")


# --- минимизация персональных данных (общая для всех провайдеров) ---


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
    """Вырезает телефоны и адреса почты. Всё, что уходит в любую модель, проходит через это."""
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


# --- схема ответа: поле в поле с LeadFacts ---


def _schema() -> dict:
    """JSON-схема ответа модели; набор полей повторяет LeadFacts."""
    return {
        "type": "object",
        "properties": {
            "request_types": {
                "type": "array",
                "items": {"type": "string", "enum": [t.value for t in RequestType]},
            },
            "jurisdiction_hint": {"type": ["string", "null"]},
            # Structured outputs не принимает `minimum`/`maximum` у целых полей и отвечает
            # на них 400, поэтому границы держит parse_facts после разбора
            # (HEADCOUNT_MIN / TIMELINE_MIN), а не схема.
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
    """Читает промпт: он живёт файлом с версией в имени, в коде — только загрузка."""
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtractionError(f"не читается промпт {PROMPT_PATH}: {exc}") from exc


def user_content(message: InboundMessage, scrubbed: Scrubbed) -> str:
    """Пользовательская часть запроса. Текст сюда попадает только после `scrub_pii`."""
    # Дата обращения идёт явной строкой: без опоры модель отсчитывает «с 20 числа»
    # от произвольного дня, и timeline_days перестаёт быть воспроизводимым.
    return (
        f"Обращение получено: {message.received_at.isoformat()}\n"
        f"Канал: {message.channel}\n"
        f"Текст обращения:\n{scrubbed.text}"
    )


# --- провайдеры ---


@dataclass(frozen=True)
class Route:
    """Куда отправлять этот лид. Решение принимается по длине текста, до всякой сети."""

    model: str
    effort: str          # "" — не слать параметр
    thinking: str        # "" — не слать параметр
    reason: str          # русский текст из каталога, помнящий свой код (RenderedText)
    reason_item: RouteReason | None = None   # тот же код с параметрами, без текста


def _route(model: str, effort: str, thinking: str, item: RouteReason) -> Route:
    """Собирает маршрут; текст причины отрисовывает каталог reasons.py, и только он."""
    return Route(model, effort, thinking, rendered(item), item)


def route(message: InboundMessage) -> Route:
    """Короткие обращения — дешёвая модель, длинные — дорогая и стабильная.

    `LLM_MODEL` выключает маршрутизацию: заданная руками модель идёт на всё, иначе
    оператор не смог бы прогнать набор на одной модели для сравнения.
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
    """Сменный адаптер модели.

    Тело запроса и разбор ответа — дело адаптера; вырезание персональных данных,
    валидация фактов и сборка LeadFacts — дело конвейера, одинаковое для всех.
    """

    name: str

    def model(self) -> str: ...

    def build_body(self, system: str, content: str) -> dict: ...

    def complete(self, body: dict) -> Completion: ...


def anthropic_client():
    """Клиент Anthropic. Ключ — из CLAUDE_KEY, запасной ANTHROPIC_API_KEY.

    Функция публичная, потому что этим же клиентом ходит translit.py: ключ читается
    в одном месте, иначе два способа его получить неизбежно разъедутся.
    """
    api_key = os.environ.get("CLAUDE_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ExtractionError("нет ключа: ни CLAUDE_KEY, ни ANTHROPIC_API_KEY не заданы")
    try:
        import anthropic
    except ImportError as exc:  # отсутствие пакета ловится до похода в сеть
        raise ExtractionError("нет пакета anthropic: pip install anthropic") from exc
    return anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_S, max_retries=MAX_RETRIES)


def anthropic_error(exc: Exception) -> ExtractionError:
    """Ошибка API → наш тип. Лимиты отделены от сети: чинятся они по-разному."""
    text = str(exc)
    # 400 «credit balance is too low» приходит обычным BadRequestError — по коду ответа
    # его от опечатки в теле не отличить, отличаем по тексту.
    if "credit balance" in text or "billing" in text.lower():
        return ProviderBudgetError(
            "anthropic: кончились деньги на аккаунте — API отвечает 400 "
            "«credit balance is too low». Что делать: пополнить баланс в Console → "
            "Plans & Billing либо переключиться: LLM_PROVIDER=pollinations. "
            f"Ответ API как есть: {text}"
        )
    return ExtractionError(f"anthropic: запрос не удался: {type(exc).__name__}: {text}")


class AnthropicProvider:
    """Claude через официальный SDK. Схему держит сам API (structured outputs)."""

    name = "anthropic"

    def __init__(self, route_: Route | None = None) -> None:
        # Маршрут задаётся на лид; без него — прежнее поведение по переменным среды.
        self.route = route_ or Route(
            os.environ.get("LLM_MODEL") or ANTHROPIC_MODEL,
            os.environ.get("LLM_EFFORT", ANTHROPIC_EFFORT),
            os.environ.get("LLM_THINKING", ANTHROPIC_THINKING),
            "маршрут не задан: значения по умолчанию",
        )

    def model(self) -> str:
        return self.route.model

    def build_body(self, system: str, content: str) -> dict:
        model = self.model()
        supports_effort = model.startswith(EFFORT_MODELS)
        body = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": content}],
            # Системный промпт и схема одинаковы для каждого лида, поэтому префикс
            # кэшируется, а волатильное (текст обращения, дата) идёт после него в messages.
            # У моделей с высоким порогом кэширования префикс до него не дотягивает и кэш
            # молча не включается; параметр безвреден, а сработал ли он, видно по счётчикам
            # cache_read/cache_write в Extraction.
            "cache_control": {"type": "ephemeral"},
            "output_config": {"format": {"type": "json_schema", "schema": _schema()}},
        }
        # Уровень усилий и мышление — конфигурация, а не константа в коде. `none`/`off`
        # означает «не слать параметр вовсе»: у моделей, которые его не принимают,
        # он не должен появляться в теле даже пустым.
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
    """Шлюз формата OpenAI chat completions (у нас — Pollinations).

    Structured outputs здесь нет, поэтому схему держим сами: кладём её в системный промпт
    и валидируем ответ тем же `parse_facts`, что и у Anthropic. `response_format`
    просим, но на него не рассчитываем — невалидный ответ будет `ExtractionError`.
    """

    name = "pollinations"

    def model(self) -> str:
        return os.environ.get("LLM_MODEL") or POLLINATIONS_MODEL

    def build_body(self, system: str, content: str) -> dict:
        schema_note = (
            "\n\n## Формат ответа\n\n"
            "Верни РОВНО один JSON-объект по этой JSON-схеме, без пояснений, "
            "без markdown-ограждения, без текста до и после:\n\n"
            f"{json.dumps(_schema(), ensure_ascii=False, indent=2)}"
        )
        return {
            "model": self.model(),
            "max_tokens": MAX_TOKENS,
            "temperature": 0,     # извлечение фактов: разнообразие ответов тут вредно
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system + schema_note},
                {"role": "user", "content": content},
            ],
        }

    def complete(self, body: dict) -> Completion:
        api_key = os.environ.get("POLLINATIONS_API_KEY")
        if not api_key:
            raise ExtractionError("нет ключа POLLINATIONS_API_KEY")
        request = urllib.request.Request(
            POLLINATIONS_URL,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                # Шлюз стоит за Cloudflare и отвечает 403 на дефолтный python-urllib:
                # собственный User-Agent обязателен.
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
    """Провайдер по имени или по `LLM_PROVIDER`. Неизвестное имя — ошибка, а не тихий дефолт."""
    key = (name or os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if key not in PROVIDERS:
        raise ExtractionError(
            f"неизвестный LLM_PROVIDER={key!r}; известны: {', '.join(sorted(PROVIDERS))}"
        )
    return PROVIDERS[key]()


# --- конвейер: одинаковый для всех провайдеров ---


def build_request_body(
    message: InboundMessage, provider: Provider | None = None
) -> tuple[dict, Scrubbed]:
    """Собирает тело запроса к выбранному провайдеру и возвращает его вместе с Scrubbed.

    Вынесено отдельной функцией, чтобы тест проверял ровно то, что уходит в сеть.
    Вырезание персональных данных стоит здесь, а не в провайдере: неочищенный текст
    до провайдера физически не доходит.
    """
    provider = provider or get_provider()
    scrubbed = scrub_pii(message.text)
    body = provider.build_body(load_prompt(), user_content(message, scrubbed))
    return body, scrubbed


def parse_facts(text: str, scrubbed: Scrubbed) -> tuple[LeadFacts, int]:
    """Текст ответа любого провайдера → `LeadFacts` плюс число отброшенных цитат.

    Валидация одна на всех провайдеров. Невалидный ответ — `ExtractionError`, а не пустой
    `LeadFacts`. Цитаты сличаются с текстом, который уходил в модель: чего в нём нет,
    то модель придумала, и такая цитата отбрасывается.
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

    # Границы, которых нет в схеме (API их не принимает), проверяем здесь — чтобы
    # ограничение не потерялось вместе с ключом схемы.
    headcount = _optional_int(raw.get("headcount"), "headcount", HEADCOUNT_MIN)
    timeline_days = _optional_int(raw.get("timeline_days"), "timeline_days", TIMELINE_MIN)

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
        # По тексту, который видела модель: маскирование контактов слов о срочности
        # не трогает, а признак обязан считаться одинаково в обоих режимах.
        urgency_stated=urgency_stated(scrubbed.text),
        budget_hint=_optional_str(raw.get("budget_hint"), "budget_hint"),
        language=language,
        is_spam=bool(raw.get("is_spam")),
        # has_contact — по факту вырезанного, а не по слову модели.
        has_contact=scrubbed.has_contact,
        confidence=confidence,
        quotes=tuple(quotes),
    )
    return facts, dropped


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


# --- свойства текста, которые решает код, а не модель ---

# Слова, которыми клиент заявляет срочность, не называя даты. Это свойство текста, а не
# суждение модели: и режим rules, и режим llm читают его отсюда, иначе признак есть в
# одном пути и молча отсутствует в другом.
URGENT_MARKERS = (
    "срочно", "urgent", "asap", "в этом месяце", "this month", "до конца месяца",
    "до пятницы", "сегодня", "today", "как можно быстрее", "лишь бы быстро",
    "на этой неделе",
)


def urgency_stated(text: str) -> bool:
    """Заявлена ли срочность словами. Дату отсюда не выводим: слово «срочно» — не дата."""
    low = text.lower()
    return any(marker in low for marker in URGENT_MARKERS)


# --- режим OFFLINE ---


def detect_language(text: str) -> str:
    """Язык по алфавиту: дешёвая детерминированная оценка для OFFLINE и для сверки с моделью.

    `mixed` — только когда второго алфавита заметно много (`MIXED_SHARE`): «офис в TECOM»
    остаётся `ru`, потому что латиницей там одно имя собственное.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    cyrillic = sum(1 for c in letters if "Ѐ" <= c <= "ӿ") / len(letters)
    # Арабица считается наравне с кириллицей: без этой ветки арабское обращение
    # объявляется английским и черновик уходит не на том языке. Диапазоны — основной
    # арабский блок и дополнительный, включая арабские формы представления.
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
    """Сеть не трогаем: детерминированная заглушка для тестов и CI.

    `confidence=0.0` и `request_types=(OTHER,)` — метка «моделью не смотрено»:
    заглушку нельзя спутать с настоящим извлечением ни в отчёте, ни в хранилище.
    """
    scrubbed = scrub_pii(message.text)
    facts = LeadFacts(
        request_types=(RequestType.OTHER,),
        language=detect_language(message.text),
        urgency_stated=urgency_stated(message.text),
        has_contact=scrubbed.has_contact,
        confidence=0.0,
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
    return os.environ.get("OFFLINE", "") not in ("", "0")


# --- точка входа ---


def extract_detailed(message: InboundMessage, provider: Provider | None = None) -> Extraction:
    """Извлечение с приборными данными: провайдер, время, токены, что вырезано, что выброшено."""
    if is_offline():
        return _offline_extraction(message)

    if not any(c.isalnum() for c in message.text):
        # Извлекать не из чего. В модель не идём: её ответ на пустоту был бы выдумкой.
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

    facts, dropped = parse_facts(completion.text, scrubbed)
    return Extraction(
        facts=facts,
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
    """Факты из обращения. Извлечь не смогли — исключение, а не пустой LeadFacts."""
    return extract_detailed(message).facts
