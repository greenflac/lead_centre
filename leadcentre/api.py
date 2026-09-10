"""HTTP-слой: FastAPI поверх того же движка, что и CLI. Логика — в функциях, не в
обработчиках (Т5): `handle_lead`, `handle_discover`, `handle_stats` вызываются тестом
напрямую, без сети и без сервера.

Три исхода вместо двух (Р1) — сквозной принцип этого файла:
  * `POST /leads` возвращает `outcome`: `ok` (карточка построена) либо ошибку с кодом,
    по которому видно, что чинить: `provider_budget` (кончились деньги у провайдера
    модели — 503, а не 500 со стектрейсом), `extraction_failed` (модель ответила не тем),
    `empty_text` (извлекать нечего). Пустая карточка вместо ошибки не отдаётся никогда.
  * запись в хранилище — отдельный блок `storage` с исходом `ok`/`rejected`/`unavailable`.
    Карточка при неудачной записи всё равно возвращается: считать её заново дороже, чем
    показать со словами «не сохранено». Но выдавать несохранённое за сохранённое нельзя.
  * `GET /stats` печатает три числа рядом — проверено / нарушений / не смогли (Р2).
  * причины карточки отдаются блоком `reasons_by_language` плюс `reasons_outcome`:
    язык выбирает интерфейс, а «причины не восстановились» — отдельный исход, а не
    русский текст в поле английского (см. `_reasons_block`).

Ключи и режимы берутся из среды и нигде не дублируются (Е1): `OFFLINE=1` выключает сеть
и для модели (extract), и для источника (GLEIF), и для хранилища (LocalStore).
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
from leadcentre.engine.extract import (
    PROMPT_VERSION,
    ExtractionError,
    ProviderBudgetError,
    extract_detailed,
    is_offline,
)
from leadcentre.engine.reasons import DEFAULT_LANGUAGE
from leadcentre.engine.score import score, score_inbound
from leadcentre.models import InboundMessage, Tier
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

DEFAULT_DISCOVER_LIMIT = 30   # ВЫБРАНО: столько же, сколько у CLI по умолчанию (Е1)
MAX_DISCOVER_LIMIT = 200      # ВЫБРАНО: предел страницы GLEIF
DEFAULT_LIST_LIMIT = 50

OUTCOME_OK = "ok"
OUTCOME_REJECTED = "rejected"
OUTCOME_UNAVAILABLE = "unavailable"

app = FastAPI(
    title="SORP Lead Centre",
    version="0.1.0",
    description="Квалификация входящих обращений и компаний из внешних реестров",
)

# Хранилище и CRM создаются один раз: у LocalStore внутри файл и замок, а у Supabase —
# ключи, читать их на каждый запрос незачем.
_store = None
_sink = None


def store():
    global _store
    if _store is None:
        _store = get_store()
    return _store


def sink():
    global _sink
    if _sink is None:
        _sink = get_sink()
    return _sink


# --- модели запросов ---


class LeadIn(BaseModel):
    text: str = Field(..., description="Текст обращения как пришёл из канала")
    channel: str = Field("form", description="jivo | whatsapp | telegram | form")
    source: str | None = Field(None, description="Откуда пришло; по умолчанию = channel")
    external_id: str | None = None
    is_synthetic: bool = Field(
        False, description="Синтетика для демо и тестов; в отчёте считается отдельно"
    )


class DisagreeIn(BaseModel):
    reason: str = Field(..., min_length=1, description="Почему оценка неверна — вход для eval")
    author: str = ""


class DiscoverIn(BaseModel):
    mode: str = Field("lapsed", description="lapsed | fresh")
    limit: int = Field(DEFAULT_DISCOVER_LIMIT, ge=1, le=MAX_DISCOVER_LIMIT)


# --- ошибки движка → понятный ответ, а не 500 ---


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    body = {"outcome": OUTCOME_UNAVAILABLE, "code": code, "message": message, **extra}
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(ProviderBudgetError)
async def _budget_handler(_: Request, exc: ProviderBudgetError) -> JSONResponse:
    """Кончились деньги у провайдера модели — это «не смогли», а не сбой сервера.

    Код 402 (Payment Required), а не 500: чинится кошельком, повторять запрос
    бессмысленно, и стектрейс заставил бы клиента искать баг в коде, которого там нет.
    402 выбран ещё и потому, что ровно его ждёт дашборд (`web/lib/api.ts`: 402/429 →
    «провайдер без бюджета»); один и тот же смысл в двух местах должен читаться
    одинаково (Е1). Машиночитаемый признак — поле `code`, а не только номер.
    """
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


# --- конвейер: вся работа здесь, обработчики только разбирают запрос (Т5) ---


def _reasons_block(restored: RestoredReasons) -> dict[str, Any]:
    """Причины в ответе API: коды, тексты по языкам и исход восстановления.

    Форма выбрана так, а не «строка на выбранном сервером языке», по трём причинам.

    1. Язык выбирает интерфейс, а не сервер. Дашборд переключает RU/EN на клиенте, без
       похода на бэкенд; попросить `?lang=en` значило бы перерисовывать карточку
       запросом и держать язык ещё и в состоянии сервера (второе место знания, Е1).
    2. `reasons_by_language` содержит ровно те языки, которые ДЕЙСТВИТЕЛЬНО собраны.
       Для старой записи без кодов там только `ru`, и отсутствие ключа `en` — машинный
       признак: подставить русский текст в английскую карточку клиент уже не может
       случайно, как и не может показать пустоту молча (есть `reasons_outcome`).
    3. `reason_items` (код плюс параметры) отдаются рядом: интерфейсу они нужны для
       привязки цитат и иконок к коду, а не к подстроке русского текста.

    Поле `reasons` (готовые русские строки) осталось на месте — на него смотрят web-мок
    и CRM; ломать их ради переезда нельзя, а источником истины оно уже не является.
    """
    return {
        "reasons": list(restored.texts.get(DEFAULT_LANGUAGE.value, restored.stored_texts)),
        "reason_items": reason_items_payload(restored.items),
        "reasons_by_language": restored.texts,
        "reasons_outcome": restored.outcome,
        "reasons_detail": restored.detail,
    }


def _live_reasons(score_obj) -> RestoredReasons:
    """Причины только что посчитанной оценки — тем же кодом, что и поднятые из базы.

    Прогон через сериализацию и разбор нарочно: то, что API показывает сейчас, обязано
    совпадать с тем, что поднимется из хранилища потом. Иначе живой и демонстрационный
    режимы разъедутся ровно там, где их никто не сравнивает (Е1).
    """
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
    """Карточка из хранилища → ответ API: причины восстанавливаются в оба языка.

    Строка оценки отдаётся как лежит в базе, но обогащается блоком причин — иначе
    интерфейс на английском получил бы русские строки из колонки `reasons` и показал
    бы их как перевод.
    """
    payload = card.as_dict()
    if payload.get("score") is not None:
        payload["score"] = {**payload["score"], **_reasons_block(card.reasons())}
    return payload


def _facts_payload(facts) -> dict[str, Any]:
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


def _lint_ok(status: str) -> bool | None:
    """Три исхода линтера → колонка. UNVERIFIABLE — это `None`, а не `False` (Р1)."""
    if status == lint_module.STATUS_OK:
        return True
    if status == lint_module.STATUS_VIOLATIONS:
        return False
    return None


def handle_lead(payload: LeadIn, today: date | None = None) -> dict[str, Any]:
    """Текст обращения → карточка. Извлечение, оценка, черновик, линтер, запись.

    Исключения движка наружу не глушатся: их превращают в понятный ответ обработчики
    выше. Тихий `except` здесь вернул бы пустую карточку, неотличимую от настоящей.
    """
    received_at = today or datetime.now(UTC).date()
    message = InboundMessage(
        external_id=payload.external_id or str(uuid.uuid4()),
        channel=payload.channel,
        text=payload.text,
        received_at=received_at,
        is_synthetic=payload.is_synthetic,
    )

    extraction = extract_detailed(message)           # может бросить ProviderBudgetError (Р1)
    facts = extraction.facts
    inbound_score = score_inbound(message, facts)
    draft = reply_module.draft(message, facts, inbound_score.tier)

    try:
        lint_result = lint_module.lint(draft)
    except reply_module.PriceListError as exc:
        # Прайс не прочитан — проверка цифр не отработала. Это «не смогли», и оно
        # обязано доехать до отчёта, а не превратиться в «нарушений нет».
        lint_result = lint_module.LintResult(
            status=lint_module.STATUS_UNVERIFIABLE,
            checks_failed=(f"прайс не прочитан: {exc}",),
        )

    run_id = str(uuid.uuid4())
    storage = {"outcome": OUTCOME_OK, "store": store().name, "detail": ""}
    # `None`, а не пустая строка: пустую строку клиент подставит в URL и получит 404
    # вместо ответа «эта карточка не сохранена».
    lead_id: str | None = None
    try:
        lead_id = store().save_lead(
            LeadRow(
                source=payload.source or payload.channel,
                channel=payload.channel,
                raw_text=extraction.scrubbed.text,   # в базу — уже без ПД
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
                # Коды, а не строки: строки соберёт отрисовка (Е1), и английский
                # у карточки, поднятой из базы, останется восстановимым.
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

    return {
        "outcome": OUTCOME_OK,
        "lead_id": lead_id,
        "channel": payload.channel,
        "language": draft.language,
        "facts": _facts_payload(facts),
        "score": _score_payload(inbound_score),
        "reply": {
            "body": draft.body,
            "outcome": draft.outcome,
            "needs_human": draft.needs_human,
            "used_prices": list(draft.used_prices),
        },
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
        },
        "storage": storage,
        "run_id": run_id,
    }


def handle_discover(payload: DiscoverIn, today: date | None = None) -> dict[str, Any]:
    """GLEIF → скоринг → хранилище. При `OFFLINE=1` источник читает кэш из `data/`."""
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
                "entity_active": company.entity_active,
                "address_lines": list(company.address_lines),
                "tier": company_score.tier.value,
                "reasons": list(company_score.reasons),
                # Коды рядом со строками: карточку компании тоже показывают
                # на двух языках, а `facts` — jsonb, отдельной колонки не нужно.
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
    """Числа для отчёта. Хранилище недоступно — это «не смогли», а не нули (Р2)."""
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


# --- обработчики ---


@app.get("/health")
def health() -> dict[str, Any]:
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
    return handle_lead(payload)


@app.get("/leads")
def get_leads(limit: int = DEFAULT_LIST_LIMIT) -> dict[str, Any]:
    """Список карточек, HIGH сверху: инбокс менеджера читается сверху вниз."""
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
    card = store().get_card(lead_id)
    if card is None:
        return JSONResponse(
            status_code=404,
            content={"outcome": OUTCOME_REJECTED, "message": f"обращение {lead_id} не найдено"},
        )
    return {"outcome": OUTCOME_OK, **_card_payload(card)}


@app.post("/leads/{lead_id}/approve", response_model=None)
def approve(lead_id: str) -> JSONResponse | dict[str, Any]:
    """Одобрение: черновик получает статус, лид уходит в CRM через `CrmSink`.

    Исход CRM возвращается как есть (`sent` / `skipped` / `rejected` / `unavailable`) —
    NullSink не выдаёт «записал в лог» за «создал лид».
    """
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
    """Несогласие менеджера: черновик отклоняется, причина пишется для eval."""
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
    return handle_discover(payload or DiscoverIn())


@app.get("/companies")
def companies(limit: int = DEFAULT_LIST_LIMIT) -> dict[str, Any]:
    try:
        rows = store().list_companies(limit)
    except StoreError as exc:
        return {"outcome": OUTCOME_UNAVAILABLE, "message": str(exc), "companies": []}
    return {"outcome": OUTCOME_OK, "count": len(rows), "companies": rows}


@app.get("/stats")
def stats() -> dict[str, Any]:
    return handle_stats()


def main() -> int:
    """Локальный запуск: `python -m leadcentre.api`. Порт — из среды, как у Vercel/Render."""
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
