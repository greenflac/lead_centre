"""HTTP layer: FastAPI over the same engine the CLI uses.

The work lives in `handle_*` functions, so tests call them without a server. Every
response carries an explicit outcome, storage failure has its own block, and a card is
never returned empty in place of an error.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, date, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from leadcentre.crm import CrmLead, get_sink
from leadcentre.engine import lint as lint_module
from leadcentre.engine import reply as reply_module
from leadcentre.engine.evidence import reason_links_payload
from leadcentre.engine.extract import (
    PROMPT_VERSION,
    ExtractionError,
    ProviderBudgetError,
    extract_detailed,
    is_offline,
)
from leadcentre.engine.reasons import DEFAULT_LANGUAGE
from leadcentre.engine.score import score, score_inbound
from leadcentre.models import InboundMessage, Tier, facts_from_dict
from leadcentre.report import build as build_report
from leadcentre.sources.gleif import GleifAdapter
from leadcentre.store import (
    REPLY_APPROVED,
    REPLY_REJECTED,
    CompanyRow,
    DisagreementRow,
    LeadCard,
    LeadRow,
    ReplyRow,
    RestoredReasons,
    ScoreRow,
    StoreError,
    StoreRejected,
    StoreUnavailable,
    get_store,
    reason_items_payload,
    restore_reasons,
)

logger = logging.getLogger("leadcentre.api")

DEFAULT_DISCOVER_LIMIT = 30   # same default as the CLI
MAX_DISCOVER_LIMIT = 200      # the GLEIF page limit
DEFAULT_LIST_LIMIT = 50

OUTCOME_OK = "ok"
OUTCOME_REJECTED = "rejected"
OUTCOME_UNAVAILABLE = "unavailable"

app = FastAPI(
    title="Lead Centre",
    version="0.1.0",
    description="Квалификация входящих обращений и компаний из внешних реестров",
)

# Why built once: LocalStore holds a file and a lock, Supabase holds keys.
_store = None
_sink = None


def store():
    """Returns the process store, built on first use."""
    global _store
    if _store is None:
        _store = get_store()
    return _store


def sink():
    """Returns the process CRM sink, built on first use."""
    global _sink
    if _sink is None:
        _sink = get_sink()
    return _sink


class LeadIn(BaseModel):
    """Body of `POST /leads`: one inbound request as it arrived from the channel."""

    text: str = Field(..., description="Текст обращения как пришёл из канала")
    channel: str = Field("form", description="jivo | whatsapp | telegram | form")
    source: str | None = Field(None, description="Откуда пришло; по умолчанию = channel")
    external_id: str | None = None
    is_synthetic: bool = Field(
        False, description="Синтетика для демо и тестов; в отчёте считается отдельно"
    )


class DisagreeIn(BaseModel):
    """Body of the disagree endpoint: why the manager rejects the score."""

    reason: str = Field(..., min_length=1, description="Почему оценка неверна — вход для eval")
    author: str = ""


class DiscoverIn(BaseModel):
    """Body of `POST /discover/run`: registry sweep mode and page size."""

    mode: str = Field("lapsed", description="lapsed | fresh")
    limit: int = Field(DEFAULT_DISCOVER_LIMIT, ge=1, le=MAX_DISCOVER_LIMIT)


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    body = {"outcome": OUTCOME_UNAVAILABLE, "code": code, "message": message, **extra}
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(ProviderBudgetError)
async def _budget_handler(_: Request, exc: ProviderBudgetError) -> JSONResponse:
    """Reports an exhausted provider budget as 402; the `code` field is the real signal."""
    return _error(
        402, "provider_budget", str(exc),
        hint="пополните баланс провайдера модели или поставьте OFFLINE=1 для демо",
    )


@app.exception_handler(ExtractionError)
async def _extraction_handler(_: Request, exc: ExtractionError) -> JSONResponse:
    return _error(502, "extraction_failed", str(exc))


@app.exception_handler(StoreUnavailable)
async def _store_unavailable_handler(_: Request, exc: StoreUnavailable) -> JSONResponse:
    return _error(503, "store_unavailable", str(exc))


@app.exception_handler(StoreRejected)
async def _store_rejected_handler(_: Request, exc: StoreRejected) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"outcome": OUTCOME_REJECTED, "code": "store_rejected", "message": str(exc)},
    )


def _reasons_block(restored: RestoredReasons) -> dict[str, Any]:
    """Builds the reasons block: codes, texts per language and a restore outcome.

    Only languages actually assembled are listed, so a missing key is a signal.
    """
    return {
        "reasons": list(restored.texts.get(DEFAULT_LANGUAGE.value, restored.stored_texts)),
        "reason_items": reason_items_payload(restored.items),
        "reasons_by_language": restored.texts,
        "reasons_outcome": restored.outcome,
        "reasons_detail": restored.detail,
    }


def _live_reasons(score_obj) -> RestoredReasons:
    """Builds the reasons block for a fresh score, through the same serialise-and-restore
    path as a stored one, so live and restored cards cannot drift apart."""
    return restore_reasons(
        {
            "reasons": list(score_obj.reasons),
            "reason_items": reason_items_payload(score_obj.reason_items),
        }
    )


def _score_payload(company_score) -> dict[str, Any]:
    return {
        "tier": company_score.tier.value,
        "address_type": company_score.address_type.value,
        "event": company_score.event.value,
        **_reasons_block(_live_reasons(company_score)),
        "evidence": [{"kind": e.kind, "value": e.value} for e in company_score.evidence],
        "violations": list(company_score.violations),
    }


def _card_payload(card: LeadCard) -> dict[str, Any]:
    """Converts a stored card into an API payload, re-rendering reasons in both languages."""
    payload = card.as_dict()
    if payload.get("score") is not None:
        restored = card.reasons()
        payload["score"] = {**payload["score"], **_reasons_block(restored)}
        links = _reason_links(
            restored.items,
            facts_from_dict(payload["lead"].get("facts") or {}),
            payload["lead"].get("received_at"),
        )
        if links is not None:
            payload["score"]["reason_links"] = links
    return payload


def _optional(key: str, value: Any) -> dict[str, Any]:
    """Keeps a key out of the payload when there is no answer, rather than sending a blank."""
    return {} if value is None else {key: value}


def _reason_links(items, facts, received_at: Any) -> list[dict[str, Any]] | None:
    """Links each reason to the quotes proving it, or None when it could not be done.

    Three outcomes, not two. A reason that cannot be quoted gets an empty list; a card
    with no quotes or no usable date gets None, and the key is then left out entirely.
    An empty list in its place would read as "every reason is unquoted", which is a
    different statement -- and the dashboard renders the two differently.
    """
    if not items or not facts.quotes:
        return None
    try:
        received_on = date.fromisoformat(str(received_at)[:10])
    except (TypeError, ValueError):
        return None
    return reason_links_payload(tuple(items), facts.quotes, facts, received_on)


def _facts_payload(facts) -> dict[str, Any]:
    return {
        "request_types": [t.value for t in facts.request_types],
        "jurisdiction_hint": facts.jurisdiction_hint,
        "headcount": facts.headcount,
        "timeline_days": facts.timeline_days,
        "urgency_stated": facts.urgency_stated,
        "budget_hint": facts.budget_hint,
        "language": facts.language,
        "is_spam": facts.is_spam,
        "has_contact": facts.has_contact,
        "confidence": facts.confidence,
        # Why the flag ships beside the number: both zeros look identical in JSON.
        "confidence_measured": facts.confidence_measured,
        "quotes": list(facts.quotes),
    }


def _lint_ok(status: str) -> bool | None:
    """Maps the three linter outcomes onto a column; unverifiable is None, not False."""
    if status == lint_module.STATUS_OK:
        return True
    if status == lint_module.STATUS_VIOLATIONS:
        return False
    return None


def handle_lead(payload: LeadIn, today: date | None = None) -> dict[str, Any]:
    """Turns request text into a card: extract, score, draft, lint, store."""
    received_at = today or datetime.now(UTC).date()
    message = InboundMessage(
        external_id=payload.external_id or str(uuid.uuid4()),
        channel=payload.channel,
        text=payload.text,
        received_at=received_at,
        is_synthetic=payload.is_synthetic,
    )

    extraction = extract_detailed(message)           # may raise ProviderBudgetError
    facts = extraction.facts
    inbound_score = score_inbound(message, facts)
    draft = reply_module.draft(message, facts, inbound_score.tier)

    try:
        lint_result = lint_module.lint(draft)
    except reply_module.PriceListError as exc:
        # Why reported: an unread price list means the number check never ran.
        lint_result = lint_module.LintResult(
            status=lint_module.STATUS_UNVERIFIABLE,
            checks_failed=(f"прайс не прочитан: {exc}",),
        )

    run_id = str(uuid.uuid4())
    storage = {"outcome": OUTCOME_OK, "store": store().name, "detail": ""}
    # Why None, not "": an empty string would be pasted into a URL and yield a 404.
    lead_id: str | None = None
    try:
        lead_id = store().save_lead(
            LeadRow(
                source=payload.source or payload.channel,
                channel=payload.channel,
                raw_text=extraction.scrubbed.text,   # stored already scrubbed of personal data
                facts=_facts_payload(facts),
                language=draft.language,
                is_synthetic=payload.is_synthetic,
                received_at=received_at,
            )
        )
        store().save_score(
            ScoreRow(
                lead_id=lead_id,
                model=extraction.model,
                prompt_version=PROMPT_VERSION,
                usage={
                    "input_tokens": extraction.input_tokens,
                    "output_tokens": extraction.output_tokens,
                    "provider": extraction.provider,
                    "offline": extraction.offline,
                },
                latency_ms=int(extraction.elapsed_s * 1000),
                run_id=run_id,
                tier=inbound_score.tier.value,
                address_type=inbound_score.address_type.value,
                event=inbound_score.event.value,
                # Why codes, not strings: rendering stays possible in both languages.
                reason_items=inbound_score.reason_items,
                evidence=tuple(
                    {"kind": e.kind, "value": e.value} for e in inbound_score.evidence
                ),
                violations=inbound_score.violations,
            )
        )
        store().save_reply(
            ReplyRow(
                lead_id=lead_id,
                language=draft.language,
                body=draft.body,
                lint_ok=_lint_ok(lint_result.status),
                lint_status=lint_result.status,
                lint_violations=lint_result.violations,
            )
        )
    except StoreRejected as exc:
        storage = {"outcome": OUTCOME_REJECTED, "store": store().name, "detail": str(exc)}
        logger.warning("хранилище отвергло запись: %s", exc)
    except StoreUnavailable as exc:
        storage = {"outcome": OUTCOME_UNAVAILABLE, "store": store().name, "detail": str(exc)}
        logger.warning("хранилище недоступно: %s", exc)

    # Why in both places: the root field stays for existing readers, from this same dict.
    reply_payload = {
        "body": draft.body,
        "language": draft.language,
        "outcome": draft.outcome,
        "needs_human": draft.needs_human,
        "used_prices": list(draft.used_prices),
    }
    return {
        "outcome": OUTCOME_OK,
        "lead_id": lead_id,
        "channel": payload.channel,
        "language": reply_payload["language"],
        "facts": _facts_payload(facts),
        "score": _score_payload(inbound_score) | _optional(
            "reason_links",
            _reason_links(inbound_score.reason_items, facts, message.received_at),
        ),
        "reply": reply_payload,
        "lint": {
            "status": lint_result.status,
            "checked": len(lint_result.checks_done),
            "violations": list(lint_result.violations),
            "unverifiable": list(lint_result.checks_failed),
            "summary": lint_result.summary(),
        },
        "extraction": {
            "provider": extraction.provider,
            "model": extraction.model,
            "offline": extraction.offline,
            "latency_ms": int(extraction.elapsed_s * 1000),
            "input_tokens": extraction.input_tokens,
            "output_tokens": extraction.output_tokens,
            "pii_scrubbed": extraction.scrubbed.summary(),
            "dropped_quotes": extraction.dropped_quotes,
            # Why exposed: the divergence between model and code is then a number.
            "timeline_from_model": extraction.timeline_from_model,
        },
        "storage": storage,
        "run_id": run_id,
    }


def handle_discover(payload: DiscoverIn, today: date | None = None) -> dict[str, Any]:
    """Runs the registry sweep: source, scoring, store; offline reads the cache."""
    today = today or datetime.now(UTC).date()
    adapter = GleifAdapter(mode=payload.mode)
    result = adapter.fetch(payload.limit)
    scores = [score(c, today) for c in result.companies]
    run_report = build_report(result, scores)

    rows = [
        CompanyRow(
            external_id=company.external_id,
            source=company.source,
            name=company.name,
            city=company.city,
            license_no=company.license_no,
            registrar_id=company.registrar_id,
            created_on=company.created_on,
            registration_status=company.registration_status,
            next_renewal_on=company.next_renewal_on,
            facts={
                "country": company.country,
                # Why both: a boolean cannot show the third outcome, so the status rides along.
                "entity_active": company.entity_active,
                "entity_status": company.entity_status.value,
                "address_lines": list(company.address_lines),
                "tier": company_score.tier.value,
                "reasons": list(company_score.reasons),
                # Why inside facts: it is jsonb, so no extra column is needed.
                "reason_items": reason_items_payload(company_score.reason_items),
                "violations": list(company_score.violations),
            },
        )
        for company, company_score in zip(result.companies, scores, strict=True)
    ]
    storage: dict[str, Any] = {"outcome": OUTCOME_OK, "store": store().name}
    try:
        upsert = store().upsert_companies(rows)
        storage.update(
            requested=upsert.requested,
            written=upsert.written,
            failed=upsert.failed,
            unavailable=upsert.unavailable,
            summary=upsert.summary(),
        )
        if upsert.unavailable:
            storage["outcome"] = OUTCOME_UNAVAILABLE
        elif upsert.failed:
            storage["outcome"] = OUTCOME_REJECTED
    except StoreError as exc:
        storage = {"outcome": OUTCOME_UNAVAILABLE, "store": store().name, "detail": str(exc)}

    return {
        "outcome": OUTCOME_OK,
        "source": run_report.source,
        "offline": adapter.offline,
        "fetched": result.fetched,
        "skipped": result.skipped,
        "checked": run_report.checked,
        "by_tier": run_report.by_tier,
        "violations": run_report.violations,
        "report": run_report.lines(),
        "storage": storage,
    }


def handle_stats() -> dict[str, Any]:
    """Returns report counters; an unavailable store is "could not", not zeros."""
    health = store().health()
    base = {
        "store": store().name,
        "store_health": {"outcome": health.outcome, "detail": health.detail},
        "offline": is_offline(),
        "crm_sink": sink().name,
    }
    if health.outcome != health.OK:
        return {**base, "outcome": OUTCOME_UNAVAILABLE,
                "message": "числа не собраны: хранилище недоступно"}
    try:
        counters = store().counters()
    except StoreError as exc:
        return {**base, "outcome": OUTCOME_UNAVAILABLE, "message": str(exc)}
    return {**base, "outcome": OUTCOME_OK, **counters}


@app.get("/health")
def health() -> dict[str, Any]:
    """Returns service health: store outcome, network mode and CRM sink name."""
    health = store().health()
    return {
        "outcome": health.outcome,
        "store": health.store,
        "detail": health.detail,
        "offline": is_offline(),
        "crm_sink": sink().name,
    }


@app.post("/leads")
def post_lead(payload: LeadIn) -> dict[str, Any]:
    """Accepts a request and returns a card; the work is in `handle_lead`."""
    return handle_lead(payload)


@app.get("/leads")
def get_leads(limit: int = DEFAULT_LIST_LIMIT) -> dict[str, Any]:
    """Returns cards with HIGH first: the inbox is read top down."""
    try:
        cards = store().list_cards(limit)
    except StoreError as exc:
        return {"outcome": OUTCOME_UNAVAILABLE, "message": str(exc), "leads": []}
    order = {t.value: i for i, t in enumerate((Tier.HIGH, Tier.MEDIUM, Tier.LOW, Tier.INVALID))}
    items = [_card_payload(card) for card in cards]
    items.sort(key=lambda c: order.get((c.get("score") or {}).get("tier", ""), len(order)))
    return {"outcome": OUTCOME_OK, "count": len(items), "leads": items}


@app.get("/leads/{lead_id}", response_model=None)
def get_lead(lead_id: str) -> JSONResponse | dict[str, Any]:
    """Returns one card by id, or 404 when it does not exist."""
    card = store().get_card(lead_id)
    if card is None:
        return JSONResponse(
            status_code=404,
            content={"outcome": OUTCOME_REJECTED, "message": f"обращение {lead_id} не найдено"},
        )
    return {"outcome": OUTCOME_OK, **_card_payload(card)}


@app.post("/leads/{lead_id}/approve", response_model=None)
def approve(lead_id: str) -> JSONResponse | dict[str, Any]:
    """Approves a draft and sends the lead to CRM, returning the sink outcome as is."""
    card = store().get_card(lead_id)
    if card is None:
        return JSONResponse(
            status_code=404,
            content={"outcome": OUTCOME_REJECTED, "message": f"обращение {lead_id} не найдено"},
        )
    updated = store().set_reply_status(lead_id, REPLY_APPROVED, datetime.now(UTC))
    facts = card.lead.get("facts") or {}
    score_row = card.score or {}
    result = sink().send(
        CrmLead(
            lead_id=lead_id,
            company_name=facts.get("jurisdiction_hint") or f"Лид {lead_id[:8]}",
            tier=score_row.get("tier", Tier.LOW.value),
            channel=card.lead.get("channel", ""),
            language=card.lead.get("language", "ru"),
            raw_text=card.lead.get("raw_text", ""),
            reasons=tuple(score_row.get("reasons") or ()),
            facts=facts,
        )
    )
    return {
        "outcome": OUTCOME_OK,
        "lead_id": lead_id,
        "reply_status": REPLY_APPROVED if updated else "черновика нет",
        "reply_updated": updated,
        "crm": {
            "sink": result.sink,
            "outcome": result.outcome,
            "objects": result.objects,
            "detail": result.detail,
            "summary": result.summary(),
        },
    }


@app.post("/leads/{lead_id}/disagree", response_model=None)
def disagree(lead_id: str, payload: DisagreeIn) -> JSONResponse | dict[str, Any]:
    """Records a manager disagreement and rejects the draft."""
    card = store().get_card(lead_id)
    if card is None:
        return JSONResponse(
            status_code=404,
            content={"outcome": OUTCOME_REJECTED, "message": f"обращение {lead_id} не найдено"},
        )
    tier_shown = (card.score or {}).get("tier", "")
    store().save_disagreement(
        DisagreementRow(
            lead_id=lead_id,
            tier_shown=tier_shown,
            reason=payload.reason,
            author=payload.author,
            created_at=datetime.now(UTC),
        )
    )
    updated = store().set_reply_status(lead_id, REPLY_REJECTED, datetime.now(UTC))
    return {
        "outcome": OUTCOME_OK,
        "lead_id": lead_id,
        "tier_shown": tier_shown,
        "reason": payload.reason,
        "reply_status": REPLY_REJECTED if updated else "черновика нет",
        "reply_updated": updated,
    }


@app.post("/discover/run")
def discover_run(payload: DiscoverIn | None = None) -> dict[str, Any]:
    """Runs the registry sweep; the work is in `handle_discover`."""
    return handle_discover(payload or DiscoverIn())


@app.get("/companies")
def companies(limit: int = DEFAULT_LIST_LIMIT) -> dict[str, Any]:
    """Returns companies found by the registry sweep."""
    try:
        rows = store().list_companies(limit)
    except StoreError as exc:
        return {"outcome": OUTCOME_UNAVAILABLE, "message": str(exc), "companies": []}
    return {"outcome": OUTCOME_OK, "count": len(rows), "companies": rows}


@app.get("/stats")
def stats() -> dict[str, Any]:
    """Returns run counters; the work is in `handle_stats`."""
    return handle_stats()


def main() -> int:
    """Runs the API locally; the port comes from the environment."""
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
