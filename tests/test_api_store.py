"""Тесты HTTP-слоя, хранилища и CRM-приёмника.

Три исхода здесь и проверяются: годно / отказ / не смогли. Ожидаемые значения —
литералы, а не импорт из проверяемого модуля: переименуют статус в коде — тест
покраснеет, а не поедет следом. Сеть не трогается ни одним тестом: хранилище
локальное, извлечение в OFFLINE.
"""
from __future__ import annotations

import json
import urllib.error
from datetime import UTC, date, datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from leadcentre import api
from leadcentre.crm import CrmConfigError, CrmLead, HubspotSink, NullSink, get_sink
from leadcentre.engine.extract import ProviderBudgetError
from leadcentre.store import LocalStore, StoreError, StoreRejected, StoreUnavailable, get_store
from leadcentre.store.base import DisagreementRow, LeadRow, ReplyRow, ScoreRow
from leadcentre.store.supabase import SupabaseStore

TEXT_RU = "переезжаем командой 8 человек, нужен офис в TECOM в этом месяце"


@pytest.fixture
def memory_store() -> LocalStore:
    return LocalStore(path=None)


@pytest.fixture
def client(monkeypatch, memory_store) -> TestClient:
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setattr(api, "_store", memory_store)
    monkeypatch.setattr(api, "_sink", NullSink())
    return TestClient(api.app, raise_server_exceptions=False)


def _lead_row() -> LeadRow:
    return LeadRow(
        source="whatsapp", channel="whatsapp", raw_text=TEXT_RU, facts={},
        language="ru", is_synthetic=True, received_at=date(2026, 9, 9),
    )


# --- хранилище: три исхода ---


def test_local_store_saves_and_reads_card(memory_store):
    lead_id = memory_store.save_lead(_lead_row())
    memory_store.save_score(ScoreRow(lead_id=lead_id, tier="HIGH", address_type="A0_unknown",
                                     event="B0_none"))
    memory_store.save_reply(ReplyRow(lead_id=lead_id, language="ru", body="текст",
                                     lint_ok=True, lint_status="OK"))
    card = memory_store.get_card(lead_id)
    assert card is not None
    assert card.lead["raw_text"] == TEXT_RU
    assert card.score["tier"] == "HIGH"
    assert card.reply["status"] == "draft"


def test_score_on_unknown_lead_is_rejected_not_silently_dropped(memory_store):
    """Отказ — отдельный исход: молча проглоченная оценка выглядела бы как записанная."""
    with pytest.raises(StoreRejected):
        memory_store.save_score(ScoreRow(lead_id="нет-такого", tier="LOW",
                                         address_type="A0_unknown", event="B0_none"))


def test_reply_with_unknown_status_is_rejected(memory_store):
    lead_id = memory_store.save_lead(_lead_row())
    with pytest.raises(StoreRejected):
        memory_store.save_reply(ReplyRow(lead_id=lead_id, language="ru", body="текст",
                                         lint_ok=True, lint_status="OK", status="одобрено"))


def test_broken_json_file_is_unavailable_not_empty(tmp_path):
    """«Не смогли прочитать» не сворачивается в «данных нет»: пустой словарь стёр бы лиды."""
    path = tmp_path / "store.json"
    path.write_text("{это не json", encoding="utf-8")
    with pytest.raises(StoreUnavailable):
        LocalStore(path=path)


def test_upsert_companies_returns_numbers_not_flag(memory_store):
    from leadcentre.store.base import CompanyRow
    rows = [
        CompanyRow(external_id="LEI1", source="gleif", name="A", city="Dubai", license_no=None,
                   registrar_id=None, created_on=None, registration_status="LAPSED",
                   next_renewal_on=None),
        CompanyRow(external_id="", source="gleif", name="B", city="Dubai", license_no=None,
                   registrar_id=None, created_on=None, registration_status="LAPSED",
                   next_renewal_on=None),
    ]
    result = memory_store.upsert_companies(rows)
    assert (result.requested, result.written, result.failed, result.unavailable) == (2, 1, 1, 0)
    # Повторный прогон обновляет, а не плодит дубли.
    memory_store.upsert_companies(rows[:1])
    assert len(memory_store.list_companies()) == 1


def test_get_store_rejects_unknown_name(monkeypatch):
    """Тихий откат на умолчание неотличим от «писали в базу и не писали» — поэтому ошибка."""
    monkeypatch.setenv("LEADCENTRE_STORE", "постгрес")
    with pytest.raises(StoreError):
        get_store()


def test_get_store_offline_is_local(monkeypatch):
    monkeypatch.delenv("LEADCENTRE_STORE", raising=False)
    monkeypatch.setenv("OFFLINE", "1")
    assert get_store().name == "local"


# --- Supabase: классификация кодов ошибок (с негативным контролем) ---


def _http_error(code: int, payload: dict) -> urllib.error.HTTPError:
    body = json.dumps(payload).encode("utf-8")
    return urllib.error.HTTPError("https://x/rest/v1/leads", code, "err", {}, BytesIO(body))


@pytest.mark.parametrize(
    ("code", "payload", "expected"),
    [
        (404, {"code": "PGRST205", "message": "Could not find the table"}, StoreUnavailable),
        (401, {"message": "Invalid API key"}, StoreUnavailable),
        (403, {"message": "forbidden"}, StoreUnavailable),
        (500, {"message": "boom"}, StoreUnavailable),
        (400, {"message": "column does not exist"}, StoreRejected),
        (409, {"message": "duplicate key"}, StoreRejected),
    ],
)
def test_supabase_error_classification(monkeypatch, code, payload, expected):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test")
    store = SupabaseStore()
    assert isinstance(store._http_error(_http_error(code, payload), "GET", "leads"), expected)


def test_supabase_requires_keys(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    with pytest.raises(StoreUnavailable):
        SupabaseStore()


# --- CRM ---


def test_null_sink_reports_skipped_not_sent():
    """NullSink ничего не отправлял; выдать это за успех — то же враньё, что и «отправлено»."""
    result = NullSink().send(CrmLead("id", "ACME", "HIGH", "form", "ru", "текст"))
    assert result.outcome == "skipped"
    assert result.ok is False


@pytest.mark.parametrize(
    ("code", "expected"),
    [(200, "sent"), (201, "sent"), (400, "rejected"), (409, "rejected"),
     (401, "unavailable"), (403, "unavailable"), (429, "unavailable"),
     (500, "unavailable"), (0, "unavailable")],
)
def test_hubspot_outcome_classification(code, expected):
    assert HubspotSink(token="x")._outcome_for(code) == expected


def test_hubspot_without_token_is_unavailable():
    result = HubspotSink(token="").send(CrmLead("id", "ACME", "HIGH", "form", "ru", "текст"))
    assert result.outcome == "unavailable"
    assert result.objects == {}


def test_hubspot_contact_needs_real_contact():
    """Пустой контакт не выдумывается: без email и телефона объект не создаётся."""
    sink = HubspotSink(token="x")
    lead = CrmLead("id", "ACME", "HIGH", "form", "ru", "текст")
    assert sink.contact_properties(lead) is None
    assert sink.contact_properties(
        CrmLead("id", "ACME", "HIGH", "form", "ru", "т", contact_email="a@example.com")
    )["email"] == "a@example.com"


def test_get_sink_rejects_unknown_name(monkeypatch):
    monkeypatch.setenv("CRM_SINK", "битрикс")
    with pytest.raises(CrmConfigError):
        get_sink()


# --- API ---


def test_post_lead_returns_card_and_stores_it(client, memory_store):
    response = client.post("/leads", json={"text": TEXT_RU, "channel": "whatsapp"})
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "ok"
    assert body["storage"]["outcome"] == "ok"
    assert body["language"] == "ru"
    assert body["lint"]["status"] in ("OK", "VIOLATIONS", "UNVERIFIABLE")
    assert len(memory_store.list_cards()) == 1


def test_reply_block_carries_the_language_of_the_body(client):
    """Язык черновика доезжает до ответа API внутри блока `reply`.

    Дефект, ради которого тест написан: поле лежало только в корне ответа, дашборд
    читал его из блока `reply`, не находил и подставлял английский — над русским
    письмом висел чип «DRAFT REPLY EN». Тот же дефект «ответ не на языке клиента»,
    только протёкший через границу API.

    Ожидаемое — литерал «ru»: текст обращения русский, значит и письмо русское.
    """
    body = client.post("/leads", json={"text": TEXT_RU, "channel": "whatsapp"}).json()
    assert body["reply"]["language"] == "ru"
    # Корневое поле осталось на месте и говорит то же самое: знание одно.
    assert body["language"] == body["reply"]["language"]


def test_reply_language_follows_the_text_not_the_default(client):
    """Негативный контроль: на английском обращении язык блока — «en», а не «ru».

    Без него тест выше зеленел бы и на поле, в которое зашит один язык.
    """
    text = "we are relocating 8 people, need an office in TECOM this month"
    body = client.post("/leads", json={"text": text, "channel": "whatsapp"}).json()
    assert body["reply"]["language"] == "en"


def test_provider_budget_error_is_402_not_500(client, monkeypatch):
    """Кончились деньги у провайдера — понятный ответ, а не сбой сервера."""
    def boom(_message):
        raise ProviderBudgetError("кредитный баланс слишком мал")

    monkeypatch.setattr(api, "extract_detailed", boom)
    response = client.post("/leads", json={"text": TEXT_RU, "channel": "form"})
    # 402 — тот же код, который дашборд читает как «провайдер без бюджета» (web/lib/api.ts).
    assert response.status_code == 402
    assert response.json()["code"] == "provider_budget"
    assert response.json()["outcome"] == "unavailable"


def test_store_unavailable_still_returns_card(client, monkeypatch):
    """Карточку посчитали — отдаём, но «не сохранено» говорим прямо."""
    def boom(_row):
        raise StoreUnavailable("схема не применена")

    monkeypatch.setattr(api.store(), "save_lead", boom)
    body = client.post("/leads", json={"text": TEXT_RU, "channel": "form"}).json()
    assert body["outcome"] == "ok"
    assert body["storage"]["outcome"] == "unavailable"
    assert body["score"]["tier"] in ("HIGH", "MEDIUM", "LOW", "INVALID")


def test_unknown_lead_is_404(client):
    assert client.post("/leads/нет-такого/approve").status_code == 404
    assert client.post(
        "/leads/нет-такого/disagree", json={"reason": "почему-то"}
    ).status_code == 404


def test_approve_and_disagree_change_status(client, memory_store):
    lead_id = client.post("/leads", json={"text": TEXT_RU, "channel": "form"}).json()["lead_id"]
    approve = client.post(f"/leads/{lead_id}/approve").json()
    assert approve["reply_updated"] is True
    assert approve["crm"]["outcome"] == "skipped"
    assert memory_store.get_card(lead_id).reply["status"] == "approved"

    disagree = client.post(
        f"/leads/{lead_id}/disagree", json={"reason": "должно быть HIGH"}
    ).json()
    assert disagree["reason"] == "должно быть HIGH"
    assert memory_store.get_card(lead_id).reply["status"] == "rejected"
    assert memory_store.counters()["disagreements"] == 1


def test_disagree_requires_reason(client):
    lead_id = client.post("/leads", json={"text": TEXT_RU, "channel": "form"}).json()["lead_id"]
    assert client.post(f"/leads/{lead_id}/disagree", json={"reason": ""}).status_code == 422


def test_stats_prints_three_numbers(client):
    client.post("/leads", json={"text": TEXT_RU, "channel": "form"})
    body = client.get("/stats").json()
    assert body["outcome"] == "ok"
    assert set(body["lint"]) == {"checked", "violations", "unverifiable"}
    assert body["leads"] == 1


def test_stats_says_unavailable_instead_of_zeros(client, monkeypatch):
    """Ноль лидов при недоступном хранилище — не отчёт, а обман."""
    from leadcentre.store.base import StoreHealth

    monkeypatch.setattr(
        api.store(), "health",
        lambda: StoreHealth("local", StoreHealth.UNAVAILABLE, "файл не читается"),
    )
    body = client.get("/stats").json()
    assert body["outcome"] == "unavailable"
    assert "leads" not in body


def test_health_does_not_hand_out_the_project_address():
    """`/health` открыт без аутентификации: адрес проекта наружу отдавать нечего."""
    store = SupabaseStore(url="https://abcdefghijklm.supabase.co", secret_key="k")
    store._request = lambda *a, **kw: []  # type: ignore[method-assign]
    detail = store.health().detail
    assert "abcdefghijklm" not in detail
    assert "supabase.co" not in detail
    assert detail  # и при этом исход не пустой: живость всё равно видна


def test_discover_offline_reads_cache_and_stores(client, memory_store):
    body = client.post("/discover/run", json={"mode": "lapsed", "limit": 3}).json()
    assert body["offline"] is True
    assert body["checked"] == 3
    assert body["storage"]["written"] == 3
    assert len(memory_store.list_companies()) == 3


def test_stored_company_carries_the_entity_status_not_only_the_flag(client, memory_store):
    """Третий исход обязан доезжать до хранилища: булев `entity_active` его не несёт.

    Литералы: в кэше `data/gleif_ae_lapsed_sample.json` все записи ACTIVE (ИЗМЕРЕНО
    2026-09-11, 60 из 60), поэтому флаг True, а статус — слово `active`.
    """
    client.post("/discover/run", json={"mode": "lapsed", "limit": 3})
    facts = [row["facts"] for row in memory_store.list_companies()]
    assert [f["entity_active"] for f in facts] == [True, True, True]
    assert [f["entity_status"] for f in facts] == ["active", "active", "active"]


def test_leads_are_sorted_high_first(client, memory_store):
    for tier in ("LOW", "HIGH", "MEDIUM"):
        lead_id = memory_store.save_lead(_lead_row())
        memory_store.save_score(ScoreRow(lead_id=lead_id, tier=tier,
                                         address_type="A0_unknown", event="B0_none"))
    tiers = [c["score"]["tier"] for c in client.get("/leads").json()["leads"]]
    assert tiers == ["HIGH", "MEDIUM", "LOW"]


# --- третий исход линтера не сворачивается в булев ---


@pytest.mark.parametrize(
    ("status", "expected"),
    [("OK", True), ("VIOLATIONS", False), ("UNVERIFIABLE", None)],
)
def test_lint_status_maps_to_three_values(status, expected):
    """`UNVERIFIABLE` — это None. Если станет False, «не проверяли» прочтут как «нарушение»."""
    assert api._lint_ok(status) is expected


def test_disagreement_row_carries_reason(memory_store):
    lead_id = memory_store.save_lead(_lead_row())
    memory_store.save_disagreement(
        DisagreementRow(lead_id=lead_id, tier_shown="MEDIUM", reason="должно быть HIGH",
                        created_at=datetime.now(UTC))
    )
    assert memory_store.counters()["disagreements"] == 1
