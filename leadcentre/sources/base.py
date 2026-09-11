"""Company sources as swappable adapters; the engine knows nothing about any one of them."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from leadcentre.models import Company


@dataclass(frozen=True)
class FetchResult:
    """Fetch result in counts: how many arrived, how many were skipped and why."""

    companies: tuple[Company, ...]
    fetched: int
    skipped: int
    source: str
    from_cache: bool

    def summary(self) -> str:
        origin = "кэш" if self.from_cache else "сеть"
        return (
            f"источник {self.source} ({origin}): получено {self.fetched}, "
            f"пропущено {self.skipped}, пригодно {len(self.companies)}"
        )


class SourceAdapter(Protocol):
    name: str

    def fetch(self, limit: int) -> FetchResult: ...
