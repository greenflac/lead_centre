"use client";

import { useState } from "react";
import { postLead } from "../lib/api";
import { ApiError, type Lead } from "../lib/types";
import LeadCard from "./LeadCard";
import { ErrorNotice } from "./ui";

const CHANNELS = [
  { value: "form", label: "Website form" },
  { value: "jivo", label: "Jivo chat" },
  { value: "whatsapp", label: "WhatsApp" },
  { value: "telegram", label: "Telegram" },
];

// Starting points for the evaluator; they are meant to be edited, not submitted as-is.
const SAMPLES: { label: string; text: string; channel: string }[] = [
  {
    label: "Urgent team relocation (RU)",
    channel: "whatsapp",
    text: "Здравствуйте! Переезжаем командой 9 человек в Дубай, нужен офис и визы на всех, лицензию тоже оформляем. Срочно, хотим закрыть в этом месяце. Бюджет есть.",
  },
  {
    label: "Package request (EN)",
    channel: "form",
    text: "We are relocating our dev team (6 people) from Yerevan to Dubai in November. We need an office, 6 employment visas and a corporate bank account. Can you handle all of it in one package?",
  },
  {
    label: "Price only (RU)",
    channel: "jivo",
    text: "скок стоит фриз зона?",
  },
  {
    label: "Off-topic",
    channel: "telegram",
    text: "Добрый день! Мы SEO-агентство, продвигаем сайты в топ Google. Готовы обсудить продвижение вашего сайта, вышлем коммерческое предложение.",
  },
];

export default function NewLeadView({
  now,
  onCreated,
  onLeadChanged,
}: {
  now: Date | null;
  onCreated: (lead: Lead) => void;
  onLeadChanged: (lead: Lead) => void;
}) {
  const [text, setText] = useState("");
  const [channel, setChannel] = useState("form");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<Lead | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const lead = await postLead(text, channel);
      setResult(lead);
      onCreated(lead);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError("unknown", String(caught)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ marginTop: 16 }}>
      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">New request</span>
          <span className="panel-note">
            Paste any text a customer could send. The card below is built the same way as every card in the inbox.
          </span>
        </div>
        <div className="card-col">
          <div className="form-grid">
            <div>
              <label className="field-label" htmlFor="request-text">
                Request text — any language, as the customer wrote it
              </label>
              <textarea
                id="request-text"
                rows={7}
                value={text}
                placeholder="Например: переезжаем командой 8 человек, нужен офис в TECOM в этом месяце, плюс визы на всех."
                onChange={(event) => setText(event.target.value)}
              />
              <div className="samples">
                <span className="panel-note">Start from:</span>
                {SAMPLES.map((sample) => (
                  <button
                    key={sample.label}
                    className="sample-btn"
                    onClick={() => {
                      setText(sample.text);
                      setChannel(sample.channel);
                    }}
                  >
                    {sample.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="form-row">
              <div className="field">
                <label className="field-label" htmlFor="channel">
                  Channel
                </label>
                <select id="channel" value={channel} onChange={(event) => setChannel(event.target.value)}>
                  {CHANNELS.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </div>
              <button className="btn btn-primary" disabled={busy || text.trim().length === 0} onClick={submit}>
                {busy ? <span className="spinner" /> : null} {busy ? "Scoring…" : "Score this request"}
              </button>
              {text.trim().length === 0 ? (
                <span className="panel-note">Type or pick a sample first.</span>
              ) : null}
            </div>
          </div>
        </div>
      </div>

      {error ? (
        <div style={{ marginTop: 16 }}>
          <ErrorNotice
            title={error.kind === "budget" ? "Model provider unavailable" : "Could not score this request"}
            message={
              error.kind === "budget"
                ? "The language-model provider refused the request on budget or rate limits. Nothing was lost — press the button again once the provider is topped up."
                : error.message
            }
            detail={error.detail}
            onRetry={submit}
          />
        </div>
      ) : null}

      {busy ? (
        <div className="notice notice-info" style={{ marginTop: 16 }}>
          <div className="notice-title">
            <span className="spinner spinner-dark" /> Working
          </div>
          Extracting facts, applying the priority rubric, drafting a reply in the customer&apos;s language.
        </div>
      ) : null}

      {result && !busy ? (
        <div style={{ marginTop: 16 }}>
          <LeadCard lead={result} now={now} onChanged={(lead) => { setResult(lead); onLeadChanged(lead); }} />
        </div>
      ) : null}

      {!result && !busy && !error ? (
        <div className="notice notice-info" style={{ marginTop: 16 }}>
          <div className="notice-title">No request scored yet</div>
          Submit a request and its card appears here — priority, reasons, evidence quotes and a draft reply.
        </div>
      ) : null}
    </div>
  );
}
