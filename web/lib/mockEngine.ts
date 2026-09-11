// Fact extraction and scoring for the mock adapter only: a port of
// leadcentre/engine/facts_rules.py, score.py::score_inbound and rubric.py.
// Why a second copy exists: a browser cannot run Python with no backend attached.
// web/scripts/crosscheck.py compares both paths field by field and CI fails on a drift.
// With NEXT_PUBLIC_API_URL set nothing here runs — the Python engine decides.

import type { Evidence, LeadFacts, Tier } from "./types";

// Thresholds mirrored from leadcentre/engine/rubric.py.
const URGENT_TIMELINE_DAYS = 60;
const PACKAGE_MIN_REQUEST_TYPES = 2;
const TEAM_MIN_HEADCOUNT = 5;
const LOW_CONFIDENCE = 0.5;
const TARGET_LANGUAGES = ["ru"];
// Why 2: at one signal HIGH covered 30 of 70 requests and stopped meaning anything.
const SIGNALS_FOR_HIGH = 2;
const LANGUAGE_NEEDS_ANOTHER_SIGNAL = true;
const LADDER: Tier[] = ["LOW", "MEDIUM", "HIGH"];
const INBOUND_BASE: Tier = "MEDIUM";
const INBOUND_BASE_NO_REQUEST: Tier = "LOW";

// Routing, mirrored from leadcentre/engine/extract.py.
const LONG_MESSAGE_CHARS = 600;
const SHORT_MODEL = "claude-haiku-4-5-20251001";
const LONG_MODEL = "claude-opus-5";

/** Which model the live pipeline would pick, and why. A routing rule, not a claim a model ran. */
export function routeForText(text: string): { model: string; reason: string } {
  const length = text.length;
  return length > LONG_MESSAGE_CHARS
    ? { model: LONG_MODEL, reason: `long request: ${length} chars > ${LONG_MESSAGE_CHARS}` }
    : { model: SHORT_MODEL, reason: `short request: ${length} chars <= ${LONG_MESSAGE_CHARS}` };
}

// Markers mirrored from leadcentre/engine/facts_rules.py.
const RULES_CONFIDENCE_MATCHED = 1.0;
const RULES_CONFIDENCE_EMPTY = 0.0;
const MIXED_SHARE = 0.2;

const SPAM_MARKERS = [
  "seo agency", "rank your website", "ищу работу", "резюме вышлю", "cv attached",
  "looking for job", "job opportunity", "арбитраж", "депозит от", "business database",
  "сдаю квартиру", "без комиссии", "buy verified",
];

const TYPE_MARKERS: [string, string[]][] = [
  ["office", ["офис", "office", "кабинет", "рабочих мест", "рабочие места", "рабочее место",
    "seats", "desk", "переговорн",
    "флекси", "flexi", "помещение", "кв.м", "sqm", "переговорк", "unit at"]],
  ["setup", ["лицензи", "licence", "license", "регистрац", "регистрируем", "зарегистр", "register",
    "открыть компанию", "открытие компании", "открываем", "open company",
    "company formation", "incorporation", "фризон",
    "фриз зона", "freezone", "free zone", "mainland", "мейнленд", "юрлиц", "филиал",
    "холдинг", "تأسيس"]],
  ["visa", ["виз", "visa", "emirates id", "резидентств", "residence", "golden"]],
  ["accounting", ["бухгалт", "бухучёт", "бухучет", "accounting", "bookkeeping", "аудит", "audit",
    "налог", " tax", "vat",
    "отчётност", "отчетност"]],
  ["renewal", ["продлен", "продли", "продлева", "renew", "истекает", "истекл", "истёк", "истек",
    "заканчивается", "expires", "expiry", "expiring", "ежегодн", "annual fee"]],
  ["bank", ["банк", "bank", "счёт в банке", "счет в банке", "открыть счёт", "открыть счет",
    "открытие счёта", "открытие счета", "open an account", "current account",
    "платёжный шлюз", "payment gateway"]],
];

// Why separate: licence words read the same for a first setup and for a renewal.
const LICENCE_MARKERS = ["лицензи", "licence", "license"];

const BUDGET_MARKERS = [
  "бюджет", "budget", "aed", "дирхам", "готовы подписать", "договор на", "цена устроит",
  "approved",
];

// Why two lists: "this month" yields a date counted from the request, "urgent" yields none.
const FRIDAY = 4; // Monday-first index

/** Days left to the last day of the month the request arrived in. */
function daysToEndOfMonth(at: Date): number {
  const last = Date.UTC(at.getUTCFullYear(), at.getUTCMonth() + 1, 0);
  return Math.round((last - Date.UTC(at.getUTCFullYear(), at.getUTCMonth(), at.getUTCDate())) / 86400000);
}

/** Monday = 0, as in Python `date.weekday()`; JS counts from Sunday. */
function weekdayMondayFirst(at: Date): number {
  return (at.getUTCDay() + 6) % 7;
}

/** Days left to the end of the ISO week the request arrived in. */
function daysToEndOfWeek(at: Date): number {
  return 6 - weekdayMondayFirst(at);
}

/** Days to the next Friday, counting the day of the request itself. */
function daysToNextFriday(at: Date): number {
  return (FRIDAY - weekdayMondayFirst(at) + 7) % 7;
}

const DEADLINE_MARKERS: [string, (at: Date) => number][] = [
  ["в этом месяце", daysToEndOfMonth],
  ["до конца месяца", daysToEndOfMonth],
  ["до конца этого месяца", daysToEndOfMonth],
  ["this month", daysToEndOfMonth],
  ["на этой неделе", daysToEndOfWeek],
  ["this week", daysToEndOfWeek],
  ["до пятницы", daysToNextFriday],
  ["by friday", daysToNextFriday],
  ["сегодня", () => 0],
  ["today", () => 0],
];

const VAGUE_URGENCY_MARKERS = [
  "срочно", "urgent", "asap", "как можно быстрее", "лишь бы быстро",
];

/** Deadline in days from the markers present; several markers yield the nearest one. */
function deadlineDays(low: string, receivedAt: Date): number | null {
  const found = DEADLINE_MARKERS.filter(([m]) => low.includes(m)).map(([, rule]) => rule(receivedAt));
  return found.length ? Math.min(...found) : null;
}

/** Urgency claimed in words only. Why exclusive with a deadline: a card must not say "no date given" when one was. */
function wordlessUrgency(low: string, timelineDaysValue: number | null): boolean {
  if (timelineDaysValue !== null) return false;
  if (DEADLINE_MARKERS.some(([m]) => low.includes(m))) return false;
  return VAGUE_URGENCY_MARKERS.some((m) => low.includes(m));
}

const MONTHS: [string, number][] = [
  ["январ", 1], ["феврал", 2], ["марта", 3], ["апрел", 4], ["мая", 5], ["июн", 6],
  ["июл", 7], ["август", 8], ["сентябр", 9], ["октябр", 10], ["ноябр", 11], ["декабр", 12],
  ["january", 1], ["february", 2], ["april", 4], ["june", 6], ["july", 7], ["august", 8],
  ["september", 9], ["october", 10], ["november", 11], ["december", 12], ["nov", 11],
  ["dec", 12],
];

const NUM_DAYS_RE = /(?:через|in|within)\s+(\d+)\s*(?:дн|дней|day|days)/i;
const NUM_WEEKS_RE = /(?:через|in|within)\s+(\d+)\s*(?:недел|week)/i;
const EXPIRES_RE = /(?:expires?|истека[а-яё]*|заканчива[а-яё]*|слетает)\D{0,25}(\d+)\s*(дн|day|week|недел)/i;
// Why an explicit lookahead: JS word boundaries are ASCII-only, Python's are not.
const WORD_TAIL = "(?![0-9A-Za-z_\\u0400-\\u04ff])";
const HEADCOUNT_RE = new RegExp(
  `(\\d+)\\s*(?:[а-яёa-z]+\\s+){0,2}?(?:человек|чел${WORD_TAIL}|людей|people|ppl${WORD_TAIL}|persons|seats|мест${WORD_TAIL}|сотрудник[а-яё]*|staff|партнёр[а-яё]*|партнер[а-яё]*|виз[а-яё]*|visas?)`,
  "gi",
);
// Why the whole sum: trimmed to its last number, a range would read as a single figure.
const MONEY_QUALIFIERS = "(?:до|от|около|примерно|порядка|up\\s+to|around|about)\\s+";
const MONEY_UNITS = "(?:aed|дирхам[а-яё]*|тысяч[а-яё]*|k\\b)";
const MONEY_RE = new RegExp(
  `(?:${MONEY_QUALIFIERS})?\\d[\\d\\s.,]*(?:\\s*[-–—]\\s*\\d[\\d\\s.,]*)?\\s*${MONEY_UNITS}` +
    `(?:\\s+(?:aed|дирхам[а-яё]*))?`,
  "i",
);

// scrub_pii's detectors, used here only to answer "is there a contact in the text".
const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;
const PHONE_RE = /(?<![\w])\+?\d[\d\-\s().]{5,}\d(?![\w])/;
const MIN_PHONE_DIGITS = 9;

// In chat a newline ends a sentence just as a full stop does.
const SENTENCE_BOUNDARIES = ".!?\n;";
const MAX_QUOTE_CHARS = 160;

/** The sentence a match sits in, trimmed on BOTH sides at word boundaries when too long. */
function sentenceSpan(text: string, start: number, end: number): [string, number, number] {
  let left = -1;
  for (const ch of SENTENCE_BOUNDARIES) left = Math.max(left, text.lastIndexOf(ch, start - 1));
  let right = text.length;
  for (const ch of SENTENCE_BOUNDARIES) {
    const pos = text.indexOf(ch, end);
    if (pos >= 0) right = Math.min(right, pos);
  }
  const fragment = text.slice(left + 1, right).trim();
  if (fragment.length <= MAX_QUOTE_CHARS) return [fragment, left + 1, right];

  const head = Math.max(left + 1, start - Math.floor(MAX_QUOTE_CHARS / 2));
  const tail = Math.min(right, head + MAX_QUOTE_CHARS);
  let cut = text.slice(head, tail);
  if (head > left + 1) {
    const space = cut.indexOf(" ");
    cut = space >= 0 ? cut.slice(space + 1) : cut;
  }
  if (tail < right) {
    const space = cut.lastIndexOf(" ");
    cut = space >= 0 ? cut.slice(0, space) : cut;
  }
  cut = cut.trim();
  return [`${head > left + 1 ? "…" : ""}${cut}${tail < right ? "…" : ""}`, head, tail];
}

// Reasons mirror leadcentre/engine/reasons.py: a code plus parameters, rendered on demand.
// Why structural: two bilingual string lists drift; one catalogue cannot.
export type ReasonLanguage = "ru" | "en";

/** Interface language. Reasons use it; the customer's text and the draft do not. */
export const REASON_UI_LANGUAGE: ReasonLanguage = "en";

export interface ReasonItem {
  code: string;
  params: Record<string, string | number>;
}

interface ReasonSpec {
  ru: string;
  en: string;
}

const REASON_CATALOGUE: Record<string, ReasonSpec> = {
  spam_or_off_topic: {
    ru: "обращение помечено как спам или не по теме",
    en: "message flagged as spam or off topic",
  },
  no_request_type: {
    ru: "из текста не извлечён ни один тип запроса",
    en: "no request type could be extracted from the text",
  },
  urgent_timeline: {
    ru: "срок {days:plural:day} — внутри горячего окна в {limit:plural:day}",
    en: "needed in {days:plural:day} — inside the hot window of {limit:plural:day}",
  },
  urgent_stated: {
    ru: "срочность заявлена словами, даты клиент не назвал",
    en: "urgency stated in words, no date given",
  },
  package_request: {
    ru: "запрошено услуг: {count} — нужен пакет",
    en: "{count:plural:service} asked about — this is a package, not a single line item",
  },
  team_over_flexi_quota: {
    ru: "команда {headcount:plural:person} — флекси не закроет визовую квоту",
    en: "team of {headcount} — flexi desk will not cover the visa quota",
  },
  budget_named: { ru: "назван бюджет: {budget}", en: "budget named: {budget}" },
  target_language: {
    ru: "язык обращения {language} — основная аудитория",
    en: "written in {language} — core audience",
  },
  target_language_alone: {
    ru: "язык обращения {language}, но других признаков нет",
    en: "written in {language}, but no other signal backs it up",
  },
  low_confidence: {
    ru: "уверенность извлечения {confidence:.2f} — ниже порога {threshold:.2f}",
    en: "extraction confidence {confidence:.2f} — below the {threshold:.2f} threshold",
  },
};

// Russian needs three numeral forms, English two (CLDR one/few/many).
const PLURAL_FORMS: Record<ReasonLanguage, Record<string, string[]>> = {
  ru: {
    day: ["день", "дня", "дней"],
    person: ["человек", "человека", "человек"],
    service: ["услуга", "услуги", "услуг"],
  },
  en: {
    day: ["day", "days"],
    person: ["person", "people"],
    service: ["service", "services"],
  },
};

function pluralIndex(language: ReasonLanguage, value: number): number {
  if (language === "en") return value === 1 ? 0 : 1;
  const n = Math.abs(value);
  if (n % 10 === 1 && n % 100 !== 11) return 0;
  if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return 1;
  return 2;
}

function pluralPhrase(language: ReasonLanguage, value: number, noun: string): string {
  return `${value} ${PLURAL_FORMS[language][noun][pluralIndex(language, value)]}`;
}

/** Renders one reason. An unknown code throws rather than yielding an empty string. */
export function renderReason(item: ReasonItem, language: ReasonLanguage): string {
  const spec = REASON_CATALOGUE[item.code];
  if (!spec) throw new Error(`unknown reason code: ${item.code}`);
  return spec[language].replace(/\{(\w+)(?::([^}]+))?\}/g, (_all, name: string, format?: string) => {
    const value = item.params[name];
    if (value === undefined) throw new Error(`reason ${item.code}: no parameter ${name}`);
    if (format?.startsWith("plural:")) {
      return pluralPhrase(language, value as number, format.slice("plural:".length));
    }
    if (format === ".2f") return (value as number).toFixed(2);
    return String(value);
  });
}

/** Renders a whole list of reasons into one language. */
export function renderReasons(items: ReasonItem[], language: ReasonLanguage): string[] {
  return items.map((item) => renderReason(item, language));
}

/** Moves a tier along LOW → MEDIUM → HIGH, clamped at both ends. */
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

/** Whether the text carries a way to reach the customer. A short digit run is not a phone. */
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
  // Deadlines named in words; wordless urgency carries no date and goes to urgency_stated.
  return deadlineDays(low, receivedAt);
}

/** "Renew the licence" is not a company setup: drops SETUP lit only by a licence word next to a renewal word. */
function dropSetupInsideRenewal(
  low: string,
  types: string[],
  fired: Record<string, string[]>,
): string[] {
  if (!types.includes("setup") || !types.includes("renewal")) return types;
  const setupMarkers = fired.setup ?? [];
  if (setupMarkers.some((marker) => !LICENCE_MARKERS.includes(marker))) return types;
  const renewalMarkers = fired.renewal ?? [];
  for (const sentence of low.split(/[.!?\n;]/)) {
    const hasLicence = setupMarkers.some((marker) => sentence.includes(marker));
    const hasRenewal = renewalMarkers.some((marker) => sentence.includes(marker));
    if (hasLicence && !hasRenewal) return types;
  }
  return types.filter((kind) => kind !== "setup");
}

export interface ExtractionResult {
  facts: LeadFacts;
  quotes: string[];
}

/** `confidence` here means "markers matched" — 1.0 or 0.0, not a model's probability. */
export function extractFacts(text: string, receivedAt: Date = new Date()): ExtractionResult {
  const low = text.toLowerCase();
  const quotes: string[] = [];
  const spans: [number, number][] = [];

  // Why a sentence: a bare stem as the quote reads as a matcher bug, not as evidence.
  const hit = (marker: string): boolean => {
    const index = low.indexOf(marker);
    if (index < 0) return false;
    // An overlapping window is the same text under another marker, not a second proof.
    const [fragment, from, to] = sentenceSpan(text, index, index + marker.length);
    const overlaps = spans.some(([takenFrom, takenTo]) => from < takenTo && takenFrom < to);
    if (fragment && !overlaps && !quotes.includes(fragment)) {
      quotes.push(fragment);
      spans.push([from, to]);
    }
    return true;
  };

  let types: string[] = [];
  const fired: Record<string, string[]> = {};
  for (const [kind, markers] of TYPE_MARKERS) {
    let matched = false;
    for (const marker of markers) {
      if (low.includes(marker)) (fired[kind] ??= []).push(marker);
      matched = hit(marker) || matched;
    }
    if (matched) types.push(kind);
  }
  types = dropSetupInsideRenewal(low, types, fired);

  // Third outcome: several different counts mean the team size does not follow at all.
  let headcount: number | null = null;
  HEADCOUNT_RE.lastIndex = 0;
  const heads = [...low.matchAll(HEADCOUNT_RE)];
  const distinct = new Set(heads.map((m) => parseInt(m[1], 10)));
  if (distinct.size === 1) {
    const head = heads[0];
    headcount = parseInt(head[1], 10);
    quotes.push(text.slice(head.index!, head.index! + head[0].length));
  }

  // Why the customer's own words: the rubric looks for a figure inside budget_hint, and a
  // figure is what separates a named budget from talk about one.
  let budget: string | null = null;
  const money = low.match(MONEY_RE);
  if (money && money.index !== undefined) {
    budget = text.slice(money.index, money.index + money[0].length).trim();
    quotes.push(budget);
  } else {
    for (const marker of BUDGET_MARKERS) {
      if (hit(marker)) {
        const index = low.indexOf(marker);
        budget = text.slice(index, index + marker.length);
        break;
      }
    }
  }

  let isSpam = false;
  for (const marker of SPAM_MARKERS) isSpam = hit(marker) || isSpam;

  const facts: LeadFacts = {
    request_types: types,
    jurisdiction_hint: null,
    headcount,
    timeline_days: timelineDays(text, receivedAt),
    urgency_stated: wordlessUrgency(low, timelineDays(text, receivedAt)),
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
  /** Reasons as code plus parameters; `renderReasons` turns them into strings. */
  reasonItems: ReasonItem[];
  evidence: Evidence[];
  violations: string[];
}

/**
 * Which quotes prove which reason: a quote counts when the same fact follows from it alone.
 * Why matched by code: reason text is bilingual and gets rewritten, the code is the contract.
 * A reason that cannot have a quote gets an empty list — a third outcome of its own.
 */
export function linkReasons(
  items: ReasonItem[],
  quotes: string[],
  facts: LeadFacts,
  receivedAt: Date = new Date(),
): { text: string; code: string; quotes: number[] }[] {
  const perQuote = quotes.map((quote) => extractFacts(quote, receivedAt).facts);
  const pick = (test: (f: LeadFacts) => boolean) =>
    perQuote.map((f, index) => (test(f) ? index : -1)).filter((index) => index >= 0);
  const byCode: Record<string, (f: LeadFacts) => boolean> = {
    urgent_timeline: (f) => f.timeline_days === facts.timeline_days,
    urgent_stated: (f) => f.urgency_stated,
    package_request: (f) => f.request_types.length > 0,
    team_over_flexi_quota: (f) => f.headcount === facts.headcount,
    // Why the same figure: "any money in the quote" proved a budget with a different sum.
    budget_named: (f) => f.budget_hint === facts.budget_hint,
    spam_or_off_topic: (f) => f.is_spam,
  };
  return items.map((item) => ({
    text: renderReason(item, REASON_UI_LANGUAGE),
    code: item.code,
    quotes: byCode[item.code] ? pick(byCode[item.code]) : [],
  }));
}

/** Port of score.score_inbound. Three outcomes: INVALID is never folded into LOW. */
/** Mirror of reasons.VIOLATION_CATALOGUE, English rendering. Checked by crosscheck.py. */
export const VIOLATION_TEXTS = {
  high_without_quote: "HIGH without a quote from the message",
  high_on_empty_text: "HIGH on an empty message text",
} as const;

export function scoreInbound(text: string, facts: LeadFacts, quotes: string[]): ScoreResult {
  const reasons: ReasonItem[] = [];
  const violations: string[] = [];
  const evidence: Evidence[] = quotes.map((value) => ({ kind: "quote", value }));

  if (facts.is_spam) {
    return {
      tier: "LOW",
      reasonItems: [{ code: "spam_or_off_topic", params: {} }],
      evidence,
      violations: [],
    };
  }

  let tier: Tier = facts.request_types.length ? INBOUND_BASE : INBOUND_BASE_NO_REQUEST;
  if (!facts.request_types.length) reasons.push({ code: "no_request_type", params: {} });

  let substantive = 0;
  if (facts.timeline_days !== null && facts.timeline_days <= URGENT_TIMELINE_DAYS) {
    substantive += 1;
    reasons.push({
      code: "urgent_timeline",
      params: { days: facts.timeline_days, limit: URGENT_TIMELINE_DAYS },
    });
  } else if (facts.urgency_stated) {
    substantive += 1;
    reasons.push({ code: "urgent_stated", params: {} });
  }
  if (facts.request_types.length >= PACKAGE_MIN_REQUEST_TYPES) {
    substantive += 1;
    reasons.push({ code: "package_request", params: { count: facts.request_types.length } });
  }
  if (facts.headcount !== null && facts.headcount >= TEAM_MIN_HEADCOUNT) {
    substantive += 1;
    reasons.push({ code: "team_over_flexi_quota", params: { headcount: facts.headcount } });
  }
  // Only a figure counts as a budget: "how much does it cost" is a question.
  if (facts.budget_hint && /\d/.test(facts.budget_hint)) {
    substantive += 1;
    reasons.push({ code: "budget_named", params: { budget: facts.budget_hint.slice(0, 40) } });
  }

  // Language is a garnish, never a reason on its own.
  if (TARGET_LANGUAGES.includes(facts.language)) {
    if (substantive || !LANGUAGE_NEEDS_ANOTHER_SIGNAL) {
      reasons.push({ code: "target_language", params: { language: facts.language } });
    } else {
      reasons.push({ code: "target_language_alone", params: { language: facts.language } });
    }
  }

  // Why two paths: a set of signals makes a request hot; one signal only lifts it from LOW.
  const enoughSignals = substantive >= SIGNALS_FOR_HIGH;
  const rescuedFromLow = substantive > 0 && tier === "LOW";
  if (enoughSignals || rescuedFromLow) tier = step(tier, 1);

  if (facts.confidence < LOW_CONFIDENCE) {
    if (LADDER.indexOf(tier) > LADDER.indexOf("MEDIUM")) tier = "MEDIUM";
    reasons.push({
      code: "low_confidence",
      params: { confidence: facts.confidence, threshold: LOW_CONFIDENCE },
    });
  }

  // Why English: these strings land on an English card. The engine renders the same two
  // codes in both languages; the port is the UI side, so it mirrors the English rendering.
  if (tier === "HIGH" && evidence.length === 0) violations.push(VIOLATION_TEXTS.high_without_quote);
  if (tier === "HIGH" && !text.trim()) violations.push(VIOLATION_TEXTS.high_on_empty_text);
  if (violations.length) tier = "INVALID";

  return { tier, reasonItems: reasons, evidence, violations };
}
