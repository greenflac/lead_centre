"""Измерительный стенд: каппа, матрица ошибок, стабильность, негативные контроли.

Стенд принимает готовый движок и не знает, как он устроен (И1): он читает разметку,
гоняет скоринг и печатает числа. Всё, что он умеет сказать, — это три исхода на каждый
блок (Р1): `годно`, `не годно`, `не смогли проверить`; третий не сворачивается в первые два.

Два режима по деньгам (бюджет Anthropic ограничен, ответ Opus — десятки секунд):
  * `--engine=rules` (по умолчанию) — факты добываются детерминированно из текста CSV,
    модель не вызывается вообще; этим гоняем весь набор сколько угодно раз;
  * `--engine=llm` — факты извлекает модель через `leadcentre.engine.extract`, ответы
    кэшируются в `data/extract_cache.json` по хэшу текста, так что повторный прогон
    денег не стоит.

Что стенд НЕ измеряет: качество на реальном потоке SORP. Разметка — синтетика, движок
и разметка сделаны в одном доме, поэтому каппа здесь называется «согласие с разметкой
автора на синтетике», а не «точность». Подробности — в eval/README.md.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import inspect
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from leadcentre.engine import extract as extract_mod  # noqa: E402
from leadcentre.models import InboundMessage, LeadFacts, RequestType, Tier  # noqa: E402

# --- константы-решения (И4) ---

# ВЫБРАНО: разметка человека тремя значениями; INVALID — исход движка, а не суждение
# человека (docs/data/inbound_seed.md, раздел «Правила разметки для владельца»).
TIERS = ("HIGH", "MEDIUM", "LOW")
URGENT_DAYS = 30           # ВЫБРАНО (правила разметки): решение нужно в пределах 30 дней
PACKAGE_MIN_TYPES = 2      # ВЫБРАНО (правила разметки): «два и более направления сразу»
URGENT_DEFAULT_DAYS = 14   # ВЫБРАНО: «срочно/asap/в этом месяце» без числа — считаем 14 дней
STABILITY_RUNS = 3         # РАСЧЁТ по docs/BLUEPRINT.md §10: «3 прогона»
KAPPA_TARGET = 0.6         # РАСЧЁТ по docs/BLUEPRINT.md §10: каппа >= 0.6
STABILITY_TARGET = 0.9     # РАСЧЁТ по docs/BLUEPRINT.md §10: >= 90%
CONTROLS_EXPECTED = 6      # РАСЧЁТ по docs/BLUEPRINT.md §10: 6 обращений, 6 из 6

SEED_PATH = ROOT / "data" / "inbound_seed.csv"
LABELS_PATH = ROOT / "eval" / "labels.csv"
CONTROLS_PATH = ROOT / "eval" / "negative_controls.csv"
CACHE_PATH = ROOT / "data" / "extract_cache.json"

EXIT_OK = 0            # годно
EXIT_FAILED = 1        # не годно: измерили и не сошлось
EXIT_UNMEASURABLE = 2  # не смогли проверить: мерить было нечем


class CannotMeasure(RuntimeError):
    """Третий исход на одном обращении: приоритет получить не смогли (Р1)."""


# --- вход ---


def load_messages(path: Path) -> list[InboundMessage]:
    rows: list[InboundMessage] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append(
                InboundMessage(
                    external_id=row["external_id"],
                    channel=row["channel"],
                    text=row["text"],
                    received_at=date.fromisoformat(row["received_at"]),
                    is_synthetic=(row.get("is_synthetic", "true").lower() == "true"),
                )
            )
    return rows


def load_labels(path: Path) -> tuple[dict[str, str], list[str]]:
    """Разметка человека: external_id -> tier. Второй элемент — брак разметки числами (Е3)."""
    labels: dict[str, str] = {}
    bad: list[str] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            ext = (row.get("external_id") or "").strip()
            value = (row.get("tier_expected") or "").strip().upper()
            if not ext:
                continue
            if value in TIERS:
                labels[ext] = value
            else:
                bad.append(f"{ext}={value or 'пусто'}")
    return labels, bad


def load_controls(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return [row for row in csv.DictReader(fh) if (row.get("external_id") or "").strip()]


# --- факты без модели: режим rules ---

SPAM_MARKERS = (
    "seo agency", "rank your website", "ищу работу", "резюме вышлю", "cv attached",
    "looking for job", "job opportunity", "арбитраж", "депозит от", "business database",
    "сдаю квартиру", "без комиссии", "buy verified",
)

TYPE_MARKERS: dict[RequestType, tuple[str, ...]] = {
    RequestType.OFFICE: (
        "офис", "office", "кабинет", "рабочих мест", "рабочие места", "seats", "desk",
        "флекси", "flexi", "помещение", "кв.м", "sqm", "переговорк", "unit at",
    ),
    RequestType.SETUP: (
        "лицензи", "licence", "license", "регистрац", "регистрируем", "register",
        "открыть компанию", "открываем", "open company", "company formation", "фризон",
        "фриз зона", "freezone", "free zone", "mainland", "мейнленд", "юрлиц", "филиал",
        "холдинг", "تأسيس",
    ),
    RequestType.VISA: ("виз", "visa", "emirates id", "резидентств", "residence", "golden"),
    RequestType.ACCOUNTING: (
        "бухгалт", "accounting", "bookkeeping", "аудит", "audit", "налог", " tax", "vat",
        "отчётност", "отчетност",
    ),
    RequestType.BANK: (
        "банк", "bank", "счёт в банке", "счет в банке", "платёжный шлюз", "payment gateway",
    ),
}

BUDGET_MARKERS = (
    "бюджет", "budget", "aed", "дирхам", "готовы подписать", "договор на", "цена устроит",
    "approved",
)

URGENT_MARKERS = (
    "срочно", "urgent", "asap", "в этом месяце", "this month", "до конца месяца",
    "до пятницы", "сегодня", "today", "как можно быстрее", "лишь бы быстро",
    "на этой неделе",
)

MONTHS = {
    "январ": 1, "феврал": 2, "марта": 3, "апрел": 4, "мая": 5, "июн": 6, "июл": 7,
    "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
    "january": 1, "february": 2, "april": 4, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12, "nov": 11, "dec": 12,
}

NUM_DAYS_RE = re.compile(r"(?:через|in|within)\s+(\d+)\s*(?:дн|дней|day|days)", re.I)
NUM_WEEKS_RE = re.compile(r"(?:через|in|within)\s+(\d+)\s*(?:недел|week)", re.I)
EXPIRES_RE = re.compile(
    r"(?:expires?|истека\w*|заканчива\w*|слетает)\D{0,25}(\d+)\s*(дн|day|week|недел)", re.I
)
HEADCOUNT_RE = re.compile(
    r"(\d+)\s*(?:человек|чел\b|людей|people|ppl|persons|seats|мест|сотрудник\w*|staff)",
    re.I,
)
MONEY_RE = re.compile(r"\d[\d\s.,]*\s*(?:aed|дирхам|тысяч|k\b)", re.I)


def _timeline_days(text: str, received_at: date) -> int | None:
    """Срок в днях, детерминированно. Не нашли — None, а не ноль (неизвестно != срочно)."""
    low = text.lower()
    m = NUM_DAYS_RE.search(low)
    if m:
        return int(m.group(1))
    m = NUM_WEEKS_RE.search(low)
    if m:
        return int(m.group(1)) * 7
    m = EXPIRES_RE.search(low)
    if m:
        value = int(m.group(1))
        return value * 7 if m.group(2).lower().startswith(("недел", "week")) else value
    best: int | None = None
    for marker, month in MONTHS.items():
        if marker not in low:
            continue
        year = received_at.year + (1 if month < received_at.month else 0)
        delta = (date(year, month, 1) - received_at).days
        if delta >= 0 and (best is None or delta < best):
            best = delta
    if best is not None:
        return best
    if any(marker in low for marker in URGENT_MARKERS):
        return URGENT_DEFAULT_DAYS
    return None


def rules_facts(message: InboundMessage) -> LeadFacts:
    """Факты из текста без модели: заглушка стенда, а не извлечение (confidence=0.0).

    Живёт в eval, а не в движке, сознательно: это прибор, которым меряют движок, и он
    обязан быть дешёвым и детерминированным. Качество извлечения меряется режимом llm.
    """
    low = message.text.lower()
    scrubbed = extract_mod.scrub_pii(message.text)
    types = tuple(
        kind for kind, markers in TYPE_MARKERS.items() if any(m in low for m in markers)
    )
    headcount = None
    found = HEADCOUNT_RE.search(low)
    if found:
        headcount = int(found.group(1))
    budget = None
    if any(marker in low for marker in BUDGET_MARKERS) or MONEY_RE.search(low):
        budget = "упомянут бюджет или готовность платить"
    return LeadFacts(
        request_types=types,
        jurisdiction_hint=None,
        headcount=headcount,
        timeline_days=_timeline_days(message.text, message.received_at),
        budget_hint=budget,
        language=extract_mod.detect_language(message.text),
        is_spam=any(marker in low for marker in SPAM_MARKERS),
        has_contact=scrubbed.has_contact,
        confidence=0.0,
        quotes=(),
    )


# --- приоритет: сперва движок, и только если его нет — заглушка стенда ---

LEAD_SCORER_NAMES = ("score_lead", "score_inbound", "lead_tier", "score_facts")


def fallback_tier(facts: LeadFacts) -> Tier:
    """DEBT(2026-09-09): в `leadcentre/engine/score.py` пока нет скоринга обращений — там
    только компании из GLEIF. Пока его нет, стенд считает приоритет сам, по тем же правилам,
    что даны владельцу для разметки (docs/data/inbound_seed.md). Как только в движке появится
    `score_lead`, стенд возьмёт его, и эта функция вызываться перестанет; чем именно считали,
    видно в шапке вывода (Е2).
    """
    if facts.is_spam:
        return Tier.LOW
    has_subject = bool(facts.request_types) or facts.headcount is not None
    if not has_subject and not facts.has_contact:
        return Tier.LOW
    urgent = facts.timeline_days is not None and facts.timeline_days <= URGENT_DAYS
    package = len(facts.request_types) >= PACKAGE_MIN_TYPES
    if urgent or bool(facts.budget_hint) or package:
        return Tier.HIGH
    if has_subject:
        return Tier.MEDIUM
    return Tier.LOW


def resolve_scorer():
    """Кто считает приоритет — выводится из того, что действительно нашлось (Е2)."""
    from leadcentre.engine import score as score_mod

    for name in LEAD_SCORER_NAMES:
        fn = getattr(score_mod, name, None)
        if callable(fn):
            return fn, f"leadcentre.engine.score.{name}"
    return (
        fallback_tier,
        "eval.run_eval.fallback_tier [DEBT: в движке скоринга обращений ещё нет]",
    )


def call_scorer(fn, facts: LeadFacts, message: InboundMessage) -> Tier:
    params = [
        p
        for p in inspect.signature(fn).parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        and p.default is inspect.Parameter.empty
    ]
    result = fn(facts, message) if len(params) >= 2 else fn(facts)
    tier = getattr(result, "tier", result)
    if not isinstance(tier, Tier):
        raise CannotMeasure(f"скоринг вернул не Tier, а {type(tier).__name__}")
    return tier


# --- кэш ответов модели ---


def cache_key(message: InboundMessage) -> str:
    provider = extract_mod.get_provider()
    parts = (extract_mod.PROMPT_VERSION, provider.name, provider.model(), message.text)
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


def facts_to_dict(facts: LeadFacts) -> dict:
    return {
        "request_types": [t.value for t in facts.request_types],
        "jurisdiction_hint": facts.jurisdiction_hint,
        "headcount": facts.headcount,
        "timeline_days": facts.timeline_days,
        "budget_hint": facts.budget_hint,
        "language": facts.language,
        "is_spam": facts.is_spam,
        "has_contact": facts.has_contact,
        "confidence": facts.confidence,
        "quotes": list(facts.quotes),
    }


def facts_from_dict(raw: dict) -> LeadFacts:
    return LeadFacts(
        request_types=tuple(RequestType(v) for v in raw.get("request_types", ())),
        jurisdiction_hint=raw.get("jurisdiction_hint"),
        headcount=raw.get("headcount"),
        timeline_days=raw.get("timeline_days"),
        budget_hint=raw.get("budget_hint"),
        language=raw.get("language", "en"),
        is_spam=bool(raw.get("is_spam")),
        has_contact=bool(raw.get("has_contact")),
        confidence=float(raw.get("confidence") or 0.0),
        quotes=tuple(raw.get("quotes", ())),
    )


@dataclass
class Engine:
    """Движок глазами стенда: обращение -> приоритет либо «не смогли» (Р1)."""

    mode: str
    scorer: object
    scorer_name: str
    cache: dict
    cache_path: Path
    use_cache: bool = True
    llm_calls: int = 0
    cache_hits: int = 0

    def facts(self, message: InboundMessage) -> LeadFacts:
        if self.mode == "rules":
            return rules_facts(message)
        key = cache_key(message)
        if self.use_cache and key in self.cache:
            self.cache_hits += 1
            return facts_from_dict(self.cache[key]["facts"])
        try:
            extraction = extract_mod.extract_detailed(message)
        except extract_mod.ExtractionError as exc:
            raise CannotMeasure(f"извлечение: {exc}") from exc
        self.llm_calls += 1
        self.cache[key] = {
            "external_id": message.external_id,
            "provider": extraction.provider,
            "model": extraction.model,
            "elapsed_s": round(extraction.elapsed_s, 3),
            "input_tokens": extraction.input_tokens,
            "output_tokens": extraction.output_tokens,
            "facts": facts_to_dict(extraction.facts),
        }
        if self.use_cache:
            save_cache(self.cache_path, self.cache)
        return extraction.facts

    def tier(self, message: InboundMessage) -> Tier:
        return call_scorer(self.scorer, self.facts(message), message)


# --- каппа Коэна: своя реализация, сторонних библиотек нет ---


def cohen_kappa(pairs: list[tuple[str, str]]) -> tuple[float | None, str]:
    """Каппа по парам (разметка человека, приоритет движка). Вырождение — третий исход (Р1).

    Возвращает (значение или None, пояснение). None означает «не смогли измерить», а не 0.0:
    ноль — это «согласия не больше случайного», вырождение — «мерить нечем».
    """
    n = len(pairs)
    if n == 0:
        return None, "нет ни одной пары (разметка/движок)"
    agreed = sum(1 for a, b in pairs if a == b)
    po = agreed / n
    left = Counter(a for a, _ in pairs)
    right = Counter(b for _, b in pairs)
    pe = sum((left[c] / n) * (right[c] / n) for c in set(left) | set(right))
    if abs(1.0 - pe) < 1e-12:
        return None, (
            f"каппа вырождена: случайное согласие pe={pe:.4f} = 1.0 — "
            "обе стороны выдали один и тот же единственный класс, делить не на что"
        )
    return (po - pe) / (1.0 - pe), f"po={po:.4f}, pe={pe:.4f}, пар {n}"


def degeneracy_notes(pairs: list[tuple[str, str]]) -> list[str]:
    """Негативный контроль самого прибора (И5): одноклассовая сторона — не «отличный результат»."""
    notes: list[str] = []
    sides = (("разметка", [a for a, _ in pairs]), ("движок", [b for _, b in pairs]))
    for who, values in sides:
        used = sorted(set(values))
        if values and len(used) <= 1:
            notes.append(
                f"ВЫРОЖДЕНО: {who} использовал(а) 1 класс из {len(TIERS)} ({used[0]}) — "
                "каппа на таком входе ничего не измеряет, как бы она ни выглядела"
            )
    return notes


def confusion(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
    matrix = {(h, e): 0 for h in TIERS for e in TIERS}
    for human, engine in pairs:
        matrix[(human, engine)] = matrix.get((human, engine), 0) + 1
    return matrix


def print_confusion(matrix: dict[tuple[str, str], int]) -> None:
    header = f"{'разметка / движок':<20}" + "".join(f"{t:>9}" for t in TIERS) + f"{'сумма':>9}"
    print(header)
    print("-" * len(header))
    for human in TIERS:
        row = [matrix.get((human, e), 0) for e in TIERS]
        print(f"{human:<20}" + "".join(f"{v:>9}" for v in row) + f"{sum(row):>9}")
    print("-" * len(header))
    totals = [sum(matrix.get((h, e), 0) for h in TIERS) for e in TIERS]
    print(f"{'сумма':<20}" + "".join(f"{v:>9}" for v in totals) + f"{sum(totals):>9}")


# --- блоки измерения ---


@dataclass
class Block:
    """Итог блока числами и одним из трёх исходов (Р1/Р2)."""

    name: str
    checked: int = 0
    matched: int = 0
    mismatched: int = 0
    unmeasured: int = 0
    verdict: str = "не смогли проверить"
    notes: list[str] = field(default_factory=list)

    def counts(self) -> str:
        return (
            f"проверено {self.checked}, совпало {self.matched}, "
            f"разошлось {self.mismatched}, не смогли {self.unmeasured}"
        )


def measure_agreement(
    engine: Engine, messages: dict[str, InboundMessage], labels_path: Path
) -> tuple[Block, list[tuple[str, str]]]:
    block = Block("1. Согласие с разметкой (каппа Коэна)")
    if not labels_path.exists():
        block.notes.append(f"не смогли измерить: нет {labels_path}")
        return block, []
    labels, bad = load_labels(labels_path)
    if bad:
        block.notes.append(
            f"строк разметки без валидного tier_expected: {len(bad)} ({', '.join(bad[:5])})"
        )
    pairs: list[tuple[str, str]] = []
    for ext, expected in sorted(labels.items()):
        message = messages.get(ext)
        if message is None:
            block.unmeasured += 1
            block.notes.append(f"{ext}: в наборе обращений такого id нет")
            continue
        try:
            tier = engine.tier(message)
        except CannotMeasure as exc:
            block.unmeasured += 1
            block.notes.append(f"{ext}: {exc}")
            continue
        if tier is Tier.INVALID:
            block.unmeasured += 1
            block.notes.append(f"{ext}: движок сказал INVALID — это не LOW и не ошибка разметки")
            continue
        pairs.append((expected, tier.value))
    block.checked = len(pairs)
    block.matched = sum(1 for a, b in pairs if a == b)
    block.mismatched = block.checked - block.matched
    block.notes.extend(degeneracy_notes(pairs))
    return block, pairs


def measure_stability(engine: Engine, messages: list[InboundMessage], runs: int) -> Block:
    block = Block(f"3. Стабильность ({runs} прогона одного входа)")
    for message in messages:
        tiers: list[str] = []
        failed = False
        for _ in range(runs):
            try:
                tiers.append(engine.tier(message).value)
            except CannotMeasure:
                failed = True
                break
        if failed:
            block.unmeasured += 1
            continue
        block.checked += 1
        if len(set(tiers)) == 1:
            block.matched += 1
        else:
            block.mismatched += 1
            block.notes.append(f"{message.external_id}: {' -> '.join(tiers)}")
    if block.checked:
        share = block.matched / block.checked
        block.notes.append(f"доля совпавших приоритетов: {share:.4f} (цель >= {STABILITY_TARGET})")
        block.verdict = "годно" if share >= STABILITY_TARGET else "не годно"
    return block


def measure_controls(engine: Engine, messages: dict[str, InboundMessage], path: Path) -> Block:
    block = Block("4. Негативные контроли")
    if not path.exists():
        block.notes.append(f"не смогли измерить: нет {path}")
        return block
    rows = load_controls(path)
    for row in rows:
        ext = row["external_id"].strip()
        must = (row.get("must_be") or "").strip().upper()
        message = messages.get(ext)
        if message is None or must not in TIERS:
            block.unmeasured += 1
            block.notes.append(
                f"{ext:8} НЕ СМОГЛИ  ожидание {must or 'пусто'}, "
                f"обращение найдено: {message is not None}"
            )
            continue
        try:
            got = engine.tier(message).value
        except CannotMeasure as exc:
            block.unmeasured += 1
            block.notes.append(f"{ext:8} НЕ СМОГЛИ  {exc}")
            continue
        block.checked += 1
        if got == must:
            block.matched += 1
            mark = "ок      "
        else:
            block.mismatched += 1
            mark = "ПРОВАЛ  "
        block.notes.append(
            f"{ext:8} {mark} обязан {must:6} получил {got:6} — {row.get('why', '')}"
        )
    total = len(rows)
    block.notes.append(
        f"итог контролей: {block.matched} из {total} "
        f"(ожидается {CONTROLS_EXPECTED} из {CONTROLS_EXPECTED})"
    )
    if block.checked == 0:
        block.verdict = "не смогли проверить"
    elif block.matched == total == CONTROLS_EXPECTED:
        block.verdict = "годно"
    else:
        block.verdict = "не годно"
    return block


# --- прогон ---


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_eval", description="стенд измерения движка")
    parser.add_argument("--engine", default="rules", choices=["rules", "llm"])
    parser.add_argument("--labels", default=str(LABELS_PATH))
    parser.add_argument("--seed", default=str(SEED_PATH))
    parser.add_argument("--controls", default=str(CONTROLS_PATH))
    parser.add_argument("--cache", default=str(CACHE_PATH))
    parser.add_argument("--runs", type=int, default=STABILITY_RUNS)
    parser.add_argument("--limit", type=int, default=0, help="ограничить набор (для llm)")
    parser.add_argument("--no-cache", action="store_true", help="не читать и не писать кэш модели")
    args = parser.parse_args(argv)

    seed_path, labels_path = Path(args.seed), Path(args.labels)
    messages = load_messages(seed_path)
    if args.limit:
        messages = messages[: args.limit]
    by_id = {m.external_id: m for m in messages}

    scorer, scorer_name = resolve_scorer()
    engine = Engine(
        mode=args.engine,
        scorer=scorer,
        scorer_name=scorer_name,
        cache=({} if args.no_cache else load_cache(Path(args.cache))),
        cache_path=Path(args.cache),
        use_cache=not args.no_cache,
    )

    print("=" * 78)
    print(
        f"стенд eval: извлечение фактов = {args.engine}, модель "
        f"{'НЕ вызывается' if args.engine == 'rules' else 'вызывается'}"
    )
    print(f"скоринг приоритета: {scorer_name}")
    print(f"вход: {seed_path} — обращений {len(messages)}")
    print(f"разметка: {labels_path}")
    print("=" * 78)

    blocks: list[Block] = []

    agreement, pairs = measure_agreement(engine, by_id, labels_path)
    kappa, kappa_note = cohen_kappa(pairs)
    degenerate = any(n.startswith("ВЫРОЖДЕНО") for n in agreement.notes)
    if kappa is None or degenerate:
        agreement.verdict = "не смогли проверить"
    elif kappa >= KAPPA_TARGET:
        agreement.verdict = "годно"
    else:
        agreement.verdict = "не годно"
    blocks.append(agreement)

    print(f"\n== {agreement.name} ==")
    print(agreement.counts())
    if kappa is None:
        print(f"каппа: не смогли измерить: {kappa_note}")
    else:
        print(f"каппа Коэна: {kappa:.4f} ({kappa_note}); цель >= {KAPPA_TARGET}")
        if degenerate:
            print("каппа посчиталась, но НЕ ЗАСЧИТЫВАЕТСЯ — см. строку ВЫРОЖДЕНО ниже")
    for note in agreement.notes:
        print(f"  {note}")
    print(f"исход блока: {agreement.verdict}")

    print("\n== 2. Матрица ошибок 3x3 ==")
    if pairs:
        print_confusion(confusion(pairs))
    else:
        print("не смогли построить: нет ни одной пары (разметка/движок)")

    stability = measure_stability(engine, messages, args.runs)
    blocks.append(stability)
    print(f"\n== {stability.name} ==")
    print(stability.counts())
    for note in stability.notes:
        print(f"  {note}")
    print(f"исход блока: {stability.verdict}")

    controls = measure_controls(engine, by_id, Path(args.controls))
    blocks.append(controls)
    print(f"\n== {controls.name} ==")
    print(controls.counts())
    for note in controls.notes:
        print(f"  {note}")
    print(f"исход блока: {controls.verdict}")

    if args.engine == "llm":
        print(
            f"\nмодель: вызовов {engine.llm_calls}, попаданий в кэш {engine.cache_hits}, "
            f"кэш: {args.cache}"
        )
        if engine.cache_hits and args.runs > 1:
            print(
                "ВНИМАНИЕ: стабильность считана по кэшу — это детерминизм скоринга, "
                "а не разброс модели. Разброс модели меряется с --no-cache и стоит денег."
            )

    print("\n" + "=" * 78)
    print("ИТОГО по блокам (Р1: три исхода, третий не сворачивается в первые два)")
    total = Block("всего")
    for block in blocks:
        print(f"  {block.name:<46} {block.verdict:<20} {block.counts()}")
        total.checked += block.checked
        total.matched += block.matched
        total.mismatched += block.mismatched
        total.unmeasured += block.unmeasured
    print(total.counts())

    verdicts = [b.verdict for b in blocks]
    if "не годно" in verdicts:
        print("вердикт стенда: НЕ ГОДНО")
        return EXIT_FAILED
    if "не смогли проверить" in verdicts:
        print("вердикт стенда: НЕ СМОГЛИ ПРОВЕРИТЬ (это не успех — Р2)")
        return EXIT_UNMEASURABLE
    print("вердикт стенда: ГОДНО")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(run())
