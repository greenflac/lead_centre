// The single API surface of the dashboard: six functions, one environment variable.
// Three outcomes, not two: data back, or an ApiError carrying a kind the UI can explain.

import companiesJson from "../mock/companies.json";
import leadsJson from "../mock/leads.json";
import statsJson from "../mock/stats.json";
import {
  normalizeCard,
  normalizeCompany,
  normalizePostedLead,
  normalizeStats,
  unwrap,
} from "./live";
import {
  extractFacts,
  linkReasons,
  REASON_UI_LANGUAGE,
  renderReasons,
  routeForText,
  scoreInbound,
} from "./mockEngine";
import { draftReply } from "./mockReply";
import { ApiError, type Company, type Lead, type LeadStatus, type Stats } from "./types";

const TARGET = (process.env.NEXT_PUBLIC_API_URL ?? "").trim().replace(/\/$/, "");

export const isMock = TARGET === "";
export const apiMode = isMock ? "mock" : "live";
export const apiBase = TARGET;

// Why same-origin: the backend sends no CORS headers, so next.config.mjs proxies /api/backend.
const BASE = isMock ? "" : "/api/backend";

// Simulated pipeline latency, so the demo form behaves like the live one. CHOSEN: 900 ms.
const MOCK_LATENCY_MS = 900;

/** Pipeline steps the form reports, in the order they execute. */
export type PipelineStep = 0 | 1 | 2 | 3;
export const PIPELINE_STEPS = 4;

const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

// Mock-mode store: decisions show at once and vanish on reload, there being no backend.
let mockLeads: Lead[] = (leadsJson as Lead[]).map((lead) => ({ ...lead }));

/** Anything `fetch` throws is a reachability failure; everything else already has a kind. */
function classifyError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  const message = error instanceof Error ? error.message : String(error);
  return new ApiError("network", "Cannot reach the Lead Centre API", message);
}

/** One fetch with the envelope rules applied: data back, or an ApiError the UI can explain. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    // Why conditional: Content-Type on a GET forces a CORS preflight the backend cannot answer.
    const headers: Record<string, string> = { ...((init?.headers as Record<string, string>) ?? {}) };
    if (init?.body !== undefined) headers["Content-Type"] = "application/json";
    response = await fetch(`${BASE}${path}`, { ...init, headers, cache: "no-store" });
  } catch (error) {
    throw classifyError(error);
  }
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    // 402/429 mean the provider refused on budget or rate limits, not a broken dashboard.
    if (response.status === 402 || response.status === 429) {
      throw new ApiError(
        "budget",
        "The language-model provider is out of budget or rate-limited",
        body || `HTTP ${response.status}`,
      );
    }
    throw new ApiError("server", `Lead Centre API returned HTTP ${response.status}`, body);
  }
  return (await response.json()) as T;
}

/** `GET /leads` — every scored request, newest envelope flattened to `Lead`. */
export async function getLeads(): Promise<Lead[]> {
  if (isMock) {
    await sleep(120);
    return mockLeads.map((lead) => ({ ...lead }));
  }
  const payload = await request<unknown>("/leads");
  const rows = unwrap(payload, "leads");
  return (Array.isArray(rows) ? rows : []).map(normalizeCard);
}

export async function getCompanies(): Promise<Company[]> {
  if (isMock) {
    await sleep(120);
    return companiesJson as Company[];
  }
  const payload = await request<unknown>("/companies");
  const rows = unwrap(payload, "companies");
  return (Array.isArray(rows) ? rows : []).map(normalizeCompany);
}

/** `GET /stats` — the counters behind the three numbers at the top of the screen. */
export async function getStats(): Promise<Stats> {
  if (isMock) {
    await sleep(80);
    return statsJson as Stats;
  }
  const payload = await request<unknown>("/stats");
  unwrap(payload, "outcome");
  return normalizeStats(payload);
}

/**
 * Scores one request. `onStep` fires when a stage has actually finished, never on a timer;
 * against the live backend the stages are unobservable, so no step is reported at all.
 */
export async function postLead(
  text: string,
  channel: string,
  onStep?: (step: PipelineStep) => void,
): Promise<Lead> {
  if (isMock) {
    const stage = MOCK_LATENCY_MS / PIPELINE_STEPS;
    await sleep(stage);
    // Contacts are located before any parsing; extractFacts.hasContact does that.
    onStep?.(0);
    await sleep(stage);
    const { facts, quotes } = extractFacts(text);
    onStep?.(1);
    await sleep(stage);
    const scored = scoreInbound(text, facts, quotes);
    onStep?.(2);
    await sleep(stage);
    const reply = draftReply(facts, scored.tier, text);
    onStep?.(3);
    const lead: Lead = {
      id: `new-${Date.now().toString(36)}`,
      channel,
      text,
      language: facts.language,
      category: "typed in demo",
      received_at: new Date().toISOString(),
      is_synthetic: true,
      tier: scored.tier,
      // Reasons in the interface language; the request text and the draft stay in the customer's.
      reasons: renderReasons(scored.reasonItems, REASON_UI_LANGUAGE),
      reason_links: linkReasons(scored.reasonItems, quotes, facts),
      evidence: scored.evidence,
      violations: scored.violations,
      facts,
      facts_source: "offline_heuristic",
      serving: {
        provider: "offline",
        model: "mockEngine.ts (offline heuristic, port of facts_rules.py)",
        latency_ms: MOCK_LATENCY_MS,
        input_tokens: 0,
        output_tokens: 0,
        cost_usd: 0,
        route_model: routeForText(text).model,
        route_reason: routeForText(text).reason,
      },
      reply,
      status: "new",
    };
    mockLeads = [lead, ...mockLeads];
    return { ...lead };
  }
  const payload = await request<unknown>("/leads", {
    method: "POST",
    body: JSON.stringify({ text, channel }),
  });
  return normalizePostedLead(payload, text, channel);
}

function setMockStatus(id: string, status: LeadStatus, reason: string | null): Lead {
  const index = mockLeads.findIndex((lead) => lead.id === id);
  if (index === -1) throw new ApiError("server", `Unknown lead ${id}`);
  const updated: Lead = { ...mockLeads[index], status, decision_reason: reason };
  mockLeads = [...mockLeads.slice(0, index), updated, ...mockLeads.slice(index + 1)];
  return { ...updated };
}

/** Re-reads a card after a decision, so the screen shows what the backend actually stored. */
async function readCard(id: string, status: LeadStatus, reason: string | null): Promise<Lead> {
  const payload = await request<unknown>(`/leads/${encodeURIComponent(id)}`);
  const card = normalizeCard(unwrap(payload, "card"));
  return { ...card, status, decision_reason: reason };
}

/** `POST /leads/{id}/approve` — hand the lead and its draft to the CRM sink. */
export async function approve(id: string): Promise<Lead> {
  if (isMock) {
    await sleep(250);
    return setMockStatus(id, "approved", null);
  }
  await request<unknown>(`/leads/${encodeURIComponent(id)}/approve`, { method: "POST" });
  // Why re-read: the decision endpoints answer with a receipt, not with the stored card.
  return readCard(id, "approved", null);
}

/** `POST /leads/{id}/disagree` — record why the card is wrong, for the eval set. */
export async function disagree(id: string, reason: string): Promise<Lead> {
  if (isMock) {
    await sleep(250);
    return setMockStatus(id, "rejected", reason);
  }
  await request<unknown>(`/leads/${encodeURIComponent(id)}/disagree`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
  return readCard(id, "rejected", reason);
}
