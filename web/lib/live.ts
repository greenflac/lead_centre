// Translation between the FastAPI backend and the shapes this dashboard renders.
//
// The wire shapes below are ИЗМЕРЕНО, not assumed: they were captured from
// `OFFLINE=1 python -m leadcentre.api` running locally on 2026-09-09 —
// GET /leads, POST /leads, GET /companies, GET /stats. Keep this module the only place
// that knows about the envelope, so components keep seeing one flat Lead type.
//
// The API answers in envelopes with three outcomes (ok / rejected / unavailable); a
// missing score is NOT quietly turned into LOW — it becomes INVALID ("not scored"), which
// is what the engine itself does when it cannot judge.

import { ApiError, type Company, type Evidence, type Lead, type LeadFacts, type Reply, type Stats, type Tier } from "./types";

type Json = Record<string, unknown>;

const asObject = (value: unknown): Json => (value && typeof value === "object" ? (value as Json) : {});
const asArray = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const asString = (value: unknown, fallback = ""): string => (typeof value === "string" ? value : fallback);
const asNumber = (value: unknown): number | null => (typeof value === "number" ? value : null);
const asBool = (value: unknown, fallback = false): boolean => (typeof value === "boolean" ? value : fallback);

const TIERS: Tier[] = ["HIGH", "MEDIUM", "LOW", "INVALID"];
const asTier = (value: unknown): Tier => (TIERS.includes(value as Tier) ? (value as Tier) : "INVALID");

function asEvidence(value: unknown): Evidence[] {
  return asArray(value).map((item) => {
    const row = asObject(item);
    return { kind: asString(row.kind, "evidence"), value: asString(row.value) };
  });
}

function asStrings(value: unknown): string[] {
  return asArray(value).filter((item): item is string => typeof item === "string");
}

export function normalizeFacts(value: unknown): LeadFacts {
  const facts = asObject(value);
  return {
    request_types: asStrings(facts.request_types),
    jurisdiction_hint: typeof facts.jurisdiction_hint === "string" ? facts.jurisdiction_hint : null,
    headcount: asNumber(facts.headcount),
    timeline_days: asNumber(facts.timeline_days),
    budget_hint: typeof facts.budget_hint === "string" ? facts.budget_hint : null,
    language: asString(facts.language, "en"),
    is_spam: asBool(facts.is_spam),
    has_contact: asBool(facts.has_contact),
    confidence: asNumber(facts.confidence) ?? 0,
  };
}

function normalizeReply(value: unknown): Reply {
  const reply = asObject(value);
  return {
    body: asString(reply.body),
    language: asString(reply.language, "en"),
    outcome: asString(reply.outcome, asString(reply.status, "draft")),
    needs_human: asBool(reply.needs_human),
    used_prices: asStrings(reply.used_prices),
  };
}

const STATUS_BY_REPLY: Record<string, Lead["status"]> = {
  draft: "new",
  approved: "approved",
  rejected: "rejected",
};

/** A card from GET /leads or GET /leads/{id}: {lead, score, reply}. */
export function normalizeCard(value: unknown): Lead {
  const card = asObject(value);
  const lead = asObject(card.lead);
  const score = asObject(card.score);
  const replyRaw = asObject(card.reply);
  const facts = normalizeFacts(lead.facts);
  const scored = Object.keys(score).length > 0;

  return {
    id: asString(lead.id, asString(card.lead_id, "unknown")),
    channel: asString(lead.channel, asString(lead.source, "form")),
    text: asString(lead.raw_text),
    language: asString(lead.language, facts.language),
    category: asString(lead.source, "inbound"),
    received_at: asString(lead.created_at) || `${asString(lead.received_at, "")}T00:00:00Z`,
    is_synthetic: asBool(lead.is_synthetic),
    tier: scored ? asTier(score.tier) : "INVALID",
    reasons: scored ? asStrings(score.reasons) : [],
    evidence: asEvidence(score.evidence),
    violations: scored ? asStrings(score.violations) : ["карточка без оценки: score отсутствует"],
    facts,
    facts_source: asString(asObject(score.usage).provider, asString(score.model, "llm")),
    reply: normalizeReply(replyRaw),
    status: STATUS_BY_REPLY[asString(replyRaw.status, "draft")] ?? "new",
  };
}

/** The flat answer of POST /leads: {lead_id, channel, language, facts, score, reply}. */
export function normalizePostedLead(value: unknown, text: string, channel: string): Lead {
  const body = asObject(value);
  const score = asObject(body.score);
  const facts = normalizeFacts(body.facts);
  const extraction = asObject(body.extraction);
  return {
    id: asString(body.lead_id, `new-${Date.now().toString(36)}`),
    channel: asString(body.channel, channel),
    text,
    language: asString(body.language, facts.language),
    category: "typed in demo",
    received_at: new Date().toISOString(),
    is_synthetic: asBool(body.is_synthetic),
    tier: asTier(score.tier),
    reasons: asStrings(score.reasons),
    evidence: asEvidence(score.evidence),
    violations: asStrings(score.violations),
    facts,
    facts_source: asString(extraction.provider, "llm"),
    reply: normalizeReply(body.reply),
    status: "new",
  };
}

/** A row of GET /companies: registry fields plus a `facts` blob holding the scoring. */
export function normalizeCompany(value: unknown): Company {
  const row = asObject(value);
  const facts = asObject(row.facts);
  return {
    id: asString(row.external_id),
    name: asString(row.name),
    city: asString(row.city),
    country: asString(facts.country, "AE"),
    address_lines: asStrings(facts.address_lines),
    registrar_id: typeof row.registrar_id === "string" ? row.registrar_id : null,
    license_no: typeof row.license_no === "string" ? row.license_no : null,
    created_on: typeof row.created_on === "string" ? row.created_on : null,
    next_renewal_on: typeof row.next_renewal_on === "string" ? row.next_renewal_on : null,
    registration_status: asString(row.registration_status),
    entity_active: asBool(facts.entity_active, true),
    tier: asTier(facts.tier),
    address_type: asString(facts.address_type, "A0_unknown"),
    event: asString(facts.event, "B0_none"),
    reasons: asStrings(facts.reasons),
    evidence: [
      ...(typeof row.external_id === "string" ? [{ kind: "lei", value: row.external_id }] : []),
      ...(typeof row.next_renewal_on === "string"
        ? [{ kind: "next_renewal_on", value: row.next_renewal_on }]
        : []),
    ],
    violations: asStrings(facts.violations),
    source: asString(row.source, "gleif"),
    is_synthetic: asBool(row.is_synthetic),
  };
}

/** GET /stats counters → the strip at the top of the dashboard. */
export function normalizeStats(value: unknown): Stats {
  const body = asObject(value);
  const byTier = asObject(body.by_tier) as Record<string, number>;
  const store = asString(body.store, "backend");
  return {
    generated_at: new Date().toISOString(),
    leads: {
      checked: asNumber(body.scores) ?? asNumber(body.leads) ?? 0,
      by_tier: byTier,
      violations: asNumber(body.score_violations) ?? 0,
      synthetic: asNumber(body.leads_synthetic) ?? 0,
      real: asNumber(body.leads_real) ?? 0,
      source: store,
    },
    companies: {
      checked: asNumber(body.companies) ?? 0,
      by_tier: {},
      violations: 0,
      skipped: 0,
      fetched: asNumber(body.companies) ?? 0,
      source: store,
    },
    notes: [asString(asObject(body.store_health).detail)].filter(Boolean),
  };
}

/**
 * Reads the envelope. `unavailable` is the backend saying "could not", which is neither
 * data nor a crash — it becomes an ApiError the UI explains, never an empty list.
 */
export function unwrap(payload: unknown, key: string): unknown {
  const body = asObject(payload);
  const outcome = asString(body.outcome, "ok");
  if (outcome === "unavailable" || outcome === "rejected") {
    const code = asString(body.code);
    const message = asString(body.message, `backend returned ${outcome}`);
    throw new ApiError(code === "provider_budget" ? "budget" : "server", message, code);
  }
  return key in body ? body[key] : body;
}
