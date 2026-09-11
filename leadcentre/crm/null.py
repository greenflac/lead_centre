"""Default sink: logs and sends nothing. A working mode, not a temporary stub — the
SKIPPED outcome says plainly that nothing was sent."""
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
