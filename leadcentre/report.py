"""Отчёт числами, а не флагом: сколько проверено, сколько нарушений, сколько не смогли (Р2)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from leadcentre.models import Score, Tier
from leadcentre.sources.base import FetchResult


@dataclass(frozen=True)
class RunReport:
    checked: int
    by_tier: dict[str, int]
    violations: int
    skipped: int
    source: str

    def lines(self) -> list[str]:
        tiers = " / ".join(f"{t}: {self.by_tier.get(t, 0)}" for t in ("HIGH", "MEDIUM", "LOW"))
        return [
            f"источник: {self.source}",
            f"проверено {self.checked}, {tiers}",
            f"не смогли оценить (INVALID) {self.by_tier.get('INVALID', 0)}, "
            f"нарушений инвариантов {self.violations}, пропущено записей {self.skipped}",
        ]


def build(result: FetchResult, scores: list[Score]) -> RunReport:
    return RunReport(
        checked=len(scores),
        by_tier=dict(Counter(s.tier.value for s in scores)),
        violations=sum(len(s.violations) for s in scores),
        skipped=result.skipped,
        source=result.source + (" [кэш]" if result.from_cache else " [сеть]"),
    )


def format_row(company, score: Score) -> str:
    evidence = ", ".join(f"{e.kind}={e.value}" for e in score.evidence[:2])
    tier = score.tier.value if score.tier is not Tier.INVALID else "INVALID"
    return (
        f"{tier:7} {company.city[:14]:14} {company.name[:36]:36} "
        f"{'; '.join(score.reasons)[:52]:52} {evidence}"
    )
