"""Выбор CRM-приёмника. По умолчанию — NullSink: чужая CRM не наполняется демо-прогонами.

`CRM_SINK=hubspot` включает HubSpot; неизвестное имя — ошибка, а не тихий откат на Null
(тихий откат неотличим от «отправили, но не дошло»).
"""
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
    "CrmConfigError",
    "REJECTED",
    "SENT",
    "SKIPPED",
    "UNAVAILABLE",
    "CrmLead",
    "CrmResult",
    "CrmSink",
    "HubspotSink",
    "NullSink",
    "get_sink",
]

SINKS = {"null": NullSink, "hubspot": HubspotSink}


class CrmConfigError(RuntimeError):
    """Приёмник настроен неизвестным именем — работать вслепую нельзя."""


def get_sink(name: str | None = None) -> CrmSink:
    key = (name or os.environ.get("CRM_SINK") or "null").strip().lower()
    if key not in SINKS:
        raise CrmConfigError(
            f"неизвестный CRM_SINK={key!r}; известны: {', '.join(sorted(SINKS))}"
        )
    return SINKS[key]()
