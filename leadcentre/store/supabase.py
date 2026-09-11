"""Хранилище в Supabase через PostgREST (`/rest/v1`), на urllib — без новых зависимостей.

Ключи (проверены в среде): `SUPABASE_URL`, `SUPABASE_SECRET_KEY` (сервер, чтение и запись),
`SUPABASE_PUBLISHABLE_KEY` (только чтение). Заголовки PostgREST: `apikey` и
`Authorization: Bearer`. Секретный ключ обходит RLS, поэтому он живёт только здесь, на
сервере, и никогда не уезжает в браузер.

Исходов три, и это половина смысла модуля:
  * 2xx — успех;
  * 400/409/422 — `StoreRejected`: данные не приняты, чинится кодом;
  * сеть, таймаут, 401/403, 404 «схема не применена», 5xx — `StoreUnavailable`: «не смогли».
Сворачивать третий исход в «нарушений нет» здесь запрещено: PGRST205 («таблицы нет»)
выглядел бы как пустая выборка, и отчёт показал бы ноль лидов вместо «хранилище пустое,
потому что схема не накатана».

Схема лежит рядом файлом `schema.sql`. Через PostgREST DDL не выполняется (проверено:
`POST /rest/v1/rpc/exec_sql` → PGRST202, функции нет), прямого подключения к Postgres в
среде нет — миграцию накатывает владелец проекта, см. докстринг `SCHEMA_PATH`.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
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
    StoreError,
    StoreHealth,
    StoreRejected,
    StoreUnavailable,
    UpsertResult,
)
from leadcentre.store.local import _counters

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
"""SQL-миграция. Накатывается владельцем: Supabase → SQL Editor, либо
`psql "$SUPABASE_DB_URL" -f leadcentre/store/schema.sql`. Ключами из среды DDL сделать
нельзя — у PostgREST нет такого эндпоинта, а строки подключения к Postgres в среде нет."""

TIMEOUT_S = 20.0          # запрос идёт из обработчика; дольше держать клиента нельзя
USER_AGENT = "leadcentre/0.1 (+Lead Centre)"
DEFAULT_LIMIT = 50        # столько строк помещается в один экран инбокса

# Коды PostgREST, означающие «схема не применена». Отделены от прочих 404.
# PGRST204/42703 — «нет такой колонки»: это НЕ отказ по данным, а недокаченная миграция,
# и лечится она средой, а не кодом. Свернуть её в StoreRejected значило бы отправить
# владельца искать баг в payload вместо того, чтобы накатить schema.sql.
SCHEMA_MISSING_CODES = ("PGRST205", "PGRST202", "PGRST204", "42P01", "42703")


class SupabaseStore:
    """PostgREST-реализация `Store`."""

    name = "supabase"

    def __init__(
        self,
        url: str | None = None,
        secret_key: str | None = None,
        read_key: str | None = None,
    ) -> None:
        self.url = (url or os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.secret_key = secret_key or os.environ.get("SUPABASE_SECRET_KEY") or ""
        # Для чтения хватает publishable-ключа; секретный — запасной вариант.
        self.read_key = (
            read_key or os.environ.get("SUPABASE_PUBLISHABLE_KEY") or self.secret_key
        )
        if not self.url or not self.secret_key:
            raise StoreUnavailable(
                "не заданы SUPABASE_URL и/или SUPABASE_SECRET_KEY — "
                "живое хранилище недоступно; для офлайна поставьте OFFLINE=1"
            )

    def _headers(self, key: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        headers.update(extra or {})
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: Any = None,
        prefer: str | None = None,
        write: bool = True,
    ) -> Any:
        """Один запрос к PostgREST. Исходов три, и они различаются здесь, а не у вызывающего."""
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{self.url}/rest/v1/{path}{query}"
        key = self.secret_key if write else self.read_key
        extra = {"Prefer": prefer} if prefer else {}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url, data=data, method=method, headers=self._headers(key, extra)
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                raw = response.read().decode("utf-8") or ""
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc, method, path) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StoreUnavailable(f"{method} {path}: сеть недоступна: {exc}") from exc
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StoreUnavailable(f"{method} {path}: ответ не JSON: {raw[:200]!r}") from exc

    def _http_error(self, exc: urllib.error.HTTPError, method: str, path: str) -> StoreError:
        detail = ""
        code = ""
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            code = str(payload.get("code") or "")
            detail = str(payload.get("message") or payload)
        except Exception:  # noqa: BLE001 — тело ошибки бывает и не JSON
            detail = exc.reason if isinstance(exc.reason, str) else str(exc.reason)
        where = f"{method} {path} → HTTP {exc.code}"
        if code in SCHEMA_MISSING_CODES or (exc.code == 404 and "schema cache" in detail):
            return StoreUnavailable(
                f"{where}: схема не применена ({code or 'PGRST'}): {detail}. "
                f"Накатите {SCHEMA_PATH.name} в SQL Editor проекта Supabase."
            )
        if exc.code in (401, 403):
            return StoreUnavailable(f"{where}: доступ закрыт ключом: {detail}")
        if 400 <= exc.code < 500:
            return StoreRejected(f"{where}: данные не приняты: {detail}")
        return StoreUnavailable(f"{where}: сторона Supabase: {detail}")

    # --- контракт Store ---

    def health(self) -> StoreHealth:
        """Три исхода: таблицы есть; данные отвергнуты; не смогли (сеть/схема/доступ)."""
        try:
            self._request("GET", "leads", params={"select": "id", "limit": "1"}, write=False)
        except StoreUnavailable as exc:
            return StoreHealth(self.name, StoreHealth.UNAVAILABLE, str(exc))
        except StoreRejected as exc:
            return StoreHealth(self.name, StoreHealth.REJECTED, str(exc))
        # Адрес проекта наружу не отдаётся: /health доступен без аутентификации, а ссылка
        # на конкретный проект — это приглашение постучаться. Живость видна и без неё.
        return StoreHealth(self.name, StoreHealth.OK, "PostgREST отвечает, таблицы на месте")

    def save_lead(self, row: LeadRow) -> str:
        result = self._request(
            "POST", "leads", body=[row.payload()], prefer="return=representation"
        )
        if not result or not isinstance(result, list) or not result[0].get("id"):
            raise StoreUnavailable("Supabase не вернул id записанного обращения")
        return str(result[0]["id"])

    def save_score(self, row: ScoreRow) -> None:
        self._request("POST", "scores", body=[row.payload()], prefer="return=minimal")

    def save_reply(self, row: ReplyRow) -> None:
        self._request("POST", "replies", body=[row.payload()], prefer="return=minimal")

    def set_reply_status(self, lead_id: str, status: str, decided_at: datetime) -> bool:
        if status not in REPLY_STATUSES:
            raise StoreRejected(f"недопустимый статус {status!r}")
        result = self._request(
            "PATCH",
            "replies",
            params={"lead_id": f"eq.{lead_id}"},
            body={"status": status, "decided_at": decided_at.isoformat()},
            prefer="return=representation",
        )
        return bool(result)

    def get_card(self, lead_id: str) -> LeadCard | None:
        leads = self._request(
            "GET", "leads", params={"id": f"eq.{lead_id}", "select": "*"}, write=False
        )
        if not leads:
            return None
        return LeadCard(
            lead=leads[0],
            score=self._latest("scores", lead_id),
            reply=self._latest("replies", lead_id),
        )

    def _latest(self, table: str, lead_id: str) -> dict[str, Any] | None:
        rows = self._request(
            "GET",
            table,
            params={"lead_id": f"eq.{lead_id}", "select": "*", "limit": "1"},
            write=False,
        )
        return rows[0] if rows else None

    def list_cards(self, limit: int = DEFAULT_LIMIT) -> list[LeadCard]:
        """Одним запросом с вложенными таблицами: N+1 запрос на список — это секунды ожидания."""
        rows = self._request(
            "GET",
            "leads",
            params={
                "select": "*,scores(*),replies(*)",
                "order": "created_at.desc",
                "limit": str(limit),
            },
            write=False,
        )
        cards: list[LeadCard] = []
        for row in rows or []:
            scores = row.pop("scores", None) or []
            replies = row.pop("replies", None) or []
            cards.append(
                LeadCard(lead=row, score=scores[0] if scores else None,
                         reply=replies[0] if replies else None)
            )
        return cards

    def upsert_companies(self, rows: list[CompanyRow]) -> UpsertResult:
        """Пачкой с `merge-duplicates`: повторный discover обновляет, а не плодит дубли."""
        payload = [r.payload() for r in rows if r.external_id]
        failed = len(rows) - len(payload)
        if not payload:
            return UpsertResult(len(rows), 0, failed, 0, self.name)
        try:
            self._request(
                "POST",
                "companies",
                params={"on_conflict": "source,external_id"},
                body=payload,
                prefer="resolution=merge-duplicates,return=minimal",
            )
        except StoreUnavailable:
            # «Не смогли» — отдельная колонка, а не failed: разные причины и разные лечения.
            return UpsertResult(len(rows), 0, failed, len(payload), self.name)
        return UpsertResult(len(rows), len(payload), failed, 0, self.name)

    def list_companies(self, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
        rows = self._request(
            "GET",
            "companies",
            params={"select": "*", "order": "created_at.desc", "limit": str(limit)},
            write=False,
        )
        return list(rows or [])

    def save_disagreement(self, row: DisagreementRow) -> None:
        self._request("POST", "disagreements", body=[row.payload()], prefer="return=minimal")

    def counters(self) -> dict[str, Any]:
        """Те же числа, что у офлайна, и считаются той же функцией."""
        leads = self._request("GET", "leads", params={"select": "*"}, write=False) or []
        scores = self._request("GET", "scores", params={"select": "*"}, write=False) or []
        replies = self._request("GET", "replies", params={"select": "*"}, write=False) or []
        companies = self._request(
            "GET", "companies", params={"select": "source"}, write=False
        ) or []
        disagreements = self._request(
            "GET", "disagreements", params={"select": "lead_id"}, write=False
        ) or []
        return _counters(leads, scores, replies, companies, disagreements)
