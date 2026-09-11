"""CRM as a swappable sink; the engine knows nothing about any particular CRM.

Outcomes are SENT, REJECTED, UNAVAILABLE and SKIPPED: "did not reach the CRM" and
"unknown whether it did" have different remedies and must not merge into False.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

SENT = "sent"
REJECTED = "rejected"
UNAVAILABLE = "unavailable"
SKIPPED = "skipped"
OUTCOMES = (SENT, REJECTED, UNAVAILABLE, SKIPPED)


@dataclass(frozen=True)
class CrmLead:
    """What goes to the CRM; `raw_text` is already scrubbed of personal data."""

    lead_id: str
    company_name: str
    tier: str
    channel: str
    language: str
    raw_text: str
    reasons: tuple[str, ...] = ()
    facts: dict[str, Any] = field(default_factory=dict)
    contact_email: str | None = None
    contact_phone: str | None = None


@dataclass(frozen=True)
class CrmResult:
    """Send outcome; `objects` lists what was actually created, not what was intended."""

    sink: str
    outcome: str
    detail: str = ""
    objects: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome == SENT

    def summary(self) -> str:
        ids = ", ".join(f"{k}={v}" for k, v in self.objects.items()) or "объектов нет"
        return f"CRM {self.sink}: {self.outcome} ({ids}) {self.detail}".strip()


class CrmSink(Protocol):
    name: str

    def send(self, lead: CrmLead) -> CrmResult: ...
