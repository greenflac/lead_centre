"use client";

import type { Evidence, Tier } from "../lib/types";

interface TierChipProps {
  tier: Tier;
}

interface SyntheticTagProps {
  what?: string;
  title?: string;
}

interface ChipProps {
  children: React.ReactNode;
  title?: string;
}

interface ErrorNoticeProps {
  title: string;
  message: string;
  detail?: string;
  onRetry?: () => void;
}

interface EmptyStateProps {
  title: string;
  hint?: string;
  action?: React.ReactNode;
}

interface LoadingRowsProps {
  rows?: number;
}

interface EvidenceListProps {
  items: Evidence[];
}

interface ConfidenceBandProps {
  value: number;
}

export type ConfidenceLevel = "High" | "Medium" | "Low";

/** Priority as a chip. INVALID reads "not scored": a third outcome, not a low tier. */
export function TierChip({ tier }: TierChipProps): React.JSX.Element {
  const label = tier === "INVALID" ? "not scored" : tier;
  return (
    <span
      className={`chip tier-${tier}`}
      title={tier === "INVALID" ? "An invariant was violated — the engine refuses to give this a priority" : undefined}
    >
      {label}
    </span>
  );
}

/** Honest provenance label. Invented data and real public records are never shown alike. */
export function SyntheticTag({
  what = "synthetic",
  title = "Invented demo data. Not a real request or customer record.",
}: SyntheticTagProps): React.JSX.Element {
  return (
    <span className="chip chip-plain" title={title}>
      {what}
    </span>
  );
}

export function Chip({ children, title }: ChipProps): React.JSX.Element {
  return (
    <span className="chip chip-plain" title={title}>
      {children}
    </span>
  );
}

/** A failure the reader can act on: what broke, the raw answer folded away, a retry. */
export function ErrorNotice({
  title,
  message,
  detail,
  onRetry,
}: ErrorNoticeProps): React.JSX.Element {
  return (
    <div className="notice notice-error">
      <div className="notice-title">{title}</div>
      <div>{message}</div>
      {/* Why folded: raw JSON on screen shows a bystander the model name and our account state. */}
      {detail ? (
        <details className="details details-inline notice-details">
          <summary className="details-summary">Technical detail</summary>
          <div className="details-body notice-detail">{detail}</div>
        </details>
      ) : null}
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

/** Empty states say what this is, why it is separate and what to do next. */
export function EmptyState({ title, hint, action }: EmptyStateProps): React.JSX.Element {
  return (
    <div className="empty-state">
      <div className="empty-state-title">{title}</div>
      {hint ? <div className="empty-state-body">{hint}</div> : null}
      {action ? <div className="actions">{action}</div> : null}
    </div>
  );
}

export function LoadingRows({ rows = 6 }: LoadingRowsProps): React.JSX.Element {
  return (
    <div aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="skeleton skeleton-row" style={{ width: `${90 - index * 7}%` }} />
      ))}
    </div>
  );
}

/** Quotes a score rests on. An empty list says so instead of rendering nothing. */
export function EvidenceList({ items }: EvidenceListProps): React.JSX.Element {
  if (!items.length) {
    return <div className="note">No evidence attached — the engine cannot raise this to HIGH.</div>;
  }
  return (
    <div>
      {items.map((item, index) =>
        item.kind === "quote" ? (
          <blockquote key={index} className={`quote${isRtlText(item.value) ? " rtl-block" : ""}`} dir="auto">
            “{item.value}”
          </blockquote>
        ) : (
          <div key={index} className="evidence-line" dir="auto">
            {item.kind} = {item.value}
          </div>
        ),
      )}
    </div>
  );
}

/** Band boundaries. CHOSEN: a reader has no scale for "0.87", so three bands carry it. */
export const CONFIDENCE_HIGH = 0.85;
export const CONFIDENCE_MEDIUM = 0.5;

export function confidenceLevel(value: number): ConfidenceLevel {
  if (value >= CONFIDENCE_HIGH) return "High";
  if (value >= CONFIDENCE_MEDIUM) return "Medium";
  return "Low";
}

export function ConfidenceBand({ value }: ConfidenceBandProps): React.JSX.Element {
  const level = confidenceLevel(value);
  const lit = level === "High" ? 3 : level === "Medium" ? 2 : 1;
  return (
    <div className="confidence">
      <div className="confidence-band" role="img" aria-label={`Extraction confidence ${level}`}>
        {[0, 1, 2].map((index) => (
          <span key={index} className={`confidence-seg${index < lit ? " on" : ""}`} />
        ))}
      </div>
      <span className="confidence-level">{level}</span>
      <span className="confidence-number">{value.toFixed(2)}</span>
    </div>
  );
}

/** Direction by the first strongly-directional character, as dir="auto" does. Picks type size only. */
export function isRtlText(text: string): boolean {
  for (const ch of text) {
    if ((ch >= "\u0600" && ch <= "\u06ff") || (ch >= "\u0750" && ch <= "\u077f") ||
        (ch >= "\ufb50" && ch <= "\ufdff") || (ch >= "\ufe70" && ch <= "\ufeff") ||
        (ch >= "\u0590" && ch <= "\u05ff") || ch === "\u200f") return true;
    if (/\p{L}/u.test(ch)) return false;
  }
  return false;
}

/** "3 h ago", relative to a passed-in clock rather than to the wall clock. */
export function timeAgo(iso: string, now: Date): string {
  const minutes = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "1 day ago" : `${days} days ago`;
}

/** Channel codes as the manager reads them; an unknown code is shown raw. */
export const CHANNEL_LABEL: Record<string, string> = {
  jivo: "Jivo chat",
  whatsapp: "WhatsApp",
  telegram: "Telegram",
  form: "Website form",
};
