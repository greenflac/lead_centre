"""Тесты адаптера GLEIF: офлайн-разбор кэша, английские названия/города, отсутствие сети.

Ожидаемое — литералы из data/gleif_ae_*_sample.json, выписанные руками (Т2).
"""
from __future__ import annotations

import json
import urllib.request
from datetime import date
from pathlib import Path

import pytest

from leadcentre.sources.base import FetchResult
from leadcentre.sources.gleif import GleifAdapter, to_company

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _is_arabic(text: str) -> bool:
    """Есть ли в строке арабские буквы (диапазон U+0600..U+06FF)."""
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def test_sample_files_exist_and_hold_60_records_each():
    """Негативный контроль прибора: если кэш пуст, «0 нарушений» ничего не значит (Р2)."""
    for name in ("gleif_ae_lapsed_sample.json", "gleif_ae_fresh_sample.json"):
        records = json.loads((DATA_DIR / name).read_text())
        assert len(records) == 60, name


def test_fetch_lapsed_offline_counts():
    result = GleifAdapter(mode="lapsed", offline=True).fetch(60)
    assert isinstance(result, FetchResult)
    assert result.fetched == 60
    assert result.skipped == 0
    assert len(result.companies) == 60
    assert result.from_cache is True
    assert result.source == "gleif:lapsed"
    assert result.fetched - result.skipped == len(result.companies)


def test_fetch_fresh_offline_counts():
    result = GleifAdapter(mode="fresh", offline=True).fetch(60)
    assert result.fetched == 60
    assert result.skipped == 0
    assert len(result.companies) == 60
    assert result.source == "gleif:fresh"


@pytest.mark.parametrize("limit", [0, 1, 30, 60, 200])
def test_limit_cuts_the_cache(limit):
    """Края и середина диапазона limit (Т3): кэша 60 записей, больше не появится."""
    result = GleifAdapter(mode="lapsed", offline=True).fetch(limit)
    assert result.fetched == min(limit, 60)
    assert len(result.companies) == min(limit, 60)


def test_summary_reports_numbers_not_a_flag():
    result = GleifAdapter(mode="lapsed", offline=True).fetch(2)
    assert result.summary() == "источник gleif:lapsed (кэш): получено 2, пропущено 0, пригодно 2"


def test_english_name_and_city_are_taken_from_other_fields():
    """У записи арабский legalName и арабский legalAddress; латиница — в otherNames/otherAddresses."""
    result = GleifAdapter(mode="lapsed", offline=True).fetch(1)
    company = result.companies[0]
    assert company.external_id == "9845003UC3QBD0576417"
    assert company.name == "CARRE DART FOR FURNITURE - L.L.C - O.P.C"
    assert company.city == "Abu Dhabi"
    assert company.country == "AE"
    assert company.registrar_id == "RA000752"
    assert company.license_no == "1020000000CN1185876"
    assert company.created_on == date(2010, 6, 29)
    assert company.entity_active is True
    assert company.registration_status == "LAPSED"
    assert company.next_renewal_on == date(2026, 9, 8)
    assert company.source == "gleif"
    assert company.is_synthetic is False
    # строки адреса собраны на всех языках: классификатор ищет маркеры латиницей
    assert "Sultan bin Khalifa bin Zayed Building" in company.address_lines
    assert any("ا" in line for line in company.address_lines), "арабские строки не потеряны"


def test_english_legal_name_is_kept_as_is():
    """Негативный контроль: если legalName уже латиницей, подмены из otherNames не происходит."""
    company = GleifAdapter(mode="fresh", offline=True).fetch(1).companies[0]
    assert company.external_id == "98450062A9RA4WDANB12"
    assert company.name == "Clean Trade FZE"
    assert company.city == "Sharjah"
    assert company.created_on == date(2026, 9, 2)
    assert company.registration_status == "ISSUED"


def test_arabic_names_are_replaced_where_the_source_has_an_english_variant():
    """Правило 1: арабское legalName подменяется en-вариантом из otherNames.

    ИЗМЕРЕНО по кэшу lapsed (60 записей): 37 legalName арабские, у 30 из них есть
    en-вариант — ровно эти 30 и подменяются. Негативный контроль (И5): оставшиеся
    7 арабских имён — не дефект адаптера, а отсутствие en-варианта в источнике;
    число печатается, а не прячется (Е3).
    """
    records = json.loads((DATA_DIR / "gleif_ae_lapsed_sample.json").read_text())
    companies = GleifAdapter(mode="lapsed", offline=True).fetch(60).companies
    assert len(records) == len(companies) == 60

    legal_names = [
        (r["attributes"]["entity"].get("legalName") or {}).get("name", "") for r in records
    ]
    assert len([n for n in legal_names if _is_arabic(n)]) == 37

    swapped = [(c, legal) for c, legal in zip(companies, legal_names) if c.name != legal]
    assert len(swapped) == 30
    # подменяются ТОЛЬКО арабские: латинское название остаётся как в реестре
    assert [legal for _, legal in swapped if not _is_arabic(legal)] == []
    assert all(not _is_arabic(c.name) for c, _ in swapped)

    left_arabic = [c for c in companies if _is_arabic(c.name)]
    assert len(left_arabic) == 7
    for company in left_arabic:
        record = next(r for r in records if r["attributes"]["lei"] == company.external_id)
        other = record["attributes"]["entity"].get("otherNames") or []
        assert not [o for o in other if o.get("language") == "en"], company.external_id


def test_latin_legal_name_is_not_replaced_by_another_form_from_other_names():
    """Правило 2 (негативный контроль подмены): у этой записи legalName латиницей,

    а в otherNames лежит ДРУГАЯ форма того же лица. Менеджер должен видеть название
    из реестра, иначе он не найдёт компанию. ИЗМЕРЕНО: LEI 9845005RS45YEC9BEB28.
    """
    records = json.loads((DATA_DIR / "gleif_ae_lapsed_sample.json").read_text())
    record = next(r for r in records if r["attributes"]["lei"] == "9845005RS45YEC9BEB28")
    entity = record["attributes"]["entity"]
    # предпосылка теста, а не его вывод: в источнике действительно есть en-вариант, и он другой
    assert entity["legalName"]["name"] == "AL DAHRA HOLDING LIMITED"
    english_variants = [
        o["name"] for o in entity.get("otherNames") or [] if o.get("language") == "en"
    ]
    assert english_variants == ["AL DAHRA HOLDING SOLE PROPRIETORSHIP LLC"]

    assert to_company(record).name == "AL DAHRA HOLDING LIMITED"

    companies = GleifAdapter(mode="lapsed", offline=True).fetch(60).companies
    company = next(c for c in companies if c.external_id == "9845005RS45YEC9BEB28")
    assert company.name == "AL DAHRA HOLDING LIMITED"


def test_country_falls_back_to_legal_address():
    """В альтернативном (английском) адресе country может отсутствовать — берём из юридического."""
    record = {
        "attributes": {
            "lei": "X" * 20,
            "entity": {
                "legalName": {"name": "شركة اختبار", "language": "ar"},
                "otherNames": [{"name": "Test Co", "language": "en"}],
                "legalAddress": {"addressLines": ["سطر"], "city": "دبي", "country": "AE"},
                "otherAddresses": [
                    {
                        "type": "ALTERNATIVE_LANGUAGE_LEGAL_ADDRESS",
                        "language": "en",
                        "addressLines": ["Meydan Grandstand, 6th floor"],
                        "city": "Dubai",
                    }
                ],
                "status": "ACTIVE",
            },
            "registration": {"status": "LAPSED", "nextRenewalDate": "2026-08-01T00:00:00Z"},
        }
    }
    company = to_company(record)
    assert company.country == "AE"
    assert company.city == "Dubai"
    assert company.name == "Test Co"
    assert company.next_renewal_on == date(2026, 8, 1)


@pytest.mark.parametrize(
    "record",
    [
        {"attributes": {}},
        {"attributes": {"entity": {"legalName": {"name": "No LEI Co"}}}},
        {"attributes": {"lei": "Y" * 20, "entity": {}}},
        {},
    ],
)
def test_records_without_minimum_fields_are_skipped(record):
    assert to_company(record) is None


def test_skipped_is_counted_not_hidden(monkeypatch):
    """Пропуск печатается числом, а не проглатывается (Е3)."""
    good = json.loads((DATA_DIR / "gleif_ae_lapsed_sample.json").read_text())[:2]
    broken = [{"attributes": {"lei": "Z" * 20, "entity": {}}}]
    adapter = GleifAdapter(mode="lapsed", offline=True)
    monkeypatch.setattr(adapter, "_raw", lambda limit: (good + broken, True))
    result = adapter.fetch(10)
    assert (result.fetched, result.skipped, len(result.companies)) == (3, 1, 2)


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        GleifAdapter(mode="whatever")


def test_offline_flag_comes_from_env(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    assert GleifAdapter(mode="lapsed").offline is True
    monkeypatch.setenv("OFFLINE", "0")
    assert GleifAdapter(mode="lapsed").offline is False
    monkeypatch.delenv("OFFLINE", raising=False)
    assert GleifAdapter(mode="lapsed").offline is False
    assert GleifAdapter(mode="lapsed", offline=True).offline is True


# --- сеть: офлайн обязан быть офлайном -------------------------------------------


class NetworkForbidden(RuntimeError):
    pass


def _boom(*args, **kwargs):
    raise NetworkForbidden("тест ходил в сеть")


def test_offline_fetch_does_not_touch_the_network(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    result = GleifAdapter(mode="lapsed", offline=True).fetch(5)
    assert len(result.companies) == 5
    assert result.from_cache is True


def test_negative_control_online_fetch_would_use_urlopen(monkeypatch):
    """Контроль прибора (И5): подмена действительно перехватывает сетевой путь."""
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(NetworkForbidden):
        GleifAdapter(mode="lapsed", offline=False).fetch(5)


def test_url_is_built_with_filters_and_sort():
    """URL не проверяется сетью, поэтому проверяется строкой (Ц10: имя эндпоинта в коде одно)."""
    url = GleifAdapter(mode="lapsed", offline=True)._url(500)
    assert url.startswith("https://api.gleif.org/api/v1/lei-records?")
    assert "filter%5Bentity.legalAddress.country%5D=AE" in url
    assert "filter%5Bregistration.status%5D=LAPSED" in url
    assert "sort=-registration.nextRenewalDate" in url
    assert "page%5Bsize%5D=200" in url  # limit срезается до 200
    fresh_url = GleifAdapter(mode="fresh", offline=True)._url(10)
    assert "sort=-entity.creationDate" in fresh_url
    assert "registration.status" not in fresh_url
