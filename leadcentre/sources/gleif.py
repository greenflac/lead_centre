"""GLEIF registry adapter; only companies holding an LEI appear here, so this is a slice
of the market. Another source plugs in by replacing this adapter."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from leadcentre.models import Company, EntityStatus
from leadcentre.sources.base import FetchResult

API = "https://api.gleif.org/api/v1/lei-records"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data"
CACHE_FILES = {
    "lapsed": CACHE_DIR / "gleif_ae_lapsed_sample.json",
    "fresh": CACHE_DIR / "gleif_ae_fresh_sample.json",
}
SORT = {"lapsed": "-registration.nextRenewalDate", "fresh": "-entity.creationDate"}


#: Registry status words -> our outcomes; `NULL` means "not reported", not "dead".
ENTITY_STATUS_WORDS: dict[str, EntityStatus] = {
    "ACTIVE": EntityStatus.ACTIVE,
    "INACTIVE": EntityStatus.INACTIVE,
    "NULL": EntityStatus.UNKNOWN,
}


def entity_status(value: str | None) -> EntityStatus:
    """Maps a registry word onto an outcome; unknown words stay UNKNOWN, never INACTIVE."""
    return ENTITY_STATUS_WORDS.get((value or "").strip().upper(), EntityStatus.UNKNOWN)


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def to_company(record: dict) -> Company | None:
    """Converts a GLEIF record into a Company, or None when required fields are missing."""
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
        entity_status=entity_status(entity.get("status")),
        entity_status_raw=(entity.get("status") or "").strip(),
        registration_status=registration.get("status") or "",
        next_renewal_on=_parse_date(registration.get("nextRenewalDate")),
    )


ALT_LEGAL_ADDRESS = "ALTERNATIVE_LANGUAGE_LEGAL_ADDRESS"

#: English language tags; `eng` is the ISO 639-2/T form, chosen but never observed.
ENGLISH_LANGUAGE_TAGS = frozenset({"en", "eng"})

LANGUAGE_SUBTAG_SEPARATORS = ("-", "_")


def is_english(tag: str | None) -> bool:
    """Reports whether a language tag is English, comparing per BCP 47, not letter by letter."""
    if not tag:
        return False
    primary = tag.strip().lower()
    for separator in LANGUAGE_SUBTAG_SEPARATORS:
        primary = primary.split(separator)[0]
    return primary in ENGLISH_LANGUAGE_TAGS


def _addresses(entity: dict) -> tuple[dict, tuple[str, ...]]:
    """Returns the address fields plus every address line in every language."""
    legal = entity.get("legalAddress") or {}
    alternatives = [
        a for a in (entity.get("otherAddresses") or []) if a.get("type") == ALT_LEGAL_ADDRESS
    ]
    english = next((a for a in alternatives if is_english(a.get("language"))), None)
    preferred = english or legal
    lines: list[str] = []
    for source in (legal, *alternatives):
        lines += [x for x in (source.get("addressLines") or []) if x]
    # Why the legal address: the alternative variant may carry no country.
    preferred = {**preferred, "country": legal.get("country") or preferred.get("country")}
    return preferred, tuple(lines)


def _english_name(entity: dict) -> str:
    """Returns a Latin name, trying sources in descending order of trust.

    Current spellings beat former ones, because a manager searching by a former name will
    not find the company.
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
            english = source is translit or is_english(item.get("language"))
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
