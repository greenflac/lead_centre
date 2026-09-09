// Client-side facts + scoring used ONLY by the mock adapter, for requests typed into the
// demo form.
//
// DEBT(2026-09-09): this is a TypeScript port of two Python modules —
// `leadcentre/engine/facts_rules.py` (deterministic fact extraction) and
// `leadcentre/engine/score.py::score_inbound` with the thresholds of
// `leadcentre/engine/rubric.py`. One piece of knowledge in two languages, which is exactly
// the disease that made the Python side merge its two copies of the fact heuristic. It
// exists only because a browser cannot run Python with no backend attached, and it must be
// re-checked against those three files whenever the rubric moves. Last synced against
// commit ae00357 ("одна эвристика фактов на всех; горизонт срочности расширен до 60 дней")
// plus SIGNALS_FOR_HIGH = 2.
// When NEXT_PUBLIC_API_URL is set, nothing in this file runs: the Python engine decides.

import type { Evidence, LeadFacts, Tier } from "./types";

// --- thresholds mirrored from leadcentre/engine/rubric.py ---
const URGENT_TIMELINE_DAYS = 60;
const PACKAGE_MIN_REQUEST_TYPES = 2;
const TEAM_MIN_HEADCOUNT = 5;
const LOW_CONFIDENCE = 0.5;
const TARGET_LANGUAGES = ["ru"];
// A single signal is not enough to be hot: at one signal, HIGH went to 30 of 70 requests
// and stopped meaning anything.
const SIGNALS_FOR_HIGH = 1;
const LANGUAGE_NEEDS_ANOTHER_SIGNAL = true;
const LADDER: Tier[] = ["LOW", "MEDIUM", "HIGH"];
const INBOUND_BASE: Tier = "MEDIUM";
const INBOUND_BASE_NO_REQUEST: Tier = "LOW";

// --- markers mirrored from leadcentre/engine/facts_rules.py ---
const URGENT_DEFAULT_DAYS = 14;
const RULES_CONFIDENCE_MATCHED = 1.0;
const RULES_CONFIDENCE_EMPTY = 0.0;
const MIXED_SHARE = 0.2;

const SPAM_MARKERS = [
  "seo agency", "rank your website", "ищу работу", "резюме вышлю", "cv attached",
  "looking for job", "job opportunity", "арбитраж", "депозит от", "business database",
  "сдаю квартиру", "без комиссии", "buy verified",
];

const TYPE_MARKERS: [string, string[]][] = [
  ["office", ["офис", "office", "кабинет", "рабочих мест", "рабочие места", "seats", "desk",
    "флекси", "flexi", "помещение", "кв.м", "sqm", "переговорк", "unit at"]],
  ["setup", ["лицензи", "licence", "license", "регистрац", "регистрируем", "register",
    "открыть компанию", "открываем", "open company", "company formation", "фризон",
    "фриз зона", "freezone", "free zone", "mainland", "мейнленд", "юрлиц", "филиал",
    "холдинг", "تأسيس"]],
  ["visa", ["виз", "visa", "emirates id", "резидентств", "residence", "golden"]],
  ["accounting", ["бухгалт", "accounting", "bookkeeping", "аудит", "audit", "налог", " tax",
    "vat", "отчётност", "отчетност"]],
  ["bank", ["банк", "bank", "счёт в банке", "счет в банке", "платёжный шлюз",
    "payment gateway"]],
];

const BUDGET_MARKERS = [
  "бюджет", "budget", "aed", "дирхам", "готовы подписать", "договор на", "цена устроит",
  "approved",
];

const URGENT_MARKERS = [
  "срочно", "urgent", "asap", "в этом месяце", "this month", "до конца месяца",
  "до пятницы", "сегодня", "today", "как можно быстрее", "лишь бы быстро",
  "на этой неделе",
];

const MONTHS: [string, number][] = [
  ["январ", 1], ["феврал", 2], ["марта", 3], ["апрел", 4], ["мая", 5], ["июн", 6],
  ["июл", 7], ["август", 8], ["сентябр", 9], ["октябр", 10], ["ноябр", 11], ["декабр", 12],
  ["january", 1], ["february", 2], ["april", 4], ["june", 6], ["july", 7], ["august", 8],
  ["september", 9], ["october", 10], ["november", 11], ["december", 12], ["nov", 11],
  ["dec", 12],
];

const NUM_DAYS_RE = /(?:через|in|within)\s+(\d+)\s*(?:дн|дней|day|days)/i;
const NUM_WEEKS_RE = /(?:через|in|within)\s+(\d+)\s*(?:недел|week)/i;
const EXPIRES_RE = /(?:expires?|истека\w*|заканчива\w*|слетает)\D{0,25}(\d+)\s*(дн|day|week|недел)/i;
const HEADCOUNT_RE = /(\d+)\s*(?:[а-яёa-z]+\s+)?(?:человек|чел\b|людей|people|ppl|persons|seats|мест\b|сотрудник\w*|staff)/i;
const MONEY_RE = /\d[\d\s.,]*\s*(?:aed|дирхам|тысяч|k\b)/i;

// scrub_pii's detectors, used here only to answer "is there a contact in the text".
const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;
const PHONE_RE = /(?<![\w])\+?\d[\d\-\s().]{5,}\d(?![\w])/;
const MIN_PHONE_DIGITS = 9;

function step(tier: Tier, delta: number): Tier {
  const index = Math.min(Math.max(LADDER.indexOf(tier) + delta, 0), LADDER.length - 1);
  return LADDER[index];
}

/** Port of extract.detect_language: alphabet share, "mixed" only when both are substantial. */
export function detectLanguage(text: string): string {
  const letters = [...text].filter((c) => /\p{L}/u.test(c));
  if (!letters.length) return "en";
  const cyrillic = letters.filter((c) => c >= "Ѐ" && c <= "ӿ").length;
  const share = cyrillic / letters.length;
  if (share >= 1 - MIXED_SHARE) return "ru";
  if (share <= MIXED_SHARE) return "en";
  return "mixed";
}

function hasContact(text: string): boolean {
  if (EMAIL_RE.test(text)) return true;
  const phone = text.match(PHONE_RE);
  if (!phone) return false;
  return (phone[0].match(/\d/g) ?? []).length >= MIN_PHONE_DIGITS;
}

/** Port of facts_rules._timeline_days. Nothing found → null; unknown is not urgent. */
function timelineDays(text: string, receivedAt: Date): number | null {
  const low = text.toLowerCase();
  const days = low.match(NUM_DAYS_RE);
  if (days) return parseInt(days[1], 10);
  const weeks = low.match(NUM_WEEKS_RE);
  if (weeks) return parseInt(weeks[1], 10) * 7;
  const expires = low.match(EXPIRES_RE);
  if (expires) {
    const value = parseInt(expires[1], 10);
    return /^(недел|week)/i.test(expires[2]) ? value * 7 : value;
  }
  let best: number | null = null;
  for (const [marker, month] of MONTHS) {
    if (!low.includes(marker)) continue;
    const year = receivedAt.getUTCFullYear() + (month < receivedAt.getUTCMonth() + 1 ? 1 : 0);
    const target = Date.UTC(year, month - 1, 1);
    const delta = Math.round((target - Date.UTC(receivedAt.getUTCFullYear(), receivedAt.getUTCMonth(), receivedAt.getUTCDate())) / 86400000);
    if (delta >= 0 && (best === null || delta < best)) best = delta;
  }
  if (best !== null) return best;
  if (URGENT_MARKERS.some((marker) => low.includes(marker))) return URGENT_DEFAULT_DAYS;
  return null;
}

export interface ExtractionResult {
  facts: LeadFacts;
  quotes: string[];
}

/**
 * Port of facts_rules.rules_facts. `confidence` here means "markers matched", not a model's
 * probability — 1.0 or 0.0, exactly as the Python module documents.
 */
export function extractFacts(text: string, receivedAt: Date = new Date()): ExtractionResult {
  const low = text.toLowerCase();
  const quotes: string[] = [];

  // Quote what actually matched, not our retelling of it.
  const hit = (marker: string): boolean => {
    const index = low.indexOf(marker);
    if (index < 0) return false;
    const fragment = text.slice(index, index + marker.length);
    if (!quotes.includes(fragment)) quotes.push(fragment);
    return true;
  };

  const types: string[] = [];
  for (const [kind, markers] of TYPE_MARKERS) {
    let matched = false;
    for (const marker of markers) matched = hit(marker) || matched;
    if (matched) types.push(kind);
  }

  let headcount: number | null = null;
  const head = low.match(HEADCOUNT_RE);
  if (head && head.index !== undefined) {
    headcount = parseInt(head[1], 10);
    const fragment = text.slice(head.index, head.index + head[0].length);
    if (!quotes.includes(fragment)) quotes.push(fragment);
  }

  // budget_hint carries the text itself, not a paraphrase: the rubric looks for a figure
  // inside it, and a paraphrase would silently swallow that signal.
  const budgetParts: string[] = [];
  const money = low.match(MONEY_RE);
  if (money && money.index !== undefined) {
    const fragment = text.slice(money.index, money.index + money[0].length).trim();
    budgetParts.push(fragment);
    if (!quotes.includes(fragment)) quotes.push(fragment);
  }
  for (const marker of BUDGET_MARKERS) {
    if (hit(marker)) {
      const index = low.indexOf(marker);
      budgetParts.push(text.slice(index, index + marker.length));
    }
  }
  const budget = [...new Set(budgetParts)].join("; ") || null;

  let isSpam = false;
  for (const marker of SPAM_MARKERS) isSpam = hit(marker) || isSpam;

  const facts: LeadFacts = {
    request_types: types,
    jurisdiction_hint: null,
    headcount,
    timeline_days: timelineDays(text, receivedAt),
    budget_hint: budget,
    language: detectLanguage(text),
    is_spam: isSpam,
    has_contact: hasContact(text),
    confidence: quotes.length ? RULES_CONFIDENCE_MATCHED : RULES_CONFIDENCE_EMPTY,
  };

  return { facts, quotes: [...new Set(quotes)] };
}

export interface ScoreResult {
  tier: Tier;
  reasons: string[];
  evidence: Evidence[];
  violations: string[];
}

/** Port of score.score_inbound. Three outcomes: INVALID is never folded into LOW. */
export function scoreInbound(text: string, facts: LeadFacts, quotes: string[]): ScoreResult {
  const reasons: string[] = [];
  const violations: string[] = [];
  const evidence: Evidence[] = quotes.map((value) => ({ kind: "quote", value }));

  if (facts.is_spam) {
    return {
      tier: "LOW",
      reasons: ["обращение помечено как спам или не по теме"],
      evidence,
      violations: [],
    };
  }

  let tier: Tier = facts.request_types.length ? INBOUND_BASE : INBOUND_BASE_NO_REQUEST;
  if (!facts.request_types.length) reasons.push("из текста не извлечён ни один тип запроса");

  let substantive = 0;
  if (facts.timeline_days !== null && facts.timeline_days <= URGENT_TIMELINE_DAYS) {
    substantive += 1;
    reasons.push(`срок ${facts.timeline_days} дн. — не больше ${URGENT_TIMELINE_DAYS}`);
  }
  if (facts.request_types.length >= PACKAGE_MIN_REQUEST_TYPES) {
    substantive += 1;
    reasons.push(`запрошено услуг: ${facts.request_types.length} — нужен пакет`);
  }
  if (facts.headcount !== null && facts.headcount >= TEAM_MIN_HEADCOUNT) {
    substantive += 1;
    reasons.push(`команда ${facts.headcount} чел. — флекси не закроет визовую квоту`);
  }
  // Only a figure counts as a budget: "how much does it cost" is a question, not a budget.
  if (facts.budget_hint && /\d/.test(facts.budget_hint)) {
    substantive += 1;
    reasons.push(`назван бюджет: ${facts.budget_hint.slice(0, 40)}`);
  }

  // Language is a garnish, never a reason on its own.
  if (TARGET_LANGUAGES.includes(facts.language)) {
    if (substantive || !LANGUAGE_NEEDS_ANOTHER_SIGNAL) {
      reasons.push(`язык обращения ${facts.language} — основная аудитория`);
    } else {
      reasons.push(`язык обращения ${facts.language}, но других признаков нет`);
    }
  }

  // Two distinct reasons to step up, deliberately not merged: a set of signals makes a
  // request hot, while a single signal only rescues a request from LOW.
  const enoughSignals = substantive >= SIGNALS_FOR_HIGH;
  const rescuedFromLow = substantive > 0 && tier === "LOW";
  if (enoughSignals || rescuedFromLow) tier = step(tier, 1);

  if (facts.confidence < LOW_CONFIDENCE) {
    if (LADDER.indexOf(tier) > LADDER.indexOf("MEDIUM")) tier = "MEDIUM";
    reasons.push(`уверенность извлечения ${facts.confidence.toFixed(2)} — ниже порога`);
  }

  if (tier === "HIGH" && evidence.length === 0) violations.push("HIGH без цитаты из обращения");
  if (tier === "HIGH" && !text.trim()) violations.push("HIGH на пустом тексте обращения");
  if (violations.length) tier = "INVALID";

  return { tier, reasons, evidence, violations };
}
