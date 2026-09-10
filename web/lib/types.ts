// Wire shapes. Kept in one place so the mock adapter and the live adapter cannot drift.
// Mirrors leadcentre/models.py (Tier, InboundMessage, LeadFacts, Score) — when the Python
// contract changes, this file changes with it.

export type Tier = "HIGH" | "MEDIUM" | "LOW" | "INVALID";

export type Channel = "jivo" | "whatsapp" | "telegram" | "form";

export interface Evidence {
  kind: string;
  value: string;
}

export interface LeadFacts {
  request_types: string[];
  jurisdiction_hint: string | null;
  headcount: number | null;
  timeline_days: number | null;
  budget_hint: string | null;
  language: string;
  is_spam: boolean;
  has_contact: boolean;
  confidence: number;
}

export interface Reply {
  body: string;
  language: string;
  /** draft | questions | spam_skipped | no_draft_needs_human — more than two outcomes. */
  outcome: string;
  needs_human: boolean;
  used_prices: string[];
  /** What the manager must know before sending (e.g. Arabic not read by a native). */
  notice?: string;
}

/** A reason together with the quotes it is derived from. Empty list = not quotable. */
export interface ReasonLink {
  text: string;
  quotes: number[];
}

/** What actually served the lead: model, latency, cost — plus the routing decision. */
export interface Serving {
  provider: string;
  model: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  /** Model the live router picks for this text, and the rule that picked it. */
  route_model: string;
  route_reason: string;
  /** Measured on the whole seed against the live pipeline, not on this request. */
  live_cost_usd_per_lead?: number;
  live_latency_s?: number;
}

export type LeadStatus = "new" | "approved" | "rejected";

export interface Lead {
  id: string;
  channel: string;
  text: string;
  language: string;
  category: string;
  received_at: string;
  is_synthetic: boolean;
  tier: Tier;
  reasons: string[];
  /**
   * Language the strings in `reasons` are actually in — what happened, not what was meant
   * (Е2). The interface is English, so anything but "en" has to be visible on the card:
   * a lead scored before the engine kept reason codes can only be shown as stored (RU).
   */
  reasons_language?: string;
  /** Why the reasons are not in the interface language. Empty when they are. */
  reasons_note?: string;
  /** reasons[i] with the indices of evidence[] that prove it. */
  reason_links?: ReasonLink[];
  evidence: Evidence[];
  violations: string[];
  facts: LeadFacts;
  /** "offline_heuristic" in mock mode, "llm" when the backend extracted the facts. */
  facts_source: string;
  serving?: Serving;
  reply: Reply;
  status: LeadStatus;
  decision_reason?: string | null;
}

export interface Company {
  id: string;
  name: string;
  city: string;
  country: string;
  address_lines: string[];
  registrar_id: string | null;
  license_no: string | null;
  created_on: string | null;
  next_renewal_on: string | null;
  registration_status: string;
  entity_active: boolean;
  tier: Tier;
  address_type: string;
  event: string;
  reasons: string[];
  /** reasons[i] with the indices of evidence[] that prove it. */
  reason_links?: ReasonLink[];
  evidence: Evidence[];
  violations: string[];
  source: string;
  is_synthetic: boolean;
}

export interface TierCounts {
  HIGH?: number;
  MEDIUM?: number;
  LOW?: number;
  INVALID?: number;
}

export interface Stats {
  generated_at: string;
  leads: {
    checked: number;
    by_tier: TierCounts;
    violations: number;
    synthetic: number;
    real: number;
    source: string;
  };
  companies: {
    checked: number;
    by_tier: TierCounts;
    violations: number;
    skipped: number;
    fetched: number;
    source: string;
  };
  notes: string[];
}

/** Errors carry a kind so the UI can say what is wrong instead of showing a blank page. */
export type ApiErrorKind = "budget" | "network" | "server" | "unknown";

export class ApiError extends Error {
  kind: ApiErrorKind;
  detail: string;

  constructor(kind: ApiErrorKind, message: string, detail = "") {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.detail = detail;
  }
}
