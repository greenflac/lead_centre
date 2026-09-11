"""Тесты адаптера GLEIF: офлайн-разбор кэша, английские названия/города, отсутствие сети.

Ожидаемое — литералы из data/gleif_ae_*_sample.json, выписанные руками.
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
    """Негативный контроль прибора: если кэш пуст, «0 нарушений» ничего не значит."""
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
    """Края и середина диапазона limit: кэша 60 записей, больше не появится."""
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


def test_no_card_shows_an_arabic_name():
    """Главный инвариант: НИ ОДНА карточка обеих выборок не показывает название арабицей.

    Написан про саму беду, а не про её случай: счёт «подменено N» переживает ровно одно
    изменение источника, а это требование переживёт любое (беда наблюдаема).
    ИЗМЕРЕНО 2026-09-09: 120 записей, арабских названий 0 (было 8).
    """
    cards = []
    for mode in ("lapsed", "fresh"):
        cards += list(GleifAdapter(mode=mode, offline=True).fetch(60).companies)
    assert len(cards) == 120
    arabic = [(c.external_id, c.name) for c in cards if _is_arabic(c.name)]
    assert arabic == [], f"карточек с арабским названием {len(arabic)}"


def test_every_arabic_legal_name_is_replaced_in_the_lapsed_sample():
    """ИЗМЕРЕНО по кэшу lapsed (60 записей): 37 legalName арабские, подменяются все 37.

    Раньше подменялось 30: у семи латиница нашлась в transliteratedOtherNames, куда
    адаптер не смотрел.
    """
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
    # подменяются ТОЛЬКО арабские: латинское название остаётся как в реестре
    assert [legal for _, legal in swapped if not _is_arabic(legal)] == []
    assert all(not _is_arabic(c.name) for c, _ in swapped)


def test_source_split_between_other_names_and_transliterated_names():
    """Откуда взялась латиница — числами, по обеим выборкам: 43 + 8 из 51."""
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
    # у всех восьми ASCII-написание в реестре действительно есть — иначе брать было бы неоткуда
    assert all(
        r["attributes"]["entity"].get("transliteratedOtherNames") for r in from_transliterated
    )


def test_ascii_name_is_taken_from_the_registry_not_guessed():
    """Третья ступень цепочки: ASCII-написание реестра, а не машинная транслитерация.

    Сверка машинной транслитерации с реестром по этим восьми дала 3 из 8: «OXrage»
    по звучанию не восстанавливается (машина слышит «OAUX REG»). Реестр имеет приоритет.
    """
    records = json.loads((DATA_DIR / "gleif_ae_fresh_sample.json").read_text())
    record = next(r for r in records if r["attributes"]["lei"] == "2549005ZMETNR15CNY09")
    entity = record["attributes"]["entity"]
    # предпосылки: имя арабское, en-варианта нет, ASCII-написание в реестре есть
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
    """Минимальная запись GLEIF: проверяем порядок ступеней, а не разбор адресов."""
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

# По одному кандидату на каждую ступень. Текст различает ступени: какой из них попал
# в карточку, видно по имени, а не по номеру.
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
ALL_OTHER_NAMES = [PREVIOUS, TRADING, ALTERNATIVE]      # порядок в записи произвольный
ALL_TRANSLITERATED = [AUTO_ASCII, PREFERRED_ASCII]      # и здесь тоже


def test_step_1_latin_legal_name_wins_over_everything():
    company = to_company(
        _record("Latin Legal Name FZE", ALL_OTHER_NAMES, ALL_TRANSLITERATED)
    )
    assert company.name == "Latin Legal Name FZE"


@pytest.mark.parametrize(
    ("other_names", "transliterated", "expected"),
    [
        # Ступень 2: официальное имя на другом языке бьёт всё остальное.
        (ALL_OTHER_NAMES, ALL_TRANSLITERATED, "Alternative Legal Name LLC"),
        # Ступень 3: ASCII-написание реестра — выше торгового и прежнего имени.
        ([PREVIOUS, TRADING], ALL_TRANSLITERATED, "Preferred Ascii FZCO"),
        # Ступень 4: торговое имя — выше прежнего.
        ([PREVIOUS, TRADING], [AUTO_ASCII], "Trading Name Traders"),
        # Ступень 5: прежнее имя — выше машинной транслитерации: оно читается.
        ([PREVIOUS], [AUTO_ASCII], "Previous Legal Name DMCC"),
        # Ступень 6: машинный ASCII — хуже всех написаний, но лучше арабской строки.
        ([], [AUTO_ASCII], "awtw askyy mashynnyy musor"),
        # Ступень 7: брать нечего — остаётся арабское имя, выдумывать адаптер не станет.
        ([], [], ARABIC),
    ],
)
def test_name_priority_chain_step_by_step(other_names, transliterated, expected):
    """Каждая ступень и её место: убираем верхние кандидатуры по одной."""
    assert to_company(_record(ARABIC, other_names, transliterated)).name == expected


def test_preferred_ascii_beats_previous_legal_name():
    """Точка правки: прежнее имя не должно обгонять актуальное ASCII реестра."""
    company = to_company(_record(ARABIC, [PREVIOUS], [PREFERRED_ASCII]))
    assert company.name == "Preferred Ascii FZCO"


def test_previous_legal_name_beats_auto_ascii():
    """Вторая точка правки: машинный ASCII ниже прежнего имени — он не читается."""
    company = to_company(_record(ARABIC, [PREVIOUS], [AUTO_ASCII]))
    assert company.name == "Previous Legal Name DMCC"


def test_untyped_other_name_is_not_used():
    """Негативный контроль: вариант без типа — не кандидат ни на одной ступени."""
    untyped = {"name": "Untyped Name LLC", "language": "en"}
    assert to_company(_record(ARABIC, [untyped], [])).name == ARABIC
    assert to_company(_record(ARABIC, [untyped], [AUTO_ASCII])).name == AUTO_ASCII["name"]


def test_non_english_other_name_is_not_used():
    """Русский вариант с правильным типом — всё равно не латиница реестра."""
    russian = dict(ALTERNATIVE, name="Альтернативное имя", language="ru")
    assert to_company(_record(ARABIC, [russian], [PREFERRED_ASCII])).name == (
        "Preferred Ascii FZCO"
    )
    assert to_company(_record(ARABIC, [russian], [])).name == ARABIC


# --- живые записи под ступени цепочки --------------------------------------------


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
    """Живые записи: сперва предпосылки из кэша, потом требование к карточке."""
    records = json.loads((DATA_DIR / f"gleif_ae_{mode}_sample.json").read_text())
    record = next(r for r in records if r["attributes"]["lei"] == lei)
    entity = record["attributes"]["entity"]
    assert _is_arabic(entity["legalName"]["name"])  # иначе сработала бы ступень 1
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
    """Отрицательный результат числом: ступень 5 на выборках не срабатывает ни разу.

    У единственной записи с PREVIOUS_LEGAL_NAME без PREFERRED_ASCII
    (529900MHS1KEGQZD7A14) есть ещё и ALTERNATIVE_LANGUAGE_LEGAL_NAME с тем же текстом,
    и карточка берёт его на ступени 2. Поэтому ступень 5 закрыта синтетикой, а не живым
    LEI: живого случая в кэше нет, и написать «проверено на живых данных» было бы неправдой.
    """
    records = json.loads((DATA_DIR / "gleif_ae_lapsed_sample.json").read_text())
    record = next(r for r in records if r["attributes"]["lei"] == "529900MHS1KEGQZD7A14")
    types = {o["type"] for o in record["attributes"]["entity"]["otherNames"]}
    assert types == {"PREVIOUS_LEGAL_NAME", "ALTERNATIVE_LANGUAGE_LEGAL_NAME"}
    assert to_company(record).name == "BEIDECK INTERNATIONAL PETROLEUM TRADING L.L.C"
    # машинная транслитерация в карточку не попала
    assert "bydyk" not in to_company(record).name


def test_which_step_serves_each_arabic_record():
    """Разбивка по ступеням числами: 37 + 10 + 4 из 51, ступени 5-7 пусты."""
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
    """В альтернативном (английском) адресе country может отсутствовать — берём из юридического."""
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
    """Пропуск печатается числом, а не проглатывается."""
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
    """Контроль прибора: подмена действительно перехватывает сетевой путь."""
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(NetworkForbidden):
        GleifAdapter(mode="lapsed", offline=False).fetch(5)


def test_url_is_built_with_filters_and_sort():
    """URL не проверяется сетью, поэтому проверяется строкой (имя эндпоинта в коде одно)."""
    url = GleifAdapter(mode="lapsed", offline=True)._url(500)
    assert url.startswith("https://api.gleif.org/api/v1/lei-records?")
    assert "filter%5Bentity.legalAddress.country%5D=AE" in url
    assert "filter%5Bregistration.status%5D=LAPSED" in url
    assert "sort=-registration.nextRenewalDate" in url
    assert "page%5Bsize%5D=200" in url  # limit срезается до 200
    fresh_url = GleifAdapter(mode="fresh", offline=True)._url(10)
    assert "sort=-entity.creationDate" in fresh_url
    assert "registration.status" not in fresh_url


# --- статус юрлица: три исхода вместо двух ----------------------------------------
#
# Дефект: `entity.get("status") == "ACTIVE"` — всё остальное, включая слово NULL
# («статус не сообщён») и отсутствие поля, означало «юрлицо неактивно» и роняло лид
# в LOW с причиной, которой реестр не давал.
#
# ИЗМЕРЕНО 2026-09-11 запросом к api.gleif.org: перечисление значений выдаёт сам API
# (`filter[entity.status]=ZZZ` → 400 «expected is one of ACTIVE, INACTIVE, NULL»),
# по ОАЭ 9369 записей: ACTIVE 9236, INACTIVE 97, NULL 36. В офлайн-выборках
# data/gleif_ae_*_sample.json (120 записей) все 120 ACTIVE — дефект на них не виден,
# поэтому вход для него сохранён отдельным файлом tests/data/gleif_ae_odd_status_sample.json
# (8 живых записей, выгрузка 2026-09-11).


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ACTIVE", "active"),
        ("INACTIVE", "inactive"),
        ("NULL", "unknown"),      # слово реестра: «статус не сообщён»
        ("active", "active"),     # регистр слова статуса решения не меняет
        (" ACTIVE ", "active"),
        ("DISSOLVED", "unknown"), # слово не из перечня реестра — не приговор юрлицу
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_entity_status_words_map_to_three_outcomes(raw, expected):
    """Ожидаемое — литералы значений, а не импорт перечисления из проверяемого модуля."""
    from leadcentre.sources.gleif import entity_status

    assert entity_status(raw).value == expected


def test_entity_status_unknown_is_not_entity_active():
    """`entity_active` остаётся булевым для web/, но третий исход в нём не прячется."""
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
    """Негативный контроль прибора: пустой файл сделал бы следующие числа бессмысленными.

    Литералы выписаны руками из выгрузки 2026-09-11 (api.gleif.org, фильтры
    entity.legalAddress.country=AE плюс registration.status / entity.status).
    """
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
    """Главное требование правки: «реестр промолчал» не печатается как «юрлицо неактивно».

    ИЗМЕРЕНО 2026-09-11 на этих же 8 записях: было 6 карточек с причиной
    `entity_inactive` (4 из них — записи со словом NULL), стало 2 — ровно те, где
    реестр написал INACTIVE.
    """
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


# --- метка языка: сравнение по BCP 47, а не буква в букву -------------------------


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("en", True),
        ("EN", True),        # метки языка регистронезависимы (RFC 5646 §2.1.1)
        ("en-US", True),     # подтег региона язык не меняет
        ("en_GB", True),
        ("eng", True),       # ISO 639-2/T того же языка
        ("ar", False),
        ("ar-AE", False),
        ("", False),
        (None, False),
        ("english", False),  # не код языка: угадывать не беремся
    ],
)
def test_english_language_tag_is_matched_by_its_primary_subtag(tag, expected):
    from leadcentre.sources.gleif import is_english

    assert is_english(tag) is expected


def test_english_address_and_name_survive_an_uppercase_language_tag():
    """Вход, на котором дефект воспроизводится: латиница помечена «EN», а не «en».

    В выборках GLEIF по ОАЭ (1000 записей, ИЗМЕРЕНО 2026-09-11) таких меток нет —
    поэтому вход здесь литеральный, а не из data/. Метка вида `pl-PL` в выдаче GLEIF
    встречается (2 записи из 3000), то есть форма с подтегом реальна.
    """
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
