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
// plus SIGNALS_FOR_HIGH = 2. Re-synced 2026-09-10 against RequestType.RENEWAL, the
// sentence-quote rule (facts_rules.hit / _sentence_around) and _drop_setup_inside_renewal;
// the cross-check of 10 requests through both paths is in web/scripts/crosscheck.py.
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
const SIGNALS_FOR_HIGH = 2;
const LANGUAGE_NEEDS_ANOTHER_SIGNAL = true;
const LADDER: Tier[] = ["LOW", "MEDIUM", "HIGH"];
const INBOUND_BASE: Tier = "MEDIUM";
const INBOUND_BASE_NO_REQUEST: Tier = "LOW";

// --- routing, mirrored from leadcentre/engine/extract.py ---
const LONG_MESSAGE_CHARS = 600;
const SHORT_MODEL = "claude-haiku-4-5-20251001";
const LONG_MODEL = "claude-opus-5";

/**
 * Which model the live pipeline would pick for this text, and why. The decision is made by
 * message length before any network call, so the mock can state it honestly — it is the
 * routing rule, not a claim that a model ran.
 */
export function routeForText(text: string): { model: string; reason: string } {
  const length = text.length;
  return length > LONG_MESSAGE_CHARS
    ? { model: LONG_MODEL, reason: `long request: ${length} chars > ${LONG_MESSAGE_CHARS}` }
    : { model: SHORT_MODEL, reason: `short request: ${length} chars <= ${LONG_MESSAGE_CHARS}` };
}

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

// Слова о лицензии звучат одинаково при первичной регистрации и при продлении.
// Порт facts_rules.LICENCE_MARKERS / _drop_setup_inside_renewal.
const LICENCE_MARKERS = ["лицензи", "licence", "license"];

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
const EXPIRES_RE = /(?:expires?|истека[а-яё]*|заканчива[а-яё]*|слетает)\D{0,25}(\d+)\s*(дн|day|week|недел)/i;
// \b и \w в JavaScript считают словом только ASCII, а в Python — и кириллицу тоже.
// ИЗМЕРЕНО сверкой (web/scripts/crosscheck.py): из-за этого «12 рабочих мест» давало
// headcount=12 в Python и null в TS, и обращение urg-02 расходилось по приоритету
// HIGH против MEDIUM. Границы слова записаны явным просмотром вперёд.
const WORD_TAIL = "(?![0-9A-Za-z_\\u0400-\\u04ff])";
const HEADCOUNT_RE = new RegExp(
  `(\\d+)\\s*(?:[а-яёa-z]+\\s+)?(?:человек|чел${WORD_TAIL}|людей|people|ppl${WORD_TAIL}|persons|seats|мест${WORD_TAIL}|сотрудник[а-яё]*|staff)`,
  "i",
);
const MONEY_RE = /\d[\d\s.,]*\s*(?:aed|дирхам|тысяч|k\b)/i;

// scrub_pii's detectors, used here only to answer "is there a contact in the text".
const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;
const PHONE_RE = /(?<![\w])\+?\d[\d\-\s().]{5,}\d(?![\w])/;
const MIN_PHONE_DIGITS = 9;

// Границы предложения в чате: перевод строки считается концом наравне с точкой.
const SENTENCE_BOUNDARIES = ".!?\n;";
const MAX_QUOTE_CHARS = 160;

/**
 * Порт facts_rules._sentence_around: предложение, внутри которого лежит совпадение.
 * Длинное подрезается вокруг самого совпадения по границам слов С ОБЕИХ сторон и
 * помечается многоточием: подрезка только справа давала цитаты вида «eezone, если…» —
 * доказательство, похожее на мусор (ИЗМЕРЕНО в Python на edge-03, 12 из 14 цитат).
 */
function sentenceAround(text: string, start: number, end: number): string {
  let left = -1;
  for (const ch of SENTENCE_BOUNDARIES) left = Math.max(left, text.lastIndexOf(ch, start - 1));
  let right = text.length;
  for (const ch of SENTENCE_BOUNDARIES) {
    const pos = text.indexOf(ch, end);
    if (pos >= 0) right = Math.min(right, pos);
  }
  const fragment = text.slice(left + 1, right).trim();
  if (fragment.length <= MAX_QUOTE_CHARS) return fragment;

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
  return `${head > left + 1 ? "…" : ""}${cut}${tail < right ? "…" : ""}`;
}

// --- причины: зеркало каталога leadcentre/engine/reasons.py ---
//
// Причина хранится структурно — код плюс параметры — и отрисовывается на языке
// интерфейса. Так устроен и Python (`Score.reason_items` + `reasons_in(language)`), и
// иначе двуязычность превращается в два независимых списка строк. Сверка
// web/scripts/crosscheck.py сличает отрисовку на ОБОИХ языках.
export type ReasonLanguage = "ru" | "en";

/** Язык интерфейса дашборда. Причины идут на нём; текст клиента и черновик — нет. */
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
    ru: "срок {days:plural:day} — не больше {limit:plural:day}",
    en: "needed in {days:plural:day} — urgency window is {limit:plural:day}",
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

// Русский требует трёх форм числительного, английский — двух (порт _plural_index_ru /
// _plural_index_en, правило CLDR one/few/many).
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

/** Отрисовка одной причины. Неизвестный код — ошибка, а не пустая строка (Р1). */
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

export function renderReasons(items: ReasonItem[], language: ReasonLanguage): string[] {
  return items.map((item) => renderReason(item, language));
}

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

/**
 * Port of facts_rules._drop_setup_inside_renewal. "Renew the licence" is not registering a
 * company: SETUP is dropped when it was lit only by a licence word standing next to a
 * renewal word in the same sentence. Measured on urg-13, where markers said setup and the
 * model said renewal — and the model was right.
 */
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

/**
 * Port of facts_rules.rules_facts. `confidence` here means "markers matched", not a model's
 * probability — 1.0 or 0.0, exactly as the Python module documents.
 */
export function extractFacts(text: string, receivedAt: Date = new Date()): ExtractionResult {
  const low = text.toLowerCase();
  const quotes: string[] = [];

  // The proof is the sentence the customer wrote, not the stem our matcher found:
  // a quote «регистрац» reads as a stemmer bug, not as evidence (facts_rules.hit).
  const hit = (marker: string): boolean => {
    const index = low.indexOf(marker);
    if (index < 0) return false;
    const fragment = sentenceAround(text, index, index + marker.length);
    if (fragment && !quotes.includes(fragment)) quotes.push(fragment);
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

  let headcount: number | null = null;
  const head = low.match(HEADCOUNT_RE);
  if (head && head.index !== undefined) {
    headcount = parseInt(head[1], 10);
    quotes.push(text.slice(head.index, head.index + head[0].length));
  }

  // budget_hint carries the text itself, not a paraphrase: the rubric looks for a figure
  // inside it, and a paraphrase would silently swallow that signal.
  const budgetParts: string[] = [];
  const money = low.match(MONEY_RE);
  if (money && money.index !== undefined) {
    const fragment = text.slice(money.index, money.index + money[0].length).trim();
    budgetParts.push(fragment);
    quotes.push(fragment);
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
  /** Причины структурно: код плюс параметры. Строки получают отрисовкой (renderReasons). */
  reasonItems: ReasonItem[];
  evidence: Evidence[];
  violations: string[];
}

/**
 * Which quotes prove which reason. Same method as web/scripts/gen_mock.py: the extractor is
 * run over each quote, and a quote counts as proof when the same fact follows from it alone
 * — no second copy of the markers (Е1). Связь идёт по КОДУ причины, а не по её тексту:
 * текст двуязычный и переписывается, код — контракт. Reasons that cannot have a quote
 * (language of the whole text, extraction confidence) get an empty list, and that third
 * outcome is not folded into "no quote found" (Р1).
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
    package_request: (f) => f.request_types.length > 0,
    team_over_flexi_quota: (f) => f.headcount === facts.headcount,
    budget_named: (f) => Boolean(f.budget_hint),
    spam_or_off_topic: (f) => f.is_spam,
  };
  return items.map((item) => ({
    text: renderReason(item, REASON_UI_LANGUAGE),
    code: item.code,
    quotes: byCode[item.code] ? pick(byCode[item.code]) : [],
  }));
}

/** Port of score.score_inbound. Three outcomes: INVALID is never folded into LOW. */
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
  }
  if (facts.request_types.length >= PACKAGE_MIN_REQUEST_TYPES) {
    substantive += 1;
    reasons.push({ code: "package_request", params: { count: facts.request_types.length } });
  }
  if (facts.headcount !== null && facts.headcount >= TEAM_MIN_HEADCOUNT) {
    substantive += 1;
    reasons.push({ code: "team_over_flexi_quota", params: { headcount: facts.headcount } });
  }
  // Only a figure counts as a budget: "how much does it cost" is a question, not a budget.
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

  // Two distinct reasons to step up, deliberately not merged: a set of signals makes a
  // request hot, while a single signal only rescues a request from LOW.
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

  if (tier === "HIGH" && evidence.length === 0) violations.push("HIGH без цитаты из обращения");
  if (tier === "HIGH" && !text.trim()) violations.push("HIGH на пустом тексте обращения");
  if (violations.length) tier = "INVALID";

  return { tier, reasonItems: reasons, evidence, violations };
}
