"""Model routing by message length, and the instrument fields that record what served a lead."""
from __future__ import annotations

import json

import pytest

from leadcentre.engine.extract import (
    AnthropicProvider,
    Completion,
    Extraction,
    Route,
    Scrubbed,
    extract_detailed,
    route,
)
from leadcentre.models import LeadFacts
from tests.conftest import make_message

HAIKU = "claude-haiku-4-5-20251001"
OPUS = "claude-opus-5"


def _message_of_length(length: int):
    """Builds a message of exactly the given length."""
    text = "а" * length
    message = make_message(text=text)
    assert len(message.text) == length  # a precondition of the test, not its conclusion
    return message


# The length threshold.


@pytest.mark.parametrize(
    ("length", "expected_model"),
    [
        (0, HAIKU),       # lower edge: empty text is still a short request
        (1, HAIKU),
        (98, HAIKU),      # the median request length in the seed
        (300, HAIKU),     # middle of the range
        (599, HAIKU),
        (600, HAIKU),     # exactly at the threshold: the comparison is strict, so still short
        (601, OPUS),      # one character more: long
        (1332, OPUS),     # the length at which the cheap model started making mistakes
        (5000, OPUS),
    ],
)
def test_long_message_threshold_is_600_chars(length, expected_model):
    result = route(_message_of_length(length))
    assert result.model == expected_model


@pytest.mark.parametrize(("length", "expected_unit"), [(599, "символов"), (600, "символов")])
def test_short_route_carries_cheap_settings_and_a_reason_with_the_number(length, expected_unit):
    result = route(_message_of_length(length))
    assert result.model == "claude-haiku-4-5-20251001"
    assert result.effort == "low"
    assert result.thinking == "off"
    # the word form as a literal: a change to plural rules must redden this
    assert f"{length} {expected_unit}" in result.reason
    assert "короткое обращение" in result.reason


@pytest.mark.parametrize(
    ("length", "expected_unit"), [(601, "символ"), (700, "символов"), (1332, "символа")]
)
def test_long_route_reason_names_the_length_and_the_threshold(length, expected_unit):
    result = route(_message_of_length(length))
    assert result.model == "claude-opus-5"
    assert result.reason == f"длинное обращение: {length} {expected_unit} > 600"


def test_route_returns_a_route_object_with_all_fields_filled():
    result = route(_message_of_length(10))
    assert isinstance(result, Route)
    assert result.reason  # the route reason reaches the lead card


def test_forced_model_env_disables_routing(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("LLM_EFFORT", "high")
    monkeypatch.setenv("LLM_THINKING", "adaptive")
    for length in (1, 600, 601, 1332):
        result = route(_message_of_length(length))
        assert result.model == "claude-sonnet-5"
        assert (result.effort, result.thinking) == ("high", "adaptive")
        assert result.reason == "LLM_MODEL задан вручную"


def test_routing_is_active_when_forced_model_is_absent(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert route(_message_of_length(601)).model == "claude-opus-5"


# The route is decided before any request.


def test_route_does_not_touch_the_network(monkeypatch):
    """Routing is decided before any network call."""
    import socket
    import urllib.request

    def boom(*args, **kwargs):
        raise AssertionError("route() пошёл в сеть")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert route(_message_of_length(601)).model == "claude-opus-5"
    assert route(_message_of_length(600)).model == "claude-haiku-4-5-20251001"


def test_route_needs_no_api_key(monkeypatch):
    monkeypatch.delenv("CLAUDE_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert route(_message_of_length(1332)).model == "claude-opus-5"


def test_provider_sends_the_routed_model_in_the_request_body():
    provider = AnthropicProvider(Route("claude-opus-5", "low", "off", "тест"))
    body = provider.build_body("system", "content")
    assert body["model"] == "claude-opus-5"
    assert body["output_config"]["effort"] == "low"
    assert body["thinking"] == {"type": "disabled"}


def test_haiku_route_does_not_get_effort_or_thinking_parameters():
    """A model that rejects the effort parameters never receives them."""
    provider = AnthropicProvider(Route("claude-haiku-4-5-20251001", "low", "off", "тест"))
    body = provider.build_body("system", "content")
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert "effort" not in body["output_config"]
    assert "thinking" not in body


# The serving model and cache counters come from the response, not the intent.


# A model answer with every required schema field, or parsing would fail for its own
# reason instead of the one under test.
MODEL_JSON = json.dumps(
    {
        "request_types": ["office"],
        "jurisdiction_hint": None,
        "headcount": 5,
        "timeline_days": 20,
        "budget_hint": None,
        "language": "ru",
        "is_spam": False,
        "has_contact": False,
        "confidence": 0.8,
        "quotes": ["нужен офис"],
    },
    ensure_ascii=False,
)


class _FakeText:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeUsage:
    def __init__(self, cache_read: int, cache_write: int) -> None:
        self.input_tokens = 3162
        self.output_tokens = 120
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_write


class _FakeResponse:
    stop_reason = "end_turn"

    def __init__(self, model: str, cache_read: int = 0, cache_write: int = 0) -> None:
        self.model = model
        self.content = [_FakeText(MODEL_JSON)]
        self.usage = _FakeUsage(cache_read, cache_write)


class _FakeProvider:
    """A provider that returns a canned completion."""

    name = "anthropic"

    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.bodies: list[dict] = []

    def model(self) -> str:
        return "claude-haiku-4-5-20251001"

    def build_body(self, system: str, content: str) -> dict:
        return {"model": self.model(), "system": system, "content": content}

    def complete(self, body: dict) -> Completion:
        self.bodies.append(body)
        usage = self.response.usage
        return Completion(
            text=self.response.content[0].text,
            model=self.response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens,
            cache_write_tokens=usage.cache_creation_input_tokens,
        )


@pytest.fixture
def _online(monkeypatch):
    """Turns the offline switch off for one test."""
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)


def test_served_model_comes_from_the_response_not_from_the_intent(_online):
    provider = _FakeProvider(_FakeResponse("claude-opus-4-8"))
    extraction = extract_detailed(make_message("нужен офис"), provider=provider)
    assert provider.model() == "claude-haiku-4-5-20251001"  # intent
    assert extraction.model == "claude-opus-4-8"            # evidence
    assert extraction.provider == "anthropic"
    assert extraction.offline is False


def test_alias_answers_with_the_dated_snapshot_id(_online):
    provider = _FakeProvider(_FakeResponse("claude-haiku-4-5-20251001"))
    extraction = extract_detailed(make_message("нужен офис"), provider=provider)
    assert extraction.model == "claude-haiku-4-5-20251001"


def test_cache_counters_come_from_usage(_online):
    provider = _FakeProvider(_FakeResponse("claude-opus-5", cache_read=3162, cache_write=0))
    extraction = extract_detailed(make_message("нужен офис"), provider=provider)
    assert extraction.cache_read_tokens == 3162
    assert extraction.cache_write_tokens == 0
    assert extraction.input_tokens == 3162
    assert extraction.output_tokens == 120


def test_cache_counters_are_zero_when_the_cache_did_not_fire(_online):
    provider = _FakeProvider(_FakeResponse("claude-haiku-4-5-20251001"))
    extraction = extract_detailed(make_message("нужен офис"), provider=provider)
    assert (extraction.cache_read_tokens, extraction.cache_write_tokens) == (0, 0)
    assert "кэш" not in extraction.served_by()


def test_served_by_prints_model_and_cache_numbers():
    extraction = Extraction(
        facts=LeadFacts(),
        provider="anthropic",
        model="claude-opus-5",
        elapsed_s=1.5,
        input_tokens=3162,
        output_tokens=120,
        cache_read_tokens=3000,
        cache_write_tokens=162,
        offline=False,
        scrubbed=Scrubbed(text="", phones=0, emails=0),
        dropped_quotes=0,
        route_reason="длинное обращение: 1332 символа > 600",
    )
    line = extraction.served_by()
    assert "обслужено: anthropic/claude-opus-5 за 1.50 с" in line
    assert "длинное обращение: 1332 символа > 600" in line
    assert "токены 3162/120" in line
    assert "кэш: прочитано 3000, записано 162" in line


def test_offline_extraction_names_the_stub_not_a_real_model(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    extraction = extract_detailed(make_message("нужен офис"))
    assert extraction.model == "offline-stub"
    assert extraction.provider == "offline"
    assert extraction.offline is True
    assert extraction.facts.confidence == 0.0
    assert extraction.route_reason == "OFFLINE: модель не вызывалась"


def test_offline_stub_says_confidence_was_not_measured(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    facts = extract_detailed(make_message("нужен офис")).facts
    assert facts.confidence_measured is False
    assert facts.confidence == 0.0


def test_a_real_extraction_reports_a_measured_confidence(_online):
    provider = _FakeProvider(_FakeResponse(HAIKU))
    facts = extract_detailed(make_message("нужен офис"), provider=provider).facts
    assert facts.confidence_measured is True


def test_cache_write_counter_comes_from_usage(_online):
    provider = _FakeProvider(_FakeResponse("claude-opus-5", cache_read=0, cache_write=3162))
    extraction = extract_detailed(make_message("нужен офис"), provider=provider)
    assert extraction.cache_write_tokens == 3162
    assert extraction.cache_read_tokens == 0
    assert "кэш: прочитано 0, записано 3162" in extraction.served_by()


class _FakeMessages:
    def __init__(self, response: _FakeResponse, bodies: list[dict]) -> None:
        self.response = response
        self.bodies = bodies

    def create(self, **body):
        self.bodies.append(body)
        return self.response


class _FakeClient:
    def __init__(self, response: _FakeResponse, bodies: list[dict]) -> None:
        self.messages = _FakeMessages(response, bodies)


def test_real_pipeline_routes_long_message_to_opus_and_records_the_reason(_online, monkeypatch):
    bodies: list[dict] = []
    response = _FakeResponse("claude-opus-5", cache_read=3000, cache_write=0)
    monkeypatch.setattr(
        AnthropicProvider, "_client", lambda self: _FakeClient(response, bodies)
    )
    monkeypatch.setenv("CLAUDE_KEY", "тест-ключа-не-требуется")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    extraction = extract_detailed(make_message("а" * 700))

    assert bodies[0]["model"] == "claude-opus-5"
    assert extraction.route_reason == "длинное обращение: 700 символов > 600"
    assert extraction.model == "claude-opus-5"
    assert extraction.cache_read_tokens == 3000


def test_real_pipeline_routes_short_message_to_haiku(_online, monkeypatch):
    bodies: list[dict] = []
    response = _FakeResponse("claude-haiku-4-5-20251001")
    monkeypatch.setattr(
        AnthropicProvider, "_client", lambda self: _FakeClient(response, bodies)
    )
    monkeypatch.setenv("CLAUDE_KEY", "тест-ключа-не-требуется")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    extraction = extract_detailed(make_message("а" * 600))

    assert bodies[0]["model"] == "claude-haiku-4-5-20251001"
    assert "effort" not in bodies[0]["output_config"]  # this model answers 400 to the effort parameter
    assert extraction.route_reason == "короткое обращение: 600 символов <= 600"
    assert extraction.model == "claude-haiku-4-5-20251001"
