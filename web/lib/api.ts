// The single API surface of the dashboard. Components never call fetch and never import
// mock JSON: they call these six functions. Swapping mock for the live backend is one
// environment variable, NEXT_PUBLIC_API_URL — nothing else in the UI changes.
//
// Three outcomes, not two: a call returns data, or throws ApiError with a kind the UI can
// explain ("provider out of budget" is not the same as "backend down").

import leadsJson from "../mock/leads.json";
import companiesJson from "../mock/companies.json";
import statsJson from "../mock/stats.json";
import { ApiError, type Company, type Lead, type LeadStatus, type Stats } from "./types";
import { extractFacts, scoreInbound } from "./mockEngine";
import { draftReply } from "./mockReply";
import {
  normalizeCard,
  normalizeCompany,
  normalizePostedLead,
  normalizeStats,
  unwrap,
} from "./live";

const TARGET = (process.env.NEXT_PUBLIC_API_URL ?? "").trim().replace(/\/$/, "");

export const isMock = TARGET === "";
export const apiMode = isMock ? "mock" : "live";
export const apiBase = TARGET;

// Requests go to this origin and Next forwards them (see the rewrite in next.config.mjs):
// the backend has no CORS headers, and a proxied same-origin call needs none.
const BASE = isMock ? "" : "/api/backend";

// Simulated latency of the demo pipeline, so the form behaves like the real thing
// (extract + score + draft take seconds against the LLM). ВЫБРАНО: 900 ms.
const MOCK_LATENCY_MS = 900;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// In-memory store for mock mode: approvals and disagreements made during a demo are
// visible immediately and disappear on reload, because there is no backend to keep them.
let mockLeads: Lead[] = (leadsJson as Lead[]).map((lead) => ({ ...lead }));

function classifyError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  const message = error instanceof Error ? error.message : String(error);
  return new ApiError("network", "Cannot reach the Lead Centre API", message);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    // Content-Type is set only when there is a body: sending it on a plain GET turns
    // every cross-origin read into a CORS preflight, and a backend without an OPTIONS
    // handler then fails with a bare "Failed to fetch" (observed against a stub API).
    const headers: Record<string, string> = { ...((init?.headers as Record<string, string>) ?? {}) };
    if (init?.body !== undefined) headers["Content-Type"] = "application/json";
    response = await fetch(`${BASE}${path}`, { ...init, headers, cache: "no-store" });
  } catch (error) {
    throw classifyError(error);
  }
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    // 402/429 from the API mean the LLM provider refused on budget or rate limits.
    // The manager must read that as "top up the provider", not as a broken dashboard.
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

export async function getStats(): Promise<Stats> {
  if (isMock) {
    await sleep(80);
    return statsJson as Stats;
  }
  const payload = await request<unknown>("/stats");
  unwrap(payload, "outcome");
  return normalizeStats(payload);
}

export async function postLead(text: string, channel: string): Promise<Lead> {
  if (isMock) {
    await sleep(MOCK_LATENCY_MS);
    const { facts, quotes } = extractFacts(text);
    const scored = scoreInbound(text, facts, quotes);
    const reply = draftReply(facts, scored.tier);
    const lead: Lead = {
      id: `new-${Date.now().toString(36)}`,
      channel,
      text,
      language: facts.language,
      category: "typed in demo",
      received_at: new Date().toISOString(),
      is_synthetic: true,
      tier: scored.tier,
      reasons: scored.reasons,
      evidence: scored.evidence,
      violations: scored.violations,
      facts,
      facts_source: "offline_heuristic",
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

async function readCard(id: string, status: LeadStatus, reason: string | null): Promise<Lead> {
  const payload = await request<unknown>(`/leads/${encodeURIComponent(id)}`);
  const card = normalizeCard(unwrap(payload, "card"));
  return { ...card, status, decision_reason: reason };
}

export async function approve(id: string): Promise<Lead> {
  if (isMock) {
    await sleep(250);
    return setMockStatus(id, "approved", null);
  }
  await request<unknown>(`/leads/${encodeURIComponent(id)}/approve`, { method: "POST" });
  // The decision endpoints answer with a CRM/eval receipt, not the card, so the card is
  // re-read: what the dashboard shows next is what the backend actually stored (Е2).
  return readCard(id, "approved", null);
}

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
