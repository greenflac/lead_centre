"use client";

import type { Evidence, Tier } from "../lib/types";

export function TierChip({ tier }: { tier: Tier }) {
  const label = tier === "INVALID" ? "NOT SCORED" : tier;
  return <span className={`chip tier-${tier}`} title={tier === "INVALID" ? "An invariant was violated — the engine refuses to give this a priority" : undefined}>{label}</span>;
}

/** Honest provenance label. Invented data and real public records are never shown alike. */
export function SyntheticTag({
  what = "synthetic data",
  title = "Invented demo data. Not a real SORP request or customer record.",
}: {
  what?: string;
  title?: string;
}) {
  return (
    <span className="chip chip-synthetic" title={title}>
      {what}
    </span>
  );
}

export function RealDataTag({ what, title }: { what: string; title: string }) {
  return (
    <span className="chip chip-plain" style={{ fontSize: 10 }} title={title}>
      {what}
    </span>
  );
}

export function Chip({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <span className="chip chip-plain" title={title}>
      {children}
    </span>
  );
}

export function ErrorNotice({
  title,
  message,
  detail,
  onRetry,
}: {
  title: string;
  message: string;
  detail?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="notice notice-error">
      <div className="notice-title">{title}</div>
      <div>{message}</div>
      {detail ? <div className="notice-detail">{detail}</div> : null}
      {onRetry ? (
        <div className="actions">
          <button className="btn" onClick={onRetry}>
            Try again
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty-state">
      <div className="empty-state-title">{title}</div>
      {hint ? <div>{hint}</div> : null}
    </div>
  );
}

export function LoadingRows({ rows = 6 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="skeleton skeleton-row" style={{ width: `${90 - index * 7}%` }} />
      ))}
    </div>
  );
}

export function EvidenceList({ items }: { items: Evidence[] }) {
  if (!items.length) {
    return <div className="panel-note">No evidence attached — the engine cannot raise this to HIGH.</div>;
  }
  return (
    <div>
      {items.map((item, index) =>
        item.kind === "quote" ? (
          <blockquote key={index} className="quote">
            “{item.value}”
          </blockquote>
        ) : (
          <div key={index} className="evidence-line">
            {item.kind} = {item.value}
          </div>
        ),
      )}
    </div>
  );
}

/** "3 h ago" — relative to a fixed clock so the demo data does not read as months old. */
export function timeAgo(iso: string, now: Date): string {
  const minutes = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "1 day ago" : `${days} days ago`;
}

export const CHANNEL_LABEL: Record<string, string> = {
  jivo: "Jivo chat",
  whatsapp: "WhatsApp",
  telegram: "Telegram",
  form: "Website form",
};
