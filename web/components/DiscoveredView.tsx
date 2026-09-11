"use client";

import { useMemo, useState } from "react";
import type { ApiError, Company, Evidence, Tier } from "../lib/types";
import { EmptyState, ErrorNotice, LoadingRows, TierChip } from "./ui";

const TIERS: Tier[] = ["HIGH", "MEDIUM", "LOW", "INVALID"];

/** Address type in words; the machine label is not text for a manager. */
const ADDRESS_LABEL: Record<string, string> = {
  A1_registrar: "Registrar address",
  A2_business_centre: "Business centre",
  A3_own: "Own office",
  A0_unknown: "Not stated",
};

const ADDRESS_TITLE: Record<string, string> = {
  A1_registrar:
    "Registered at the registration agent's own address — a flexi desk or virtual office, not premises of its own.",
  A2_business_centre: "Registered at a business centre — a serviced office shared with other tenants.",
  A3_own: "Registered at an office of its own, or one it leases.",
  A0_unknown: "The registry record does not say what kind of address this is.",
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-09-04" -> "4 Sep 2026". Why hand-parsed: toLocaleDateString varies by locale and zone. */
export function humanDate(value: string): string | null {
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!parts) return null;
  const month = Number(parts[2]);
  const day = Number(parts[3]);
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return `${day} ${MONTHS[month - 1]} ${parts[1]}`;
}

/** Evidence fields the screen can read, in order of use: first two in the cell, all four in the title. */
const EVIDENCE_FIELDS: { kind: string; label: string; isDate: boolean; mono: boolean }[] = [
  { kind: "next_renewal_on", label: "Renewal due", isDate: true, mono: false },
  { kind: "lei", label: "LEI", isDate: false, mono: true },
  { kind: "license_no", label: "Licence", isDate: false, mono: true },
  { kind: "created_on", label: "Registered", isDate: true, mono: false },
];

/** How many evidence fields fit the measured column width. */
const VISIBLE_EVIDENCE = 2;

type EvidencePart = {
  kind: string;
  label: string;
  mono: boolean;
  /** Empty means the record has no such field; the cell says so rather than going blank. */
  text: string;
};

/** Three outcomes: fields read, fields of an unknown kind, and nothing attached at all. */
export function readEvidence(items: Evidence[]): {
  parts: EvidencePart[];
  unread: Evidence[];
  hasAny: boolean;
} {
  const byKind = new Map(items.map((item) => [item.kind, item.value]));
  const parts = EVIDENCE_FIELDS.map((field) => {
    const raw = byKind.get(field.kind);
    const value = raw === undefined || raw === "" ? "" : raw;
    const text = value && field.isDate ? humanDate(value) ?? value : value;
    return { kind: field.kind, label: field.label, mono: field.mono, text };
  });
  const unread = items.filter((item) => !EVIDENCE_FIELDS.some((field) => field.kind === item.kind));
  return { parts, unread, hasAny: items.length > 0 };
}

/** Full evidence text for the tooltip, in the same words as the cell. */
function evidenceTitle(parts: EvidencePart[], unread: Evidence[]): string {
  const lines = parts.map((part) => (part.text ? `${part.label}: ${part.text}` : `${part.label}: not recorded`));
  for (const item of unread) lines.push(`Not recognised — ${item.kind} = ${item.value}`);
  return lines.join("\n");
}

/** The evidence column, with all three outcomes spelled out in words. */
function EvidenceCell({ company }: { company: Company }): React.JSX.Element {
  const { parts, unread, hasAny } = readEvidence(company.evidence);
  const readable = parts.filter((part) => part.text).length;

  if (!hasAny) {
    return (
      <td className="clip" title="Nothing was attached to this row, so there is nothing to check against the registry.">
        <span className="note">no evidence attached</span>
      </td>
    );
  }

  if (readable === 0) {
    // Evidence is attached but no field is known: not the same as an empty cell.
    return (
      <td className="clip" title={evidenceTitle(parts, unread)}>
        <span className="note">evidence not recognised ({unread.map((item) => item.kind).join(", ")})</span>
      </td>
    );
  }

  return (
    <td className="clip" title={evidenceTitle(parts, unread)}>
      {parts.slice(0, VISIBLE_EVIDENCE).map((part, index) => (
        <span key={part.kind}>
          {index > 0 ? <span className="ev-sep">·</span> : null}
          <span className="ev-label">{part.label} </span>
          {part.text ? (
            <span className={part.mono ? "cell-mono" : undefined}>{part.text}</span>
          ) : (
            <span className="note">not recorded</span>
          )}
        </span>
      ))}
    </td>
  );
}

/** The address-type column. A code the screen cannot name is said so, not left blank. */
function AddressCell({ type }: { type: string }): React.JSX.Element {
  const label = ADDRESS_LABEL[type];
  if (label) {
    return (
      <td className="clip" title={ADDRESS_TITLE[type]}>
        {label}
      </td>
    );
  }
  // Third outcome: a type is set but the screen cannot put it into words.
  return (
    <td className="clip" title={`The engine reported address type "${type}", which this screen cannot put into words.`}>
      <span className="note">not recognised ({type})</span>
    </td>
  );
}

interface DiscoveredViewProps {
  companies: Company[];
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
}

/** The registry watchlist: one public LEI record per row. */
export default function DiscoveredView({
  companies,
  loading,
  error,
  onRetry,
}: DiscoveredViewProps): React.JSX.Element {
  const [tierFilter, setTierFilter] = useState<Tier | null>(null);

  const rows = useMemo(
    () => (tierFilter ? companies.filter((item) => item.tier === tierFilter) : companies),
    [companies, tierFilter],
  );

  if (error) {
    return (
      <div style={{ marginTop: 16 }}>
        <ErrorNotice
          title="Cannot load the watchlist"
          message={error.message}
          detail={error.detail}
          onRetry={onRetry}
        />
      </div>
    );
  }

  return (
    <div style={{ marginTop: 16 }}>
      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Discovered — LEI registry watchlist</span>
          <span className="note">{loading ? "loading…" : `${rows.length} of ${companies.length} companies`}</span>
          <div className="filters" style={{ marginLeft: "auto" }}>
            <button className="filter-btn" aria-pressed={tierFilter === null} onClick={() => setTierFilter(null)}>
              all
            </button>
            {TIERS.filter((tier) => companies.some((item) => item.tier === tier)).map((tier) => (
              <button
                key={tier}
                className="filter-btn"
                aria-pressed={tierFilter === tier}
                onClick={() => setTierFilter(tierFilter === tier ? null : tier)}
              >
                {tier === "INVALID" ? "not scored" : tier.toLowerCase()}{" "}
                {companies.filter((item) => item.tier === tier).length}
              </button>
            ))}
          </div>
        </div>

        <div className="watchlist-note">
          <strong>Watchlist, not a mailing list.</strong>
          <span>
            No phone numbers, no email addresses, no contact person — deliberately. A manager decides whether a company
            is worth approaching; the tool never hands over a way to cold-contact it.
          </span>
          <span>
            Every row is a <strong>real public registry record</strong> (GLEIF LEI, UAE slice, cached 2026-09-09). The
            company, the address and the dates are the registry&apos;s; the priority and the reason are{" "}
            <strong>our conclusion</strong>, and the evidence names the fields of the record it rests on.
          </span>
        </div>

        {loading ? (
          <LoadingRows rows={10} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="No companies in this view"
            hint="The daily registry run found nothing at this priority. Clear the filter to see the rest."
          />
        ) : (
          <div className="table-wrap">
            <table className="grid grid-fixed">
              <colgroup>
                <col className="c-priority" />
                <col className="c-company" />
                <col className="c-flag" />
                <col className="c-city" />
                <col className="c-why" />
                <col className="c-evidence" />
              </colgroup>
              <thead>
                <tr>
                  <th>Priority</th>
                  <th>Company</th>
                  <th>Address type</th>
                  <th>City</th>
                  <th>Why it is here</th>
                  <th>Evidence in the registry record</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((company) => (
                  <tr key={company.id}>
                    <td>
                      <TierChip tier={company.tier} />
                    </td>
                    <td className="cell-name clip" title={company.name}>
                      {company.name}
                    </td>
                    <AddressCell type={company.address_type} />
                    {/* Printed as the registry has it: a field of the record, not our conclusion. */}
                    <td className="clip" title={company.city || "The registry record has no city for this entry."}>
                      {company.city || <span className="note">not stated</span>}
                    </td>
                    <td className="cell-reason clip" title={company.reasons.join("; ")}>
                      {company.reasons.length ? company.reasons.join("; ") : <span className="note">no trigger event</span>}
                    </td>
                    <EvidenceCell company={company} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <div className="footnote">
        Source: GLEIF LEI registry (public, no authentication), UAE slice, most recently lapsed registrations. The
        records are real public registry entries and the evidence quotes their fields; the priority and the reason are
        produced by the same rubric that scores the inbox. Hover a row to see the full text of a clipped cell — the
        evidence tooltip also carries the licence number and the registration date.
      </div>
    </div>
  );
}
