"""Рубрика как данные, а не как код: пороги и матрица меняются здесь и только здесь (Е1).

Происхождение значений (И4):
- Пороги дней — ВЫБРАНО (автор, 2026-09-09), исходя из цикла продления LEI (12 мес) и
  из того, что просрочка свежее 90 дней ещё «горячая». Мутация любого порога обязана
  красить тест (Т1) — см. tests/test_score.py.
- Адреса регистраторов — ИЗМЕРЕНО по выдаче GLEIF (data/gleif_ae_*_sample.json) и
  ресерчу 06 §4(b).
"""
from __future__ import annotations

from leadcentre.models import AddressType, Event, Tier

# --- пороги (константы-решения) ---
NEW_ENTITY_DAYS = 90        # «создана недавно»
RENEWAL_SOON_DAYS = 60      # «продление на подходе»
LAPSED_FRESH_DAYS = 90      # просрочка свежее этого — полноценный повод
TARGET_CITIES = ("dubai",)  # где SORP работает; сравнение по нижнему регистру

# --- матрица A x B ---
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

# --- классификатор адреса ---
# Здания-регистраторы фризон: адрес компании совпадает с адресом самой зоны,
# значит физического офиса за ним нет.
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

# RA-коды органов регистрации, чьи компании по умолчанию сидят на адресе зоны (ИЗМЕРЕНО:
# доминируют в выдаче по ОАЭ, см. docs/data/gleif_schema.md).
REGISTRAR_AUTHORITY_IDS = ("RA000752",)


# --- ось C: намерение и срочность входящего обращения ---
# Пороги — ВЫБРАНО (автор, 2026-09-09) из практики: месяц — обычный горизонт переезда
# и регистрации в ОАЭ, поэтому срок до 30 дней считаем срочным. Мутация обязана красить тест.
URGENT_TIMELINE_DAYS = 30
PACKAGE_MIN_REQUEST_TYPES = 2   # два и более типа запроса = клиенту нужен пакет услуг
TEAM_MIN_HEADCOUNT = 5          # с пяти человек флекси перестаёт закрывать визовую квоту
LOW_CONFIDENCE = 0.5            # ниже этого извлечению не доверяем настолько, чтобы поднимать
TARGET_LANGUAGES = ("ru",)      # основная аудитория SORP

# Сколько содержательных признаков нужно, чтобы обращение стало горячим. ВЫБРАНО
# (автор, 2026-09-09) после осмотра дашборда: при пороге 1 горячими оказывались 30
# обращений из 70. Приоритет, который выдаётся сорока процентам входящих, менеджер
# перестаёт читать как приоритет — HIGH обязан быть редким.
SIGNALS_FOR_HIGH = 2

# Язык — не самостоятельный повод считать лид горячим: по-русски пишут и те, кто просто
# спрашивает цену. Он работает только как довесок к содержательному признаку, иначе в
# карточке появляется причина «HIGH, потому что по-русски», которой менеджер не поверит.
LANGUAGE_NEEDS_ANOTHER_SIGNAL = True

# Базовый уровень входящего обращения до модификаторов оси C.
INBOUND_BASE = Tier.MEDIUM      # человек написал сам — это уже интерес
INBOUND_BASE_NO_REQUEST = Tier.LOW  # из текста не извлечено ни одного типа запроса

# Порядок ступеней для повышения и понижения на одну.
TIER_LADDER = (Tier.LOW, Tier.MEDIUM, Tier.HIGH)
