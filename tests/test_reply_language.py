"""Тесты выбора языка ответа (`leadcentre/engine/reply.py`).

Правило, ради которого файл заведён: язык ответа определялся наличием хотя бы одного
символа письменности. В обращении edge-03 из репозитория 893 кириллических буквы и 23
арабских — одна прощальная фраза, — и весь черновик уходил на арабском. Клиент, который
написал по-русски, получал письмо, которого не просил.

Ожидаемое — литералы: коды языков выписаны строками, порог доли — числом. Из модуля
не импортируются ни `DOMINANT_SCRIPT_SHARE`, ни `SCRIPT_RANGES`, ни `SUPPORTED_LANGUAGES`:
сдвинут порог или сузят диапазоны — тест обязан покраснеть, а не поехать следом.
Сети и модели не требуется: правило детерминированное.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from leadcentre.engine.reply import detect_script_language, resolve_language
from leadcentre.models import InboundMessage, LeadFacts

SEED_CSV = Path(__file__).resolve().parents[1] / "data" / "inbound_seed.csv"

RU = "нам нужен офис в Дубае на восемь человек и визы на всех сотрудников"
AR = "نحتاج مكتبًا في دبي لفريق من ستة أشخاص مع تأشيرات عمل ونود معرفة التكلفة"
EN = "we need an office in Dubai for eight people and employment visas for the team"


def _facts(language: str | None) -> LeadFacts:
    return LeadFacts(request_types=(), language=language)


def _message(text: str) -> InboundMessage:
    return InboundMessage(
        external_id="probe", channel="form", text=text, received_at=date(2026, 9, 9)
    )


def _seed_text(external_id: str) -> str:
    with SEED_CSV.open(encoding="utf-8", newline="") as fh:
        return next(r["text"] for r in csv.DictReader(fh) if r["external_id"] == external_id)


# --- письменность называет язык, когда она преобладает --------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (RU, "ru"),
        (AR, "ar"),
        (EN, None),          # латиница языка не доказывает — третий исход
        ("", None),          # мерить нечего
        ("+971 55 000 0000", None),
    ],
)
def test_script_names_the_language_only_when_it_dominates(text, expected):
    assert detect_script_language(text) == expected


def test_one_foreign_phrase_does_not_change_the_language():
    """Живой дефект: вежливая фраза в конце длинного письма — не смена языка разговора.

    Длина письма здесь существенна и потому задана явно: в коротком сообщении та же фраза
    занимает треть текста, и тогда язык действительно неясен — этот край проверяет
    `test_half_and_half_is_the_third_outcome`.
    """
    text = (RU + ". ") * 8 + "سنكون في دبي في نهاية أكتوبر"
    assert detect_script_language(text) == "ru"


def test_seed_edge_03_is_russian_not_arabic():
    """Тот самый вход из репозитория, а не только выдуманная строка."""
    assert detect_script_language(_seed_text("edge-03")) == "ru"


def test_seed_arabic_request_stays_arabic():
    """Негативный контроль правила: на настоящем арабском обращении оно обязано сказать ar."""
    assert detect_script_language(_seed_text("gen-19")) == "ar"


# --- край: письменности поровну, решать по ней нельзя ---------------------------------


def test_half_and_half_is_the_third_outcome():
    """Ни одна письменность не преобладает — правило молчит, решает флаг языка."""
    text = "офис в Дубае " + "مكتب في دبي"
    assert detect_script_language(text) is None


@pytest.mark.parametrize(
    ("arabic_letters", "russian_letters", "expected"),
    [
        # Оба края и середина по доле арабского среди опознанных букв.
        (100, 0, "ar"),      # 1.00
        (85, 15, "ar"),      # 0.85 — выше порога
        (80, 20, "ar"),      # 0.80 — ровно порог, ещё называем
        (79, 21, None),      # 0.79 — уже нет
        (50, 50, None),      # поровну
        (20, 80, "ru"),      # зеркально
    ],
)
def test_the_share_threshold_is_0_8(arabic_letters, russian_letters, expected):
    """Порог доли — 0.8, и он проверяется с обеих сторон границы."""
    text = "ا" * arabic_letters + "я" * russian_letters
    assert detect_script_language(text) == expected


# --- решение целиком: письменность важнее флага, неизвестный флаг уходит в английский ---


def test_script_beats_the_flag_when_they_disagree():
    """Флаг заполняет модель и ошибается; письменность — свидетельство."""
    assert resolve_language(_message(RU), _facts("ar")) == "ru"


def test_flag_decides_when_the_script_is_silent():
    assert resolve_language(_message(EN), _facts("en")) == "en"


@pytest.mark.parametrize("flag", ["mixed", "", None, "de"])
def test_unknown_flag_falls_back_to_english(flag):
    """«mixed», пустое и язык без шаблонов — общий язык, а не молчание и не угадывание."""
    assert resolve_language(_message(EN), _facts(flag)) == "en"
