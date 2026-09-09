"""Источник — сменный адаптер. GLEIF в демо, платный провайдер или выгрузка клиента —
такая же реализация протокола; движок про источник ничего не знает."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from leadcentre.models import Company


@dataclass(frozen=True)
class FetchResult:
    """Числами, а не флагом (Р2/Е3): сколько получили, сколько пропустили и почему."""

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
