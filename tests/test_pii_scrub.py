"""Personal data never reaches a provider, a store or a CRM.

Inputs are literals; no marker or pattern is imported from the module under test.
Every provider path is covered, because scrubbing is a property of the pipeline.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from leadcentre import api
from leadcentre.crm.base import CrmResult
from leadcentre.engine.extract import (
    AnthropicProvider,
    Completion,
    OpenAICompatibleProvider,
    build_request_body,
    extract_detailed,
    parse_facts,
    scrub_pii,
)
from leadcentre.store import LocalStore
from tests.conftest import make_message

# Inputs as literals; no marker is imported from the module under test.

PHONE_INTL_SPACES = "+971 50 123 4567"
EMAIL = "ivan.petrov@example.com"

# A request with both kinds of personal data, used across the whole pipeline.
TEXT_WITH_PII = (
    f"Нужен офис в TECOM, звоните {PHONE_INTL_SPACES} или пишите {EMAIL}"
)
TEXT_WITH_PII_SCRUBBED = "Нужен офис в TECOM, звоните [phone] или пишите [email]"

# A request with none: the pipeline must leave it untouched.
TEXT_CLEAN = "переезжаем командой 8 человек, нужен офис в TECOM в этом месяце"


def _pii_fragments(text: str) -> list[str]:
    """Returns the personal-data fragments that must never appear downstream."""
    return [frag for frag in ("+971", "123 4567", "1234567", EMAIL, "ivan.petrov") if frag]


def assert_no_pii(payload: str) -> None:
    """Asserts that no personal-data fragment appears anywhere in the given text."""
    for fragment in _pii_fragments(TEXT_WITH_PII):
        assert fragment not in payload, f"в тело запроса уехали ПД: {fragment!r}"
    assert "[phone]" in payload, "в теле запроса нет маски телефона — текст не тот"
    assert "[email]" in payload, "в теле запроса нет маски почты — текст не тот"


# The function itself, over phone numbers in several formats.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # international, spaced
        ("звоните +971 50 123 4567", "звоните [phone]"),
        # international, hyphenated
        ("тел. +971-50-123-4567 срочно", "тел. [phone] срочно"),
        # brackets around the operator code
        ("мой номер +971 (50) 123-45-67", "мой номер [phone]"),
        # local, unspaced, no plus
        ("call 0501234567 please", "call [phone] please"),
        # local, spaced
        ("тел 050 123 4567", "тел [phone]"),
        # no separators at all
        ("+971501234567 напишите", "[phone] напишите"),
    ],
)
def test_phone_is_masked_in_every_format(text, expected):
    result = scrub_pii(text)
    assert result.text == expected
    assert result.phones == 1
    assert result.emails == 0
    assert result.has_contact is True


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("пишите на ivan@example.com", "пишите на [email]"),
        ("IVAN.Petrov+lead@sub.example.co.uk сюда", "[email] сюда"),
        ("почта: a-b_c%d@example-mail.ae, ответьте", "почта: [email], ответьте"),
    ],
)
def test_email_is_masked(text, expected):
    result = scrub_pii(text)
    assert result.text == expected
    assert result.emails == 1
    assert result.phones == 0
    assert result.has_contact is True


def test_several_contacts_in_one_text_are_all_masked_and_counted_by_numbers():
    result = scrub_pii(
        "+971501234567 и mail@example.com и +971 4 123 4567, ещё second@example.org"
    )
    assert result.text == "[phone] и [email] и [phone], ещё [email]"
    assert result.phones == 2
    assert result.emails == 2
    assert result.has_contact is True


def test_arabic_text_keeps_its_words_and_loses_the_phone():
    result = scrub_pii("اتصل بي على +971 50 123 4567 من فضلك")
    assert result.text == "اتصل بي على [phone] من فضلك"
    assert result.phones == 1
    assert result.has_contact is True


@pytest.mark.parametrize(
    "text",
    [
        TEXT_CLEAN,
        "نحتاج مكتب في دبي لفريق من 8 أشخاص",          # Arabic with no personal data
        "нужен офис на 8 человек к 2026-09-20",         # a date is not a phone number
        "офис 12, этаж 3",                              # small numbers
        "бюджет до 150 000 AED в год",                  # money is not a phone number
        "",                                             # edge: empty text
    ],
)
def test_text_without_contacts_is_returned_byte_for_byte(text):
    result = scrub_pii(text)
    assert result.text == text
    assert result.phones == 0
    assert result.emails == 0
    assert result.has_contact is False


# The number-length threshold, at both edges.


@pytest.mark.parametrize(
    ("text", "expected", "phones"),
    [
        ("сумма 1234567 дирхам", "сумма 1234567 дирхам", 0),        # 7 digits: not a number
        ("у нас 12345678 дирхам бюджет", "у нас 12345678 дирхам бюджет", 0),  # 8: not a number
        ("номер 123456789", "номер [phone]", 1),                    # 9: a number
        ("номер 1234567890 записан", "номер [phone] записан", 1),   # 10: a number
        ("+971 50 123 4567 это 12 цифр", "[phone] это 12 цифр", 1), # middle of the range
    ],
)
def test_phone_threshold_is_nine_digits_from_both_sides(text, expected, phones):
    result = scrub_pii(text)
    assert result.text == expected
    assert result.phones == phones


# The pipeline: a provider receives already scrubbed text.


class _RecordingProvider:
    """A provider that records the body it was handed instead of calling out."""

    name = "anthropic"

    def __init__(self, answer: str = "") -> None:
        self.bodies: list[dict] = []
        self.answer = answer or MODEL_ANSWER

    def model(self) -> str:
        return "claude-haiku-4-5-20251001"

    def build_body(self, system: str, content: str) -> dict:
        return {"model": self.model(), "system": system,
                "messages": [{"role": "user", "content": content}]}

    def complete(self, body: dict) -> Completion:
        self.bodies.append(body)
        return Completion(text=self.answer, model=self.model(),
                          input_tokens=100, output_tokens=10)


# A model answer whose has_contact deliberately lies, checked below.
MODEL_ANSWER = json.dumps(
    {
        "request_types": ["office"],
        "jurisdiction_hint": None,
        "headcount": None,
        "timeline_days": None,
        "budget_hint": None,
        "language": "ru",
        "is_spam": False,
        "has_contact": False,
        "confidence": 0.8,
        "quotes": ["Нужен офис в TECOM"],
    },
    ensure_ascii=False,
)


@pytest.fixture
def _online(monkeypatch):
    """Turns the offline switch off for one test."""
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)


def test_build_request_body_hands_the_provider_scrubbed_text():
    """The request body handed to a provider is already scrubbed."""
    provider = _RecordingProvider()
    body, scrubbed = build_request_body(make_message(TEXT_WITH_PII), provider)

    assert scrubbed.text == TEXT_WITH_PII_SCRUBBED
    assert scrubbed.phones == 1
    assert scrubbed.emails == 1
    assert_no_pii(json.dumps(body, ensure_ascii=False))


def test_pipeline_sends_scrubbed_text_even_though_the_provider_did_not_ask(_online):
    """The pipeline scrubs even when the provider never asked it to."""
    provider = _RecordingProvider()
    extraction = extract_detailed(make_message(TEXT_WITH_PII), provider=provider)

    assert len(provider.bodies) == 1
    sent = json.dumps(provider.bodies[0], ensure_ascii=False)
    assert_no_pii(sent)
    assert provider.bodies[0]["messages"][0]["content"].endswith(TEXT_WITH_PII_SCRUBBED)
    assert extraction.scrubbed.text == TEXT_WITH_PII_SCRUBBED
    assert extraction.scrubbed.summary() == "вырезано: телефонов 1, адресов почты 1"


class _FakeMessages:
    def __init__(self, bodies: list[dict]) -> None:
        self.bodies = bodies

    def create(self, **body):
        self.bodies.append(body)
        return _FakeResponse()


class _FakeText:
    type = "text"
    text = MODEL_ANSWER


class _FakeUsage:
    input_tokens = 100
    output_tokens = 10
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _FakeResponse:
    stop_reason = "end_turn"
    model = "claude-haiku-4-5-20251001"
    content = (_FakeText(),)
    usage = _FakeUsage()


class _FakeClient:
    def __init__(self, bodies: list[dict]) -> None:
        self.messages = _FakeMessages(bodies)


def test_real_anthropic_provider_receives_no_pii_in_the_call_body(_online, monkeypatch):
    bodies: list[dict] = []
    monkeypatch.setattr(AnthropicProvider, "_client", lambda self: _FakeClient(bodies))
    monkeypatch.setenv("CLAUDE_KEY", "ключ-не-нужен-клиент-подменён")

    extract_detailed(make_message(TEXT_WITH_PII))

    assert len(bodies) == 1
    assert_no_pii(json.dumps(bodies[0], ensure_ascii=False))


class _FakeHttpResponse:
    """A minimal stand-in for an HTTP response."""

    def __init__(self) -> None:
        self._payload = json.dumps(
            {
                "choices": [{"message": {"content": MODEL_ANSWER}, "finish_reason": "stop"}],
                "model": "openai",
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
            ensure_ascii=False,
        ).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def test_openai_compatible_provider_puts_no_pii_on_the_wire(_online, monkeypatch):
    """The gateway provider puts no personal data on the wire."""
    sent: list[bytes] = []

    def fake_urlopen(request, timeout=None):
        sent.append(request.data)
        return _FakeHttpResponse()

    monkeypatch.setenv("LLM_PROVIDER", "pollinations")
    monkeypatch.setenv("POLLINATIONS_API_KEY", "ключ-не-нужен-сеть-подменена")
    monkeypatch.setattr("leadcentre.engine.extract.urllib.request.urlopen", fake_urlopen)

    extraction = extract_detailed(make_message(TEXT_WITH_PII),
                                  provider=OpenAICompatibleProvider())

    assert len(sent) == 1
    assert_no_pii(sent[0].decode("utf-8"))
    assert extraction.scrubbed.text == TEXT_WITH_PII_SCRUBBED


def test_offline_stub_also_scrubs_the_text(monkeypatch):
    """The offline stub scrubs too, so tests cannot pass by skipping the work."""
    monkeypatch.setenv("OFFLINE", "1")
    extraction = extract_detailed(make_message(TEXT_WITH_PII))

    assert extraction.offline is True
    assert extraction.scrubbed.text == TEXT_WITH_PII_SCRUBBED
    assert extraction.facts.has_contact is True


# has_contact is set by code, not by the model.


def _answer(**overrides) -> str:
    payload = json.loads(MODEL_ANSWER)
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_has_contact_is_true_although_the_model_answered_false():
    """has_contact follows what was cut out, not what the model claimed."""
    scrubbed = scrub_pii(TEXT_WITH_PII)
    facts, _dropped, _model_timeline = parse_facts(
        _answer(has_contact=False), scrubbed, date(2026, 9, 9)
    )
    assert facts.has_contact is True


def test_has_contact_is_false_although_the_model_answered_true():
    """The same in the other direction: the model cannot invent a contact."""
    scrubbed = scrub_pii(TEXT_CLEAN)
    facts, _dropped, _model_timeline = parse_facts(
        _answer(has_contact=True, quotes=["нужен офис"]), scrubbed, date(2026, 9, 9)
    )
    assert facts.has_contact is False


def test_has_contact_in_the_pipeline_follows_what_was_cut_out(_online):
    provider = _RecordingProvider(answer=_answer(has_contact=False))
    with_pii = extract_detailed(make_message(TEXT_WITH_PII), provider=provider)
    assert with_pii.facts.has_contact is True

    clean = extract_detailed(
        make_message(TEXT_CLEAN),
        provider=_RecordingProvider(answer=_answer(has_contact=True, quotes=["нужен офис"])),
    )
    assert clean.facts.has_contact is False


# Through HTTP: store and CRM receive already scrubbed text.


class _RecordingSink:
    """A CRM sink that records the lead it was handed."""

    name = "recording"

    def __init__(self) -> None:
        self.leads: list = []

    def send(self, lead):
        self.leads.append(lead)
        return CrmResult(sink=self.name, outcome="skipped", detail="тестовый приёмник")


@pytest.fixture
def _client(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setattr(api, "_store", LocalStore(path=None))
    monkeypatch.setattr(api, "_sink", _RecordingSink())
    return TestClient(api.app, raise_server_exceptions=False)


def test_stored_lead_and_crm_lead_carry_no_pii(_client):
    """Neither the stored lead nor the CRM lead carries personal data."""
    lead_id = _client.post(
        "/leads", json={"text": TEXT_WITH_PII, "channel": "whatsapp"}
    ).json()["lead_id"]

    stored = api.store().get_card(lead_id).lead["raw_text"]
    assert stored == TEXT_WITH_PII_SCRUBBED
    for fragment in _pii_fragments(TEXT_WITH_PII):
        assert fragment not in json.dumps(api.store().get_card(lead_id).lead,
                                          ensure_ascii=False)

    _client.post(f"/leads/{lead_id}/approve")
    sent = api.sink().leads
    assert len(sent) == 1
    assert sent[0].raw_text == TEXT_WITH_PII_SCRUBBED
    assert sent[0].contact_email is None
    assert sent[0].contact_phone is None
