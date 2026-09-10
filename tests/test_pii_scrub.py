"""Вырезание персональных данных: функция, конвейер и тело запроса, уходящее провайдеру.

Почему файл существует. Проверка перед релизом подменила `scrub_pii` на возврат входа
как есть — сюита осталась зелёной (576 из 576). То есть маскирование работало, но его
никто не сторожил: сломайся оно молча, телефон и почта клиента уехали бы в модель,
в базу и в карточку CRM.

Что здесь проверяется и почему именно так:
  * сама `scrub_pii` — телефоны в разных форматах, почта, несколько ПД в одном тексте,
    арабский текст, текст без ПД (обязан остаться байт в байт);
  * порог `MIN_PHONE_DIGITS` — с обоих краёв (8 цифр не номер, 9 цифр номер), чтобы
    сдвиг порога в любую сторону красил тест, а не только в одну;
  * **тело запроса, которое реально уходит провайдеру** — и через подставного провайдера,
    и через настоящий `AnthropicProvider` с подменённым клиентом, и через настоящий
    `OpenAICompatibleProvider` с подменённым `urlopen`: маскирование обязано быть
    свойством конвейера, а не вежливой договорённостью с провайдером;
  * `has_contact` — ставится кодом по факту вырезанного, а не берётся из ответа модели;
  * сквозь HTTP: в хранилище и в карточку уходит уже вычищенный текст.

Ожидаемое — литералы: ни `MIN_PHONE_DIGITS`, ни `PHONE_MASK`, ни `EMAIL_MASK`
из `extract` не импортируются, иначе тест поедет вместе с константой и промолчит.
Сети нет нигде: OFFLINE, подставные провайдеры, подменённый `urlopen`.
"""
from __future__ import annotations

import json

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

# --- входные данные: литералы, ни одного импорта маркеров из проверяемого модуля ---

PHONE_INTL_SPACES = "+971 50 123 4567"
EMAIL = "ivan.petrov@example.com"

# Обращение с обоими видами ПД: на нём проверяется весь конвейер до провайдера.
TEXT_WITH_PII = (
    f"Нужен офис в TECOM, звоните {PHONE_INTL_SPACES} или пишите {EMAIL}"
)
TEXT_WITH_PII_SCRUBBED = "Нужен офис в TECOM, звоните [phone] или пишите [email]"

# Обращение без ПД: конвейер обязан оставить его нетронутым.
TEXT_CLEAN = "переезжаем командой 8 человек, нужен офис в TECOM в этом месяце"


def _pii_fragments(text: str) -> list[str]:
    """Куски, которых в теле запроса быть не должно ни в каком виде.

    Проверяем не только строку целиком, но и её части: провайдеру может уйти текст
    с переносами или другой раскладкой пробелов, а «123 4567» из номера — уже утечка.
    """
    return [frag for frag in ("+971", "123 4567", "1234567", EMAIL, "ivan.petrov") if frag]


def assert_no_pii(payload: str) -> None:
    """Негативный контроль тела запроса: ПД нет, а маски на месте.

    Обе половины обязательны: пустое тело тоже «не содержит ПД», и без второй половины
    проверка зеленела бы на любом мусоре.
    """
    for fragment in _pii_fragments(TEXT_WITH_PII):
        assert fragment not in payload, f"в тело запроса уехали ПД: {fragment!r}"
    assert "[phone]" in payload, "в теле запроса нет маски телефона — текст не тот"
    assert "[email]" in payload, "в теле запроса нет маски почты — текст не тот"


# --- сама функция: телефоны в разных форматах ---------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # международный с пробелами
        ("звоните +971 50 123 4567", "звоните [phone]"),
        # международный с дефисами
        ("тел. +971-50-123-4567 срочно", "тел. [phone] срочно"),
        # скобки вокруг кода оператора
        ("мой номер +971 (50) 123-45-67", "мой номер [phone]"),
        # местный слитно, без плюса
        ("call 0501234567 please", "call [phone] please"),
        # местный с пробелами
        ("тел 050 123 4567", "тел [phone]"),
        # без разделителей вовсе
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
    """Счётчики числами, а не флагом: «что-то вырезали» не отличает 1 от 3."""
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
        "نحتاج مكتب في دبي لفريق من 8 أشخاص",          # арабский без ПД
        "нужен офис на 8 человек к 2026-09-20",         # дата — не телефон
        "офис 12, этаж 3",                              # мелкие числа
        "бюджет до 150 000 AED в год",                  # деньги — не телефон
        "",                                             # край: пустой текст
    ],
)
def test_text_without_contacts_is_returned_byte_for_byte(text):
    """Негативный контроль: вход, на котором прибор обязан молчать."""
    result = scrub_pii(text)
    assert result.text == text
    assert result.phones == 0
    assert result.emails == 0
    assert result.has_contact is False


# --- порог длины номера: оба края ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected", "phones"),
    [
        ("сумма 1234567 дирхам", "сумма 1234567 дирхам", 0),        # 7 цифр — не номер
        ("у нас 12345678 дирхам бюджет", "у нас 12345678 дирхам бюджет", 0),  # 8 — не номер
        ("номер 123456789", "номер [phone]", 1),                    # 9 — уже номер
        ("номер 1234567890 записан", "номер [phone] записан", 1),   # 10 — номер
        ("+971 50 123 4567 это 12 цифр", "[phone] это 12 цифр", 1), # середина диапазона
    ],
)
def test_phone_threshold_is_nine_digits_from_both_sides(text, expected, phones):
    """Порог сторожится с обеих сторон: 8 цифр — не телефон, 9 — телефон.

    Сдвиг `MIN_PHONE_DIGITS` в любую сторону красит этот тест: вниз — покраснеет
    строка про 8 цифр, вверх — строка про 9.
    """
    result = scrub_pii(text)
    assert result.text == expected
    assert result.phones == phones


# --- конвейер: провайдер получает уже вычищенный текст --------------------------------


class _RecordingProvider:
    """Провайдер-заглушка, записывающий тело запроса. В сеть не ходит.

    Он намеренно «не просит» очищенный текст: маскирование обязано случиться до него.
    """

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


# Ответ модели: обязательные поля схемы на месте, has_contact намеренно врёт (см. ниже).
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
    """OFFLINE выключен, но сети всё равно нет: провайдер подставной."""
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)


def test_build_request_body_hands_the_provider_scrubbed_text():
    """Тело собрано конвейером — ПД в нём нет, и это видно в самом теле, а не в счётчике."""
    provider = _RecordingProvider()
    body, scrubbed = build_request_body(make_message(TEXT_WITH_PII), provider)

    assert scrubbed.text == TEXT_WITH_PII_SCRUBBED
    assert scrubbed.phones == 1
    assert scrubbed.emails == 1
    assert_no_pii(json.dumps(body, ensure_ascii=False))


def test_pipeline_sends_scrubbed_text_even_though_the_provider_did_not_ask(_online):
    """Сквозь `extract_detailed`: до провайдера доезжает только вычищенный текст."""
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
    """Настоящий провайдер, подменён только клиент: смотрим ровно то, что уходит в SDK."""
    bodies: list[dict] = []
    monkeypatch.setattr(AnthropicProvider, "_client", lambda self: _FakeClient(bodies))
    monkeypatch.setenv("CLAUDE_KEY", "ключ-не-нужен-клиент-подменён")

    extract_detailed(make_message(TEXT_WITH_PII))

    assert len(bodies) == 1
    assert_no_pii(json.dumps(bodies[0], ensure_ascii=False))


class _FakeHttpResponse:
    """Ответ шлюза в формате OpenAI chat completions."""

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
    """Самый строгий срез: перехвачен `urlopen`, проверено тело HTTP-запроса как есть."""
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
    """OFFLINE — тоже конвейер: карточка и хранилище получают текст без ПД."""
    monkeypatch.setenv("OFFLINE", "1")
    extraction = extract_detailed(make_message(TEXT_WITH_PII))

    assert extraction.offline is True
    assert extraction.scrubbed.text == TEXT_WITH_PII_SCRUBBED
    assert extraction.facts.has_contact is True


# --- has_contact ставит код, а не модель ---------------------------------------------


def _answer(**overrides) -> str:
    payload = json.loads(MODEL_ANSWER)
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_has_contact_is_true_although_the_model_answered_false():
    """Модель контактов не видит: она отвечает по вычищенному тексту и врёт по определению."""
    scrubbed = scrub_pii(TEXT_WITH_PII)
    facts, _dropped = parse_facts(_answer(has_contact=False), scrubbed)
    assert facts.has_contact is True


def test_has_contact_is_false_although_the_model_answered_true():
    """Обратная сторона той же мутации: слово модели не должно перевешивать факт."""
    scrubbed = scrub_pii(TEXT_CLEAN)
    facts, _dropped = parse_facts(
        _answer(has_contact=True, quotes=["нужен офис"]), scrubbed
    )
    assert facts.has_contact is False


def test_has_contact_in_the_pipeline_follows_what_was_cut_out(_online):
    """Тот же вывод, но сквозь конвейер: `extract_detailed` не спрашивает модель о контактах."""
    provider = _RecordingProvider(answer=_answer(has_contact=False))
    with_pii = extract_detailed(make_message(TEXT_WITH_PII), provider=provider)
    assert with_pii.facts.has_contact is True

    clean = extract_detailed(
        make_message(TEXT_CLEAN),
        provider=_RecordingProvider(answer=_answer(has_contact=True, quotes=["нужен офис"])),
    )
    assert clean.facts.has_contact is False


# --- сквозь HTTP: в хранилище и в CRM уезжает уже вычищенный текст ---------------------


class _RecordingSink:
    """Приёмник CRM, который только запоминает лид. Ничего никуда не отправляет."""

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
    """Дальний край конвейера: база и карточка CRM видят текст уже без телефона и почты."""
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
