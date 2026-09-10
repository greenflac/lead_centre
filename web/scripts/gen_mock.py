"""Генератор mock-данных для дашборда: настоящие файлы репозитория → web/mock/*.json.

Почему генератор, а не рукописный JSON: и приоритеты, и причины, и evidence, и
черновики ответов должны быть теми же, что выдаст живой бэкенд. Поэтому скрипт
импортирует настоящий движок — `leadcentre.engine.score.score_inbound`, `score.score`,
`leadcentre.engine.reply.draft`, `leadcentre.sources.gleif.GleifAdapter` — и не
воспроизводит ни одну из этих формул у себя.

Что скрипт делает сам и почему это честно помечено:
  * `extract_facts_offline()` — заменитель LLM-извлечения (`engine/extract.py` ходит в
    Anthropic/Pollinations, а mock обязан собираться без сети и без ключа). Это
    ЭВРИСТИКА ДЛЯ ДЕМО-ДАННЫХ, не движок: она отмечена в каждой карточке полем
    `facts_source: "offline_heuristic"`, и дашборд показывает это в карточке.
    Когда бэкенд поднимется, mock перестаёт использоваться (NEXT_PUBLIC_API_URL).

Вход:  data/inbound_seed.csv (70 синтетических обращений),
       data/gleif_ae_lapsed_sample.json (60 просрочек LEI, через GleifAdapter offline).
Выход: web/mock/leads.json, web/mock/companies.json, web/mock/stats.json.

Запуск: python3 web/scripts/gen_mock.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from leadcentre.engine import extract as extract_mod
from leadcentre.engine import reply as reply_mod
from leadcentre.engine.facts_rules import rules_facts
from leadcentre.engine.reasons import Language, ReasonCode
from leadcentre.engine.reasons import render as render_reason
from leadcentre.engine.score import score, score_inbound
from leadcentre.models import InboundMessage, Tier
from leadcentre.sources.gleif import GleifAdapter

# --- чем обслужен лид (README обещает это в карточке) ---
#
# В mock-режиме модель НЕ вызывается: факты даёт офлайн-эвристика rules_facts. Поэтому в
# карточку идёт то, что действительно исполнилось: имя эвристики, её замеренное время
# и нулевая стоимость. Отдельной строкой — решение маршрутизатора: какую модель выбрал бы
# живой контур для этого текста. Решение настоящее, его принимает extract.route() по длине
# обращения, до всякой сети, поэтому его можно показать честно и назвать «маршрут», а не
# «обслужено».
OFFLINE_MODEL = "rules_facts (offline heuristic)"

# Язык интерфейса дашборда. Причины движка отрисовываются на нём (каталог
# engine/reasons.py двуязычный), текст обращения и черновик — на языке клиента.
UI_LANGUAGE = Language.EN

# ИЗМЕРЕНО, прогон набора на живом контуре, записан в README §«Measured»:
# $0.0033 за обращение при холодном кэше; задержка извлечения — 35.1 с на 8 обращений.
# Числа сюда попадают как справка «сколько стоит то же самое на модели», и в карточке
# помечены как замер по набору, а не как замер этого обращения.
LIVE_COST_USD_PER_LEAD = 0.0033
LIVE_LATENCY_S = 35.1 / 8

SEED_CSV = REPO / "data" / "inbound_seed.csv"
OUT_DIR = REPO / "web" / "mock"

# «Сегодня» демо-данных. ВЫБРАНО: дата, на которую снят seed и выборка GLEIF — иначе
# «время с получения» уедет и все обращения станут месячной давности.
TODAY = date(2026, 9, 9)
NOW = datetime(2026, 9, 9, 18, 0, tzinfo=UTC)

# --- эвристика извлечения (только для mock, см. докстринг) ---


JURISDICTION_MARKERS = (
    ("mainland", ("mainland", "майнленд", "материк", "det", "дед")),
    ("freezone", ("фризон", "фриз зон", "free zone", "freezone", "ifza", "meydan", "dmcc",
                  "шамс", "shams", "rakez", "saif")),
)

# Фразы срочности → срок в днях. ВЫБРАНО (автор, 2026-09-09) по формулировкам seed;
# порог «горячего» срока живёт в rubric.URGENT_TIMELINE_DAYS, здесь только чтение текста.
TIMELINE_PHRASES: tuple[tuple[str, int], ...] = (
    ("срочно", 5),
    ("asap", 5),
    ("urgent", 5),
    ("до конца месяца", 20),
    ("в этом месяце", 20),
    ("this month", 20),
    ("на этой неделе", 5),
    ("this week", 5),
    ("завтра", 1),
    ("сегодня", 1),
    ("через месяц", 30),
    ("с 1 октября", 22),
    ("1 октября", 22),
    ("в октябре", 30),
    ("in october", 30),
    ("в ноябре", 60),
    ("in nov", 60),
    ("к декабрю", 85),
)
TIMELINE_RE_DAYS = re.compile(r"через\s+(\d{1,3})\s*(дн|дней|дня)", re.IGNORECASE)
TIMELINE_RE_WEEKS = re.compile(r"через\s+(\d{1,2})\s*(недел)", re.IGNORECASE)
TIMELINE_RE_EXPIRES = re.compile(r"(истека\w*|expires?)\D{0,20}(\d{1,3})\s*(дн|day)", re.IGNORECASE)

HEADCOUNT_RE = re.compile(
    r"(\d{1,3})\s*(человек|чел\b|сотрудник\w*|рабочих мест|рабочих\s+мест|мест\b|ppl\b|"
    r"people|employees|staff|виз\w*|visas?)",
    re.IGNORECASE,
)
HEADCOUNT_RE_TEAM = re.compile(r"(?:команд\w*|team)\D{0,12}(\d{1,3})", re.IGNORECASE)
PHONE_RE = re.compile(r"\+?\d[\d\-\s()]{8,}\d")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Уверенность извлечения. ВЫБРАНО: в mock нет модели, поэтому число выводится из того,
# сколько фактов реально нашлось, и никогда не выдаётся за замер (поле facts_source).
CONF_BASE = 0.55
CONF_PER_FACT = 0.09
CONF_MAX = 0.93
CONF_EMPTY = 0.0


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?\n])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _quote_for(text: str, marker: str) -> str | None:
    """Цитата-доказательство: предложение, в котором нашёлся маркер."""
    low = text.lower()
    if marker not in low:
        return None
    for sentence in _sentences(text):
        if marker in sentence.lower():
            return sentence[:180]
    return text.strip()[:180]


CATEGORY_BY_PREFIX = {
    "urg": "urgent corporate",
    "gen": "general setup question",
    "prc": "price-only",
    "visa": "visa",
    "acct": "accounting & banking",
    "spam": "spam / off-topic",
    "edge": "edge case",
}



# --- какая цитата доказывает какую причину (02_references.md §6.3) ---
#
# Связь не выводится из формулировки причины руками: по каждой цитате прогоняется тот же
# извлекатель rules_facts, что дал факты, и цитата признаётся доказательством, если из неё
# самой следует тот же факт (никаких копий маркеров). Причины, у которых цитаты быть
# не может (язык обращения, уверенность извлечения), получают пустой список: третий исход
# «не цитируется» не сворачивается в «цитата не нашлась».

def _facts_of(fragment: str, received_on: date) -> object:
    return rules_facts(InboundMessage(
        external_id="quote", channel="form", text=fragment, received_at=received_on,
    ))


def link_reasons(result, quotes, facts, received_on: date) -> list[dict]:
    """Для каждой причины — индексы цитат, из которых она следует.

    Связь идёт по КОДУ причины (`Score.reason_items`), а не по началу её текста:
    формулировки живут в `engine/reasons.py` и переписываются, а код — контракт.
    """
    per_quote = [_facts_of(q, received_on) for q in quotes]

    def pick(test) -> list[int]:
        return [i for i, f in enumerate(per_quote) if test(f)]

    by_code = {
        ReasonCode.URGENT_TIMELINE: lambda f: f.timeline_days == facts.timeline_days,
        ReasonCode.PACKAGE_REQUEST: lambda f: bool(f.request_types),
        ReasonCode.TEAM_OVER_FLEXI_QUOTA: lambda f: f.headcount == facts.headcount,
        # Цитата доказывает бюджет, только если из неё следует ТОТ ЖЕ бюджет. Просто
        # «в цитате нашлось хоть что-то денежное» подсовывало под причину
        # «budget named: 150 тысяч» предложение про оборот «около 4 млн AED» (edge-03):
        # доказательство называло другое число, чем причина.
        ReasonCode.BUDGET_NAMED: lambda f: f.budget_hint == facts.budget_hint,
        ReasonCode.SPAM_OR_OFF_TOPIC: lambda f: f.is_spam,
    }
    linked: list[dict] = []
    texts = result.reasons_in(UI_LANGUAGE)
    for item, text in zip(result.reason_items, texts, strict=True):
        test = by_code.get(item.code)
        linked.append({"text": text, "code": item.code.value,
                       "quotes": pick(test) if test else []})
    return linked


def build_leads() -> list[dict]:
    prices = reply_mod.load_prices()
    rows = list(csv.DictReader(SEED_CSV.open(encoding="utf-8")))
    leads: list[dict] = []
    for index, row in enumerate(rows):
        external_id = row["external_id"]
        text = row["text"]
        received_on = date.fromisoformat(row["received_at"])
        # Время внутри суток — детерминированное, чтобы «2 ч назад» не прыгало между
        # прогонами: минута выводится из позиции строки, а не из random.
        received_at = datetime(
            received_on.year, received_on.month, received_on.day,
            8 + (index * 7) % 11, (index * 17) % 60, tzinfo=UTC,
        )
        message = InboundMessage(
            external_id=external_id,
            channel=row["channel"],
            text=text,
            received_at=received_on,
            is_synthetic=row["is_synthetic"].strip().lower() == "true",
        )
        # Общая с измерительным стендом эвристика: своя копия расходилась.
        started = time.perf_counter()
        facts = rules_facts(
            InboundMessage(
                external_id=external_id,
                channel=row["channel"],
                text=text,
                received_at=date.fromisoformat(row["received_at"][:10]),
            )
        )
        heuristic_ms = (time.perf_counter() - started) * 1000
        result = score_inbound(message, facts)
        drafted = reply_mod.draft(message, facts, result.tier, prices)
        chosen = extract_mod.route(message)
        # Подпись маршрута берётся из каталога движка на языке интерфейса, а не собирается
        # здесь: своя английская формулировка была вторым источником текста и разошлась
        # бы с русской при первой же правке. `Route.reason` остаётся русской строкой для
        # отчётов, но помнит свой код — из кода и отрисовывается английский.
        route_reason = render_reason(chosen.reason_item, UI_LANGUAGE)
        quotes = [e.value for e in result.evidence if e.kind == "quote"]
        leads.append({
            "id": external_id,
            "channel": message.channel,
            "text": text,
            "language": row["language"],
            "category": CATEGORY_BY_PREFIX.get(external_id.split("-")[0], "other"),
            "received_at": received_at.isoformat().replace("+00:00", "Z"),
            "is_synthetic": message.is_synthetic,
            "tier": result.tier.value,
            # Причины — на языке интерфейса. Текст обращения и черновик остаются на языке
            # клиента: язык интерфейса и язык переписки — разные вещи.
            "reasons": list(result.reasons_in(UI_LANGUAGE)),
            "reason_links": link_reasons(result, quotes, facts, received_on),
            "evidence": [{"kind": e.kind, "value": e.value} for e in result.evidence],
            "violations": list(result.violations),
            "facts": {
                "request_types": [r.value for r in facts.request_types],
                "jurisdiction_hint": facts.jurisdiction_hint,
                "headcount": facts.headcount,
                "timeline_days": facts.timeline_days,
                "budget_hint": facts.budget_hint,
                "language": facts.language,
                "is_spam": facts.is_spam,
                "has_contact": facts.has_contact,
                "confidence": facts.confidence,
            },
            "facts_source": "offline_heuristic",
            # Чем обслужен лид: исполнилось — офлайн-эвристика; маршрут — настоящее
            # решение extract.route() по длине текста.
            "serving": {
                "provider": "offline",
                "model": OFFLINE_MODEL,
                "latency_ms": round(heuristic_ms, 2),
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "route_model": chosen.model,
                "route_reason": route_reason,
                "live_cost_usd_per_lead": LIVE_COST_USD_PER_LEAD,
                "live_latency_s": round(LIVE_LATENCY_S, 1),
            },
            "reply": {
                "body": drafted.body,
                "language": drafted.language,
                "outcome": drafted.outcome,
                "needs_human": drafted.needs_human,
                "used_prices": list(drafted.used_prices),
                "notice": drafted.notice,
            },
            "status": "new",
        })
    leads.sort(key=lambda item: item["received_at"], reverse=True)
    return leads


def build_companies() -> list[dict]:
    fetched = GleifAdapter(mode="lapsed", offline=True).fetch(60)
    out: list[dict] = []
    for company in fetched.companies:
        result = score(company, TODAY)
        out.append({
            "id": company.external_id,
            "name": company.name,
            "city": company.city,
            "country": company.country,
            "address_lines": list(company.address_lines),
            "registrar_id": company.registrar_id,
            "license_no": company.license_no,
            "created_on": company.created_on.isoformat() if company.created_on else None,
            "next_renewal_on": (
                company.next_renewal_on.isoformat() if company.next_renewal_on else None
            ),
            "registration_status": company.registration_status,
            "entity_active": company.entity_active,
            "tier": result.tier.value,
            "address_type": result.address_type.value,
            "event": result.event.value,
            "reasons": list(result.reasons_in(UI_LANGUAGE)),
            "evidence": [{"kind": e.kind, "value": e.value} for e in result.evidence],
            "violations": list(result.violations),
            "source": company.source,
            "is_synthetic": company.is_synthetic,
        })
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INVALID": 3}
    out.sort(key=lambda item: (order.get(item["tier"], 9), item["name"]))
    return out, fetched


def build_stats(leads: list[dict], companies: list[dict], fetched) -> dict:
    def by_tier(items: list[dict]) -> dict[str, int]:
        counts = {t.value: 0 for t in Tier}
        for item in items:
            counts[item["tier"]] = counts.get(item["tier"], 0) + 1
        return counts

    return {
        "generated_at": NOW.isoformat().replace("+00:00", "Z"),
        "leads": {
            "checked": len(leads),
            "by_tier": by_tier(leads),
            "violations": sum(len(lead["violations"]) for lead in leads),
            "synthetic": sum(1 for lead in leads if lead["is_synthetic"]),
            "real": sum(1 for lead in leads if not lead["is_synthetic"]),
            "source": "data/inbound_seed.csv",
        },
        "companies": {
            "checked": len(companies),
            "by_tier": by_tier(companies),
            "violations": sum(len(c["violations"]) for c in companies),
            "skipped": fetched.skipped,
            "fetched": fetched.fetched,
            "source": fetched.source + (" [cache]" if fetched.from_cache else " [network]"),
        },
        "notes": [
            "Mock mode: data generated from repository files, no backend attached.",
            "Lead facts come from an offline heuristic, not from the LLM extractor.",
        ],
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    leads = build_leads()
    companies, fetched = build_companies()
    stats = build_stats(leads, companies, fetched)
    for name, payload in (("leads", leads), ("companies", companies), ("stats", stats)):
        path = OUT_DIR / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        print(f"{path.relative_to(REPO)}: "
              f"{len(payload) if isinstance(payload, list) else 'object'}")
    # Числами, а не флагом.
    print(f"leads: checked {len(leads)}, tiers {stats['leads']['by_tier']}, "
          f"violations {stats['leads']['violations']}")
    print(f"companies: checked {len(companies)}, tiers {stats['companies']['by_tier']}, "
          f"skipped {fetched.skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
