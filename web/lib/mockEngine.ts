// Client-side scoring used ONLY by the mock adapter, for requests typed into the demo form.
//
// DEBT(2026-09-09): this mirrors leadcentre/engine/rubric.py + score.score_inbound in
// TypeScript, so one piece of knowledge lives in two places (violates the single-source
// rule). It exists because the demo form must produce a card with no backend attached and
// the browser cannot run the Python engine. Constants below are IMPORTED VALUES copied from
// rubric.py — when NEXT_PUBLIC_API_URL is set, none of this file runs: the live adapter
// delegates to the Python engine, which stays authoritative.

import type { Evidence, Lead, LeadFacts, Reply, Tier } from "./types";

// --- constants copied from leadcentre/engine/rubric.py (see DEBT note above) ---
const URGENT_TIMELINE_DAYS = 30;
const PACKAGE_MIN_REQUEST_TYPES = 2;
const TEAM_MIN_HEADCOUNT = 5;
const LOW_CONFIDENCE = 0.5;
const TARGET_LANGUAGES = ["ru"];
// Language alone never makes a lead hot: "HIGH because it is in Russian" is a reason no
// manager believes. It only counts on top of a substantive signal.
const LANGUAGE_NEEDS_ANOTHER_SIGNAL = true;
const LADDER: Tier[] = ["LOW", "MEDIUM", "HIGH"];
const INBOUND_BASE: Tier = "MEDIUM";
const INBOUND_BASE_NO_REQUEST: Tier = "LOW";

const REQUEST_MARKERS: Record<string, string[]> = {
  office: ["офис", "рабочих мест", "рабочие места", "флекси", "flexi", "office", "desk", "коворкинг", "аренд"],
  setup: ["лицензи", "регистрац", "открыт", "компани", "фризон", "фриз зон", "freezone", "free zone", "mainland", "майнленд", "setup", "licen", "юрлиц"],
  visa: ["виз", "visa", "резидент", "emirates id", "golden", "квота"],
  accounting: ["бухгалтер", "accounting", "vat", "ндс", "corporate tax", "налог", "аудит", "audit"],
  bank: ["банк", "bank", "счет", "счёт", "account", "платёжн", "платежн"],
};

const JURISDICTIONS: [string, string[]][] = [
  ["mainland", ["mainland", "майнленд", "материк", "det"]],
  ["freezone", ["фризон", "фриз зон", "free zone", "freezone", "ifza", "meydan", "dmcc", "shams", "rakez"]],
];

const TIMELINE_PHRASES: [string, number][] = [
  ["срочно", 5], ["asap", 5], ["urgent", 5],
  ["до конца месяца", 20], ["в этом месяце", 20], ["this month", 20],
  ["на этой неделе", 5], ["this week", 5], ["завтра", 1], ["сегодня", 1],
  ["через месяц", 30], ["в октябре", 30], ["in october", 30],
  ["в ноябре", 60], ["in nov", 60],
];

const SPAM_MARKERS = ["seo", "продвижен", "крипт", "crypto", "арбитраж", "базу контактов",
  "база контактов", "резюме", "cv attached", "ищу работу", "вакансию", "сдам квартиру",
  "аренда квартиры", "подписчик"];

const BUDGET_MARKERS = ["бюджет", "budget", "готовы подписать", "ready to sign", "aed", "дирхам"];

const PHONE_RE = /\+?\d[\d\-\s()]{8,}\d/;
const EMAIL_RE = /[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}/;
const CYRILLIC_RE = /[Ѐ-ӿ]/;
const ARABIC_RE = /[؀-ۿ]/;

function step(tier: Tier, delta: number): Tier {
  const index = Math.min(Math.max(LADDER.indexOf(tier) + delta, 0), LADDER.length - 1);
  return LADDER[index];
}

function sentences(text: string): string[] {
  return text.split(/(?<=[.!?\n])\s+/).map((s) => s.trim()).filter(Boolean);
}

function quoteFor(text: string, marker: string): string | null {
  const found = sentences(text).find((s) => s.toLowerCase().includes(marker));
  return (found ?? text.trim()).slice(0, 180) || null;
}

export function detectLanguage(text: string): string {
  const cyrillic = (text.match(/[Ѐ-ӿ]/g) ?? []).length;
  const latin = (text.match(/[A-Za-z]/g) ?? []).length;
  if (ARABIC_RE.test(text) && !CYRILLIC_RE.test(text) && latin === 0) return "ar";
  if (cyrillic && latin) {
    const share = Math.min(cyrillic, latin) / (cyrillic + latin);
    if (share > 0.2) return "mixed";
  }
  return cyrillic > latin ? "ru" : "en";
}

export function extractFacts(text: string): LeadFacts {
  const low = text.toLowerCase();
  const quotes: string[] = [];
  const language = detectLanguage(text);

  if (!text.trim()) {
    return {
      request_types: [], jurisdiction_hint: null, headcount: null, timeline_days: null,
      budget_hint: null, language, is_spam: false, has_contact: false, confidence: 0,
    };
  }

  const requestTypes: string[] = [];
  for (const [request, markers] of Object.entries(REQUEST_MARKERS)) {
    const hit = markers.find((m) => low.includes(m));
    if (hit) {
      requestTypes.push(request);
      const quote = quoteFor(text, hit);
      if (quote && !quotes.includes(quote)) quotes.push(quote);
    }
  }

  let jurisdiction: string | null = null;
  for (const [name, markers] of JURISDICTIONS) {
    if (markers.some((m) => low.includes(m))) { jurisdiction = name; break; }
  }

  let headcount: number | null = null;
  const headMatch = text.match(/(\d{1,3})\s*(человек|чел\b|сотрудник\w*|рабочих мест|мест\b|ppl\b|people|employees|виз\w*|visas?)/i)
    ?? text.match(/(?:команд\w*|team)\D{0,12}(\d{1,3})/i);
  if (headMatch) {
    const value = parseInt(headMatch[1], 10);
    if (value >= 1 && value <= 500) headcount = value;
  }

  let timeline: number | null = null;
  const daysMatch = text.match(/через\s+(\d{1,3})\s*(дн|дней|дня)/i);
  const weeksMatch = text.match(/через\s+(\d{1,2})\s*недел/i);
  if (daysMatch) timeline = parseInt(daysMatch[1], 10);
  else if (weeksMatch) timeline = parseInt(weeksMatch[1], 10) * 7;
  else {
    for (const [phrase, days] of TIMELINE_PHRASES) {
      if (low.includes(phrase)) {
        timeline = days;
        const quote = quoteFor(text, phrase);
        if (quote && !quotes.includes(quote)) quotes.push(quote);
        break;
      }
    }
  }

  const budget = BUDGET_MARKERS.find((m) => low.includes(m)) ?? null;
  const isSpam = SPAM_MARKERS.some((m) => low.includes(m));
  const hasContact = PHONE_RE.test(text) || EMAIL_RE.test(text);

  const found = [requestTypes.length > 0, jurisdiction !== null, headcount !== null,
    timeline !== null, budget !== null, hasContact].filter(Boolean).length;
  let confidence = requestTypes.length ? Math.min(0.93, 0.55 + 0.09 * found) : 0.35;
  if (isSpam) confidence = 0.8;
  if (text.trim().length < 12 && !requestTypes.length) confidence = 0.2;

  return {
    request_types: requestTypes,
    jurisdiction_hint: jurisdiction,
    headcount,
    timeline_days: timeline,
    budget_hint: budget,
    language,
    is_spam: isSpam,
    has_contact: hasContact,
    confidence: Math.round(confidence * 100) / 100,
  };
}

export interface ScoreResult {
  tier: Tier;
  reasons: string[];
  evidence: Evidence[];
  violations: string[];
}

/** Mirrors score.score_inbound: three outcomes, INVALID never folded into LOW. */
export function scoreInbound(text: string, facts: LeadFacts, quotes: string[]): ScoreResult {
  const reasons: string[] = [];
  const violations: string[] = [];

  if (facts.is_spam) {
    return {
      tier: "LOW",
      reasons: ["обращение помечено как спам или не по теме"],
      evidence: quotes.map((q) => ({ kind: "quote", value: q })),
      violations: [],
    };
  }

  let tier: Tier = facts.request_types.length ? INBOUND_BASE : INBOUND_BASE_NO_REQUEST;
  if (!facts.request_types.length) reasons.push("из текста не извлечён ни один тип запроса");

  let bumps = 0;
  if (facts.timeline_days !== null && facts.timeline_days <= URGENT_TIMELINE_DAYS) {
    bumps += 1;
    reasons.push(`срок ${facts.timeline_days} дн. — не больше ${URGENT_TIMELINE_DAYS}`);
  }
  if (facts.request_types.length >= PACKAGE_MIN_REQUEST_TYPES) {
    bumps += 1;
    reasons.push(`запрошено услуг: ${facts.request_types.length} — нужен пакет`);
  }
  if (facts.headcount !== null && facts.headcount >= TEAM_MIN_HEADCOUNT) {
    bumps += 1;
    reasons.push(`команда ${facts.headcount} чел. — флекси не закроет визовую квоту`);
  }
  // Only a figure counts as a budget: the question "how much is it" is not a budget.
  if (facts.budget_hint && /\d/.test(facts.budget_hint)) {
    bumps += 1;
    reasons.push(`назван бюджет: ${facts.budget_hint.slice(0, 40)}`);
  }
  if (TARGET_LANGUAGES.includes(facts.language)) {
    if (bumps || !LANGUAGE_NEEDS_ANOTHER_SIGNAL) {
      bumps += 1;
      reasons.push(`язык обращения ${facts.language} — основная аудитория`);
    } else {
      reasons.push(`язык обращения ${facts.language}, но других признаков нет`);
    }
  }
  tier = step(tier, bumps);

  if (facts.confidence < LOW_CONFIDENCE) {
    if (LADDER.indexOf(tier) > LADDER.indexOf("MEDIUM")) tier = "MEDIUM";
    reasons.push(`уверенность извлечения ${facts.confidence.toFixed(2)} — ниже порога`);
  }

  const evidence: Evidence[] = quotes.map((q) => ({ kind: "quote", value: q }));
  if (tier === "HIGH" && evidence.length === 0) violations.push("HIGH без цитаты из обращения");
  if (tier === "HIGH" && !text.trim()) violations.push("HIGH на пустом тексте обращения");
  if (violations.length) tier = "INVALID";

  return { tier, reasons, evidence, violations };
}

export function collectQuotes(text: string, facts: LeadFacts): string[] {
  const low = text.toLowerCase();
  const quotes: string[] = [];
  for (const [, markers] of Object.entries(REQUEST_MARKERS)) {
    const hit = markers.find((m) => low.includes(m));
    if (hit) {
      const quote = quoteFor(text, hit);
      if (quote && !quotes.includes(quote)) quotes.push(quote);
    }
  }
  for (const [phrase] of TIMELINE_PHRASES) {
    if (low.includes(phrase)) {
      const quote = quoteFor(text, phrase);
      if (quote && !quotes.includes(quote)) quotes.push(quote);
      break;
    }
  }
  return quotes.slice(0, 3);
}
