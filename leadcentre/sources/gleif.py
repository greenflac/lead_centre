"""Адаптер GLEIF (api.gleif.org, без аутентификации).

Границы источника: в GLEIF попадают только компании, получившие LEI, — по ОАЭ это около
девяти тысяч записей (docs/data/gleif_schema.md), а не весь рынок. Другой источник
подключается заменой адаптера, движок при этом не меняется.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from leadcentre.models import Company
from leadcentre.sources.base import FetchResult

API = "https://api.gleif.org/api/v1/lei-records"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data"
CACHE_FILES = {
    "lapsed": CACHE_DIR / "gleif_ae_lapsed_sample.json",
    "fresh": CACHE_DIR / "gleif_ae_fresh_sample.json",
}
SORT = {"lapsed": "-registration.nextRenewalDate", "fresh": "-entity.creationDate"}


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def to_company(record: dict) -> Company | None:
    """Запись GLEIF → Company. None, если нет минимума полей (пойдёт в skipped)."""
    attrs = record.get("attributes") or {}
    entity, registration = attrs.get("entity") or {}, attrs.get("registration") or {}
    lei = attrs.get("lei")
    if not lei or not entity.get("legalName"):
        return None
    address, extra_lines = _addresses(entity)
    return Company(
        source="gleif",
        external_id=lei,
        name=_english_name(entity),
        city=address.get("city") or "",
        country=address.get("country") or "",
        address_lines=extra_lines,
        registrar_id=(entity.get("registeredAt") or {}).get("id"),
        license_no=entity.get("registeredAs"),
        created_on=_parse_date(entity.get("creationDate")),
        entity_active=entity.get("status") == "ACTIVE",
        registration_status=registration.get("status") or "",
        next_renewal_on=_parse_date(registration.get("nextRenewalDate")),
    )


ALT_LEGAL_ADDRESS = "ALTERNATIVE_LANGUAGE_LEGAL_ADDRESS"


def _addresses(entity: dict) -> tuple[dict, tuple[str, ...]]:
    """Адрес для полей и все строки адреса на всех языках для классификатора.

    Юридический адрес в GLEIF часто арабский, а английский вариант лежит в otherAddresses.
    Классификатор ищет маркеры латиницей, поэтому строки объединяются, а город берётся
    из английского варианта, если он есть.
    """
    legal = entity.get("legalAddress") or {}
    alternatives = [
        a for a in (entity.get("otherAddresses") or []) if a.get("type") == ALT_LEGAL_ADDRESS
    ]
    english = next((a for a in alternatives if a.get("language") == "en"), None)
    preferred = english or legal
    lines: list[str] = []
    for source in (legal, *alternatives):
        lines += [x for x in (source.get("addressLines") or []) if x]
    # country берём из юридического адреса: в альтернативном варианте его может не быть
    preferred = {**preferred, "country": legal.get("country") or preferred.get("country")}
    return preferred, tuple(lines)


def _english_name(entity: dict) -> str:
    """Название латиницей, по порядку доверия к источнику.

    Порядок — данные, а не ветвление, потому что каждая ступень куплена наблюдением:

    1. Латинское `legalName` — как в реестре, подменять нечем и незачем.
    2. `ALTERNATIVE_LANGUAGE_LEGAL_NAME` — официальное имя на другом языке.
    3. `PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME` — ASCII-написание, выбранное самим
       реестром. Стоит выше прежнего и торгового имени: у LEI 213800MZ26M5G2T1NB81
       en-вариант — это `NEW APPOLO DMCC` с типом PREVIOUS_LEGAL_NAME, а компания
       сейчас `NEW APOLLO FZCO`. Менеджер ищет по названию из карточки, и по прежнему
       имени он текущую компанию не найдёт.
    4. Торговое имя, затем прежнее — хуже актуального, но это написания из реестра.
    5. `AUTO_ASCII_TRANSLITERATED_LEGAL_NAME` — машинная транслитерация самого реестра
       («bydyk antrnashwnal ltjart albtrwlywm»). Читается плохо, поэтому ниже прежнего
       имени, но выше арабской строки, которую менеджер не прочитает вовсе.
    6. Арабское имя как последнее средство: выдумывать написание адаптер не станет.
    """
    legal = (entity.get("legalName") or {}).get("name", "")
    if any("A" <= ch.upper() <= "Z" for ch in legal):
        return legal
    other_names = entity.get("otherNames") or []
    translit = entity.get("transliteratedOtherNames") or []
    for source, kind in (
        (other_names, "ALTERNATIVE_LANGUAGE_LEGAL_NAME"),
        (translit, "PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME"),
        (other_names, "TRADING_OR_OPERATING_NAME"),
        (other_names, "PREVIOUS_LEGAL_NAME"),
        (translit, "AUTO_ASCII_TRANSLITERATED_LEGAL_NAME"),
    ):
        for item in source:
            english = source is translit or item.get("language") == "en"
            if item.get("type") == kind and english and item.get("name"):
                return item["name"]
    return legal


class GleifAdapter:
    name = "gleif"

    def __init__(self, mode: str = "lapsed", offline: bool | None = None) -> None:
        if mode not in SORT:
            raise ValueError(f"неизвестный режим {mode!r}, ожидается один из {tuple(SORT)}")
        self.mode = mode
        self.offline = os.environ.get("OFFLINE") == "1" if offline is None else offline

    def _url(self, limit: int) -> str:
        params = {
            "filter[entity.legalAddress.country]": "AE",
            "sort": SORT[self.mode],
            "page[size]": str(min(limit, 200)),
        }
        if self.mode == "lapsed":
            params["filter[registration.status]"] = "LAPSED"
        return f"{API}?{urllib.parse.urlencode(params)}"

    def _raw(self, limit: int) -> tuple[list[dict], bool]:
        if self.offline:
            return json.loads(CACHE_FILES[self.mode].read_text())[:limit], True
        request = urllib.request.Request(
            self._url(limit), headers={"Accept": "application/vnd.api+json"}
        )
        with urllib.request.urlopen(request, timeout=40) as response:
            return json.load(response)["data"], False

    def fetch(self, limit: int = 60) -> FetchResult:
        records, from_cache = self._raw(limit)
        companies = [c for c in (to_company(r) for r in records) if c is not None]
        return FetchResult(
            companies=tuple(companies),
            fetched=len(records),
            skipped=len(records) - len(companies),
            source=f"{self.name}:{self.mode}",
            from_cache=from_cache,
        )
