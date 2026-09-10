"""CRM — сменный приёмник, как источник и хранилище. Движок про CRM ничего не знает.

Три исхода вместо двух, и здесь это не формальность: «лид не уехал в HubSpot»
и «мы не знаем, уехал ли» лечатся по-разному, а склеенные в `False` они одинаково
выглядят в логе.
  * `SENT` — CRM подтвердила приём и вернула идентификаторы;
  * `REJECTED` — CRM отвергла данные (4xx по существу: нет обязательного поля, дубль);
  * `UNAVAILABLE` — «не смогли»: сеть, таймаут, 401/403, 429, 5xx.
`NullSink` возвращает четвёртый, честный вариант — `SKIPPED`: он ничего не отправлял,
и выдавать это за успех нельзя (вердикт выводится из того, что исполнилось).
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
    """То, что уходит в CRM. Тексту обращения тут место, персональным данным — нет:
    в `raw_text` кладётся уже вычищенный `scrub_pii` текст (см. api.py)."""

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
    """Исход отправки. `objects` — то, что реально создано, а не то, что задумывалось."""

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
