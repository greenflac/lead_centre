"use client";

import { useState } from "react";
import { isMock, postLead, type PipelineStep } from "../lib/api";
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
  // Сколько шагов конвейера действительно отработало. Шаг зажигается по факту исполнения,
  // а не по таймеру: индикатор, который движется сам по себе, измеряет не работу.
  const [step, setStep] = useState<number>(-1);

  async function submit() {
    setBusy(true);
    setError(null);
    setStep(-1);
    try {
      const lead = await postLead(text, channel, (done: PipelineStep) => setStep(done));
      setResult(lead);
      onCreated(lead);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError("unknown", String(caught)));
    } finally {
      setBusy(false);
    }
  }

  const STEPS = [
    ["Mask contacts.", "Phone numbers and e-mail addresses are cut out of the text before anything is sent to the model."],
    ["Extract facts.", "Services asked for, head count, timeline, jurisdiction, language, plus a confidence figure."],
    ["Score.", "The model proposes facts, the code decides the priority — a fixed rubric with reasons and invariants, not the model’s opinion."],
    ["Draft a reply.", "In the customer’s language, with price ranges taken from the demo price list and never invented."],
  ];

  return (
    <div style={{ marginTop: 16 }}>
      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">New request</span>
          <span className="note">
            Paste any text a customer could send. The card below is built the same way as every card in the inbox.
          </span>
        </div>
        <div className="new-request-body">
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
                <span className="note">Start from:</span>
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
                <span className="note">Type or pick a sample first.</span>
              ) : null}
            </div>
          </div>

          <aside className="pipeline">
            <div className="section-title">What happens on submit</div>
            <ol className="pipeline-steps">
              {STEPS.map(([title, body], index) => {
                const done = index <= step;
                const active = busy && index === step + 1;
                return (
                  <li key={title} className={done ? "done" : active ? "active" : ""}>
                    <span className="pipeline-mark">
                      {done ? "\u2713" : active ? <span className="spinner" /> : index + 1}
                    </span>
                    <span>
                      <strong>{title}</strong> {body}
                    </span>
                  </li>
                );
              })}
            </ol>
            <div className="note">
              A request with no evidence quote cannot be raised to HIGH: the engine returns &ldquo;not scored&rdquo;
              instead of guessing.
            </div>
            {isMock ? (
              <div className="note" style={{ marginTop: 8 }}>
                <strong>Mock mode:</strong> no backend is attached, so steps 1–4 run in the browser against the same
                rubric and the same demo price list. Connect the Python backend and it does exactly the same work
                instead, with no change to what you see here.
              </div>
            ) : null}
          </aside>
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
        <div className="notice" style={{ marginTop: 16 }}>
          <div className="notice-title">
            <span className="spinner" /> Working — step {Math.min(step + 2, 4)} of 4
          </div>
          The pipeline is listed on the right; a step lights up when it has actually finished.
        </div>
      ) : null}

      {result && !busy ? (
        <div style={{ marginTop: 16 }}>
          <LeadCard lead={result} now={now} onChanged={(lead) => { setResult(lead); onLeadChanged(lead); }} />
        </div>
      ) : null}

      {!result && !busy && !error ? (
        <div className="notice" style={{ marginTop: 16 }}>
          <div className="notice-title">No request scored yet</div>
          Submit a request and its card appears here — priority, reasons, evidence quotes and a draft reply.
        </div>
      ) : null}
    </div>
  );
}
