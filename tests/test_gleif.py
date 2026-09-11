"""GLEIF adapter: name resolution, addresses, statuses and the offline guarantee.

Fixtures are live registry records kept under tests/data and data/, so no network runs.
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
    """Reports whether a string contains Arabic letters."""
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def test_sample_files_exist_and_hold_60_records_each():
    """The offline samples exist and hold the expected number of records."""
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
    result = GleifAdapter(mode="lapsed", offline=True).fetch(limit)
    assert result.fetched == min(limit, 60)
    assert len(result.companies) == min(limit, 60)


def test_summary_reports_numbers_not_a_flag():
    result = GleifAdapter(mode="lapsed", offline=True).fetch(2)
    assert result.summary() == "источник gleif:lapsed (кэш): получено 2, пропущено 0, пригодно 2"


def test_english_name_and_city_are_taken_from_other_fields():
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
    # address lines are gathered in every language; the classifier matches Latin
    assert "Sultan bin Khalifa bin Zayed Building" in company.address_lines
    assert any("ا" in line for line in company.address_lines), "арабские строки не потеряны"


def test_english_legal_name_is_kept_as_is():
    company = GleifAdapter(mode="fresh", offline=True).fetch(1).companies[0]
    assert company.external_id == "98450062A9RA4WDANB12"
    assert company.name == "Clean Trade FZE"
    assert company.city == "Sharjah"
    assert company.created_on == date(2026, 9, 2)
    assert company.registration_status == "ISSUED"


def test_no_card_shows_an_arabic_name():
    cards = []
    for mode in ("lapsed", "fresh"):
        cards += list(GleifAdapter(mode=mode, offline=True).fetch(60).companies)
    assert len(cards) == 120
    arabic = [(c.external_id, c.name) for c in cards if _is_arabic(c.name)]
    assert arabic == [], f"карточек с арабским названием {len(arabic)}"


def test_every_arabic_legal_name_is_replaced_in_the_lapsed_sample():
    records = json.loads((DATA_DIR / "gleif_ae_lapsed_sample.json").read_text())
    companies = GleifAdapter(mode="lapsed", offline=True).fetch(60).companies
    assert len(records) == len(companies) == 60

    legal_names = [
        (r["attributes"]["entity"].get("legalName") or {}).get("name", "") for r in records
    ]
    arabic_in_source = [n for n in legal_names if _is_arabic(n)]
    assert len(arabic_in_source) == 37

    swapped = [(c, legal) for c, legal in zip(companies, legal_names) if c.name != legal]
    assert len(swapped) == 37
    # only Arabic names are replaced; a Latin one stays as the registry wrote it
    assert [legal for _, legal in swapped if not _is_arabic(legal)] == []
    assert all(not _is_arabic(c.name) for c, _ in swapped)


def test_source_split_between_other_names_and_transliterated_names():
    """Both other-name blocks are searched, not just the first."""
    records = json.loads((DATA_DIR / "gleif_ae_lapsed_sample.json").read_text())
    records += json.loads((DATA_DIR / "gleif_ae_fresh_sample.json").read_text())
    arabic = [
        r
        for r in records
        if _is_arabic((r["attributes"]["entity"].get("legalName") or {}).get("name", ""))
    ]
    assert len(arabic) == 51

    from_other_names = [
        r
        for r in arabic
        if [o for o in r["attributes"]["entity"].get("otherNames") or []
            if o.get("language") == "en"]
    ]
    from_transliterated = [r for r in arabic if r not in from_other_names]
    assert len(from_other_names) == 43
    assert len(from_transliterated) == 8
    # all eight really do have a registry ASCII spelling, or there would be none to take
    assert all(
        r["attributes"]["entity"].get("transliteratedOtherNames") for r in from_transliterated
    )


def test_ascii_name_is_taken_from_the_registry_not_guessed():
    records = json.loads((DATA_DIR / "gleif_ae_fresh_sample.json").read_text())
    record = next(r for r in records if r["attributes"]["lei"] == "2549005ZMETNR15CNY09")
    entity = record["attributes"]["entity"]
    # preconditions: Arabic name, no English variant, a registry ASCII spelling exists
    assert _is_arabic(entity["legalName"]["name"])
    assert [o for o in entity.get("otherNames") or [] if o.get("language") == "en"] == []
    assert [o["name"] for o in entity["transliteratedOtherNames"]] == [
        "OXrage Consulting - F.Z.E"
    ]

    assert to_company(record).name == "OXrage Consulting - F.Z.E"

    companies = GleifAdapter(mode="fresh", offline=True).fetch(60).companies
    company = next(c for c in companies if c.external_id == "2549005ZMETNR15CNY09")
    assert company.name == "OXrage Consulting - F.Z.E"


def _record(legal_name: str, other_names=(), transliterated=()) -> dict:
    """Builds a minimal GLEIF record for the name-priority tests."""
    return {
        "attributes": {
            "lei": "T" * 20,
            "entity": {
                "legalName": {"name": legal_name},
                "otherNames": list(other_names),
                "transliteratedOtherNames": list(transliterated),
                "legalAddress": {"addressLines": ["линия"], "city": "Dubai", "country": "AE"},
                "status": "ACTIVE",
            },
            "registration": {"status": "ISSUED", "nextRenewalDate": "2027-01-01T00:00:00Z"},
        }
    }


ARABIC = "شركة اختبار ذ.م.م"

# One candidate per step, worded so the winning step is visible by name, not by number.
ALTERNATIVE = {
    "name": "Alternative Legal Name LLC",
    "language": "en",
    "type": "ALTERNATIVE_LANGUAGE_LEGAL_NAME",
}
PREFERRED_ASCII = {
    "name": "Preferred Ascii FZCO",
    "type": "PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME",
}
TRADING = {
    "name": "Trading Name Traders",
    "language": "en",
    "type": "TRADING_OR_OPERATING_NAME",
}
PREVIOUS = {
    "name": "Previous Legal Name DMCC",
    "language": "en",
    "type": "PREVIOUS_LEGAL_NAME",
}
AUTO_ASCII = {
    "name": "awtw askyy mashynnyy musor",
    "type": "AUTO_ASCII_TRANSLITERATED_LEGAL_NAME",
}
ALL_OTHER_NAMES = [PREVIOUS, TRADING, ALTERNATIVE]      # the order inside a record is arbitrary
ALL_TRANSLITERATED = [AUTO_ASCII, PREFERRED_ASCII]      # here too


def test_step_1_latin_legal_name_wins_over_everything():
    company = to_company(
        _record("Latin Legal Name FZE", ALL_OTHER_NAMES, ALL_TRANSLITERATED)
    )
    assert company.name == "Latin Legal Name FZE"


@pytest.mark.parametrize(
    ("other_names", "transliterated", "expected"),
    [
        # step 2: an official name in another language beats the rest
        (ALL_OTHER_NAMES, ALL_TRANSLITERATED, "Alternative Legal Name LLC"),
        # step 3: the registry ASCII spelling beats trade and former names
        ([PREVIOUS, TRADING], ALL_TRANSLITERATED, "Preferred Ascii FZCO"),
        # step 4: a trade name beats a former one
        ([PREVIOUS, TRADING], [AUTO_ASCII], "Trading Name Traders"),
        # step 5: a former name beats a machine transliteration, being readable
        ([PREVIOUS], [AUTO_ASCII], "Previous Legal Name DMCC"),
        # step 6: machine ASCII is worst of the spellings, better than Arabic
        ([], [AUTO_ASCII], "awtw askyy mashynnyy musor"),
        # step 7: nothing to take, so the Arabic name stands
        ([], [], ARABIC),
    ],
)
def test_name_priority_chain_step_by_step(other_names, transliterated, expected):
    assert to_company(_record(ARABIC, other_names, transliterated)).name == expected


def test_preferred_ascii_beats_previous_legal_name():
    company = to_company(_record(ARABIC, [PREVIOUS], [PREFERRED_ASCII]))
    assert company.name == "Preferred Ascii FZCO"


def test_previous_legal_name_beats_auto_ascii():
    """A former legal name beats a machine transliteration, which reads worse."""
    company = to_company(_record(ARABIC, [PREVIOUS], [AUTO_ASCII]))
    assert company.name == "Previous Legal Name DMCC"


def test_untyped_other_name_is_not_used():
    untyped = {"name": "Untyped Name LLC", "language": "en"}
    assert to_company(_record(ARABIC, [untyped], [])).name == ARABIC
    assert to_company(_record(ARABIC, [untyped], [AUTO_ASCII])).name == AUTO_ASCII["name"]


def test_non_english_other_name_is_not_used():
    """An other-name in another language is not used."""
    russian = dict(ALTERNATIVE, name="Альтернативное имя", language="ru")
    assert to_company(_record(ARABIC, [russian], [PREFERRED_ASCII])).name == (
        "Preferred Ascii FZCO"
    )
    assert to_company(_record(ARABIC, [russian], [])).name == ARABIC




@pytest.mark.parametrize(
    ("lei", "mode", "expected_name", "step_field", "step_type"),
    [
        (
            "9845003UC3QBD0576417",
            "lapsed",
            "CARRE DART FOR FURNITURE - L.L.C - O.P.C",
            "otherNames",
            "ALTERNATIVE_LANGUAGE_LEGAL_NAME",
        ),
        (
            "213800MZ26M5G2T1NB81",
            "lapsed",
            "NEW APOLLO FZCO",
            "transliteratedOtherNames",
            "PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME",
        ),
        (
            "549300YML8Z8EW6FKO20",
            "lapsed",
            "ASPIRE INTERNATIONAL GENERAL TRADING L.L.C",
            "otherNames",
            "TRADING_OR_OPERATING_NAME",
        ),
        (
            "2549005ZMETNR15CNY09",
            "fresh",
            "OXrage Consulting - F.Z.E",
            "transliteratedOtherNames",
            "PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME",
        ),
    ],
)
def test_live_records_land_on_the_expected_step(lei, mode, expected_name, step_field, step_type):
    records = json.loads((DATA_DIR / f"gleif_ae_{mode}_sample.json").read_text())
    record = next(r for r in records if r["attributes"]["lei"] == lei)
    entity = record["attributes"]["entity"]
    assert _is_arabic(entity["legalName"]["name"])  # otherwise step 1 would have fired
    assert expected_name in [
        o["name"] for o in entity.get(step_field) or [] if o.get("type") == step_type
    ]

    assert to_company(record).name == expected_name
    company = next(
        c
        for c in GleifAdapter(mode=mode, offline=True).fetch(60).companies
        if c.external_id == lei
    )
    assert company.name == expected_name


def test_previous_legal_name_is_never_reached_on_this_cache():
    """On this cache the former-name step is never reached, so it is exercised synthetically."""
    records = json.loads((DATA_DIR / "gleif_ae_lapsed_sample.json").read_text())
    record = next(r for r in records if r["attributes"]["lei"] == "529900MHS1KEGQZD7A14")
    types = {o["type"] for o in record["attributes"]["entity"]["otherNames"]}
    assert types == {"PREVIOUS_LEGAL_NAME", "ALTERNATIVE_LANGUAGE_LEGAL_NAME"}
    assert to_company(record).name == "BEIDECK INTERNATIONAL PETROLEUM TRADING L.L.C"
    # no machine transliteration reached the card
    assert "bydyk" not in to_company(record).name


def test_which_step_serves_each_arabic_record():
    records = json.loads((DATA_DIR / "gleif_ae_lapsed_sample.json").read_text())
    records += json.loads((DATA_DIR / "gleif_ae_fresh_sample.json").read_text())
    steps = {
        "alternative": ("otherNames", "ALTERNATIVE_LANGUAGE_LEGAL_NAME"),
        "preferred_ascii": ("transliteratedOtherNames",
                            "PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME"),
        "trading": ("otherNames", "TRADING_OR_OPERATING_NAME"),
        "previous": ("otherNames", "PREVIOUS_LEGAL_NAME"),
        "auto_ascii": ("transliteratedOtherNames", "AUTO_ASCII_TRANSLITERATED_LEGAL_NAME"),
    }
    counts = dict.fromkeys(steps, 0)
    counts["арабское"] = 0
    for record in records:
        entity = record["attributes"]["entity"]
        if not _is_arabic((entity.get("legalName") or {}).get("name", "")):
            continue
        name = to_company(record).name
        for step, (field, kind) in steps.items():
            names = [o["name"] for o in entity.get(field) or [] if o.get("type") == kind]
            if name in names:
                counts[step] += 1
                break
        else:
            counts["арабское"] += 1
    assert counts == {
        "alternative": 37,
        "preferred_ascii": 10,
        "trading": 4,
        "previous": 0,
        "auto_ascii": 0,
        "арабское": 0,
    }


def test_country_falls_back_to_legal_address():
    """The country comes from the legal address, which the alternative may lack."""
    record = {
        "attributes": {
            "lei": "X" * 20,
            "entity": {
                "legalName": {"name": "شركة اختبار", "language": "ar"},
                "otherNames": [
                    {
                        "name": "Test Co",
                        "language": "en",
                        "type": "ALTERNATIVE_LANGUAGE_LEGAL_NAME",
                    }
                ],
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
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(NetworkForbidden):
        GleifAdapter(mode="lapsed", offline=False).fetch(5)


def test_url_is_built_with_filters_and_sort():
    url = GleifAdapter(mode="lapsed", offline=True)._url(500)
    assert url.startswith("https://api.gleif.org/api/v1/lei-records?")
    assert "filter%5Bentity.legalAddress.country%5D=AE" in url
    assert "filter%5Bregistration.status%5D=LAPSED" in url
    assert "sort=-registration.nextRenewalDate" in url
    assert "page%5Bsize%5D=200" in url  # the limit is capped
    fresh_url = GleifAdapter(mode="fresh", offline=True)._url(10)
    assert "sort=-entity.creationDate" in fresh_url
    assert "registration.status" not in fresh_url


# Entity status: three outcomes, not two. The main samples hold only active records, so
# the input for this lives in its own fixture of real odd-status records.


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ACTIVE", "active"),
        ("INACTIVE", "inactive"),
        ("NULL", "unknown"),      # the registry word for "status not reported"
        ("active", "active"),     # the case of the status word changes nothing
        (" ACTIVE ", "active"),
        ("DISSOLVED", "unknown"), # a word outside the vocabulary is not a verdict on the entity
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_entity_status_words_map_to_three_outcomes(raw, expected):
    from leadcentre.sources.gleif import entity_status

    assert entity_status(raw).value == expected


def test_entity_status_unknown_is_not_entity_active():
    from leadcentre.sources.gleif import to_company

    record = {
        "attributes": {
            "lei": "TESTLEI0000000000099",
            "entity": {
                "legalName": {"name": "Unknown Status Trading LLC"},
                "status": "NULL",
                "legalAddress": {"city": "Dubai", "country": "AE", "addressLines": ["Office 1"]},
            },
            "registration": {"status": "ISSUED", "nextRenewalDate": "2027-01-01T00:00:00Z"},
        }
    }
    company = to_company(record)
    assert company.entity_status.value == "unknown"
    assert company.entity_status_raw == "NULL"
    assert company.entity_active is False


ODD_STATUS_SAMPLE = Path(__file__).resolve().parent / "data" / "gleif_ae_odd_status_sample.json"


def test_odd_status_sample_is_what_the_registry_really_returned():
    """The odd-status fixture is a real registry export, not a hand-written file."""
    records = json.loads(ODD_STATUS_SAMPLE.read_text())
    assert len(records) == 8
    seen = sorted(
        (r["attributes"]["entity"].get("status"), r["attributes"]["registration"]["status"])
        for r in records
    )
    assert seen == [
        ("ACTIVE", "PENDING_TRANSFER"),
        ("ACTIVE", "PENDING_TRANSFER"),
        ("INACTIVE", "RETIRED"),
        ("INACTIVE", "RETIRED"),
        ("NULL", "ANNULLED"),
        ("NULL", "ANNULLED"),
        ("NULL", "DUPLICATE"),
        ("NULL", "DUPLICATE"),
    ]


def test_no_card_of_the_odd_sample_claims_an_inactive_entity_the_registry_never_reported():
    from datetime import date as _date

    from leadcentre.engine.reasons import ReasonCode
    from leadcentre.engine.score import score

    records = json.loads(ODD_STATUS_SAMPLE.read_text())
    companies = [to_company(r) for r in records]
    scores = [score(c, _date(2026, 9, 11)) for c in companies]
    codes = [{i.code for i in s.reason_items} for s in scores]

    inactive = [c.external_id for c, s in zip(companies, codes, strict=True)
                if ReasonCode.ENTITY_INACTIVE in s]
    unknown = [c.external_id for c, s in zip(companies, codes, strict=True)
               if ReasonCode.ENTITY_STATUS_UNKNOWN in s]
    assert len(inactive) == 2
    assert len(unknown) == 4
    for company, its_codes in zip(companies, codes, strict=True):
        if company.entity_status_raw == "NULL":
            assert ReasonCode.ENTITY_INACTIVE not in its_codes, company.external_id




@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("en", True),
        ("EN", True),        # language tags are case-insensitive
        ("en-US", True),     # a region subtag does not change the language
        ("en_GB", True),
        ("eng", True),       # the ISO 639-2/T form of the same language
        ("ar", False),
        ("ar-AE", False),
        ("", False),
        (None, False),
        ("english", False),  # not a language code: nothing is guessed
    ],
)
def test_english_language_tag_is_matched_by_its_primary_subtag(tag, expected):
    from leadcentre.sources.gleif import is_english

    assert is_english(tag) is expected


def test_english_address_and_name_survive_an_uppercase_language_tag():
    """An upper-case or region-tagged language label still counts as English."""
    record = {
        "attributes": {
            "lei": "TESTLEI0000000000100",
            "entity": {
                "legalName": {"name": "شركة الاختبار"},
                "status": "ACTIVE",
                "legalAddress": {
                    "city": "دبي",
                    "country": "AE",
                    "addressLines": ["شارع الاختبار"],
                },
                "otherAddresses": [
                    {
                        "type": "ALTERNATIVE_LANGUAGE_LEGAL_ADDRESS",
                        "language": "EN-US",
                        "city": "Dubai",
                        "addressLines": ["Test Street"],
                    }
                ],
                "otherNames": [
                    {
                        "type": "ALTERNATIVE_LANGUAGE_LEGAL_NAME",
                        "language": "EN-US",
                        "name": "Test Trading L.L.C",
                    }
                ],
            },
            "registration": {"status": "ISSUED", "nextRenewalDate": "2027-01-01T00:00:00Z"},
        }
    }
    company = to_company(record)
    assert company.name == "Test Trading L.L.C"
    assert company.city == "Dubai"
    assert company.country == "AE"
