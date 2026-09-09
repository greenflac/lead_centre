"use client";

import { useEffect, useState } from "react";
import type { Lead } from "../lib/types";
import { ApiError } from "../lib/types";
import { approve, disagree } from "../lib/api";
import { CHANNEL_LABEL, Chip, ErrorNotice, EvidenceList, SyntheticTag, TierChip, timeAgo } from "./ui";

const REQUEST_LABEL: Record<string, string> = {
  office: "office",
  setup: "company setup",
  visa: "visas",
  accounting: "accounting",
  bank: "bank account",
  other: "other",
};

const OUTCOME_NOTE: Record<string, string> = {
  draft: "Draft reply — prices are ranges from the demo price list, never a quote.",
  questions: "Too few facts for numbers: the engine asks instead of guessing a price.",
  spam_skipped: "Marked as spam or off-topic — no reply is drafted at all.",
};

function factValue(value: string | number | null | undefined, suffix = ""): React.ReactNode {
  if (value === null || value === undefined || value === "") {
    return <dd className="empty">not stated</dd>;
  }
  return (
    <dd>
      {value}
      {suffix}
    </dd>
  );
}

export default function LeadCard({
  lead,
  now,
  onChanged,
}: {
  lead: Lead;
  now: Date | null;
  onChanged: (lead: Lead) => void;
}) {
  const [busy, setBusy] = useState<"approve" | "disagree" | null>(null);
  const [showReason, setShowReason] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    setShowReason(false);
    setReason("");
    setError(null);
  }, [lead.id]);

  async function run(action: "approve" | "disagree") {
    setBusy(action);
    setError(null);
    try {
      const updated = action === "approve" ? await approve(lead.id) : await disagree(lead.id, reason.trim());
      onChanged(updated);
      setShowReason(false);
      setReason("");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError("unknown", String(caught)));
    } finally {
      setBusy(null);
    }
  }

  const facts = lead.facts;
  const decided = lead.status !== "new";

  return (
    <div className="panel">
      <div className="card-head">
        <div className="card-head-row">
          <TierChip tier={lead.tier} />
          <span className="card-id">{lead.id}</span>
          <Chip title="Channel the request arrived from">{CHANNEL_LABEL[lead.channel] ?? lead.channel}</Chip>
          <Chip title="Language detected in the request text">{lead.language}</Chip>
          {lead.is_synthetic ? <SyntheticTag /> : null}
          {lead.status === "approved" ? <span className="chip chip-status-approved">sent to CRM</span> : null}
          {lead.status === "rejected" ? <span className="chip chip-status-rejected">disagreed</span> : null}
          <span className="lead-row-meta">{now ? timeAgo(lead.received_at, now) : ""}</span>
        </div>
      </div>

      <div className="card-body">
        <div className="card-col">
          <div className="section">
            <div className="section-title">Request text (shown as received)</div>
            <div className="message-text">{lead.text.trim() ? lead.text : "— empty message —"}</div>
          </div>

          <div className="section">
            <div className="section-title">Extracted facts</div>
            <dl className="facts">
              <dt>asking for</dt>
              {facts.request_types.length ? (
                <dd>{facts.request_types.map((r) => REQUEST_LABEL[r] ?? r).join(", ")}</dd>
              ) : (
                <dd className="empty">nothing extracted</dd>
              )}
              <dt>people</dt>
              {factValue(facts.headcount)}
              <dt>timeline</dt>
              {factValue(facts.timeline_days, " days")}
              <dt>jurisdiction</dt>
              {factValue(facts.jurisdiction_hint)}
              <dt>budget signal</dt>
              {factValue(facts.budget_hint)}
              <dt>language</dt>
              {factValue(facts.language)}
              <dt>contact in text</dt>
              <dd>{facts.has_contact ? "yes (masked before the model)" : "no"}</dd>
            </dl>
            <div className="section-title" style={{ marginTop: 12 }}>
              Extraction confidence — {facts.confidence.toFixed(2)}
            </div>
            <div className="confidence-bar">
              <div className="confidence-fill" style={{ width: `${Math.round(facts.confidence * 100)}%` }} />
            </div>
            <div className="panel-note" style={{ marginTop: 6 }}>
              Facts source: {lead.facts_source === "offline_heuristic" ? "offline heuristic (mock mode)" : lead.facts_source}
            </div>
          </div>

          <div className="section">
            <div className="section-title">Provenance</div>
            <dl className="facts">
              <dt>request id</dt>
              <dd style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{lead.id}</dd>
              <dt>channel</dt>
              <dd>{CHANNEL_LABEL[lead.channel] ?? lead.channel}</dd>
              <dt>received</dt>
              <dd>{lead.received_at.replace("T", " ").replace(/\.\d+/, "").replace("Z", " UTC")}</dd>
              <dt>seed category</dt>
              <dd>{lead.category}</dd>
            </dl>
            <div className="panel-note" style={{ marginTop: 8 }}>
              {lead.is_synthetic
                ? "Invented request written for this demo. SORP has no exported request log yet, so nothing here is a real customer."
                : "Real request record."}
            </div>
          </div>
        </div>

        <div className="card-col">
          <div className="section">
            <div className="section-title">Why this priority</div>
            {lead.reasons.length ? (
              <ul className="reasons">
                {lead.reasons.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            ) : (
              <div className="panel-note">Base level, no modifier applied.</div>
            )}
            {lead.violations.length ? (
              <div className="notice notice-error" style={{ marginTop: 10 }}>
                <div className="notice-title">Invariant violated — priority withheld</div>
                <ul className="reasons">
                  {lead.violations.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>

          <div className="section">
            <div className="section-title">Evidence (quotes from the request)</div>
            <EvidenceList items={lead.evidence} />
          </div>

          <div className="section">
            <div className="section-title">Draft reply — {lead.reply.language.toUpperCase()}</div>
            {lead.reply.body ? (
              <div className="draft">{lead.reply.body}</div>
            ) : (
              <div className="draft draft-empty">No reply drafted.</div>
            )}
            <div className="panel-note" style={{ marginTop: 6 }}>
              {OUTCOME_NOTE[lead.reply.outcome] ?? lead.reply.outcome}
              {lead.reply.needs_human ? " Flagged for a human before sending." : ""}
            </div>

            {decided ? (
              <div className="decision-note">
                {lead.status === "approved"
                  ? "Approved — lead and draft handed to the CRM sink. Nothing is sent to the customer automatically."
                  : `Disagreed${lead.decision_reason ? `: ${lead.decision_reason}` : ""}. Logged for the eval set.`}
              </div>
            ) : (
              <>
                <div className="actions">
                  <button
                    className="btn btn-primary"
                    disabled={busy !== null}
                    onClick={() => run("approve")}
                  >
                    {busy === "approve" ? <span className="spinner" /> : null} Approve → CRM
                  </button>
                  <button className="btn" disabled={busy !== null} onClick={() => setShowReason((v) => !v)}>
                    Disagree
                  </button>
                  <span className="panel-note">Nothing is sent to the customer — a human always presses the button.</span>
                </div>
                {showReason ? (
                  <div className="reason-form">
                    <input
                      type="text"
                      placeholder="What is wrong with this card? (goes to the eval set)"
                      value={reason}
                      onChange={(event) => setReason(event.target.value)}
                      autoFocus
                    />
                    <button
                      className="btn"
                      disabled={busy !== null || reason.trim().length === 0}
                      onClick={() => run("disagree")}
                    >
                      {busy === "disagree" ? <span className="spinner spinner-dark" /> : null} Save disagreement
                    </button>
                  </div>
                ) : null}
              </>
            )}

            {error ? (
              <div style={{ marginTop: 10 }}>
                <ErrorNotice
                  title={error.kind === "budget" ? "Model provider unavailable" : "Action failed"}
                  message={error.message}
                  detail={error.detail}
                />
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
