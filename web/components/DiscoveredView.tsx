"use client";

import { useMemo, useState } from "react";
import type { ApiError, Company, Tier } from "../lib/types";
import { EmptyState, ErrorNotice, LoadingRows, RealDataTag, SyntheticTag, TierChip } from "./ui";

const TIERS: Tier[] = ["HIGH", "MEDIUM", "LOW", "INVALID"];

const ADDRESS_LABEL: Record<string, string> = {
  A1_registrar: "registrar address (flexi/virtual)",
  A2_business_centre: "business centre",
  A3_own: "own or leased office",
  A0_unknown: "address unknown",
};

function evidenceRows(company: Company): { kind: string; value: string }[] {
  // Two lines are enough to prove the trigger: which record, and the date it turns on.
  const order = ["lei", "next_renewal_on", "license_no", "created_on"];
  return [...company.evidence]
    .sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind))
    .slice(0, 2);
}

export default function DiscoveredView({
  companies,
  loading,
  error,
  onRetry,
}: {
  companies: Company[];
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
}) {
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
          <span className="panel-note">{loading ? "loading…" : `${rows.length} of ${companies.length} companies`}</span>
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
              <thead>
                <tr>
                  <th>Priority</th>
                  <th>Company</th>
                  <th>City</th>
                  <th>Registration authority</th>
                  <th>Licence no.</th>
                  <th>Why it is here</th>
                  <th>Evidence</th>
                  <th>Provenance</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((company) => (
                  <tr key={company.id}>
                    <td>
                      <TierChip tier={company.tier} />
                    </td>
                    <td className="cell-name">
                      {company.name}
                      <div className="panel-note">{ADDRESS_LABEL[company.address_type] ?? company.address_type}</div>
                    </td>
                    <td>{company.city || <span className="panel-note">unknown</span>}</td>
                    <td className="cell-mono">{company.registrar_id ?? "—"}</td>
                    <td className="cell-mono">{company.license_no ?? "—"}</td>
                    <td className="cell-reason">
                      {company.reasons.length ? company.reasons.join("; ") : <span className="panel-note">no trigger event</span>}
                    </td>
                    <td>
                      {evidenceRows(company).map((item) => (
                        <div key={item.kind} className="evidence-line">
                          {item.kind}={item.value}
                        </div>
                      ))}
                    </td>
                    <td>
                      {company.is_synthetic ? (
                        <SyntheticTag />
                      ) : (
                        <RealDataTag
                          what="public registry record"
                          title="Real GLEIF LEI record, cached sample of 2026-09-09. The priority and the reason are ours; the fields are the registry's."
                        />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <div className="footnote">
        Source: GLEIF LEI registry (public, no authentication), UAE slice, most recently lapsed registrations. Records
        are real public registry entries; the priority, the reason and the evidence are produced by the same rubric that
        scores the inbox.
      </div>
    </div>
  );
}
