"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { approve, disagree } from "../lib/api";
import { ApiError, type Lead } from "../lib/types";
import {
  CHANNEL_LABEL,
  Chip,
  ConfidenceBand,
  ErrorNotice,
  EvidenceList,
  isRtlText,
  SyntheticTag,
  TierChip,
  timeAgo,
} from "./ui";

/** Request-type codes as words on the card. An unknown code is printed raw, never hidden. */
const REQUEST_LABEL: Record<string, string> = {
  office: "office",
  setup: "company setup",
  visa: "visas",
  accounting: "accounting",
  renewal: "renewal",
  bank: "bank account",
  other: "other",
};

/** One line under the draft saying which of the four drafting outcomes this card got. */
const OUTCOME_NOTE: Record<string, string> = {
  draft: "Draft reply — prices are ranges from the demo price list, never a quote.",
  questions: "Too few facts for numbers: the engine asks instead of guessing a price.",
  spam_skipped: "Marked as spam or off-topic — no reply is drafted at all.",
  no_draft_needs_human: "No draft: the engine refused rather than send something it cannot check.",
};

/** How many quotes are shown before the list is folded. ВЫБРАНО: a card is read, not scrolled. */
const EVIDENCE_SHOWN = 3;

interface Span {
  start: number;
  end: number;
  active: boolean;
}

/**
 * The request text with the quotes marked inside it (02_references.md §6.3: the proof
 * stands next to the claim, not in a separate list). Quotes are slices of this very text,
 * so they are located by search rather than by an offset we would have to trust; the
 * trailing ellipsis of a trimmed quote is dropped before searching.
 */
function markQuotes(text: string, quotes: string[], activeIndices: number[]): React.ReactNode {
  const spans: Span[] = [];
  quotes.forEach((quote, index) => {
    const needle = quote.endsWith("…") ? quote.slice(0, -1) : quote;
    if (needle.length < 4) return;
    const start = text.indexOf(needle);
    if (start < 0) return;
    spans.push({ start, end: start + needle.length, active: activeIndices.includes(index) });
  });
  if (!spans.length) return text;

  // Quotes overlap (one sentence can prove several facts), and nested <mark> elements
  // would nest highlights: overlapping spans are merged into one.
  spans.sort((a, b) => a.start - b.start);
  const merged: Span[] = [];
  for (const span of spans) {
    const last = merged[merged.length - 1];
    if (last && span.start <= last.end) {
      last.end = Math.max(last.end, span.end);
      last.active = last.active || span.active;
    } else {
      merged.push({ ...span });
    }
  }

  const out: React.ReactNode[] = [];
  let cursor = 0;
  merged.forEach((span, index) => {
    if (span.start > cursor) out.push(text.slice(cursor, span.start));
    out.push(
      <mark key={index} className={span.active ? "on" : undefined}>
        {text.slice(span.start, span.end)}
      </mark>,
    );
    cursor = span.end;
  });
  if (cursor < text.length) out.push(text.slice(cursor));
  return out;
}

/** One `dt`/`dd` pair, or nothing at all when the fact is not there — never an empty row. */
function factRow(
  label: string,
  value: string | number | null | undefined,
  suffix = "",
): React.JSX.Element | null {
  if (value === null || value === undefined || value === "") return null;
  return (
    <>
      <dt>{label}</dt>
      <dd dir="auto">
        {value}
        {suffix}
      </dd>
    </>
  );
}

interface LeadCardProps {
  lead: Lead;
  /** Clock the "3 h ago" column is measured against; null until the client has mounted. */
  now: Date | null;
  onChanged: (lead: Lead) => void;
}

/**
 * One request in full: its text with the quotes marked inside it, the extracted facts, the
 * reasons behind the priority, the draft reply, and the two decision buttons.
 */
export default function LeadCard({ lead, now, onChanged }: LeadCardProps): React.JSX.Element {
  const [busy, setBusy] = useState<"approve" | "disagree" | null>(null);
  const [showReason, setShowReason] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [activeReason, setActiveReason] = useState<number | null>(null);
  const [allEvidence, setAllEvidence] = useState(false);

  useEffect(() => {
    setShowReason(false);
    setReason("");
    setError(null);
    setActiveReason(null);
    setAllEvidence(false);
  }, [lead.id]);

  async function run(action: "approve" | "disagree"): Promise<void> {
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
  const quotes = useMemo(
    () => lead.evidence.filter((item) => item.kind === "quote").map((item) => item.value),
    [lead.evidence],
  );
  const links = lead.reason_links ?? lead.reasons.map((text) => ({ text, quotes: [] }));
  const activeQuotes = activeReason === null ? [] : links[activeReason]?.quotes ?? [];

  const serving = lead.serving;

  // Измеряем сам факт обрезки, а не предполагаем его по длине строки: перенос зависит от
  // ширины колонки, языка и подсветки цитаты, и любая оценка «по символам» врёт.
  const textRef = useRef<HTMLDivElement>(null);
  const [textClipped, setTextClipped] = useState(false);
  useEffect(() => {
    const node = textRef.current;
    setTextClipped(node ? node.scrollHeight > node.clientHeight + 1 : false);
  }, [lead.id, lead.text, activeQuotes]);

  return (
    <div className="panel">
      <div className="card-head">
        <div className="card-head-row">
          <TierChip tier={lead.tier} />
          {/* Машинного id в шапке нет намеренно: моноширинный `edge-02` на самом видном
              месте читается как лог и выдаёт внутренние имена тест-кейсов. Значение
              лежит в раскрытии «How this was scored» как `request id`. */}
          <Chip title="Channel the request arrived from">{CHANNEL_LABEL[lead.channel] ?? lead.channel}</Chip>
          <Chip title="Language detected in the request text">{lead.language}</Chip>
          {lead.is_synthetic ? <SyntheticTag /> : null}
          {lead.status === "approved" ? <span className="chip chip-status">sent to CRM</span> : null}
          {lead.status === "rejected" ? <span className="chip chip-status">disagreed</span> : null}
          <span className="lead-row-meta">{now ? timeAgo(lead.received_at, now) : ""}</span>
        </div>
      </div>

      <div className="card-body">
        <div className="card-col">
          <div className="section">
            <div className="section-title">Request text (as received)</div>
            {/* dir="auto" — направление задаёт первый сильный символ содержимого: инбокс
                смешанный, и глобальный dir="rtl" сломал бы русские и английские строки. */}
            <div
              ref={textRef}
              className={`message-text${isRtlText(lead.text) ? " rtl-block" : ""}`}
              dir="auto"
            >
              {lead.text.trim() ? markQuotes(lead.text, quotes, activeQuotes) : "— empty message —"}
            </div>
            {/* Обрезка обязана быть видна. Коробка и раньше прокручивалась, но кончалась
                на половине строки и без единого признака, что текст продолжается: на
                кадре 07 это читалось как ошибка вёрстки, и обрыв приходился ровно на
                вопрос клиента. Высота теперь кратна строке, а факт обрезки — измеряется
                и подписывается, а не подразумевается. */}
            {textClipped ? (
              <div className="note" style={{ marginTop: 4 }}>
                The request is longer than the box — scroll inside it to read the rest.
              </div>
            ) : null}
          </div>

          <div className="section">
            <div className="section-title">Extracted facts</div>
            <dl className="facts">
              <dt>asking for</dt>
              {facts.request_types.length ? (
                <dd>{facts.request_types.map((r) => REQUEST_LABEL[r] ?? r).join(", ")}</dd>
              ) : (
                <dd style={{ fontWeight: 400 }}>nothing extracted</dd>
              )}
              {factRow("people", facts.headcount)}
              {factRow("timeline", facts.timeline_days, " days")}
              {factRow("jurisdiction", facts.jurisdiction_hint)}
              {factRow("budget signal", facts.budget_hint)}
              <dt>contact in text</dt>
              <dd>{facts.has_contact ? "yes (masked before the model)" : "no"}</dd>
            </dl>
          </div>

          <div className="section">
            <div className="section-title-row">
              <span className="section-title">Evidence — quotes from the request</span>
              {lead.evidence.length > EVIDENCE_SHOWN ? (
                <button className="btn-text btn" onClick={() => setAllEvidence((v) => !v)}>
                  {allEvidence ? "show fewer" : `show all ${lead.evidence.length}`}
                </button>
              ) : null}
            </div>
            <EvidenceList
              items={allEvidence ? lead.evidence : lead.evidence.slice(0, EVIDENCE_SHOWN)}
            />
          </div>

          {/* Инженерное — под одно раскрытие (владелец, 2026-09-10): в основном потоке
              остаётся то, по чему менеджер принимает решение по лиду. Данные не выброшены:
              они честные и нужны техническому зрителю, просто в один клик от карточки. */}
          <details className="details section">
            <summary className="details-summary">How this was scored</summary>
            <div className="details-body">
              <div className="section-title">Extraction confidence</div>
              <ConfidenceBand value={facts.confidence} />

              <div className="section-title" style={{ marginTop: 16 }}>
                Provenance — what served this lead
              </div>
              <dl className="served">
                <dt>request id</dt>
                <dd>{lead.id}</dd>
                <dt>received</dt>
                <dd>{lead.received_at.replace("T", " ").replace(/\.\d+/, "").replace("Z", " UTC")}</dd>
                <dt>seed category</dt>
                <dd>{lead.category}</dd>
                {serving ? (
                  <>
                    <dt>facts by</dt>
                    <dd>{serving.model}</dd>
                    <dt>time</dt>
                    <dd>
                      {serving.latency_ms < 10
                        ? `${serving.latency_ms.toFixed(2)} ms`
                        : `${(serving.latency_ms / 1000).toFixed(2)} s`}
                    </dd>
                    <dt>cost</dt>
                    <dd>
                      {serving.provider === "offline"
                        ? `$${serving.cost_usd.toFixed(4)}`
                        : "not counted by the backend"}{" "}
                      · {serving.input_tokens}/{serving.output_tokens} tokens
                    </dd>
                    <dt>live route</dt>
                    <dd>
                      {serving.route_model} — {serving.route_reason}
                    </dd>
                    {serving.live_cost_usd_per_lead ? (
                      <>
                        <dt>on the model</dt>
                        <dd>
                          ${serving.live_cost_usd_per_lead.toFixed(4)} · ~{serving.live_latency_s} s per
                          request (measured over the whole seed, not this one)
                        </dd>
                      </>
                    ) : null}
                  </>
                ) : (
                  <>
                    <dt>facts by</dt>
                    <dd>{lead.facts_source}</dd>
                  </>
                )}
              </dl>
            </div>
          </details>
        </div>

        <div className="card-col">
          <div className="section">
            <div className="section-title-row">
              <span className="section-title">Why this priority</span>
              <span className="note">the model reads the facts; these rules are fixed, not written by it</span>
            </div>
            {links.length ? (
              <ul className="reasons">
                {links.map((item, index) => {
                  const quotable = item.quotes.length > 0;
                  return (
                    <li key={index}>
                      <button
                        className="reason-btn"
                        aria-pressed={activeReason === index}
                        disabled={!quotable}
                        title={quotable ? "Highlight the quote this is built on" : "Not derived from a quote"}
                        onClick={() => setActiveReason(activeReason === index ? null : index)}
                      >
                        {item.text}
                        {/* Цитата разворачивается у нажатой причины. У ненажатой не пишется
                            ничего: объяснять механику интерфейса под каждой строкой — это
                            служебный текст в рабочем потоке. Помечается только обратное —
                            причина, у которой цитаты быть не может. */}
                        {activeReason === index ? (
                          <span className="reason-cite" dir="auto">
                            “{quotes[item.quotes[0]]}”
                          </span>
                        ) : quotable ? null : (
                          <span className="reason-cite">no quote — this reason is about the request as a whole</span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <div className="note">Base level, no modifier applied.</div>
            )}
            {/* Причины на языке интерфейса — обязательство карточки, и невыполнимость
                этого обязательства видна, а не спрятана: у лида, оценённого до того,
                как движок начал хранить коды причин, английского текста не существует. */}
            {lead.reasons_note ? (
              <div className="note" style={{ marginTop: 8 }}>{lead.reasons_note}</div>
            ) : null}
            {lead.violations.length ? (
              <div className="notice notice-error" style={{ marginTop: 16 }}>
                <div className="notice-title">Failed the engine&rsquo;s own check — priority withheld</div>
                <ul className="reasons">
                  {lead.violations.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>

          <div className="section">
            <div className="section-title-row">
              <span className="section-title">Draft reply</span>
              <span className="chip chip-plain">{lead.reply.language.toUpperCase()}</span>
            </div>
            {lead.reply.body ? (
              <div className={`draft${isRtlText(lead.reply.body) ? " rtl-block" : ""}`} dir="auto">
                {lead.reply.body}
              </div>
            ) : (
              <div className="draft draft-empty">No reply drafted.</div>
            )}
            {lead.reply.notice ? (
              <div className="notice" style={{ marginTop: 12 }}>
                <div className="notice-title">Before sending</div>
                <div>{lead.reply.notice}</div>
              </div>
            ) : null}
            <div className="note" style={{ marginTop: 8 }}>
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
                  <button className="btn btn-primary" disabled={busy !== null} onClick={() => run("approve")}>
                    {busy === "approve" ? <span className="spinner" /> : null} Approve, send to CRM
                  </button>
                  <button className="btn btn-text" disabled={busy !== null} onClick={() => setShowReason((v) => !v)}>
                    Disagree
                  </button>
                  <span className="note">Nothing is sent to the customer — a human presses the button.</span>
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
                      {busy === "disagree" ? <span className="spinner" /> : null} Save disagreement
                    </button>
                  </div>
                ) : null}
              </>
            )}

            {error ? (
              <div style={{ marginTop: 12 }}>
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
