"""HubSpot sink: company, contact and deal over the CRM API, on urllib.

Objects are created first and associated afterwards. Partial success is reported as
counts plus created ids, never a boolean, or the next run would duplicate. Disabled by
default, so demo runs cannot fill somebody else's CRM.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from leadcentre.crm.base import (
    REJECTED,
    SENT,
    UNAVAILABLE,
    CrmLead,
    CrmResult,
)

API = "https://api.hubapi.com"
TIMEOUT_S = 20.0            # Why short: this runs inside a request handler.
USER_AGENT = "leadcentre/0.1 (+Lead Centre)"

DEFAULT_PIPELINE = "default"
DEFAULT_DEALSTAGE = "appointmentscheduled"

OBJ_COMPANIES = "companies"
OBJ_CONTACTS = "contacts"
OBJ_DEALS = "deals"


class HubspotSink:
    """HubSpot CRM sink.

    Company reads and writes are verified against the live API; contacts, deals and v4
    associations follow the documentation and are UNVERIFIED against a live portal.
    """

    name = "hubspot"

    def __init__(self, token: str | None = None) -> None:
        # Why "" is not treated as unset: a fallback here once wrote to a real CRM from a test.
        self.token = os.environ.get("HUBSPOT_PERSONAL_KEY", "") if token is None else token
        self.pipeline = os.environ.get("HUBSPOT_PIPELINE") or DEFAULT_PIPELINE
        self.dealstage = os.environ.get("HUBSPOT_DEALSTAGE") or DEFAULT_DEALSTAGE

    def _call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """Returns (status, body); the caller decides the outcome."""
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{API}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                raw = response.read().decode("utf-8") or "{}"
                return response.status, json.loads(raw or "{}")
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:  # noqa: BLE001 - an error body is not always JSON
                payload = {"message": str(exc.reason)}
            return exc.code, payload
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return 0, {"message": f"сеть недоступна: {exc}"}

    @staticmethod
    def _outcome_for(status: int) -> str:
        """Maps an HTTP status onto an outcome; auth, rate and 5xx are "could not"."""
        if 200 <= status < 300:
            return SENT
        if status in (401, 403, 429) or status >= 500 or status == 0:
            return UNAVAILABLE
        return REJECTED


    def company_properties(self, lead: CrmLead) -> dict[str, str]:
        return {
            "name": lead.company_name,
            "city": str(lead.facts.get("jurisdiction_hint") or "Dubai"),
            "description": lead.raw_text[:1000],
            "hs_lead_status": "NEW",
        }

    def contact_properties(self, lead: CrmLead) -> dict[str, str] | None:
        """Builds the contact body, or None when the client left no email or phone."""
        properties: dict[str, str] = {}
        if lead.contact_email:
            properties["email"] = lead.contact_email
        if lead.contact_phone:
            properties["phone"] = lead.contact_phone
        if not properties:
            return None
        properties["company"] = lead.company_name
        properties["hs_language"] = lead.language
        return properties

    def deal_properties(self, lead: CrmLead) -> dict[str, str]:
        return {
            "dealname": f"[{lead.tier}] {lead.company_name}",
            "pipeline": self.pipeline,
            "dealstage": self.dealstage,
            "description": "; ".join(lead.reasons)[:1000],
        }

    def send(self, lead: CrmLead) -> CrmResult:
        if not self.token:
            return CrmResult(
                sink=self.name,
                outcome=UNAVAILABLE,
                detail="HUBSPOT_PERSONAL_KEY не задан — отправка не выполнялась",
            )

        objects: dict[str, str] = {}
        problems: list[str] = []
        worst = SENT

        def step(kind: str, path: str, body: dict) -> str | None:
            nonlocal worst
            status, payload = self._call("POST", path, body)
            outcome = self._outcome_for(status)
            if outcome != SENT:
                problems.append(f"{kind}: HTTP {status} {payload.get('message', payload)}")
                # "Could not" outranks "rejected": the remedies differ.
                worst = UNAVAILABLE if UNAVAILABLE in (worst, outcome) else REJECTED
                return None
            object_id = str(payload.get("id") or "")
            if object_id:
                objects[kind] = object_id
            return object_id or None

        company_id = step(
            OBJ_COMPANIES, "/crm/v3/objects/companies",
            {"properties": self.company_properties(lead)},
        )
        contact_properties = self.contact_properties(lead)
        contact_id = (
            step(OBJ_CONTACTS, "/crm/v3/objects/contacts", {"properties": contact_properties})
            if contact_properties
            else None
        )
        deal_id = step(
            OBJ_DEALS, "/crm/v3/objects/deals", {"properties": self.deal_properties(lead)}
        )

        for from_type, from_id, to_type, to_id in (
            (OBJ_DEALS, deal_id, OBJ_COMPANIES, company_id),
            (OBJ_DEALS, deal_id, OBJ_CONTACTS, contact_id),
            (OBJ_CONTACTS, contact_id, OBJ_COMPANIES, company_id),
        ):
            if not from_id or not to_id:
                continue
            status, payload = self._call(
                "PUT",
                f"/crm/v4/objects/{from_type}/{from_id}/associations/default/{to_type}/{to_id}",
                None,
            )
            if self._outcome_for(status) != SENT:
                problems.append(
                    f"связь {from_type}->{to_type}: HTTP {status} {payload.get('message', payload)}"
                )

        # No object created is not success, whatever the individual steps returned.
        if not objects:
            return CrmResult(
                sink=self.name,
                outcome=worst if worst != SENT else UNAVAILABLE,
                detail="; ".join(problems) or "HubSpot не создал ни одного объекта",
            )
        return CrmResult(
            sink=self.name,
            outcome=SENT if not problems else worst,
            detail="; ".join(problems),
            objects=objects,
        )
