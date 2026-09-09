"""Приёмник по умолчанию: пишет в лог и ничего не отправляет.

Не «заглушка на время», а рабочий режим: демо и CI не должны создавать записи в чужой
CRM, а исход `SKIPPED` честно говорит, что отправки не было. Возвращать отсюда `SENT`
было бы ровно тем дефектом, из-за которого написано Е2.
"""
from __future__ import annotations

import logging

from leadcentre.crm.base import SKIPPED, CrmLead, CrmResult

logger = logging.getLogger("leadcentre.crm")


class NullSink:
    name = "null"

    def send(self, lead: CrmLead) -> CrmResult:
        logger.info(
            "CRM отправка пропущена (NullSink): lead=%s tier=%s компания=%r канал=%s",
            lead.lead_id, lead.tier, lead.company_name, lead.channel,
        )
        return CrmResult(
            sink=self.name,
            outcome=SKIPPED,
            detail="NullSink: отправки не было, запись только в лог",
        )
