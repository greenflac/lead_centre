"use client";

import { useMemo, useState } from "react";
import type { ApiError, Lead, Tier } from "../lib/types";
import LeadCard from "./LeadCard";
import { CHANNEL_LABEL, EmptyState, ErrorNotice, LoadingRows, SyntheticTag, TierChip, timeAgo } from "./ui";

const TIERS: Tier[] = ["HIGH", "MEDIUM", "LOW", "INVALID"];

function preview(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  if (!flat) return "— empty message —";
  return flat.length > 130 ? `${flat.slice(0, 130)}…` : flat;
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

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return leads.filter((lead) => {
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
          <span className="panel-note">
            {loading ? "loading…" : `${filtered.length} of ${leads.length}`}
          </span>
          <div className="filters" style={{ marginLeft: "auto" }}>
            <button
              className="filter-btn"
              aria-pressed={tierFilter === null}
              onClick={() => setTierFilter(null)}
            >
              all
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
        <div className="panel-head" style={{ background: "var(--surface)" }}>
          <input
            className="search-box"
            type="text"
            placeholder="Search text, id or channel"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <div className="lead-list">
          {loading ? (
            <LoadingRows rows={8} />
          ) : filtered.length === 0 ? (
            <EmptyState
              title="Nothing matches this filter"
              hint="Clear the priority filter or the search box."
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
                  {lead.status === "approved" ? <span className="chip chip-status-approved">in CRM</span> : null}
                  {lead.status === "rejected" ? <span className="chip chip-status-rejected">disagreed</span> : null}
                  <span className="lead-row-meta">{now ? timeAgo(lead.received_at, now) : ""}</span>
                </div>
                <div className="lead-row-preview">{preview(lead.text)}</div>
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
        <div className="footnote">
          Every card here is built from <code>data/inbound_seed.csv</code> — invented requests, marked{" "}
          <SyntheticTag /> in the card. SORP has no real request log yet, and we do not pretend otherwise.
        </div>
      </div>
    </div>
  );
}
