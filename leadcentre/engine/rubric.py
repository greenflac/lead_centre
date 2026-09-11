"""Scoring rubric as data: thresholds, the A x B matrix and marker lists.

Every value here is a decision constant guarded by tests/test_score.py; the scoring
logic that reads them lives in engine/score.py.
"""
from __future__ import annotations

from leadcentre.models import AddressType, Event, Tier

NEW_ENTITY_DAYS = 90
RENEWAL_SOON_DAYS = 60
LAPSED_FRESH_DAYS = 90

# Why this list: it separates "known status, no event" from "word we do not know".
# Source: the GLEIF API returns the enumeration itself on an invalid filter value.
STATUS_LAPSED = "LAPSED"
STATUS_ISSUED = "ISSUED"
KNOWN_REGISTRATION_STATUSES = (
    STATUS_ISSUED,
    STATUS_LAPSED,
    "ANNULLED",
    "PENDING_TRANSFER",
    "PENDING_ARCHIVAL",
    "DUPLICATE",
    "RETIRED",
    "MERGED",
)

# Why four lists instead of one string: the registry records the city as the filer typed
# it (any case, Arabic, a district instead of the city, an address with a separator).
# A spelling in none of them is the third outcome "unrecognised", not "wrong city".
# Measured against the GLEIF samples in data/; score.classify_city applies them in order.

#: Matched as a substring, so it also covers "Dubai Silicon Oasis".
TARGET_CITY_MARKERS = ("dubai", "دبي")

#: Dubai districts whose name lacks the city; compared to a whole address part, not as
#: a substring.
TARGET_DISTRICTS = ("nad al sheba", "jumeirah lakes towers")

#: Negative control: while these stay off-target the rule has not decayed into "any UAE city".
NON_TARGET_CITY_MARKERS = (
    "abu dhabi", "أبو ظبي", "أبوظبي",
    "sharjah", "الشارقة",
    "ajman", "عجمان",
    "ras al khaimah", "ras al-khaimah", "رأس الخيمة",
    "fujairah", "الفجيرة",            # chosen, not observed in the samples
    "umm al quwain", "أم القيوين",    # chosen, not observed in the samples
    "al ain", "العين",                # chosen, not observed in the samples
)

#: Districts of other emirates; without them these would fall into "unrecognised" and
#: read as "possibly Dubai".
NON_TARGET_DISTRICTS = ("al reem island", "جزيرة الريم")

#: Alef variants folded before comparison; otherwise each letter doubles the marker list.
CITY_ALEF_FORMS = "أإآٱ"
CITY_ALEF_CANONICAL = "ا"

#: Separators inside a city field written as an address line.
CITY_PART_SEPARATORS = ",/;|"

MATRIX: dict[tuple[AddressType, Event], Tier] = {
    (AddressType.REGISTRAR, Event.LAPSED): Tier.HIGH,
    (AddressType.REGISTRAR, Event.NEW_ENTITY): Tier.HIGH,
    (AddressType.REGISTRAR, Event.RENEWAL_SOON): Tier.MEDIUM,
    (AddressType.REGISTRAR, Event.NONE): Tier.LOW,
    (AddressType.BUSINESS_CENTRE, Event.LAPSED): Tier.MEDIUM,
    (AddressType.BUSINESS_CENTRE, Event.NEW_ENTITY): Tier.MEDIUM,
    (AddressType.BUSINESS_CENTRE, Event.RENEWAL_SOON): Tier.MEDIUM,
    (AddressType.BUSINESS_CENTRE, Event.NONE): Tier.LOW,
    (AddressType.OWN, Event.LAPSED): Tier.MEDIUM,
    (AddressType.OWN, Event.NEW_ENTITY): Tier.LOW,
    (AddressType.OWN, Event.RENEWAL_SOON): Tier.LOW,
    (AddressType.OWN, Event.NONE): Tier.LOW,
    (AddressType.UNKNOWN, Event.LAPSED): Tier.MEDIUM,
    (AddressType.UNKNOWN, Event.NEW_ENTITY): Tier.MEDIUM,
    (AddressType.UNKNOWN, Event.RENEWAL_SOON): Tier.LOW,
    (AddressType.UNKNOWN, Event.NONE): Tier.LOW,
}

# Why these matter: the company address equals the free-zone address, so there is no
# physical office behind it.
REGISTRAR_ADDRESS_MARKERS = (
    "meydan grandstand",
    "ifza business park",
    "dubai digital park",
    "dubai silicon oasis",
    "sharjah publishing city",
    "shams business center",
    "rakez business zone",
    "dmcc business centre",
    "jumeirah lakes towers, dmcc",
    "creative tower",
    "fujairah free zone",
    "saif zone",
    "ajman free zone",
)

BUSINESS_CENTRE_MARKERS = (
    "business centre",
    "business center",
    "office suite",
    "regus",
    "servcorp",
)

#: Registration authorities whose companies sit on the zone address by default.
REGISTRAR_AUTHORITY_IDS = ("RA000752",)


# Why 60: office and incorporation decisions in the UAE are taken one to two months ahead.
URGENT_TIMELINE_DAYS = 60
PACKAGE_MIN_REQUEST_TYPES = 2
TEAM_MIN_HEADCOUNT = 5          # Why 5: above this a flexi-desk stops covering the visa quota.
LOW_CONFIDENCE = 0.5
TARGET_LANGUAGES = ("ru",)

# Why 2: HIGH has to stay rare, or managers stop reading it as a priority.
SIGNALS_FOR_HIGH = 2

# Why: Russian alone also describes people just asking a price, so it only counts as an
# add-on to a substantive signal.
LANGUAGE_NEEDS_ANOTHER_SIGNAL = True

INBOUND_BASE = Tier.MEDIUM
INBOUND_BASE_NO_REQUEST = Tier.LOW

#: Tier order for the one-step raise and lower.
TIER_LADDER = (Tier.LOW, Tier.MEDIUM, Tier.HIGH)
