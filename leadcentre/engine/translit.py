"""Latin spellings for Arabic company names, with machine guesses marked as guesses.

Three outcomes: from the registry, machine transliterated, or failed — and a failure is
never silently replaced by the original. An object is returned because a string would
lose its provenance.
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

TRANSLIT_MODEL = ANTHROPIC_MODEL          # the same small model used for short requests
MAX_TOKENS = 200                          # the answer is a single name line
MAX_NAME_CHARS = 300                      # Why 300: longer is not a name but noise
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
    FROM_SOURCE = "from_source"   # Latin from the registry: a fact, not a guess
    MACHINE = "machine"           # produced by the model
    FAILED = "failed"             # could not; never silently replaced


@dataclass(frozen=True)
class TranslitName:
    """A name together with its provenance, which is part of the value."""

    status: TranslitStatus
    text: str | None                  # Latin text, or None on failure
    original: str                     # the original name from the registry
    source: str                       # provenance: registry, model or cache
    error: str | None = None
    cached: bool = False

    @property
    def is_official(self) -> bool:
        """True only for a registry spelling; a machine guess is never official."""
        return self.status is TranslitStatus.FROM_SOURCE

    @property
    def ok(self) -> bool:
        return self.text is not None

    def display(self) -> str:
        """Returns the text to show; the value itself appends the machine-guess mark."""
        if self.status is TranslitStatus.FAILED:
            return f"{self.original} (латиницы нет: {self.error})"
        if self.status is TranslitStatus.FROM_SOURCE:
            return self.text or self.original
        return f"{self.text} ({MACHINE_MARK})"


def source_latin_name(entity: dict) -> tuple[str, str] | None:
    """Returns a Latin name found in the record itself, with the field it came from."""
    for field in ("otherNames", "transliteratedOtherNames"):
        for other in entity.get(field) or []:
            name = (other.get("name") or "").strip()
            if name and LATIN_RE.search(name) and not ARABIC_RE.search(name):
                return name, f"{field}/{other.get('type') or '?'}"
    return None


def english_name(entity: dict) -> TranslitName:
    """Returns the record's Latin name, falling back to transliteration."""
    legal = ((entity.get("legalName") or {}).get("name") or "").strip()
    found = source_latin_name(entity)
    if found:
        name, field = found
        return TranslitName(TranslitStatus.FROM_SOURCE, name, legal, field)
    if not ARABIC_RE.search(legal):
        return TranslitName(TranslitStatus.FROM_SOURCE, legal or None, legal, "legalName")
    return transliterate(legal)


def cache_key(name: str) -> str:
    """Builds the cache key; the prompt version is included because answers change with it."""
    payload = f"{PROMPT_VERSION}\x1f{TRANSLIT_MODEL}\x1f{' '.join(name.split())}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def load_cache() -> dict[str, dict]:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        # Why noisy: an empty dict here would silently send everything to the network.
        raise ExtractionError(f"кэш транслитераций не читается: {CACHE_PATH}") from None


def save_cache(cache: dict[str, dict]) -> None:
    """Writes the cache sorted and indented, since the file lives in the repository."""
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
    """Rejects input before any network call: empty, over-long, or with no Arabic letters."""
    cleaned = name.strip()
    if not cleaned:
        return "пустая строка"
    if len(cleaned) > MAX_NAME_CHARS:
        return f"слишком длинно ({len(cleaned)} символов) — это не название"
    if not ARABIC_RE.search(cleaned):
        return "нет арабских букв — транслитерировать нечего"
    return None


def transliterate(name: str, *, cache: dict[str, dict] | None = None) -> TranslitName:
    """Transliterates an Arabic name; offline mode uses the on-disk cache only."""
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
    """Makes one model call; a non-Latin answer counts as a failure."""
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
    """Warms the cache for a list of names, returning counts rather than a flag."""
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
