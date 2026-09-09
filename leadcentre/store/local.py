"""Офлайн-реализация хранилища: JSON-файл в `data/` или память.

Зачем: `OFFLINE=1` обязан работать без сети — тесты и CI не ходят наружу (Т4), а демо
не должно останавливаться из-за чужой аварии. Интерфейс тот же `Store`, поэтому переход
на Supabase — смена реализации, а не переписывание API.

Запись атомарна (временный файл плюс `os.replace`): оборванный процесс не оставляет
наполовину записанный JSON, который потом читается как «данных нет».
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from leadcentre.store.base import (
    REPLY_STATUSES,
    CompanyRow,
    DisagreementRow,
    LeadCard,
    LeadRow,
    ReplyRow,
    ScoreRow,
    StoreHealth,
    StoreRejected,
    StoreUnavailable,
    UpsertResult,
)

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "store_offline.json"
COLLECTIONS = ("leads", "scores", "replies", "companies", "disagreements")


def _empty() -> dict[str, list[dict[str, Any]]]:
    return {name: [] for name in COLLECTIONS}


class LocalStore:
    """JSON-файл или память. `path=None` — только память (для тестов)."""

    name = "local"

    def __init__(self, path: Path | str | None = DEFAULT_PATH) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._data = _empty()
        if self.path is not None:
            self._load()

    # --- файл ---

    def _load(self) -> None:
        assert self.path is not None
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # «Не смогли прочитать» — не то же самое, что «пусто» (Р1): пустой словарь
            # здесь молча стёр бы накопленные обращения.
            raise StoreUnavailable(f"локальное хранилище не прочитано: {self.path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise StoreUnavailable(f"локальное хранилище не словарь: {self.path}")
        data = _empty()
        for name in COLLECTIONS:
            rows = raw.get(name) or []
            if not isinstance(rows, list):
                raise StoreUnavailable(f"раздел {name} в {self.path} не список")
            data[name] = rows
        self._data = data

    def _flush(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, self.path)
        except OSError as exc:
            raise StoreUnavailable(f"локальное хранилище не записано: {self.path}: {exc}") from exc

    # --- контракт Store ---

    def health(self) -> StoreHealth:
        where = str(self.path) if self.path else "память"
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return StoreHealth(self.name, StoreHealth.UNAVAILABLE, f"{where}: {exc}")
        return StoreHealth(self.name, StoreHealth.OK, f"офлайн-хранилище: {where}")

    def save_lead(self, row: LeadRow) -> str:
        lead_id = row.id or str(uuid.uuid4())
        payload = row.payload()
        payload["id"] = lead_id
        payload["created_at"] = datetime.now(UTC).isoformat()
        with self._lock:
            self._data["leads"].append(payload)
            self._flush()
        return lead_id

    def save_score(self, row: ScoreRow) -> None:
        with self._lock:
            if not self._find("leads", row.lead_id):
                raise StoreRejected(f"оценка на неизвестное обращение {row.lead_id!r}")
            self._data["scores"].append(row.payload())
            self._flush()

    def save_reply(self, row: ReplyRow) -> None:
        payload = row.payload()  # проверка статуса живёт в ReplyRow (Е1)
        with self._lock:
            if not self._find("leads", row.lead_id):
                raise StoreRejected(f"черновик на неизвестное обращение {row.lead_id!r}")
            self._data["replies"].append(payload)
            self._flush()

    def set_reply_status(self, lead_id: str, status: str, decided_at: datetime) -> bool:
        if status not in REPLY_STATUSES:
            raise StoreRejected(f"недопустимый статус {status!r}")
        with self._lock:
            rows = [r for r in self._data["replies"] if r.get("lead_id") == lead_id]
            if not rows:
                return False
            for row in rows:
                row["status"] = status
                row["decided_at"] = decided_at.isoformat()
            self._flush()
        return True

    def get_card(self, lead_id: str) -> LeadCard | None:
        with self._lock:
            lead = self._find("leads", lead_id)
            if lead is None:
                return None
            return LeadCard(
                lead=dict(lead),
                score=self._last("scores", lead_id),
                reply=self._last("replies", lead_id),
            )

    def list_cards(self, limit: int = 50) -> list[LeadCard]:
        with self._lock:
            leads = list(self._data["leads"])
            cards = [
                LeadCard(
                    lead=dict(lead),
                    score=self._last("scores", lead.get("id", "")),
                    reply=self._last("replies", lead.get("id", "")),
                )
                for lead in leads
            ]
        return cards[:limit]

    def upsert_companies(self, rows: list[CompanyRow]) -> UpsertResult:
        written = 0
        failed = 0
        with self._lock:
            index = {
                (c.get("source"), c.get("external_id")): c for c in self._data["companies"]
            }
            for row in rows:
                if not row.external_id:
                    failed += 1   # отказ, а не «не смогли»: данные негодные
                    continue
                payload = row.payload()
                key = (row.source, row.external_id)
                if key in index:
                    index[key].update(payload)
                else:
                    self._data["companies"].append(payload)
                    index[key] = payload
                written += 1
            self._flush()
        return UpsertResult(
            requested=len(rows), written=written, failed=failed, unavailable=0, store=self.name
        )

    def list_companies(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(c) for c in self._data["companies"][:limit]]

    def save_disagreement(self, row: DisagreementRow) -> None:
        with self._lock:
            if not self._find("leads", row.lead_id):
                raise StoreRejected(f"несогласие по неизвестному обращению {row.lead_id!r}")
            self._data["disagreements"].append(row.payload())
            self._flush()

    def counters(self) -> dict[str, Any]:
        with self._lock:
            return _counters(
                self._data["leads"],
                self._data["scores"],
                self._data["replies"],
                self._data["companies"],
                self._data["disagreements"],
            )

    # --- вспомогательное ---

    def _find(self, collection: str, lead_id: str) -> dict[str, Any] | None:
        key = "id" if collection == "leads" else "lead_id"
        for row in self._data[collection]:
            if row.get(key) == lead_id:
                return row
        return None

    def _last(self, collection: str, lead_id: str) -> dict[str, Any] | None:
        rows = [r for r in self._data[collection] if r.get("lead_id") == lead_id]
        return dict(rows[-1]) if rows else None


def _counters(
    leads: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    replies: list[dict[str, Any]],
    companies: list[dict[str, Any]],
    disagreements: list[dict[str, Any]],
) -> dict[str, Any]:
    """Числа для отчёта. Общая для обеих реализаций (Е1): считается одинаково.

    `lint`: проверено / нарушений / не смогли — три числа рядом, без агрегатного
    булева (Р2). `lint_ok is None` — это «не смогли», а не «нарушение».
    """
    by_tier = Counter(s.get("tier", "?") for s in scores)
    lint_checked = sum(1 for r in replies if r.get("lint_ok") is not None)
    lint_violations = sum(len(r.get("lint_violations") or []) for r in replies)
    lint_unverifiable = sum(1 for r in replies if r.get("lint_ok") is None)
    return {
        "leads": len(leads),
        "leads_synthetic": sum(1 for lead in leads if lead.get("is_synthetic")),
        "leads_real": sum(1 for lead in leads if not lead.get("is_synthetic")),
        "scores": len(scores),
        "by_tier": {tier: by_tier.get(tier, 0) for tier in sorted(by_tier)},
        "score_violations": sum(len(s.get("violations") or []) for s in scores),
        "replies": len(replies),
        "replies_by_status": dict(Counter(r.get("status", "?") for r in replies)),
        "lint": {
            "checked": lint_checked,
            "violations": lint_violations,
            "unverifiable": lint_unverifiable,
        },
        "companies": len(companies),
        "disagreements": len(disagreements),
    }
