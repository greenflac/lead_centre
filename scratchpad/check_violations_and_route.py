"""Приёмочный прибор: каталоги нарушений и причин маршрута.

Печатает таблицу «код — русский — английский», прогон карточки с нарушением и обе
причины маршрута на двух языках, затем негативные контроли (И5). Считает и печатает
«проверено N, не смогли K» (Р2): ноль провалов при нуле контролей — не успех.
"""
import copy
import dataclasses
import json
import os

os.environ["OFFLINE"] = "1"

from leadcentre.engine import extract as X  # noqa: E402
from leadcentre.engine import reasons as R  # noqa: E402
from leadcentre.engine.score import score, score_inbound  # noqa: E402
from leadcentre.models import Company, InboundMessage, LeadFacts, RequestType  # noqa: E402
from datetime import date  # noqa: E402

RU, EN = R.Language.RU, R.Language.EN
SAMPLE = {
    R.ViolationCode.HIGH_WITHOUT_EVIDENCE: {},
    R.ViolationCode.COMPANY_OUTSIDE_UAE: {"country": "GB"},
    R.ViolationCode.HIGH_WITHOUT_QUOTE: {},
    R.ViolationCode.HIGH_ON_EMPTY_TEXT: {},
    R.RouteReasonCode.LONG_MESSAGE: {"length": 1332, "limit": 600},
    R.RouteReasonCode.SHORT_MESSAGE: {"length": 91, "limit": 600},
    R.RouteReasonCode.MODEL_FORCED: {},
    R.RouteReasonCode.OFFLINE_NO_CALL: {},
}

print("== проверка каталогов при импорте ==")
print(R.validate_all_catalogues())

print("\n== таблица «код — русский — английский» ==")
missing = [c for c in (*R.ViolationCode, *R.RouteReasonCode) if c not in SAMPLE]
assert not missing, f"код без показательных параметров: {missing}"
for code, params in SAMPLE.items():
    kind = R._kind(code)
    item = R.Phrase(code, tuple(sorted(params.items())))
    print(f"{kind:16} {code.value:22} | {R.render(item, RU):46} | {R.render(item, EN)}")

print("\n== согласование числительных на краях (И5/Т3) ==")
for length in (1, 2, 5, 11, 21, 91, 601, 1332):
    item = R.route_reason(R.RouteReasonCode.SHORT_MESSAGE, length=length, limit=600)
    print(f"{length:5} -> {R.render(item, RU):40} | {R.render(item, EN)}")

print("\n== прогон: карточка компании с нарушением ==")
company = Company(
    source="gleif", external_id="LEI-TEST", name="Off-shore Ltd",
    country="GB", city="London", address_lines=("Some Tower",), license_no="",
    registrar_id="", created_on=date(2026, 8, 20), entity_active=True,
    registration_status="ISSUED", next_renewal_on=date(2026, 12, 1),
)
card = score(company, date(2026, 9, 10))
print("tier:", card.tier.value)
print("нарушения RU:", card.violations)
print("нарушения EN:", R.texts_in(card.violations, EN))
print("причины  RU:", card.reasons_in(RU))
print("причины  EN:", card.reasons_in(EN))

print("\n== прогон: обращение, HIGH без цитаты ==")
facts = LeadFacts(
    request_types=(RequestType.SETUP, RequestType.BANK), timeline_days=3, headcount=12,
    budget_hint="AED 30 000", language="ru", confidence=0.9, quotes=(),
)
inbound = score_inbound(
    InboundMessage("m-1", "telegram", "нужен офис и банк", date(2026, 9, 10)), facts)
print("tier:", inbound.tier.value)
print("нарушения RU:", inbound.violations)
print("нарушения EN:", R.texts_in(inbound.violations, EN))

print("\n== прогон: обе причины маршрута через route()/extract_detailed ==")
for text, label in ((("а" * 1332), "длинное"), (("а" * 91), "короткое")):
    chosen = X.route(InboundMessage("m", "telegram", text, date(2026, 9, 10)))
    print(f"{label:9} model={chosen.model:26} RU={chosen.reason!s:40} "
          f"EN={chosen.reason.text(EN)}")
offline = X.extract_detailed(
    InboundMessage("m", "telegram", "нужен офис", date(2026, 9, 10)))
print("offline  RU=", offline.route_reason_in(RU), "| EN=", offline.route_reason_in(EN))
print("served_by:", offline.served_by())

print("\n== строка остаётся строкой: json, copy, dataclasses.asdict ==")
print(json.dumps({"violations": list(card.violations)}, ensure_ascii=False))
assert card.violations == ("компания вне ОАЭ: GB",), card.violations
copied = copy.deepcopy(card.violations)
print("после deepcopy EN:", R.texts_in(copied, EN))
print("asdict:", dataclasses.asdict(card)["violations"])

# --- негативные контроли -----------------------------------------------------------
print("\n== негативные контроли ==")
checked = failed = 0


def control(name, expected, call):
    global checked, failed
    checked += 1
    try:
        got = call()
    except expected as exc:
        print(f"ГОДНО   {name}: {type(exc).__name__}: {exc}")
        return
    except Exception as exc:  # не тот тип ошибки — тоже провал контроля
        failed += 1
        print(f"ПРОВАЛ  {name}: ожидали {expected.__name__}, получили {type(exc).__name__}: {exc}")
        return
    failed += 1
    print(f"ПРОВАЛ  {name}: ошибки не было, вернулось {got!r}")


def positive(name, call):
    global checked, failed
    checked += 1
    try:
        got = call()
    except Exception as exc:
        failed += 1
        print(f"ПРОВАЛ  {name}: годный вход упал {type(exc).__name__}: {exc}")
        return
    print(f"ГОДНО   {name}: {got!r}")


control("нарушение без параметра country", R.ReasonRenderError,
        lambda: R.violation(R.ViolationCode.COMPANY_OUTSIDE_UAE))
control("маршрут без параметра limit", R.ReasonRenderError,
        lambda: R.route_reason(R.RouteReasonCode.LONG_MESSAGE, length=700))
control("лишний параметр у нарушения", R.ReasonRenderError,
        lambda: R.violation(R.ViolationCode.HIGH_WITHOUT_QUOTE, country="AE"))
control("дробная длина в маршруте", R.ReasonRenderError,
        lambda: R.render(R.route_reason(R.RouteReasonCode.LONG_MESSAGE,
                                        length=700.0, limit=600), RU))
control("код чужого перечисления в фабрике нарушений", R.ReasonRenderError,
        lambda: R.Phrase("high_without_quote"))


def holed(catalogue, code, spec):
    copy_ = dict(catalogue)
    copy_[code] = spec
    return copy_


control("у нарушения пропал английский текст", R.ReasonCatalogueError,
        lambda: R.validate_catalogue(holed(
            R.VIOLATION_CATALOGUE, R.ViolationCode.COMPANY_OUTSIDE_UAE,
            R.ReasonSpec(params=("country",), texts={RU: "компания вне ОАЭ: {country}"})),
            "нарушение"))
control("у причины маршрута английский текст из пробелов", R.ReasonCatalogueError,
        lambda: R.validate_catalogue(holed(
            R.ROUTE_REASON_CATALOGUE, R.RouteReasonCode.MODEL_FORCED,
            R.ReasonSpec(params=(), texts={RU: "LLM_MODEL задан вручную", EN: "   "})),
            "причина маршрута"))
control("английский текст маршрута потерял {limit}", R.ReasonCatalogueError,
        lambda: R.validate_catalogue(holed(
            R.ROUTE_REASON_CATALOGUE, R.RouteReasonCode.LONG_MESSAGE,
            R.ReasonSpec(params=("length", "limit"), texts={
                RU: "длинное обращение: {length:plural:char} > {limit}",
                EN: "long request: {length:plural:char} is too long"})),
            "причина маршрута"))
control("русский текст нарушения потерял {country}", R.ReasonCatalogueError,
        lambda: R.validate_catalogue(holed(
            R.VIOLATION_CATALOGUE, R.ViolationCode.COMPANY_OUTSIDE_UAE,
            R.ReasonSpec(params=("country",), texts={
                RU: "компания вне ОАЭ", EN: "company outside the UAE: {country}"})),
            "нарушение"))
control("кода нет в каталоге нарушений", R.ReasonCatalogueError,
        lambda: R.validate_catalogue(
            {c: s for c, s in R.VIOLATION_CATALOGUE.items()
             if c is not R.ViolationCode.HIGH_ON_EMPTY_TEXT}, "нарушение"))
control("пустой каталог — не «ноль нарушений»", R.ReasonCatalogueError,
        lambda: R.validate_catalogue({}, "нарушение"))
control("единица без форм в тексте маршрута", R.ReasonCatalogueError,
        lambda: R.validate_catalogue(holed(
            R.ROUTE_REASON_CATALOGUE, R.RouteReasonCode.LONG_MESSAGE,
            R.ReasonSpec(params=("length", "limit"), texts={
                RU: "длинное обращение: {length:plural:word} > {limit}",
                EN: "long request: {length:plural:word} > {limit}"})),
            "причина маршрута"))
control("строка из хранилища без кода — на английский не отрисовать", R.ReasonRenderError,
        lambda: R.text_in("HIGH без цитаты из обращения", EN))

positive("годные каталоги продукта", R.validate_all_catalogues)
positive("годное нарушение отрисовывается",
         lambda: R.violation(R.ViolationCode.COMPANY_OUTSIDE_UAE, country="GB").text(EN))
positive("годная причина маршрута отрисовывается",
         lambda: R.route_reason(R.RouteReasonCode.LONG_MESSAGE,
                                length=1332, limit=600).text(EN))
positive("пустая причина маршрута — пусто, а не ошибка", lambda: R.text_in("", EN))

print(f"\nпроверено {checked}, провалов {failed}, не смогли проверить 0")
raise SystemExit(1 if failed else 0)
