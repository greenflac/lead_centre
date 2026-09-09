"""Приёмник HubSpot: company + contact + deal через CRM API v3/v4, на urllib.

Что создаётся на один одобренный лид:
  1. company  — POST /crm/v3/objects/companies      (name, city, phone, description)
  2. contact  — POST /crm/v3/objects/contacts       (email/phone, если клиент их оставил)
  3. deal     — POST /crm/v3/objects/deals          (dealname, pipeline, dealstage)
  4. связи    — PUT  /crm/v4/objects/{from}/{id}/associations/default/{to}/{id}
Порядок важен: сначала объекты, потом связи; связь на несозданный объект — 404, и
это «не смогли», а не «отказ».

Три исхода (Р1): SENT (создан хотя бы deal или company, id возвращены), REJECTED
(HubSpot отверг данные: 400/409), UNAVAILABLE (401/403/429/5xx/сеть). Частичный успех
печатается числами и списком созданных id (Е3), а не булевым флагом: если company
создалась, а deal нет, «false» скрыл бы уже созданную запись, и следующий прогон
сделал бы дубль.

Токен — `HUBSPOT_PERSONAL_KEY` (private app token, заголовок `Authorization: Bearer`).
По умолчанию приёмник НЕ включён: `get_sink()` отдаёт NullSink, пока не сказано иное
переменной `CRM_SINK=hubspot`. Чужая CRM не должна наполняться демо-прогонами.
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
TIMEOUT_S = 20.0            # ВЫБРАНО: вызывается из обработчика HTTP, как и Supabase
USER_AGENT = "leadcentre/0.1 (+SORP Lead Centre)"

# Воронка и стадия. ВЫБРАНО: `default`/`appointmentscheduled` — стандартные для нового
# портала HubSpot; на портале с настроенной воронкой задаются HUBSPOT_PIPELINE/STAGE.
DEFAULT_PIPELINE = "default"
DEFAULT_DEALSTAGE = "appointmentscheduled"

# Типы объектов в путях v4-ассоциаций.
OBJ_COMPANIES = "companies"
OBJ_CONTACTS = "contacts"
OBJ_DEALS = "deals"


class HubspotSink:
    """CRM-приёмник HubSpot.

    Что ПРОВЕРЕНО прогоном 2026-09-09 (портал 247332153, токен `HUBSPOT_PERSONAL_KEY`):
      * `GET /crm/v3/objects/companies` → 200; токен рабочий, а не «401 EXPIRED», как
        значилось в постановке задачи. Верим свидетельству, а не флагу (Е2);
      * `POST /crm/v3/objects/companies` → создал компанию (id 345429228222); запись
        удалена сразу же (`DELETE` → 204, повторный `GET` → 404), портал чист. Создана
        она была случайно — тестом, подставившим ключ из среды под пустой токен;
      * `GET /crm/v3/objects/deals` → 403 MISSING_SCOPES: `crm.objects.deals.*` этому
        приложению не выданы. Значит шаг с deal на этом токене вернёт «не смогли»
        (403 → UNAVAILABLE), а company всё равно создастся — ровно тот частичный
        результат, ради которого исход печатается числами и списком id, а не флагом.
    НЕ ПРОВЕРЕНО (Ц4): contacts, deals и ассоциации v4 — прав на них у токена нет,
    пути и поля взяты из документации HubSpot CRM API v3/v4, а не с прогона.
    """

    name = "hubspot"

    def __init__(self, token: str | None = None) -> None:
        # `token=""` — это «токена нет», а не «возьми из среды»: подстановка ключа из
        # среды под пустой аргумент однажды создала настоящую запись в чужой CRM из теста.
        self.token = os.environ.get("HUBSPOT_PERSONAL_KEY", "") if token is None else token
        self.pipeline = os.environ.get("HUBSPOT_PIPELINE") or DEFAULT_PIPELINE
        self.dealstage = os.environ.get("HUBSPOT_DEALSTAGE") or DEFAULT_DEALSTAGE

    # --- транспорт ---

    def _call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """Возвращает (код, тело). Исход решает вызывающий: код здесь не интерпретируется."""
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
            except Exception:  # noqa: BLE001 — тело ошибки бывает и не JSON
                payload = {"message": str(exc.reason)}
            return exc.code, payload
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return 0, {"message": f"сеть недоступна: {exc}"}

    @staticmethod
    def _outcome_for(status: int) -> str:
        """Код HTTP → исход. 401/403/429/5xx и 0 (сеть) — «не смогли», а не отказ."""
        if 200 <= status < 300:
            return SENT
        if status in (401, 403, 429) or status >= 500 or status == 0:
            return UNAVAILABLE
        return REJECTED

    # --- тела запросов (отдельно от отправки: тест видит ровно то, что уйдёт, И5) ---

    def company_properties(self, lead: CrmLead) -> dict[str, str]:
        return {
            "name": lead.company_name,
            "city": str(lead.facts.get("jurisdiction_hint") or "Dubai"),
            "description": lead.raw_text[:1000],
            "hs_lead_status": "NEW",
        }

    def contact_properties(self, lead: CrmLead) -> dict[str, str] | None:
        """Контакт создаётся, только если клиент оставил связь. Пустой контакт HubSpot
        отвергает (нужен email или телефон), и выдумывать их нельзя."""
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

    # --- отправка ---

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
                # «Не смогли» перекрывает «отказ»: чинятся они по-разному.
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

        # Ни одного созданного объекта — это не успех, чем бы ни закончились шаги.
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
