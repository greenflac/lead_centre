"""Умолчание CRM: без явной переменной приёмник — заглушка, и она честно говорит «пропущено».

Почему файл существует. Проверка перед релизом подменила умолчание с `NullSink` на живой
`HubspotSink` — сюита осталась зелёной (576 из 576). То есть «по умолчанию ничего никуда
не отправляется» было обещанием, которое ничем не держалось: чужая CRM наполнилась бы
демо-прогонами, а узнали бы мы об этом от владельца портала.

Проверяется четыре утверждения, и каждое — отдельная мутация:
  1. переменной нет — приёмник заглушка (`null`), а не HubSpot;
  2. заглушка возвращает исход «пропущено», а не «отправлено» (вердикт — из того,
     что исполнилось, а `SKIPPED` не сворачивается в успех);
  3. живой приёмник включается только явным значением `CRM_SINK=hubspot`;
  4. неизвестное значение — ошибка, а не тихий откат на заглушку (тихий откат
     неотличим от «отправили, но не дошло»).

Ожидаемое — литералы: имена приёмников и исходы записаны строками, из
`leadcentre.crm` не импортируются ни `SINKS`, ни `SKIPPED`, ни `SENT`.
Сети нет: ни один тест не вызывает `send` у HubSpot с настоящим токеном.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from leadcentre import api
from leadcentre.crm import CrmConfigError, CrmLead, HubspotSink, NullSink, get_sink
from leadcentre.store import LocalStore

TEXT = "переезжаем командой 8 человек, нужен офис в TECOM в этом месяце"


@pytest.fixture
def _no_crm_env(monkeypatch):
    """Среда без единого слова о CRM: ровно то состояние, в котором приезжает релиз."""
    monkeypatch.delenv("CRM_SINK", raising=False)
    monkeypatch.delenv("HUBSPOT_PERSONAL_KEY", raising=False)


# --- 1. умолчание ---------------------------------------------------------------------


def test_default_sink_without_env_is_the_stub(_no_crm_env):
    sink = get_sink()
    assert sink.name == "null"
    assert isinstance(sink, NullSink)
    assert not isinstance(sink, HubspotSink)


def test_empty_env_value_is_still_the_stub(_no_crm_env, monkeypatch):
    """Пустая переменная — это «не задано», а не «задано что-то живое»."""
    monkeypatch.setenv("CRM_SINK", "")
    assert get_sink().name == "null"


def test_whitespace_env_value_is_an_error_not_a_live_sink(_no_crm_env, monkeypatch):
    """ИЗМЕРЕНО 2026-09-10: `CRM_SINK="   "` — не «не задано», а неизвестное имя.

    Пустая строка отсекается `or` до `strip()`, пробельная — уже нет, и падает в ветку
    неизвестного имени. Асимметрия зафиксирована как есть: обе стороны безопасны (никуда
    ничего не уходит), и ни одна из них не даёт живого приёмника. Меняется поведение —
    красит этот тест, а не проезжает молча.
    """
    monkeypatch.setenv("CRM_SINK", "   ")
    with pytest.raises(CrmConfigError):
        get_sink()


# --- 2. заглушка не выдаёт себя за отправку -------------------------------------------


def test_default_sink_reports_skipped_not_sent(_no_crm_env):
    result = get_sink().send(CrmLead("id-1", "ACME", "HIGH", "form", "ru", "текст"))
    assert result.sink == "null"
    assert result.outcome == "skipped"
    assert result.outcome != "sent"
    assert result.ok is False
    assert result.objects == {}


def test_default_pipeline_creates_nothing_in_a_foreign_crm(_no_crm_env, monkeypatch):
    """Сквозь HTTP, с настоящим `get_sink()`: одобрение лида не создаёт объектов в CRM.

    `api._sink` сброшен в None намеренно — приёмник выбирается тем же кодом, что и в проде,
    а не подставляется тестом.
    """
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setattr(api, "_store", LocalStore(path=None))
    monkeypatch.setattr(api, "_sink", None)
    client = TestClient(api.app, raise_server_exceptions=False)

    lead_id = client.post("/leads", json={"text": TEXT, "channel": "form"}).json()["lead_id"]
    crm = client.post(f"/leads/{lead_id}/approve").json()["crm"]

    assert crm["sink"] == "null"
    assert crm["outcome"] == "skipped"
    assert crm["objects"] == {}


# --- 3. живой приёмник — только по явному значению -------------------------------------


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
    """Явный аргумент сильнее переменной: иначе прогон под чужой средой уехал бы в HubSpot."""
    monkeypatch.setenv("CRM_SINK", "hubspot")
    assert get_sink("null").name == "null"


# --- 4. неизвестное значение — ошибка, а не тихий откат --------------------------------


@pytest.mark.parametrize("value", ["битрикс", "amocrm", "hubspot2", "nul"])
def test_unknown_value_raises_instead_of_falling_back_to_the_stub(
    _no_crm_env, monkeypatch, value
):
    """Тихий откат неотличим от «отправили, но не дошло», поэтому исход тут — исключение."""
    monkeypatch.setenv("CRM_SINK", value)
    with pytest.raises(CrmConfigError) as exc:
        get_sink()
    assert value.lower() in str(exc.value)
    assert "null" in str(exc.value) and "hubspot" in str(exc.value)


def test_unknown_name_argument_also_raises(_no_crm_env):
    with pytest.raises(CrmConfigError):
        get_sink("почта голубем")
