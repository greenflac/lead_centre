"""Engine wording as data: codes and parameters in, localised text out.

Three catalogues share one renderer, and no other module holds wording. Plural agreement
is part of the template, so adding a language is a row, not a template edit. Anything
unrenderable raises, and the catalogues are validated on import.
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
    """Languages every catalogue entry must exist in; a gap breaks the import."""

    RU = "ru"
    EN = "en"


#: Default render language, used to fill fields declared as plain string tuples.
DEFAULT_LANGUAGE = Language.RU


class ReasonError(Exception):
    """Base class for reason errors, so they can be caught as one group."""


class ReasonCatalogueError(ReasonError):
    """The catalogue itself is malformed: missing language, mismatched parameters."""


class ReasonRenderError(ReasonError):
    """The reason cannot be rendered: missing or extra parameter, unknown code."""


class ReasonCode(str, Enum):
    """Reason code; the value is a stable machine key, so renaming one is a migration."""

    LEI_LAPSED_FRESH = "lei_lapsed_fresh"
    LEI_LAPSED_LONG_AGO = "lei_lapsed_long_ago"
    ENTITY_RECENTLY_CREATED = "entity_recently_created"
    LEI_RENEWAL_SOON = "lei_renewal_soon"
    CITY_OFF_TARGET = "city_off_target"
    CITY_NOT_SET = "city_not_set"
    CITY_UNRECOGNISED = "city_unrecognised"
    ENTITY_INACTIVE = "entity_inactive"
    ENTITY_STATUS_UNKNOWN = "entity_status_unknown"
    ENTITY_STATUS_NOT_SET = "entity_status_not_set"
    REGISTRATION_STATUS_NO_EVENT = "registration_status_no_event"
    REGISTRATION_STATUS_UNKNOWN = "registration_status_unknown"
    REGISTRATION_STATUS_NOT_SET = "registration_status_not_set"
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
    """Invariant violation code; separate from ReasonCode because a reason explains a
    tier while a violation cancels it."""

    HIGH_WITHOUT_EVIDENCE = "high_without_evidence"
    COMPANY_OUTSIDE_UAE = "company_outside_uae"
    HIGH_WITHOUT_QUOTE = "high_without_quote"
    HIGH_ON_EMPTY_TEXT = "high_on_empty_text"


class RouteReasonCode(str, Enum):
    """Why a lead went to this model; shown in the interface, so both languages live here."""

    LONG_MESSAGE = "long_message"
    SHORT_MESSAGE = "short_message"
    MODEL_FORCED = "model_forced"
    OFFLINE_NO_CALL = "offline_no_call"


PLURAL_SPEC = "plural:"


def _plural_index_ru(n: int) -> int:
    """Returns the Russian plural index, following the CLDR one/few/many rule."""
    if n % 10 == 1 and n % 100 != 11:
        return 0
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return 1
    return 2


def _plural_index_en(n: int) -> int:
    """Returns the English plural index."""
    return 0 if n == 1 else 1


PLURAL_RULES: dict[Language, Callable[[int], int]] = {
    Language.RU: _plural_index_ru,
    Language.EN: _plural_index_en,
}

#: Required form count per language; a mismatch is caught on import, not in production.
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
    """Agrees a number with its noun; a non-integer or unknown unit raises."""
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
    """Catalogue formatter: standard specs plus `plural:<unit>`; parameter gaps raise."""

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
    """Returns the parameter names a template actually substitutes."""
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


@dataclass(frozen=True)
class ReasonSpec:
    """One catalogue entry: declared parameters plus the text per language."""

    params: tuple[str, ...]
    texts: Mapping[Language, str]


CATALOGUE: dict[ReasonCode, ReasonSpec] = {
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
        # Why separate from CITY_OFF_TARGET: an unknown spelling is not a wrong city.
        params=("city",),
        texts={
            Language.RU: "город не распознан ({city}) — ступень понижена, проверьте вручную",
            Language.EN: "city not recognised ({city}) — tier lowered, check by hand",
        },
    ),
    ReasonCode.CITY_NOT_SET: ReasonSpec(
        # Why a separate code: a placeholder word would put text inside score.py.
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
    ReasonCode.ENTITY_STATUS_UNKNOWN: ReasonSpec(
        # Why separate from ENTITY_INACTIVE: registry silence is no reason for LOW.
        params=("status",),
        texts={
            Language.RU: "статус юрлица в реестре не определён ({status}) — "
                         "выше среднего не поднимаем, проверьте вручную",
            Language.EN: "entity status is undetermined in the registry ({status}) — "
                         "capped at medium, check by hand",
        },
    ),
    ReasonCode.ENTITY_STATUS_NOT_SET: ReasonSpec(
        params=(),
        texts={
            Language.RU: "статуса юрлица в записи реестра нет — "
                         "выше среднего не поднимаем, проверьте вручную",
            Language.EN: "the registry record carries no entity status — "
                         "capped at medium, check by hand",
        },
    ),
    ReasonCode.REGISTRATION_STATUS_NO_EVENT: ReasonSpec(
        # Why printed at all: otherwise "no event" looks like a fresh registration.
        params=("status",),
        texts={
            Language.RU: "статус регистрации {status}: повода по реестру не считаем",
            Language.EN: "registration status {status}: no registry event is counted",
        },
    ),
    ReasonCode.REGISTRATION_STATUS_UNKNOWN: ReasonSpec(
        params=("status",),
        texts={
            Language.RU: "статус регистрации не из перечня реестра ({status}) — "
                         "повод по реестру определить не смогли",
            Language.EN: "registration status is outside the registry list ({status}) — "
                         "could not tell whether there is an event",
        },
    ),
    ReasonCode.REGISTRATION_STATUS_NOT_SET: ReasonSpec(
        params=(),
        texts={
            Language.RU: "статус регистрации не указан — повод по реестру "
                         "определить не смогли",
            Language.EN: "registration status is not set — could not tell whether "
                         "there is an event",
        },
    ),
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
        # Why separate from LOW_CONFIDENCE: a zero reads as a measurement that never ran.
        params=(),
        texts={
            Language.RU: "уверенность извлечения не измерялась — ступень не поднимаем",
            Language.EN: "extraction confidence was not measured — tier not raised",
        },
    ),
}


VIOLATION_CATALOGUE: dict[ViolationCode, ReasonSpec] = {
    ViolationCode.HIGH_WITHOUT_EVIDENCE: ReasonSpec(
        params=(),
        texts={
            Language.RU: "HIGH без доказательства",
            Language.EN: "HIGH without evidence",
        },
    ),
    ViolationCode.COMPANY_OUTSIDE_UAE: ReasonSpec(
        # Why a parameter: inlining it would rebuild the second language in score.py.
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


ROUTE_REASON_CATALOGUE: dict[RouteReasonCode, ReasonSpec] = {
    RouteReasonCode.LONG_MESSAGE: ReasonSpec(
        # Why a parameter: the threshold lives in extract.py and may change without edits.
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


#: Code type -> its catalogue and the word naming an entry; a new catalogue is a row here.
CATALOGUES: dict[type, tuple[Mapping[Any, ReasonSpec], str]] = {
    ReasonCode: (CATALOGUE, "причина"),
    ViolationCode: (VIOLATION_CATALOGUE, "нарушение"),
    RouteReasonCode: (ROUTE_REASON_CATALOGUE, "причина маршрута"),
}


def _kind(code: object) -> str:
    """Returns the word naming this kind of entry in an error message."""
    entry = CATALOGUES.get(type(code))
    return entry[1] if entry else "запись каталога"


def _spec_for(code: object) -> ReasonSpec:
    """Looks an entry up by code; an unknown code and an unknown code type raise apart."""
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
    """Validates one catalogue and returns how many code-language pairs were checked."""
    checked = 0
    if not catalogue:
        # Zero violations over zero checks is not success.
        raise ReasonCatalogueError(f"каталог ({kind}) пуст: проверять нечего")
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
    """Validates every registered catalogue and returns the check count for each."""
    return {kind: validate_catalogue(catalogue, kind)
            for catalogue, kind in CATALOGUES.values()}


@dataclass(frozen=True, order=True)
class Phrase:
    """An applied catalogue entry: a code plus parameters, carrying no text.

    Parameters are a tuple of pairs so the phrase stays hashable and test-comparable.
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
        """Renders this phrase in the given language."""
        return render(self, language)


class Reason(Phrase):
    """A priority reason."""


class Violation(Phrase):
    """An invariant violation: why a card is INVALID."""


class RouteReason(Phrase):
    """A model route reason."""


def _params(params: dict[str, object]) -> tuple[tuple[str, object], ...]:
    return tuple(sorted(params.items()))


def reason(code: ReasonCode, **params: object) -> Reason:
    """Builds a reason; parameters are checked against the catalogue at the call site."""
    return Reason(code, _params(params))


def violation(code: ViolationCode, **params: object) -> Violation:
    """Builds a violation: `violation(ViolationCode.COMPANY_OUTSIDE_UAE, country="GB")`."""
    return Violation(code, _params(params))


def route_reason(code: RouteReasonCode, **params: object) -> RouteReason:
    """Builds a route reason: `route_reason(..., length=1332, limit=600)`."""
    return RouteReason(code, _params(params))


def render(item: Phrase, language: Language = DEFAULT_LANGUAGE) -> str:
    """Renders one phrase; anything that cannot be rendered raises."""
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
    """Renders a sequence of phrases into a tuple of strings."""
    return tuple(render(item, language) for item in items)


class RenderedText(str):
    """A string that remembers its code, so another language is re-rendered, never stored."""

    item: Phrase
    language: Language

    def __new__(cls, item: Phrase, language: Language = DEFAULT_LANGUAGE) -> Self:
        rendered_text = super().__new__(cls, render(item, language))
        rendered_text.item = item
        rendered_text.language = language
        return rendered_text

    def text(self, language: Language = DEFAULT_LANGUAGE) -> str:
        """Returns the same meaning in another language."""
        return render(self.item, language)

    def __reduce__(self):
        # Why defined: otherwise a copy loses the code and stops being bilingual.
        return (RenderedText, (self.item, self.language))


def rendered(item: Phrase, language: Language = DEFAULT_LANGUAGE) -> RenderedText:
    """Renders a phrase into a code-carrying default-language string."""
    return RenderedText(item, language)


def rendered_all(
    items: tuple[Phrase, ...], language: Language = DEFAULT_LANGUAGE
) -> tuple[RenderedText, ...]:
    """Renders phrases into a tuple of code-carrying strings."""
    return tuple(RenderedText(item, language) for item in items)


def text_in(value: str, language: Language = DEFAULT_LANGUAGE) -> str:
    """Returns the text in the given language; a code-less stored string raises."""
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
    """Returns a tuple of such texts in the given language."""
    return tuple(text_in(value, language) for value in values)


# Why on import: an under-translated entry must break the build, not show as a blank.
validate_all_catalogues()
