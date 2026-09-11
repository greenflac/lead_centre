"""Single place that chooses a CRM sink; the default sends nothing, and an unknown name
raises, since a silent fallback looks exactly like "sent, but never arrived"."""
from __future__ import annotations

import os

from leadcentre.crm.base import (
    OUTCOMES,
    REJECTED,
    SENT,
    SKIPPED,
    UNAVAILABLE,
    CrmLead,
    CrmResult,
    CrmSink,
)
from leadcentre.crm.hubspot import HubspotSink
from leadcentre.crm.null import NullSink

__all__ = [
    "OUTCOMES",
    "REJECTED",
    "SENT",
    "SKIPPED",
    "UNAVAILABLE",
    "CrmConfigError",
    "CrmLead",
    "CrmResult",
    "CrmSink",
    "HubspotSink",
    "NullSink",
    "get_sink",
]

SINKS = {"null": NullSink, "hubspot": HubspotSink}


class CrmConfigError(RuntimeError):
    """The sink was configured with an unknown name."""


def get_sink(name: str | None = None) -> CrmSink:
    key = (name or os.environ.get("CRM_SINK") or "null").strip().lower()
    if key not in SINKS:
        raise CrmConfigError(
            f"неизвестный CRM_SINK={key!r}; известны: {', '.join(sorted(SINKS))}"
        )
    return SINKS[key]()
