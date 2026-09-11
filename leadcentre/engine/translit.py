"""Латинское написание арабских названий компаний. Машинная догадка помечена как догадка.

Зачем: в карточке менеджер видит название, которое не может ни прочитать, ни найти.
Транслитерация делает его читаемым — и ничем больше не является: для юридических
документов она не годится, носителем языка не проверена.

Исходов три:
  * `FROM_SOURCE` — латиница есть в самой записи реестра, модель не вызывалась;
  * `MACHINE` — машинная транслитерация (или её же значение из кэша);
  * `FAILED` — не смогли (сеть, лимиты, пустой/невалидный ответ, нечего транслитерировать).
`FAILED` НЕ подменяется исходным арабским молча: вызывающий получает `text=None` и
причину в `error`, а не строку, которую можно случайно показать как имя.

Почему возвращается объект, а не строка: строка теряет происхождение. Официальное имя
из реестра и машинная догадка — разные вещи, и разница обязана дожить до карточки,
а не потеряться в первой же переменной `name`. Поэтому у результата есть `is_official`,
`status` и `display()`, который сам дописывает пометку к машинному варианту.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from leadcentre.engine.extract import (
    ANTHROPIC_MODEL,
    MAX_RETRIES,
    ExtractionError,
    anthropic_client,
    anthropic_error,
    is_offline,
)

# --- константы-решения ---

TRANSLIT_MODEL = ANTHROPIC_MODEL          # та же младшая модель, что и для коротких обращений
MAX_TOKENS = 200                          # ответ — одна строка названия
MAX_NAME_CHARS = 300                      # длиннее — это не название, а мусор
PROMPT_VERSION = "translit_v1"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / f"{PROMPT_VERSION}.md"
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "translit_cache.json"

MACHINE_MARK = "машинная транслитерация"
DISCLAIMER = (
    "Машинная транслитерация: носителем языка не проверялась, для юридических документов "
    "не годится. Официальное название компании — арабское, из реестра."
)

ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿ]")
LATIN_RE = re.compile(r"[A-Za-z]")


class TranslitStatus(str, Enum):
    FROM_SOURCE = "from_source"   # латиница пришла из реестра — это факт, а не догадка
    MACHINE = "machine"           # получено от модели
    FAILED = "failed"             # не смогли; арабским молча не подменяем


@dataclass(frozen=True)
class TranslitName:
    """Имя вместе с его происхождением. Происхождение — часть значения, а не комментарий."""

    status: TranslitStatus
    text: str | None                  # латиница; None, если не смогли
    original: str                     # исходное название как в реестре
    source: str                       # откуда взято: поле реестра, модель, кэш
    error: str | None = None
    cached: bool = False

    @property
    def is_official(self) -> bool:
        """True только для написания из реестра. Машинная догадка официальной не бывает."""
        return self.status is TranslitStatus.FROM_SOURCE

    @property
    def ok(self) -> bool:
        return self.text is not None

    def display(self) -> str:
        """Что показать менеджеру. Машинный вариант всегда несёт пометку — забыть её
        нельзя, потому что дописывает её не вызывающий, а само значение."""
        if self.status is TranslitStatus.FAILED:
            return f"{self.original} (латиницы нет: {self.error})"
        if self.status is TranslitStatus.FROM_SOURCE:
            return self.text or self.original
        return f"{self.text} ({MACHINE_MARK})"


# --- что уже есть в записи реестра ---


def source_latin_name(entity: dict) -> tuple[str, str] | None:
    """Латинское название из самой записи GLEIF: (название, каким полем дано).

    Просматриваются и `otherNames`, и `transliteratedOtherNames`: во втором лежит
    ASCII-написание из реестра, и оно ближе к истине, чем машинная догадка.
    """
    for field in ("otherNames", "transliteratedOtherNames"):
        for other in entity.get(field) or []:
            name = (other.get("name") or "").strip()
            if name and LATIN_RE.search(name) and not ARABIC_RE.search(name):
                return name, f"{field}/{other.get('type') or '?'}"
    return None


def english_name(entity: dict) -> TranslitName:
    """Латинское название записи: из реестра, если оно там есть; иначе — транслитерация."""
    legal = ((entity.get("legalName") or {}).get("name") or "").strip()
    found = source_latin_name(entity)
    if found:
        name, field = found
        return TranslitName(TranslitStatus.FROM_SOURCE, name, legal, field)
    if not ARABIC_RE.search(legal):
        # Название и так латиницей — это тоже «из источника», модель не нужна.
        return TranslitName(TranslitStatus.FROM_SOURCE, legal or None, legal, "legalName")
    return transliterate(legal)


# --- кэш на диске ---


def cache_key(name: str) -> str:
    """Ключ — хэш нормализованного названия плюс версия промпта.

    Версия входит в ключ, потому что со сменой промпта меняются и ответы: закэшированное
    от прежнего промпта не должно выдаваться за новое.
    """
    payload = f"{PROMPT_VERSION}\x1f{TRANSLIT_MODEL}\x1f{' '.join(name.split())}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def load_cache() -> dict[str, dict]:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        # Битый кэш — это «не смогли прочитать», а не «пусто»: пустой словарь молча
        # отправил бы всё в сеть. Поэтому шумим.
        raise ExtractionError(f"кэш транслитераций не читается: {CACHE_PATH}") from None


def save_cache(cache: dict[str, dict]) -> None:
    """Пишем отсортированно и с отступами: файл лежит в репозитории, дифф должен читаться."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtractionError(f"не читается промпт {PROMPT_PATH}: {exc}") from exc


def _junk_reason(name: str) -> str | None:
    """Мусор отсеиваем до сети: пустое, слишком длинное, без арабских букв."""
    cleaned = name.strip()
    if not cleaned:
        return "пустая строка"
    if len(cleaned) > MAX_NAME_CHARS:
        return f"слишком длинно ({len(cleaned)} символов) — это не название"
    if not ARABIC_RE.search(cleaned):
        return "нет арабских букв — транслитерировать нечего"
    return None


def transliterate(name: str, *, cache: dict[str, dict] | None = None) -> TranslitName:
    """Арабское название → латиница. Кэш на диске, в OFFLINE — только кэш."""
    reason = _junk_reason(name)
    if reason:
        return TranslitName(TranslitStatus.FAILED, None, name, "проверка входа",
                            error=reason)

    own_cache = cache is None
    store = load_cache() if own_cache else cache
    key = cache_key(name)
    hit = store.get(key)
    if hit:
        return TranslitName(TranslitStatus.MACHINE, hit["text"], name,
                            f"кэш/{hit.get('model', '?')}", cached=True)

    if is_offline():
        return TranslitName(TranslitStatus.FAILED, None, name, "кэш",
                            error="OFFLINE=1 и в кэше этого названия нет")

    try:
        text = _ask_model(name)
    except ExtractionError as exc:
        return TranslitName(TranslitStatus.FAILED, None, name, "модель", error=str(exc))

    store[key] = {"text": text, "arabic": name, "model": TRANSLIT_MODEL,
                  "prompt": PROMPT_VERSION}
    if own_cache:
        save_cache(store)
    return TranslitName(TranslitStatus.MACHINE, text, name, f"модель/{TRANSLIT_MODEL}")


def _ask_model(name: str) -> str:
    """Один запрос к модели. Ответ обязан быть латиницей — иначе это «не смогли»."""
    client = anthropic_client()
    try:
        response = client.with_options(max_retries=MAX_RETRIES).messages.create(
            model=TRANSLIT_MODEL,
            max_tokens=MAX_TOKENS,
            system=load_prompt(),
            messages=[{"role": "user", "content": name}],
        )
    except Exception as exc:
        raise anthropic_error(exc) from exc

    if response.stop_reason not in ("end_turn", "stop_sequence"):
        raise ExtractionError(f"модель не договорила: stop_reason={response.stop_reason}")
    text = " ".join(
        block.text.strip() for block in response.content if block.type == "text"
    ).strip()
    if not text:
        raise ExtractionError("пустой ответ модели")
    if ARABIC_RE.search(text):
        raise ExtractionError(f"в ответе остался арабский текст: {text!r}")
    if not LATIN_RE.search(text):
        raise ExtractionError(f"в ответе нет латиницы: {text!r}")
    if "\n" in text:
        raise ExtractionError(f"ответ из нескольких строк: {text!r}")
    return text


def warm_cache(names: list[str]) -> tuple[int, int, list[TranslitName]]:
    """Прогреть кэш списком названий. Числами, а не флагом: сколько получили,
    сколько не смогли. Кэш читается и пишется один раз, а не на каждое имя."""
    store = load_cache()
    results = []
    done = failed = 0
    for name in names:
        result = transliterate(name, cache=store)
        results.append(result)
        if result.ok:
            done += 1
        else:
            failed += 1
    save_cache(store)
    return done, failed, results
