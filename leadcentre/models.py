"""Общие типы движка. Одно знание — одно место: формы данных живут здесь."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from leadcentre.engine.reasons import (
    DEFAULT_LANGUAGE,
    Language,
    Reason,
    ReasonRenderError,
    render_all,
)


class Tier(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INVALID = "INVALID"  # инвариант нарушен: карточку показывать нельзя


class AddressType(str, Enum):
    """Ось A: где компания сидит по документам."""

    REGISTRAR = "A1_registrar"        # адрес здания фризоны-регистратора: флекси/виртуальный
    BUSINESS_CENTRE = "A2_business_centre"  # бизнес-центр/сервисный офис
    OWN = "A3_own"                    # собственный/арендованный офис
    UNKNOWN = "A0_unknown"


class Event(str, Enum):
    """Ось B: что произошло и когда — повод для разговора."""

    LAPSED = "B1_lapsed"              # регистрация LEI просрочена
    NEW_ENTITY = "B2_new"             # юрлицо создано недавно
    RENEWAL_SOON = "B3_renewal_soon"  # продление LEI на подходе
    NONE = "B0_none"


class CityMatch(str, Enum):
    """Что удалось решить по строке города из реестра. Исходов три, а не два.

    `UNRECOGNISED` — не «не тот город»: реестр написал что-то, чего нет ни в целевых,
    ни в нецелевых списках (новый район, опечатка, третье написание). Свернуть его в
    `OFF_TARGET` — значит потерять счётчик того, насколько списки отстали от реестра;
    свернуть в `TARGET` — раздать HIGH по догадке.
    """

    TARGET = "target"                # эмират Дубай в любом написании или его район
    OFF_TARGET = "off_target"        # другой эмират: Абу-Даби, Шарджа, Аджман, РАК
    UNRECOGNISED = "unrecognised"    # строка есть, но решить по ней нельзя
    NOT_SET = "not_set"              # города в записи нет вовсе


class EntityStatus(str, Enum):
    """Что реестр сказал о существовании юрлица. Исхода три, а не два.

    `UNKNOWN` — не «неактивно»: GLEIF отдельным значением `NULL` сообщает, что статус
    ему не передали (ИЗМЕРЕНО 2026-09-11: по ОАЭ 36 записей из 9369, см. `sources/gleif.py`).
    Свернуть его в `INACTIVE` — значит напечатать на карточке «юрлицо неактивно» там, где
    реестр этого не говорил, и уронить лид до LOW по выдуманному факту.

    Значения — наши машинные ключи (они уезжают в API), а не слова реестра: слова реестра
    переводит в них адаптер источника, потому что вокабуляр у каждого источника свой.
    """

    ACTIVE = "active"        # реестр сказал: юрлицо действует
    INACTIVE = "inactive"    # реестр сказал: юрлицо не действует
    UNKNOWN = "unknown"      # реестр не сказал ничего или сказал незнакомое слово


@dataclass(frozen=True)
class Company:
    """Компания из внешнего источника, приведённая к общему виду."""

    source: str
    external_id: str            # LEI или иной идентификатор источника
    name: str
    city: str
    country: str
    address_lines: tuple[str, ...]
    registrar_id: str | None    # орган регистрации (RA-код GLEIF)
    license_no: str | None
    created_on: date | None
    entity_status: EntityStatus
    registration_status: str    # сырое слово реестра: ISSUED / LAPSED / RETIRED / ...
    next_renewal_on: date | None
    entity_status_raw: str = ""  # что стояло в поле источника — для печати в причине
    is_synthetic: bool = False

    @property
    def entity_active(self) -> bool:
        """Совместимость с потребителями, у которых поле булево (`web/`, payload API).

        Третий исход в булевом поле не виден: и INACTIVE, и UNKNOWN дают False.
        Код движка обязан читать `entity_status`, а не это поле, — здесь оно одно
        и вычисляемое, чтобы второго способа узнать статус не завелось (Е1).
        """
        return self.entity_status is EntityStatus.ACTIVE


@dataclass(frozen=True)
class Evidence:
    """Доказательство повода: без него HIGH не выдаётся (инвариант в score.py)."""

    kind: str
    value: str


@dataclass(frozen=True)
class Score:
    """Приоритет и то, из чего он получился.

    Причины хранятся структурно — `reason_items` (код плюс параметры). Поле `reasons`
    осталось кортежем строк, но перестало быть самостоятельным знанием: когда есть
    `reason_items`, оно вычисляется из них отрисовкой на русском и передавать его
    одновременно нельзя (двух источников текста быть не должно). Пустой
    `reason_items` с готовыми строками остаётся ровно для одного случая: карточка,
    поднятая из хранилища, где сохранены только строки.
    """

    tier: Tier
    address_type: AddressType
    event: Event
    reasons: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    violations: tuple[str, ...] = ()
    reason_items: tuple[Reason, ...] = ()

    def __post_init__(self) -> None:
        if self.reason_items:
            if self.reasons:
                raise ValueError(
                    "Score: причины заданы дважды — reason_items и reasons; "
                    "строки собирает отрисовка, передавать их отдельно нельзя"
                )
            object.__setattr__(
                self, "reasons", render_all(self.reason_items, DEFAULT_LANGUAGE)
            )

    def reasons_in(self, language: Language) -> tuple[str, ...]:
        """Причины на нужном языке — это берёт интерфейс.

        Три исхода, а не два: есть структурные причины — отрисуем на любом языке;
        причин нет вовсе — пустой кортеж; есть только строки из хранилища — отрисовать
        не на чем, и это `ReasonRenderError`, а не молчаливая подмена русским текстом.
        """
        if self.reason_items:
            return render_all(self.reason_items, language)
        if not self.reasons:
            return ()
        if language is DEFAULT_LANGUAGE:
            return self.reasons
        raise ReasonRenderError(
            f"причины восстановлены строками без кодов: язык {language.value} "
            f"не отрисовать (сохранено {len(self.reasons)} строк на "
            f"{DEFAULT_LANGUAGE.value})"
        )


# --- входящие обращения ---


class RequestType(str, Enum):
    OFFICE = "office"
    SETUP = "setup"
    VISA = "visa"
    ACCOUNTING = "accounting"
    RENEWAL = "renewal"          # продление лицензии, визы, Ejari — повторяющаяся выручка
    BANK = "bank"
    OTHER = "other"


@dataclass(frozen=True)
class InboundMessage:
    """Обращение из канала SORP: чат Jivo, WhatsApp, Telegram, форма сайта."""

    external_id: str
    channel: str            # jivo | whatsapp | telegram | form
    text: str
    received_at: date
    is_synthetic: bool = True


@dataclass(frozen=True)
class LeadFacts:
    """Факты, извлечённые моделью из текста обращения. Модель предлагает — код решает.

    Контракт между extract.py (заполняет) и score.py/reply.py (читают). Менять только
    вместе с тестами, которые держат литералы схемы.
    """

    request_types: tuple[RequestType, ...] = ()
    jurisdiction_hint: str | None = None      # mainland | freezone | конкретная зона | None
    headcount: int | None = None
    timeline_days: int | None = None          # через сколько дней клиенту нужно решение
    urgency_stated: bool = False              # срочность заявлена словами, даты в тексте нет
    budget_hint: str | None = None
    language: str = "en"                      # ru | en | ar | mixed
    is_spam: bool = False
    has_contact: bool = False
    confidence: float = 0.0                   # 0..1, уверенность модели в извлечении
    # Мерили ли уверенность вообще. Ноль в `confidence` бывает двух разных сортов:
    # модель посмотрела и не уверена — и никто не смотрел (офлайн-заглушка, `OFFLINE=1`).
    # Свернуть второе в первое — значит подать «не измеряли» как измеренную низкую
    # уверенность; на карточке это видно как «уверенность 0.00 — ниже порога 0.50»
    # там, где измерения не было. Умолчание True: заполняет и модель, и правила.
    confidence_measured: bool = True
    quotes: tuple[str, ...] = field(default_factory=tuple)  # цитаты-доказательства
