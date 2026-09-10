"use client";

import type { Evidence, Tier } from "../lib/types";

export function TierChip({ tier }: { tier: Tier }) {
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
  title = "Invented demo data. Not a real SORP request or customer record.",
}: {
  what?: string;
  title?: string;
}) {
  return (
    <span className="chip chip-plain" title={title}>
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

/**
 * Empty states answer three questions: what this is, why it is separate, what to do next
 * (02_references.md §4). A dead end behind a clickable filter reads as a prototype.
 */
export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-state-title">{title}</div>
      {hint ? <div className="empty-state-body">{hint}</div> : null}
      {action ? <div className="actions">{action}</div> : null}
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

/**
 * Extraction confidence as a band, not as a decimal (02_references.md §6.5). A reader has
 * no scale for "0.87", and "1.00" asks for faith. Thresholds ВЫБРАНО (author, 2026-09-10):
 * the offline heuristic reports 1.00 ("markers matched") or 0.00 ("nothing matched"), and
 * the LLM extractor returns its own number in between; 0.85 and 0.5 split it into three.
 */
export const CONFIDENCE_HIGH = 0.85;
export const CONFIDENCE_MEDIUM = 0.5;

export function confidenceLevel(value: number): "High" | "Medium" | "Low" {
  if (value >= CONFIDENCE_HIGH) return "High";
  if (value >= CONFIDENCE_MEDIUM) return "Medium";
  return "Low";
}

export function ConfidenceBand({ value }: { value: number }) {
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

/**
 * Direction of a block of customer text, by the first strongly-directional character —
 * the same rule the browser applies for dir="auto".
 * The attribute stays "auto" so the browser decides the layout; this only picks the type
 * size, because Naskh at the Latin size reads noticeably smaller (§3.2).
 */
export function isRtlText(text: string): boolean {
  for (const ch of text) {
    if ((ch >= "\u0600" && ch <= "\u06ff") || (ch >= "\u0750" && ch <= "\u077f") ||
        (ch >= "\ufb50" && ch <= "\ufdff") || (ch >= "\ufe70" && ch <= "\ufeff") ||
        (ch >= "\u0590" && ch <= "\u05ff") || ch === "\u200f") return true;
    if (/\p{L}/u.test(ch)) return false;
  }
  return false;
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
