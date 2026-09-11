"""Тексты движка как данные, а не как готовые строки.

Движок называет *код* и *параметры*; текст на любом из поддержанных языков собирается
здесь и только здесь. Каталогов три, машинка у них одна:

* `CATALOGUE` — причины приоритета (`ReasonCode`), их печатает карточка;
* `VIOLATION_CATALOGUE` — нарушения инвариантов (`ViolationCode`), из-за которых карточка
  становится INVALID; их читает человек в отчёте прогона, поэтому английский им нужен так
  же, как причинам;
* `ROUTE_REASON_CATALOGUE` — почему лид ушёл на эту модель (`RouteReasonCode`); эта строка
  видна в интерфейсе внутри «How this was scored», поэтому английский нужен и ей.

Ни `score.py`, ни `extract.py`, ни дашборд формулировок не собирают и переводов у себя
не держат: второго источника текста в проекте нет.

Три вещи, ради которых модуль устроен именно так:

1. **Числительные.** Русский требует трёх форм («1 день / 2 дня / 5 дней»), английский —
   двух. Поэтому текст причины — не «одна format-строка на язык» с вклеенной единицей,
   а шаблон, в котором число и его существительное подставляются вместе:
   `{days:plural:day}`. Формы живут в `PLURAL_FORMS`, правило выбора формы — в
   `PLURAL_RULES`, по одному на язык. Добавить язык с четырьмя формами (польский,
   арабский) — значит дописать строку в обе таблицы, не трогая ни одного шаблона.
2. **Явная ошибка вместо пустой строки** («не смогли» — отдельный исход). Запись без
   нужного параметра, лишний параметр, неизвестный код, отсутствующий текст на одном из
   языков — всё это `ReasonError`, а не молчаливое пустое место в карточке.
3. **Каталоги проверяются на импорте.** `validate_all_catalogues()` вызывается при загрузке
   модуля и проходит по всем трём: набор плейсхолдеров в русском и английском шаблонах
   обязан совпадать с объявленным списком параметров, и оба языка обязаны быть заполнены.
   Это гейт в коде, а не строка в правилах.

Поля, объявленные кортежем строк и читаемые другими модулями (`Score.violations`,
`Extraction.route_reason`), заполняются `RenderedText` — строкой, которая помнит свой код
и потому отрисовывается на втором языке без второго хранилища текста.
"""
from __future__ import annotations

import string
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Self

__all__ = [
    "CATALOGUE",
    "CATALOGUES",
    "DEFAULT_LANGUAGE",
    "ROUTE_REASON_CATALOGUE",
    "VIOLATION_CATALOGUE",
    "Language",
    "Phrase",
    "Reason",
    "ReasonCatalogueError",
    "ReasonCode",
    "ReasonError",
    "ReasonRenderError",
    "ReasonSpec",
    "RenderedText",
    "RouteReason",
    "RouteReasonCode",
    "Violation",
    "ViolationCode",
    "reason",
    "render",
    "render_all",
    "rendered",
    "rendered_all",
    "route_reason",
    "text_in",
    "texts_in",
    "validate_all_catalogues",
    "validate_catalogue",
    "violation",
]


class Language(str, Enum):
    """Языки, на которых причина обязана существовать. Добавление языка сюда без
    текстов и форм числительных роняет импорт модуля — молча недопереведённым
    интерфейс не станет."""

    RU = "ru"
    EN = "en"


#: Язык отрисовки по умолчанию: им заполняется `Score.reasons`, чтобы остальной код
#: и тесты, читающие кортеж строк, продолжали работать без правок.
DEFAULT_LANGUAGE = Language.RU


class ReasonError(Exception):
    """Общий предок ошибок причин: их ловят целиком, если ловят вообще."""


class ReasonCatalogueError(ReasonError):
    """Каталог собран неверно: нет текста на языке, разъехались параметры и т. п."""


class ReasonRenderError(ReasonError):
    """Причину нельзя отрисовать: нет параметра, лишний параметр, неизвестный код."""


class ReasonCode(str, Enum):
    """Код причины. Значение — стабильный машинный ключ: он уезжает в API, в хранилище
    и в интерфейс, поэтому переименование кода — миграция, а не правка текста."""

    # --- компания из реестра (оси A и B) ---
    LEI_LAPSED_FRESH = "lei_lapsed_fresh"
    LEI_LAPSED_LONG_AGO = "lei_lapsed_long_ago"
    ENTITY_RECENTLY_CREATED = "entity_recently_created"
    LEI_RENEWAL_SOON = "lei_renewal_soon"
    CITY_OFF_TARGET = "city_off_target"
    CITY_NOT_SET = "city_not_set"
    CITY_UNRECOGNISED = "city_unrecognised"
    ENTITY_INACTIVE = "entity_inactive"
    # --- входящее обращение (ось C) ---
    SPAM_OR_OFF_TOPIC = "spam_or_off_topic"
    NO_REQUEST_TYPE = "no_request_type"
    URGENT_TIMELINE = "urgent_timeline"
    URGENT_STATED = "urgent_stated"
    PACKAGE_REQUEST = "package_request"
    TEAM_OVER_FLEXI_QUOTA = "team_over_flexi_quota"
    BUDGET_NAMED = "budget_named"
    TARGET_LANGUAGE = "target_language"
    TARGET_LANGUAGE_ALONE = "target_language_alone"
    LOW_CONFIDENCE = "low_confidence"
    CONFIDENCE_NOT_MEASURED = "confidence_not_measured"


class ViolationCode(str, Enum):
    """Код нарушения инварианта: почему карточка получила INVALID.

    Отдельное перечисление, а не продолжение `ReasonCode`: причина объясняет ступень,
    нарушение её отменяет. Слить их в одно — значит потерять возможность спросить
    «а нарушения-то были?» иначе как по тексту.
    """

    HIGH_WITHOUT_EVIDENCE = "high_without_evidence"
    COMPANY_OUTSIDE_UAE = "company_outside_uae"
    HIGH_WITHOUT_QUOTE = "high_without_quote"
    HIGH_ON_EMPTY_TEXT = "high_on_empty_text"


class RouteReasonCode(str, Enum):
    """Код причины маршрута модели: почему лид ушёл именно на эту модель.

    Виден в интерфейсе («How this was scored»), поэтому обязан существовать на обоих
    языках здесь, а не переводом на стороне дашборда.
    """

    LONG_MESSAGE = "long_message"
    SHORT_MESSAGE = "short_message"
    MODEL_FORCED = "model_forced"
    OFFLINE_NO_CALL = "offline_no_call"


# --- числительные -----------------------------------------------------------------

#: Префикс спецификатора формата, включающий согласование числа с существительным:
#: `{days:plural:day}` -> «14 дней» / «14 days».
PLURAL_SPEC = "plural:"


def _plural_index_ru(n: int) -> int:
    """Три формы русского: 1 день / 2 дня / 5 дней.

    Ветвление повторяет правило CLDR для ru (one / few / many).
    """
    if n % 10 == 1 and n % 100 != 11:
        return 0
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return 1
    return 2


def _plural_index_en(n: int) -> int:
    """Две формы английского: 1 day / 2 days."""
    return 0 if n == 1 else 1


PLURAL_RULES: dict[Language, Callable[[int], int]] = {
    Language.RU: _plural_index_ru,
    Language.EN: _plural_index_en,
}

#: Сколько форм обязана иметь каждая единица в каждом языке. Расхождение ловится
#: на импорте: правило, выбирающее третью форму из двух, — это IndexError в проде.
PLURAL_FORM_COUNTS: dict[Language, int] = {Language.RU: 3, Language.EN: 2}

PLURAL_FORMS: dict[Language, dict[str, tuple[str, ...]]] = {
    Language.RU: {
        "char": ("символ", "символа", "символов"),
        "day": ("день", "дня", "дней"),
        "person": ("человек", "человека", "человек"),
        "service": ("услуга", "услуги", "услуг"),
    },
    Language.EN: {
        "char": ("char", "chars"),
        "day": ("day", "days"),
        "person": ("person", "people"),
        "service": ("service", "services"),
    },
}


def plural_phrase(language: Language, value: object, noun: str) -> str:
    """«14» + `day` -> «14 дней» / «14 days». Дробное число и неизвестная единица —
    ошибка: согласовать форму не с чем, а тихо напечатать «14.0 день» хуже падения."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReasonRenderError(
            f"согласование числа требует int, получено {value!r} ({type(value).__name__})"
        )
    forms = PLURAL_FORMS[language].get(noun)
    if forms is None:
        known = ", ".join(sorted(PLURAL_FORMS[language]))
        raise ReasonRenderError(
            f"нет форм единицы {noun!r} для языка {language.value}; известны: {known}"
        )
    return f"{value} {forms[PLURAL_RULES[language](abs(value))]}"


class _ReasonFormatter(string.Formatter):
    """Формат записи каталога: обычные спецификаторы плюс `plural:<единица>`.

    Отсутствующий и лишний параметр — явные ошибки (негативный контроль модуля).
    """

    def __init__(self, language: Language, code: Enum) -> None:
        self.language = language
        self.code = code
        self.kind = _kind(code)

    def get_value(self, key, args, kwargs):
        try:
            return super().get_value(key, args, kwargs)
        except (KeyError, IndexError) as exc:
            raise ReasonRenderError(
                f"{self.kind} {self.code.value}: не передан параметр {key!r} "
                f"(язык {self.language.value})"
            ) from exc

    def format_field(self, value, format_spec):
        if format_spec.startswith(PLURAL_SPEC):
            return plural_phrase(self.language, value, format_spec[len(PLURAL_SPEC):])
        return super().format_field(value, format_spec)

    def check_unused_args(self, used_args, args, kwargs) -> None:
        extra = sorted(set(kwargs) - set(used_args))
        if extra:
            raise ReasonRenderError(
                f"{self.kind} {self.code.value}: параметры не используются текстом "
                f"({', '.join(extra)}); язык {self.language.value}"
            )


def _placeholders(template: str) -> frozenset[str]:
    """Имена параметров, которые шаблон действительно подставляет."""
    return frozenset(
        name.split(".")[0].split("[")[0]
        for _, name, _, _ in string.Formatter().parse(template)
        if name
    )


def _plural_nouns(template: str) -> frozenset[str]:
    return frozenset(
        spec[len(PLURAL_SPEC):]
        for _, name, spec, _ in string.Formatter().parse(template)
        if name and spec and spec.startswith(PLURAL_SPEC)
    )


# --- каталог ----------------------------------------------------------------------


@dataclass(frozen=True)
class ReasonSpec:
    """Одна причина: список её параметров и текст на каждом языке.

    Параметры объявлены явно, а не выведены из русского шаблона: объявление — это то,
    с чем сверяются оба текста, иначе «в русском забыли подставить» читалось бы как
    «параметр не нужен».
    """

    params: tuple[str, ...]
    texts: Mapping[Language, str]


CATALOGUE: dict[ReasonCode, ReasonSpec] = {
    # --- компания из реестра ---
    ReasonCode.LEI_LAPSED_FRESH: ReasonSpec(
        params=("days",),
        texts={
            Language.RU: "регистрация LEI просрочена на {days:plural:day}",
            Language.EN: "LEI registration lapsed {days:plural:day} ago",
        },
    ),
    ReasonCode.LEI_LAPSED_LONG_AGO: ReasonSpec(
        params=("days",),
        texts={
            Language.RU: "регистрация LEI просрочена давно — на {days:plural:day}",
            Language.EN: "LEI registration lapsed long ago — {days:plural:day} past due",
        },
    ),
    ReasonCode.ENTITY_RECENTLY_CREATED: ReasonSpec(
        params=("days",),
        texts={
            Language.RU: "юрлицо создано {days:plural:day} назад",
            Language.EN: "entity incorporated {days:plural:day} ago",
        },
    ),
    ReasonCode.LEI_RENEWAL_SOON: ReasonSpec(
        params=("days",),
        texts={
            Language.RU: "продление LEI через {days:plural:day}",
            Language.EN: "LEI renewal due in {days:plural:day}",
        },
    ),
    ReasonCode.CITY_OFF_TARGET: ReasonSpec(
        params=("city",),
        texts={
            Language.RU: "город вне целевых ({city}) — ступень понижена",
            Language.EN: "city outside the target list ({city}) — tier lowered",
        },
    ),
    ReasonCode.CITY_UNRECOGNISED: ReasonSpec(
        # Третий исход по городу: строка есть, но ни в целевых написаниях, ни в
        # нецелевых её нет. Отдельный код, а не CITY_OFF_TARGET: «мы не узнали
        # написание» и «это другой эмират» — разные новости и для менеджера, и для
        # того, кто поддерживает списки написаний в rubric.py.
        params=("city",),
        texts={
            Language.RU: "город не распознан ({city}) — ступень понижена, проверьте вручную",
            Language.EN: "city not recognised ({city}) — tier lowered, check by hand",
        },
    ),
    ReasonCode.CITY_NOT_SET: ReasonSpec(
        # Отдельный код, а не подстановка слова «не указан» в предыдущий: иначе
        # заглушка отсутствующего значения оказывается текстом внутри score.py.
        params=(),
        texts={
            Language.RU: "город не указан — ступень понижена",
            Language.EN: "city is not set — tier lowered",
        },
    ),
    ReasonCode.ENTITY_INACTIVE: ReasonSpec(
        params=(),
        texts={
            Language.RU: "юрлицо неактивно",
            Language.EN: "entity is not active",
        },
    ),
    # --- входящее обращение ---
    ReasonCode.SPAM_OR_OFF_TOPIC: ReasonSpec(
        params=(),
        texts={
            Language.RU: "обращение помечено как спам или не по теме",
            Language.EN: "message flagged as spam or off topic",
        },
    ),
    ReasonCode.NO_REQUEST_TYPE: ReasonSpec(
        params=(),
        texts={
            Language.RU: "из текста не извлечён ни один тип запроса",
            Language.EN: "no request type could be extracted from the text",
        },
    ),
    ReasonCode.URGENT_STATED: ReasonSpec(
        params=(),
        texts={
            Language.RU: "срочность заявлена словами, даты клиент не назвал",
            Language.EN: "urgency stated in words, no date given",
        },
    ),
    ReasonCode.URGENT_TIMELINE: ReasonSpec(
        params=("days", "limit"),
        texts={
            Language.RU: "срок {days:plural:day} — внутри горячего окна "
                         "в {limit:plural:day}",
            Language.EN: "needed in {days:plural:day} — inside the hot window "
                         "of {limit:plural:day}",
        },
    ),
    ReasonCode.PACKAGE_REQUEST: ReasonSpec(
        params=("count",),
        texts={
            Language.RU: "запрошено услуг: {count} — нужен пакет",
            Language.EN: "{count:plural:service} asked about — this is a package, not a "
                         "single line item",
        },
    ),
    ReasonCode.TEAM_OVER_FLEXI_QUOTA: ReasonSpec(
        params=("headcount",),
        texts={
            Language.RU: "команда {headcount:plural:person} — флекси не закроет "
                         "визовую квоту",
            Language.EN: "team of {headcount} — flexi desk will not cover the visa quota",
        },
    ),
    ReasonCode.BUDGET_NAMED: ReasonSpec(
        params=("budget",),
        texts={
            Language.RU: "назван бюджет: {budget}",
            Language.EN: "budget named: {budget}",
        },
    ),
    ReasonCode.TARGET_LANGUAGE: ReasonSpec(
        params=("language",),
        texts={
            Language.RU: "язык обращения {language} — основная аудитория",
            Language.EN: "written in {language} — core audience",
        },
    ),
    ReasonCode.TARGET_LANGUAGE_ALONE: ReasonSpec(
        params=("language",),
        texts={
            Language.RU: "язык обращения {language}, но других признаков нет",
            Language.EN: "written in {language}, but no other signal backs it up",
        },
    ),
    ReasonCode.LOW_CONFIDENCE: ReasonSpec(
        params=("confidence", "threshold"),
        texts={
            Language.RU: "уверенность извлечения {confidence:.2f} — ниже порога "
                         "{threshold:.2f}",
            Language.EN: "extraction confidence {confidence:.2f} — below the "
                         "{threshold:.2f} threshold",
        },
    ),
    ReasonCode.CONFIDENCE_NOT_MEASURED: ReasonSpec(
        # Третий исход рядом с LOW_CONFIDENCE: «не измеряли» — не «измерили и мало».
        # Отдельный код, а не ноль в том же тексте: ноль на видном месте карточки
        # читается как измерение, которого не было (офлайн-заглушка, OFFLINE=1).
        params=(),
        texts={
            Language.RU: "уверенность извлечения не измерялась — ступень не поднимаем",
            Language.EN: "extraction confidence was not measured — tier not raised",
        },
    ),
}


# --- нарушения инвариантов --------------------------------------------------------

VIOLATION_CATALOGUE: dict[ViolationCode, ReasonSpec] = {
    ViolationCode.HIGH_WITHOUT_EVIDENCE: ReasonSpec(
        params=(),
        texts={
            Language.RU: "HIGH без доказательства",
            Language.EN: "HIGH without evidence",
        },
    ),
    ViolationCode.COMPANY_OUTSIDE_UAE: ReasonSpec(
        # Страна подставляется параметром, а не вклеивается в строку в score.py:
        # иначе английский текст пришлось бы собирать там же во второй раз.
        params=("country",),
        texts={
            Language.RU: "компания вне ОАЭ: {country}",
            Language.EN: "company outside the UAE: {country}",
        },
    ),
    ViolationCode.HIGH_WITHOUT_QUOTE: ReasonSpec(
        params=(),
        texts={
            Language.RU: "HIGH без цитаты из обращения",
            Language.EN: "HIGH without a quote from the message",
        },
    ),
    ViolationCode.HIGH_ON_EMPTY_TEXT: ReasonSpec(
        params=(),
        texts={
            Language.RU: "HIGH на пустом тексте обращения",
            Language.EN: "HIGH on an empty message text",
        },
    ),
}


# --- причина маршрута модели ------------------------------------------------------

ROUTE_REASON_CATALOGUE: dict[RouteReasonCode, ReasonSpec] = {
    RouteReasonCode.LONG_MESSAGE: ReasonSpec(
        # Порог — параметр, а не число в тексте: LONG_MESSAGE_CHARS живёт в extract.py
        #, сюда он приезжает значением, и правка константы не требует правки текстов.
        params=("length", "limit"),
        texts={
            Language.RU: "длинное обращение: {length:plural:char} > {limit}",
            Language.EN: "long request: {length:plural:char} > {limit}",
        },
    ),
    RouteReasonCode.SHORT_MESSAGE: ReasonSpec(
        params=("length", "limit"),
        texts={
            Language.RU: "короткое обращение: {length:plural:char} <= {limit}",
            Language.EN: "short request: {length:plural:char} <= {limit}",
        },
    ),
    RouteReasonCode.MODEL_FORCED: ReasonSpec(
        params=(),
        texts={
            Language.RU: "LLM_MODEL задан вручную",
            Language.EN: "LLM_MODEL set by hand",
        },
    ),
    RouteReasonCode.OFFLINE_NO_CALL: ReasonSpec(
        params=(),
        texts={
            Language.RU: "OFFLINE: модель не вызывалась",
            Language.EN: "OFFLINE: no model was called",
        },
    ),
}


#: Реестр каталогов: тип кода -> сам каталог и слово, которым запись называется в
#: сообщении об ошибке. Отрисовка и проверка полноты ходят сюда, поэтому третий
#: каталог получил ту же машинку строкой в реестре, а не копией кода.
CATALOGUES: dict[type, tuple[Mapping[Any, ReasonSpec], str]] = {
    ReasonCode: (CATALOGUE, "причина"),
    ViolationCode: (VIOLATION_CATALOGUE, "нарушение"),
    RouteReasonCode: (ROUTE_REASON_CATALOGUE, "причина маршрута"),
}


def _kind(code: object) -> str:
    """Как называть запись в сообщении об ошибке: «причина», «нарушение», ..."""
    entry = CATALOGUES.get(type(code))
    return entry[1] if entry else "запись каталога"


def _spec_for(code: object) -> ReasonSpec:
    """Запись каталога по коду. Неизвестный код и неизвестный тип кода — разные
    ошибки: первое — дыра в каталоге, второе — в аргумент положили не то."""
    entry = CATALOGUES.get(type(code))
    if entry is None:
        raise ReasonRenderError(
            f"неизвестный тип кода: {code!r} ({type(code).__name__}); "
            f"известны: {', '.join(sorted(t.__name__ for t in CATALOGUES))}"
        )
    spec = entry[0].get(code)
    if spec is None:
        raise ReasonRenderError(f"неизвестный код {entry[1]}: {code!r}")
    return spec


def validate_catalogue(
    catalogue: Mapping[Any, ReasonSpec] = CATALOGUE, kind: str = "причина"
) -> int:
    """Проверить каталог целиком; вернуть число проверенных пар «код-язык».

    Аргумент нужен ради негативного контроля: подсунуть каталог с дырой и увидеть, что
    прибор говорит «нет». Возвращаемое число — «проверено N»: ноль нарушений
    при нуле проверок успехом не считается.
    """
    checked = 0
    if not catalogue:
        # Ноль нарушений при нуле проверок успехом не считается.
        raise ReasonCatalogueError(f"каталог ({kind}) пуст: проверять нечего")
    # Перечисление кодов берётся из самого каталога, а не зашито: так одна и та же
    # проверка сторожит все три каталога и любой следующий.
    code_type = type(next(iter(catalogue)))
    missing_codes = sorted(set(code_type) - set(catalogue), key=lambda c: c.value)
    if missing_codes:
        raise ReasonCatalogueError(
            f"в каталоге ({kind}) нет записей: "
            + ", ".join(c.value for c in missing_codes)
        )
    for language, forms in PLURAL_FORMS.items():
        expected = PLURAL_FORM_COUNTS[language]
        for noun, variants in forms.items():
            if len(variants) != expected:
                raise ReasonCatalogueError(
                    f"единица {noun!r} языка {language.value}: форм {len(variants)}, "
                    f"нужно {expected}"
                )
    for code, spec in catalogue.items():
        declared = frozenset(spec.params)
        for language in Language:
            template = spec.texts.get(language)
            if not template or not template.strip():
                raise ReasonCatalogueError(
                    f"{kind} {code.value}: нет текста на языке {language.value}"
                )
            used = _placeholders(template)
            if used != declared:
                raise ReasonCatalogueError(
                    f"{kind} {code.value}, язык {language.value}: текст подставляет "
                    f"{sorted(used)}, объявлено {sorted(declared)}"
                )
            unknown = _plural_nouns(template) - set(PLURAL_FORMS[language])
            if unknown:
                raise ReasonCatalogueError(
                    f"{kind} {code.value}, язык {language.value}: нет форм для "
                    f"{sorted(unknown)}"
                )
            checked += 1
    return checked


def validate_all_catalogues() -> dict[str, int]:
    """Проверить все каталоги реестра; вернуть «проверено N» по каждому.

    Числа возвращаются, а не печатаются: их приводит приёмочный прогон и тест.
    """
    return {kind: validate_catalogue(catalogue, kind)
            for catalogue, kind in CATALOGUES.values()}


# --- фраза: код плюс параметры ----------------------------------------------------


@dataclass(frozen=True, order=True)
class Phrase:
    """Запись каталога в применённом виде: код плюс параметры. Текста внутри нет.

    Параметры хранятся кортежем пар, а не словарём: фраза остаётся хешируемой и
    сравнимой, а значит её можно класть в множество и сравнивать в тесте литералом.
    Собирать удобнее фабриками `reason` / `violation` / `route_reason`.

    Наследники ниже различают сущности (причина, нарушение, причина маршрута) и
    ничего не добавляют: машинка одна, каталоги разные.
    """

    code: Any
    params: tuple[tuple[str, object], ...] = field(default=())

    def __post_init__(self) -> None:
        spec = _spec_for(self.code)
        kind = _kind(self.code)
        given = {name for name, _ in self.params}
        if len(given) != len(self.params):
            raise ReasonRenderError(f"{kind} {self.code.value}: параметр задан дважды")
        declared = set(spec.params)
        if given != declared:
            raise ReasonRenderError(
                f"{kind} {self.code.value}: параметры {sorted(given)}, "
                f"объявлены {sorted(declared)}"
            )

    def values(self) -> dict[str, object]:
        return dict(self.params)

    def text(self, language: Language = DEFAULT_LANGUAGE) -> str:
        """Отрисовка на указанном языке. Удобство поверх `render`."""
        return render(self, language)


class Reason(Phrase):
    """Причина приоритета (`CATALOGUE`)."""


class Violation(Phrase):
    """Нарушение инварианта (`VIOLATION_CATALOGUE`): почему карточка INVALID."""


class RouteReason(Phrase):
    """Причина маршрута модели (`ROUTE_REASON_CATALOGUE`)."""


def _params(params: dict[str, object]) -> tuple[tuple[str, object], ...]:
    return tuple(sorted(params.items()))


def reason(code: ReasonCode, **params: object) -> Reason:
    """Собрать причину: `reason(ReasonCode.URGENT_TIMELINE, days=14, limit=60)`.

    Параметры сверяются с каталогом сразу — код без параметров падает в точке вызова,
    а не через два слоя в интерфейсе.
    """
    return Reason(code, _params(params))


def violation(code: ViolationCode, **params: object) -> Violation:
    """Собрать нарушение: `violation(ViolationCode.COMPANY_OUTSIDE_UAE, country="GB")`."""
    return Violation(code, _params(params))


def route_reason(code: RouteReasonCode, **params: object) -> RouteReason:
    """Собрать причину маршрута: `route_reason(..., length=1332, limit=600)`."""
    return RouteReason(code, _params(params))


# --- отрисовка --------------------------------------------------------------------


def render(item: Phrase, language: Language = DEFAULT_LANGUAGE) -> str:
    """Текст одной фразы. Любая невозможность — `ReasonRenderError`, не пустая строка."""
    if not isinstance(language, Language):
        raise ReasonRenderError(f"неизвестный язык отрисовки: {language!r}")
    spec = _spec_for(item.code)
    template = spec.texts.get(language)
    if not template or not template.strip():
        raise ReasonRenderError(
            f"{_kind(item.code)} {item.code.value}: нет текста на языке {language.value}"
        )
    return _ReasonFormatter(language, item.code).vformat(template, (), item.values())


def render_all(
    items: tuple[Phrase, ...], language: Language = DEFAULT_LANGUAGE
) -> tuple[str, ...]:
    """Отрисовка списка — единственный способ получить кортеж строк."""
    return tuple(render(item, language) for item in items)


class RenderedText(str):
    """Строка, помнящая код и параметры, из которых она собрана.

    Нужна ради совместимости там, где поле объявлено кортежем строк и таким читается
    другими модулями, хранилищем и тестами: `Score.violations`, `Extraction.route_reason`.
    Значение — обычная строка на языке по умолчанию, поэтому сравнение, `json.dumps` и
    форматирование работают с ней как с любой другой. Текст на другом языке собирается
    из каталога, а не хранится рядом вторым текстом: и русский, и английский собирает
    `render`, второго источника формулировок нет.
    """

    item: Phrase
    language: Language

    def __new__(cls, item: Phrase, language: Language = DEFAULT_LANGUAGE) -> Self:
        rendered_text = super().__new__(cls, render(item, language))
        rendered_text.item = item
        rendered_text.language = language
        return rendered_text

    def text(self, language: Language = DEFAULT_LANGUAGE) -> str:
        """Тот же смысл на другом языке."""
        return render(self.item, language)

    def __reduce__(self):
        # copy/deepcopy/pickle: без этого копия теряет код и перестаёт быть двуязычной,
        # а `dataclasses.asdict` в хранилище копирует значения именно так.
        return (RenderedText, (self.item, self.language))


def rendered(item: Phrase, language: Language = DEFAULT_LANGUAGE) -> RenderedText:
    """Строка на языке по умолчанию, не теряющая кода: см. `RenderedText`."""
    return RenderedText(item, language)


def rendered_all(
    items: tuple[Phrase, ...], language: Language = DEFAULT_LANGUAGE
) -> tuple[RenderedText, ...]:
    """Кортеж таких строк — им заполняются поля, объявленные как кортеж строк."""
    return tuple(RenderedText(item, language) for item in items)


def text_in(value: str, language: Language = DEFAULT_LANGUAGE) -> str:
    """Текст на нужном языке из строки, которая может помнить свой код.

    Три исхода, а не два: строка знает код — отрисуем на любом языке; строки нет
    вовсе — пусто; строка пришла из хранилища без кода — на другой язык её не
    отрисовать, и это `ReasonRenderError`, а не молчаливая выдача русского текста.
    """
    if isinstance(value, RenderedText):
        return value.text(language)
    if not value:
        return value
    if language is DEFAULT_LANGUAGE:
        return value
    raise ReasonRenderError(
        f"текст {value!r} восстановлен строкой без кода: язык {language.value} "
        f"не отрисовать (сохранено на {DEFAULT_LANGUAGE.value})"
    )


def texts_in(
    values: tuple[str, ...], language: Language = DEFAULT_LANGUAGE
) -> tuple[str, ...]:
    """То же для кортежа: нарушения карточки на нужном языке."""
    return tuple(text_in(value, language) for value in values)


# Каталоги проверяются при загрузке модуля: недопереведённая запись роняет импорт,
# а не показывается пустым местом в карточке или в отчёте (гейт, а не
# договорённость). Проверяются все три, а не только причины.
validate_all_catalogues()
