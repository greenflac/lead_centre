"""Тесты отчёта и точки входа. Логика вызывается функцией, не подпроцессом (Т5)."""
from __future__ import annotations

import urllib.request
from datetime import date

import pytest

from leadcentre import report
from leadcentre.cli import discover, main
from leadcentre.engine.score import score
from leadcentre.models import AddressType, Event, Evidence, Score, Tier
from leadcentre.sources.base import FetchResult
from leadcentre.sources.gleif import GleifAdapter
from tests.conftest import ADDR_REGISTRAR, TODAY, lapsed_company, make_company


def _result(skipped: int = 2, from_cache: bool = True) -> FetchResult:
    return FetchResult(
        companies=(), fetched=10, skipped=skipped, source="gleif:lapsed", from_cache=from_cache
    )


def test_build_counts_every_tier_including_invalid():
    scores = [
        Score(Tier.HIGH, AddressType.REGISTRAR, Event.LAPSED),
        Score(Tier.HIGH, AddressType.REGISTRAR, Event.NEW_ENTITY),
        Score(Tier.MEDIUM, AddressType.OWN, Event.LAPSED),
        Score(Tier.LOW, AddressType.OWN, Event.NONE),
        Score(Tier.INVALID, AddressType.OWN, Event.NONE, violations=("a", "b")),
    ]
    built = report.build(_result(), scores)
    assert built.checked == 5
    assert built.by_tier == {"HIGH": 2, "MEDIUM": 1, "LOW": 1, "INVALID": 1}
    assert built.violations == 2
    assert built.skipped == 2
    assert built.source == "gleif:lapsed [кэш]"


def test_build_marks_network_origin():
    assert report.build(_result(from_cache=False), []).source == "gleif:lapsed [сеть]"


def test_lines_print_numbers_not_a_flag():
    scores = [Score(Tier.INVALID, AddressType.OWN, Event.NONE, violations=("x",))]
    lines = report.build(_result(skipped=3), scores).lines()
    assert lines[0] == "источник: gleif:lapsed [кэш]"
    assert lines[1] == "проверено 1, HIGH: 0 / MEDIUM: 0 / LOW: 0"
    assert lines[2] == (
        "не смогли оценить (INVALID) 1, нарушений инвариантов 1, пропущено записей 3"
    )


def test_empty_run_is_not_a_success():
    """Ноль нарушений при нуле проверок — не успех: числа должны это показывать (Р2)."""
    lines = report.build(_result(skipped=0), []).lines()
    assert "проверено 0" in lines[1]
    assert "нарушений инвариантов 0" in lines[2]


def test_format_row_contains_tier_city_name_and_evidence():
    company = make_company(city="Dubai", name="Test Trading LLC")
    row = report.format_row(
        company,
        Score(
            Tier.HIGH,
            AddressType.REGISTRAR,
            Event.LAPSED,
            reasons=("регистрация LEI просрочена 10 дн.",),
            evidence=(Evidence("lei", "TESTLEI0000000000001"), Evidence("license_no", "LIC-1")),
        ),
    )
    assert row.startswith("HIGH   ")
    assert "Dubai" in row
    assert "Test Trading LLC" in row
    assert "регистрация LEI просрочена 10 дн." in row
    assert row.endswith("lei=TESTLEI0000000000001, license_no=LIC-1")


def test_format_row_shows_invalid_tier():
    company = make_company()
    row = report.format_row(company, Score(Tier.INVALID, AddressType.OWN, Event.NONE))
    assert row.startswith("INVALID")


# --- cli.discover ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")


def test_discover_offline_scores_the_whole_cache(capsys):
    run_report = discover("lapsed", 60, today=date(2026, 9, 9))
    assert run_report.checked == 60
    assert run_report.skipped == 0
    assert run_report.source == "gleif:lapsed [кэш]"
    assert sum(run_report.by_tier.values()) == 60
    out = capsys.readouterr().out
    assert "проверено 60" in out
    assert len(out.strip().splitlines()) == 60 + 1 + 3  # строки лидов, пустая, три итога


def test_discover_offline_does_not_touch_the_network(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise AssertionError("cli пошёл в сеть при OFFLINE=1")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert discover("fresh", 5, today=date(2026, 9, 9)).checked == 5
    capsys.readouterr()


def test_discover_is_deterministic_for_a_fixed_today(capsys):
    first = discover("lapsed", 20, today=date(2026, 9, 9))
    second = discover("lapsed", 20, today=date(2026, 9, 9))
    capsys.readouterr()
    assert first == second


def test_discover_tier_mix_matches_scoring_the_same_companies(capsys):
    """Отчёт не пересчитывает по-своему: те же компании — те же ступени."""
    today = date(2026, 9, 9)
    companies = GleifAdapter(mode="lapsed", offline=True).fetch(20).companies
    expected: dict[str, int] = {}
    for company in companies:
        key = score(company, today).tier.value
        expected[key] = expected.get(key, 0) + 1
    run_report = discover("lapsed", 20, today=today)
    capsys.readouterr()
    assert run_report.by_tier == expected


def test_discover_fresh_mode_finds_new_entities(capsys):
    """Свежий срез на дату кэша даёт хотя бы один повод «новое юрлицо»."""
    today = date(2026, 9, 9)
    companies = GleifAdapter(mode="fresh", offline=True).fetch(60).companies
    events = [score(c, today).event for c in companies]
    assert Event.NEW_ENTITY in events
    discover("fresh", 60, today=today)
    capsys.readouterr()


def test_main_discover_returns_zero(capsys):
    assert main(["discover", "--mode", "lapsed", "--limit", "3"]) == 0
    out = capsys.readouterr().out
    assert "источник: gleif:lapsed [кэш]" in out


def test_main_rejects_unknown_command(capsys):
    with pytest.raises(SystemExit):
        main(["nonsense"])


def test_scoring_a_known_lapsed_company_end_to_end():
    """Сквозной проход: запись кэша → Company → Score, ожидания литералами."""
    company = GleifAdapter(mode="lapsed", offline=True).fetch(1).companies[0]
    result = score(company, date(2026, 9, 9))
    # адрес Абу-Даби, орган регистрации RA000752 → ось A1; просрочка 1 день → B1
    assert result.address_type is AddressType.REGISTRAR
    assert result.event is Event.LAPSED
    # HIGH понижен до MEDIUM: город не Дубай
    assert result.tier is Tier.MEDIUM
    assert result.violations == ()


def test_synthetic_high_lead_survives_end_to_end():
    company = lapsed_company(1, city="Dubai", address_lines=ADDR_REGISTRAR)
    result = score(company, TODAY)
    assert result.tier is Tier.HIGH
    assert result.evidence[0] == Evidence("lei", "TESTLEI0000000000001")
