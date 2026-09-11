"""Single place that chooses a storage implementation.

An unknown value raises rather than falling back: a typo would otherwise produce a run
that reports writing to a database while writing nothing.
"""
from __future__ import annotations

import os

from leadcentre.store.base import (
    REASONS_EMPTY,
    REASONS_INVALID,
    REASONS_NO_CODES,
    REASONS_OK,
    REASONS_OUTCOMES,
    REPLY_APPROVED,
    REPLY_DRAFT,
    REPLY_REJECTED,
    REPLY_STATUSES,
    CompanyRow,
    DisagreementRow,
    LeadCard,
    LeadRow,
    ReplyRow,
    RestoredReasons,
    ScoreRow,
    Store,
    StoreError,
    StoreHealth,
    StoreRejected,
    StoreUnavailable,
    UpsertResult,
    parse_reason_items,
    reason_items_payload,
    restore_reasons,
    restore_score,
)
from leadcentre.store.local import DEFAULT_PATH, LocalStore
from leadcentre.store.supabase import SCHEMA_PATH, SupabaseStore

__all__ = [
    "DEFAULT_PATH",
    "REASONS_EMPTY",
    "REASONS_INVALID",
    "REASONS_NO_CODES",
    "REASONS_OK",
    "REASONS_OUTCOMES",
    "REPLY_APPROVED",
    "REPLY_DRAFT",
    "REPLY_REJECTED",
    "REPLY_STATUSES",
    "SCHEMA_PATH",
    "CompanyRow",
    "DisagreementRow",
    "LeadCard",
    "LeadRow",
    "LocalStore",
    "ReplyRow",
    "RestoredReasons",
    "ScoreRow",
    "Store",
    "StoreError",
    "StoreHealth",
    "StoreRejected",
    "StoreUnavailable",
    "SupabaseStore",
    "UpsertResult",
    "get_store",
    "is_offline",
    "parse_reason_items",
    "reason_items_payload",
    "restore_reasons",
    "restore_score",
]


def is_offline() -> bool:
    """Reports offline mode, using the same switch as the rest of the engine."""
    return os.environ.get("OFFLINE", "") not in ("", "0")


def get_store(kind: str | None = None) -> Store:
    """Returns the store chosen by the environment; an unknown name raises."""
    key = (kind or os.environ.get("LEADCENTRE_STORE") or "").strip().lower()
    if key == "local":
        return LocalStore()
    if key == "supabase":
        return SupabaseStore()
    if key:
        raise StoreError(
            f"неизвестный LEADCENTRE_STORE={key!r}; известны: local, supabase"
        )
    if is_offline() or not os.environ.get("SUPABASE_URL"):
        return LocalStore()
    return SupabaseStore()
