"""Причины приоритета как данные, а не как готовый текст (Е1).

Движок называет *код* причины и её *параметры*; текст на любом из поддержанных языков
собирается здесь и только здесь. До этого причины были русскими строками, собранными
в `score.py`, и на английском интерфейсе выглядели недоделкой; второго источника текста
теперь нет — `score.py` строк не собирает.

Три вещи, ради которых модуль устроен именно так:

1. **Числительные.** Русский требует трёх форм («1 день / 2 дня / 5 дней»), английский —
   двух. Поэтому текст причины — не «одна format-строка на язык» с вклеенной единицей,
   а шаблон, в котором число и его существительное подставляются вместе:
   `{days:plural:day}`. Формы живут в `PLURAL_FORMS`, правило выбора формы — в
   `PLURAL_RULES`, по одному на язык. Добавить язык с четырьмя формами (польский,
   арабский) — значит дописать строку в обе таблицы, не трогая ни одного шаблона.
2. **Явная ошибка вместо пустой строки** (Р1: «не смогли» — отдельный исход). Причина без
   нужного параметра, лишний параметр, неизвестный код, отсутствующий текст на одном из
   языков — всё это `ReasonError`, а не молчаливое пустое место в карточке.
3. **Каталог проверяется на импорте.** `validate_catalogue()` вызывается при загрузке
   модуля: набор плейсхолдеров в русском и английском шаблонах обязан совпадать с
   объявленным списком параметров, и оба языка обязаны быть заполнены. Это гейт в коде,
   а не строка в правилах (Ц7).
"""
from __future__ import annotations

import string
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "CATALOGUE",
    "DEFAULT_LANGUAGE",
    "Language",
    "Reason",
    "ReasonCatalogueError",
    "ReasonCode",
    "ReasonError",
    "ReasonRenderError",
    "reason",
    "render",
    "render_all",
    "validate_catalogue",
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
    ENTITY_INACTIVE = "entity_inactive"
    # --- входящее обращение (ось C) ---
    SPAM_OR_OFF_TOPIC = "spam_or_off_topic"
    NO_REQUEST_TYPE = "no_request_type"
    URGENT_TIMELINE = "urgent_timeline"
    PACKAGE_REQUEST = "package_request"
    TEAM_OVER_FLEXI_QUOTA = "team_over_flexi_quota"
    BUDGET_NAMED = "budget_named"
    TARGET_LANGUAGE = "target_language"
    TARGET_LANGUAGE_ALONE = "target_language_alone"
    LOW_CONFIDENCE = "low_confidence"


# --- числительные -----------------------------------------------------------------

#: Префикс спецификатора формата, включающий согласование числа с существительным:
#: `{days:plural:day}` -> «14 дней» / «14 days».
PLURAL_SPEC = "plural:"


def _plural_index_ru(n: int) -> int:
    """Три формы русского: 1 день / 2 дня / 5 дней. РАСЧЁТ по правилу CLDR для ru
    (one / few / many); проверено на 0..120 в негативном контроле."""
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
        "day": ("день", "дня", "дней"),
        "person": ("человек", "человека", "человек"),
        "service": ("услуга", "услуги", "услуг"),
    },
    Language.EN: {
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
    """Формат причин: обычные спецификаторы плюс `plural:<единица>`.

    Отсутствующий и лишний параметр — явные ошибки (негативный контроль модуля).
    """

    def __init__(self, language: Language, code: ReasonCode) -> None:
        self.language = language
        self.code = code

    def get_value(self, key, args, kwargs):
        try:
            return super().get_value(key, args, kwargs)
        except (KeyError, IndexError) as exc:
            raise ReasonRenderError(
                f"причина {self.code.value}: не передан параметр {key!r} "
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
                f"причина {self.code.value}: параметры не используются текстом "
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
    ReasonCode.URGENT_TIMELINE: ReasonSpec(
        params=("days", "limit"),
        texts={
            Language.RU: "срок {days:plural:day} — не больше {limit:plural:day}",
            Language.EN: "needed in {days:plural:day} — urgency window is "
                         "{limit:plural:day}",
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
}


def validate_catalogue(catalogue: Mapping[ReasonCode, ReasonSpec] = CATALOGUE) -> int:
    """Проверить каталог целиком; вернуть число проверенных пар «код-язык».

    Аргумент нужен ради негативного контроля: подсунуть каталог с дырой и увидеть, что
    прибор говорит «нет» (И5). Возвращаемое число — «проверено N» (Р2): ноль нарушений
    при нуле проверок успехом не считается.
    """
    checked = 0
    missing_codes = sorted(set(ReasonCode) - set(catalogue), key=lambda c: c.value)
    if missing_codes:
        raise ReasonCatalogueError(
            "в каталоге нет причин: " + ", ".join(c.value for c in missing_codes)
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
                    f"причина {code.value}: нет текста на языке {language.value}"
                )
            used = _placeholders(template)
            if used != declared:
                raise ReasonCatalogueError(
                    f"причина {code.value}, язык {language.value}: текст подставляет "
                    f"{sorted(used)}, объявлено {sorted(declared)}"
                )
            unknown = _plural_nouns(template) - set(PLURAL_FORMS[language])
            if unknown:
                raise ReasonCatalogueError(
                    f"причина {code.value}, язык {language.value}: нет форм для "
                    f"{sorted(unknown)}"
                )
            checked += 1
    return checked


# --- сама причина -----------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Reason:
    """Причина приоритета: код плюс параметры. Текста внутри нет — он в каталоге.

    Параметры хранятся кортежем пар, а не словарём: причина остаётся хешируемой и
    сравнимой, а значит её можно класть в множество и сравнивать в тесте литералом.
    Собирать удобнее фабрикой `reason(code, **params)`.
    """

    code: ReasonCode
    params: tuple[tuple[str, object], ...] = field(default=())

    def __post_init__(self) -> None:
        spec = CATALOGUE.get(self.code)
        if spec is None:
            raise ReasonRenderError(f"неизвестный код причины: {self.code!r}")
        given = {name for name, _ in self.params}
        if len(given) != len(self.params):
            raise ReasonRenderError(f"причина {self.code.value}: параметр задан дважды")
        declared = set(spec.params)
        if given != declared:
            raise ReasonRenderError(
                f"причина {self.code.value}: параметры {sorted(given)}, "
                f"объявлены {sorted(declared)}"
            )

    def values(self) -> dict[str, object]:
        return dict(self.params)

    def text(self, language: Language = DEFAULT_LANGUAGE) -> str:
        """Отрисовка на указанном языке. Удобство поверх `render`."""
        return render(self, language)


def reason(code: ReasonCode, **params: object) -> Reason:
    """Собрать причину: `reason(ReasonCode.URGENT_TIMELINE, days=14, limit=60)`.

    Параметры сверяются с каталогом сразу — код без параметров падает в точке вызова,
    а не через два слоя в интерфейсе.
    """
    return Reason(code, tuple(sorted(params.items())))


def render(item: Reason, language: Language = DEFAULT_LANGUAGE) -> str:
    """Текст одной причины. Любая невозможность — `ReasonRenderError`, не пустая строка."""
    if not isinstance(language, Language):
        raise ReasonRenderError(f"неизвестный язык отрисовки: {language!r}")
    spec = CATALOGUE.get(item.code)
    if spec is None:
        raise ReasonRenderError(f"неизвестный код причины: {item.code!r}")
    template = spec.texts.get(language)
    if not template or not template.strip():
        raise ReasonRenderError(
            f"причина {item.code.value}: нет текста на языке {language.value}"
        )
    return _ReasonFormatter(language, item.code).vformat(template, (), item.values())


def render_all(
    items: tuple[Reason, ...], language: Language = DEFAULT_LANGUAGE
) -> tuple[str, ...]:
    """Отрисовка списка причин — единственный способ получить кортеж строк причин."""
    return tuple(render(item, language) for item in items)


# Каталог проверяется при загрузке модуля: недопереведённая причина роняет импорт,
# а не показывается пустым местом в карточке (Ц7 — гейт, а не договорённость).
validate_catalogue()
