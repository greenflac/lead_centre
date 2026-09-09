"""Точка входа тонкая: вся логика в функциях, чтобы её мог вызвать тест (Т5)."""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime

from leadcentre import report
from leadcentre.engine.score import score
from leadcentre.sources.gleif import GleifAdapter


def discover(mode: str, limit: int, today: date | None = None) -> report.RunReport:
    today = today or datetime.now(UTC).date()
    adapter = GleifAdapter(mode=mode)
    result = adapter.fetch(limit)
    scores = [score(c, today) for c in result.companies]
    for company, company_score in zip(result.companies, scores, strict=True):
        print(report.format_row(company, company_score))
    run_report = report.build(result, scores)
    print()
    for line in run_report.lines():
        print(line)
    return run_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leadcentre")
    parser.add_argument("command", choices=["discover", "fetch"])
    parser.add_argument("--mode", default="lapsed", choices=["lapsed", "fresh"])
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args(argv)
    if args.command == "discover":
        discover(args.mode, args.limit)
    else:
        result = GleifAdapter(mode=args.mode, offline=False).fetch(args.limit)
        print(result.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
