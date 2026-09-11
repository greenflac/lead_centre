"""The CRM sink is a stub unless explicitly configured, so demo runs cannot fill a real CRM."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from leadcentre import api
from leadcentre.crm import CrmConfigError, CrmLead, HubspotSink, NullSink, get_sink
from leadcentre.store import LocalStore

TEXT = "переезжаем командой 8 человек, нужен офис в TECOM в этом месяце"


@pytest.fixture
def _no_crm_env(monkeypatch):
    """Clears the CRM environment for one test."""
    monkeypatch.delenv("CRM_SINK", raising=False)
    monkeypatch.delenv("HUBSPOT_PERSONAL_KEY", raising=False)


# 1. The default.


def test_default_sink_without_env_is_the_stub(_no_crm_env):
    sink = get_sink()
    assert sink.name == "null"
    assert isinstance(sink, NullSink)
    assert not isinstance(sink, HubspotSink)


def test_empty_env_value_is_still_the_stub(_no_crm_env, monkeypatch):
    monkeypatch.setenv("CRM_SINK", "")
    assert get_sink().name == "null"


def test_whitespace_env_value_is_an_error_not_a_live_sink(_no_crm_env, monkeypatch):
    monkeypatch.setenv("CRM_SINK", "   ")
    with pytest.raises(CrmConfigError):
        get_sink()


# 2. The stub does not pass itself off as a send.


def test_default_sink_reports_skipped_not_sent(_no_crm_env):
    result = get_sink().send(CrmLead("id-1", "ACME", "HIGH", "form", "ru", "текст"))
    assert result.sink == "null"
    assert result.outcome == "skipped"
    assert result.outcome != "sent"
    assert result.ok is False
    assert result.objects == {}


def test_default_pipeline_creates_nothing_in_a_foreign_crm(_no_crm_env, monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setattr(api, "_store", LocalStore(path=None))
    monkeypatch.setattr(api, "_sink", None)
    client = TestClient(api.app, raise_server_exceptions=False)

    lead_id = client.post("/leads", json={"text": TEXT, "channel": "form"}).json()["lead_id"]
    crm = client.post(f"/leads/{lead_id}/approve").json()["crm"]

    assert crm["sink"] == "null"
    assert crm["outcome"] == "skipped"
    assert crm["objects"] == {}


# 3. A live sink only on an explicit value.


def test_live_sink_requires_an_explicit_env_value(_no_crm_env, monkeypatch):
    monkeypatch.setenv("CRM_SINK", "hubspot")
    sink = get_sink()
    assert sink.name == "hubspot"
    assert isinstance(sink, HubspotSink)


@pytest.mark.parametrize("value", ["HubSpot", " hubspot "])
def test_explicit_value_is_read_case_and_space_insensitively(_no_crm_env, monkeypatch, value):
    monkeypatch.setenv("CRM_SINK", value)
    assert get_sink().name == "hubspot"


def test_name_argument_wins_over_the_env(_no_crm_env, monkeypatch):
    monkeypatch.setenv("CRM_SINK", "hubspot")
    assert get_sink("null").name == "null"


# 4. An unknown value raises rather than falling back.


@pytest.mark.parametrize("value", ["битрикс", "amocrm", "hubspot2", "nul"])
def test_unknown_value_raises_instead_of_falling_back_to_the_stub(
    _no_crm_env, monkeypatch, value
):
    monkeypatch.setenv("CRM_SINK", value)
    with pytest.raises(CrmConfigError) as exc:
        get_sink()
    assert value.lower() in str(exc.value)
    assert "null" in str(exc.value) and "hubspot" in str(exc.value)


def test_unknown_name_argument_also_raises(_no_crm_env):
    with pytest.raises(CrmConfigError):
        get_sink("почта голубем")
