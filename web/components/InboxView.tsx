"use client";

import { useMemo, useState } from "react";
import type { ApiError, Lead, Tier } from "../lib/types";
import LeadCard from "./LeadCard";
import { CHANNEL_LABEL, EmptyState, ErrorNotice, isRtlText, LoadingRows, TierChip, timeAgo } from "./ui";

const TIERS: Tier[] = ["HIGH", "MEDIUM", "LOW", "INVALID"];

const PREVIEW_CHARS = 130;
// Изоляты направления, которые ставит наш черновик и часто ставят почтовые клиенты.
const ISOLATE_OPEN = /[\u2066\u2067\u2068]/g;
const ISOLATE_CLOSE = /\u2069/g;

/**
 * Превью строки списка. Обрезка по кодовым единицам может разрезать текст внутри изолята
 * направления, а непарный LRI/RLI действует до конца абзаца и утаскивает в свой ран весь
 * следующий текст — так ведут себя двунаправленные строки в браузере. Поэтому после обрезки
 * недостающие PDI дописываются.
 */
function preview(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  if (!flat) return "— empty message —";
  if (flat.length <= PREVIEW_CHARS) return flat;
  const cut = flat.slice(0, PREVIEW_CHARS);
  const unclosed = (cut.match(ISOLATE_OPEN) ?? []).length - (cut.match(ISOLATE_CLOSE) ?? []).length;
  return `${cut}${"\u2069".repeat(Math.max(0, unclosed))}…`;
}

/**
 * The reason without its threshold half:
 * "needed in 14 days — urgency window is 60 days" → "needed in 14 days".
 * Both dashes are the catalogue's own separators, in either language.
 */
function shortReason(reason: string): string {
  return reason.split(" — ")[0].split(", но ")[0].split(", but ")[0];
}

/**
 * Reasons that say nothing in a one-line preview: the language of the request is already
 * shown as its own chip on the row.
 *
 * Matched by CODE, never by the text of the reason. The text is bilingual and gets
 * rewritten; a filter reading `startsWith("язык обращения")` silently stopped working the
 * day the interface switched to English, and "written in ru — core audience" started
 * taking one of the three preview slots.
 */
const PREVIEW_SKIPPED_CODES = new Set(["target_language", "target_language_alone"]);

/** One line of "why", so the manager decides what to open without opening it (Einstein). */
function whyLine(lead: Lead): string {
  const links = lead.reason_links;
  // Без кодов (старая строка из хранилища) отбросить нечего — берём всё, а не наугад по
  // тексту: третий исход «не знаем кодов» не притворяется отбором.
  const kept = links
    ? links.filter((item) => !PREVIEW_SKIPPED_CODES.has(item.code ?? "")).map((item) => item.text)
    : lead.reasons;
  const parts = (kept.length ? kept : lead.reasons).map(shortReason);
  return parts.slice(0, 3).join(" · ");
}

export default function InboxView({
  leads,
  loading,
  error,
  now,
  selectedId,
  onSelect,
  onLeadChanged,
  onRetry,
}: {
  leads: Lead[];
  loading: boolean;
  error: ApiError | null;
  now: Date | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onLeadChanged: (lead: Lead) => void;
  onRetry: () => void;
}) {
  const [tierFilter, setTierFilter] = useState<Tier | null>(null);
  const [query, setQuery] = useState("");

  // Позиция в списке — самостоятельный канал приоритета (01_material.md §3.2) и ответ на
  // вопрос «что делать сейчас», а не «что у нас есть» (02_references.md §1.8). Внутри тира
  // порядок по свежести: два обращения одного тира разделяет только время ожидания.
  const TIER_ORDER: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2, INVALID: 3 };

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return [...leads].sort((a, b) => {
      const byTier = (TIER_ORDER[a.tier] ?? 9) - (TIER_ORDER[b.tier] ?? 9);
      if (byTier !== 0) return byTier;
      return new Date(b.received_at).getTime() - new Date(a.received_at).getTime();
    }).filter((lead) => {
      if (tierFilter && lead.tier !== tierFilter) return false;
      if (!needle) return true;
      return (
        lead.text.toLowerCase().includes(needle) ||
        lead.id.toLowerCase().includes(needle) ||
        lead.channel.toLowerCase().includes(needle)
      );
    });
  }, [leads, tierFilter, query]);

  const selected = leads.find((lead) => lead.id === selectedId) ?? filtered[0] ?? null;

  if (error) {
    return (
      <div style={{ marginTop: 16 }}>
        <ErrorNotice
          title={error.kind === "budget" ? "Model provider unavailable" : "Cannot load the inbox"}
          message={
            error.kind === "budget"
              ? "The language-model provider refused the request on budget or rate limits. Existing cards still open; new ones cannot be scored until the provider is topped up."
              : error.message
          }
          detail={error.detail}
          onRetry={onRetry}
        />
      </div>
    );
  }

  return (
    <div className="inbox">
      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Inbox</span>
          <span className="note">
            {loading ? "loading…" : `${filtered.length} of ${leads.length}`}
          </span>
          <input
            className="search-box"
            style={{ marginLeft: "auto" }}
            type="text"
            placeholder="Search text, id or channel"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <div className="toolbar">
          <div className="filters">
            <button
              className="filter-btn"
              aria-pressed={tierFilter === null}
              onClick={() => setTierFilter(null)}
            >
              all {leads.length}
            </button>
            {TIERS.map((tier) => (
              <button
                key={tier}
                className="filter-btn"
                aria-pressed={tierFilter === tier}
                onClick={() => setTierFilter(tierFilter === tier ? null : tier)}
              >
                {tier === "INVALID" ? "not scored" : tier.toLowerCase()}{" "}
                {leads.filter((lead) => lead.tier === tier).length}
              </button>
            ))}
          </div>
        </div>
        <div className="lead-list">
          {loading ? (
            <LoadingRows rows={8} />
          ) : filtered.length === 0 ? (
            <EmptyState
              title={
                tierFilter === "INVALID"
                  ? "No request was refused a priority"
                  : "Nothing matches this filter"
              }
              hint={
                tierFilter === "INVALID"
                  ? "«Not scored» is a third outcome, not a low priority: the engine returns it when an invariant is broken — for example HIGH without a quote from the request text. On this seed no request hit that, and an empty list here is the honest result, not a missing screen."
                  : "Clear the priority filter or the search box."
              }
              action={
                <button
                  className="btn"
                  onClick={() => {
                    setTierFilter(null);
                    setQuery("");
                  }}
                >
                  Show all {leads.length}
                </button>
              }
            />
          ) : (
            filtered.map((lead) => (
              <button
                key={lead.id}
                className={`lead-row tier-${lead.tier}`}
                aria-current={selected?.id === lead.id}
                onClick={() => onSelect(lead.id)}
              >
                <div className="lead-row-top">
                  <TierChip tier={lead.tier} />
                  <span className="channel">{CHANNEL_LABEL[lead.channel] ?? lead.channel}</span>
                  {lead.status === "approved" ? <span className="chip chip-status">in CRM</span> : null}
                  {lead.status === "rejected" ? <span className="chip chip-status">disagreed</span> : null}
                  <span className="lead-row-meta">{now ? timeAgo(lead.received_at, now) : ""}</span>
                </div>
                {/* dir="auto": арабская строка обязана начинаться справа, соседняя русская —
                    слева. */}
                <div
                  className={`lead-row-preview${isRtlText(lead.text) ? " rtl-block" : ""}`}
                  dir="auto"
                >
                  {preview(lead.text)}
                </div>
                {whyLine(lead) ? <div className="lead-row-why">{whyLine(lead)}</div> : null}
              </button>
            ))
          )}
        </div>
      </div>

      <div>
        {loading ? (
          <div className="panel">
            <LoadingRows rows={10} />
          </div>
        ) : selected ? (
          <LeadCard lead={selected} now={now} onChanged={onLeadChanged} />
        ) : (
          <div className="panel">
            <EmptyState title="No request selected" hint="Pick a request on the left to open its card." />
          </div>
        )}
      </div>
    </div>
  );
}
