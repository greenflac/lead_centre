// Draft replies for the demo form, mock mode only: a port of leadcentre/engine/reply.py
// and the ranges of data/pricelist_demo.yaml. The numbers are a DEMO price list.
// Why a second copy exists: the browser cannot run the Python drafter with no backend.

import type { LeadFacts, Reply } from "./types";

const MIN_CONFIDENCE_FOR_PRICE = 0.5;
const URGENT_TIMELINE_DAYS = 7;

interface PriceItem { label_ru: string; label_en: string; unit_ru: string; unit_en: string; min: number; max: number }

const PRICES: Record<string, PriceItem> = {
  office_mini_year: { label_ru: "мини-офис в бизнес-центре", label_en: "small private office in a business centre", unit_ru: "в год", unit_en: "per year", min: 35000, max: 60000 },
  flexi_desk_year: { label_ru: "флекси-деск", label_en: "flexi-desk", unit_ru: "в год", unit_en: "per year", min: 6000, max: 15000 },
  setup_mainland_package: { label_ru: "регистрация компании mainland (лицензия и госсборы, пакет)", label_en: "mainland company setup package (licence and government fees)", unit_ru: "разово", unit_en: "one-off", min: 15000, max: 35000 },
  setup_freezone_package: { label_ru: "регистрация компании во фризоне (пакет)", label_en: "free zone company setup package", unit_ru: "разово", unit_en: "one-off", min: 12500, max: 27000 },
  visa_employment: { label_ru: "рабочая виза под ключ, на человека", label_en: "employment visa, all-in, per person", unit_ru: "за визу", unit_en: "per visa", min: 3500, max: 7000 },
  accounting_month: { label_ru: "бухгалтерское сопровождение", label_en: "accounting services", unit_ru: "в месяц", unit_en: "per month", min: 1500, max: 6000 },
  renewal_year: { label_ru: "ежегодное продление (лицензия, Ejari, визовая квота)", label_en: "annual renewal (licence, Ejari, visa quota)", unit_ru: "в год", unit_en: "per year", min: 15000, max: 30000 },
};

const PRICE_KEYS: Record<string, string[]> = {
  office: ["office_mini_year", "flexi_desk_year"],
  setup: ["setup_mainland_package", "setup_freezone_package"],
  visa: ["visa_employment"],
  accounting: ["accounting_month"],
  renewal: ["renewal_year"],
  bank: [],
  other: [],
};

const SUBSTANCE: Record<string, { ru: string; en: string }> = {
  office: {
    ru: "По офису: у нас собственный бизнес-центр в Дубае, есть мини-офисы и флекси-десками закрываем визовую квоту.",
    en: "On the office: we run our own business centre in Dubai, with small private offices and flexi-desks that cover the visa quota.",
  },
  setup: {
    ru: "По регистрации: считаем оба варианта — mainland и фризона, выбор зависит от вида деятельности и того, нужны ли визы.",
    en: "On the setup: we compare both routes — mainland and free zone; the choice depends on your activity and how many visas you need.",
  },
  visa: {
    ru: "По визам: оформляем рабочие визы под ключ — медкомиссия, Emirates ID, штамп; квота считается от площади офиса.",
    en: "On visas: we handle employment visas end to end — medical, Emirates ID, stamping; the quota depends on your office space.",
  },
  accounting: {
    ru: "По бухгалтерии: ведём учёт, VAT и корпоративный налог, объём работ зависит от числа операций в месяц.",
    en: "On accounting: we cover bookkeeping, VAT and corporate tax; the scope depends on your monthly transaction volume.",
  },
  renewal: {
    ru: "По продлению: собираем пакетом лицензию, Ejari и визовую квоту — документы лучше подавать заранее, просрочка добавляет штрафы.",
    en: "On renewals: we bundle the licence, Ejari and the visa quota — filing early avoids the late-renewal penalties.",
  },
  bank: {
    ru: "По счёту: сопровождаем открытие в местных банках, решение принимает банк, мы готовим комплект и защищаем заявку.",
    en: "On banking: we support account opening with local banks — the bank decides, we prepare and defend the application.",
  },
  other: {
    ru: "Спасибо за обращение — разберём вашу задачу по шагам.",
    en: "Thanks for reaching out — let us take your case step by step.",
  },
};

const GREETING = { ru: "Здравствуйте!", en: "Hello," };
const DISCLAIMER = {
  ru: "Это рыночный диапазон, итог зависит от вида деятельности и числа виз — посчитаем точно после короткого разговора.",
  en: "This is a market range; the final figure depends on your activity and visa count — we will price it exactly after a short call.",
};
const CLOSER_URGENT = {
  ru: "Вижу, что сроки сжатые: возьмём в работу сегодня — во сколько удобно созвониться?",
  en: "Your timeline looks tight: we can start today — what time suits a call?",
};
const CLOSER_MEETING = {
  ru: "Удобно встретиться в нашем офисе в Дубае на этой неделе или созвониться?",
  en: "Would a meeting at our Dubai office this week work, or a call instead?",
};
const QUESTIONS = {
  ru: [
    "Здравствуйте! Чтобы ответить по делу и без лишних цифр, уточните пару вещей.",
    "Что именно нужно в первую очередь — регистрация компании, офис, визы или бухгалтерия?",
    "Планируете mainland или фризону и сколько виз потребуется?",
    "К какому сроку нужно решение?",
    "Ответьте одной строкой — подготовим расчёт и вышлем в течение дня.",
  ],
  en: [
    "Hello, to answer precisely and without guessing numbers, a couple of questions.",
    "What do you need first — company setup, an office, visas or accounting?",
    "Are you looking at mainland or a free zone, and how many visas do you need?",
    "By when do you need this done?",
    "One line back is enough — we will prepare the numbers and send them the same day.",
  ],
};

// Why NBSP: a plain space lets a line break fall between "AED" and its own figure.
const NBSP = "\u00a0";

/** Thousands separated by NBSP, so a sum never breaks across lines. */
function amount(value: number): string {
  return value.toLocaleString("en-US").replace(/,/g, NBSP);
}

// Why isolates: without them a Latin-and-digit sum falls apart inside an Arabic line.
const LRI = "\u2066";
const PDI = "\u2069";

// Why a word joiner: a browser may break after the dash, and half a range reads as one price.
const WORD_JOINER = "\u2060";

/** One price range, held together as a single unbreakable, direction-isolated run. */
function priceFragment(item: PriceItem): string {
  return `${LRI}AED${NBSP}${amount(item.min)}${WORD_JOINER}–${WORD_JOINER}${amount(item.max)}${PDI}`;
}

/** A whole price line: what it is, the range, and the unit it is priced in. */
function priceLine(key: string, language: "ru" | "en"): string {
  const item = PRICES[key];
  const label = language === "ru" ? item.label_ru : item.label_en;
  const unit = language === "ru" ? item.unit_ru : item.unit_en;
  return language === "ru"
    ? `Ориентир по рынку: ${label} — ${priceFragment(item)} ${unit}.`
    : `Market range: ${label} — ${priceFragment(item)} ${unit}.`;
}

// Why: the closing line asks only about facts we do NOT have; asking twice reads as unread.
const CLOSER_QUESTION_ORDER = ["headcount", "timeline_days", "jurisdiction_hint", "has_contact"] as const;

const FACT_IS_KNOWN: Record<string, (f: LeadFacts) => boolean> = {
  headcount: (f) => f.headcount !== null,
  timeline_days: (f) => f.timeline_days !== null,
  jurisdiction_hint: (f) => Boolean(f.jurisdiction_hint),
  has_contact: (f) => f.has_contact,
};

const CLOSER_QUESTION: Record<string, { ru: string; en: string }> = {
  headcount: {
    ru: "Подскажите, сколько человек планируете нанять в первый год?",
    en: "Could you tell us how many people you plan to hire in the first year?",
  },
  timeline_days: {
    ru: "К какому сроку нужно, чтобы всё было готово?",
    en: "By when do you need everything up and running?",
  },
  jurisdiction_hint: {
    ru: "Смотрите mainland или фризону — или как раз хотите сравнить два варианта?",
    en: "Are you leaning towards mainland or a free zone — or would you compare both?",
  },
  has_contact: {
    ru: "Оставьте номер WhatsApp — пришлём расчёт туда и не потеряем ваш вопрос.",
    en: "Share a WhatsApp number and we will send the numbers there.",
  },
};

const GROUNDING: Record<string, { ru: string; en: string }> = {
  headcount: { ru: "вас {value} человек", en: "there are {value} of you" },
  timeline_days: { ru: "срок {value} дн.", en: "your timeline is {value} days" },
  jurisdiction_hint: { ru: "формат {value}", en: "you are looking at {value}" },
};
const GROUNDED_MEETING_TAIL = {
  ru: "предлагаю созвон сегодня или встречу в нашем офисе в Дубае.",
  en: "let us do a call today or meet at our Dubai office.",
};
const GROUNDING_JOINER = { ru: " и ", en: " and " };

/** A meeting offer that repeats back what the customer already told us, at most two facts. */
function groundedMeeting(facts: LeadFacts, language: "ru" | "en"): string {
  const parts: string[] = [];
  for (const key of CLOSER_QUESTION_ORDER) {
    const template = GROUNDING[key];
    if (!template || !FACT_IS_KNOWN[key](facts)) continue;
    const value = String((facts as unknown as Record<string, unknown>)[key]);
    parts.push(template[language].replace("{value}", `${LRI}${value}${PDI}`));
    if (parts.length === 2) break;
  }
  if (!parts.length) return CLOSER_MEETING[language];
  const lead = parts.join(GROUNDING_JOINER[language]);
  return `${lead[0].toUpperCase()}${lead.slice(1)} — ${GROUNDED_MEETING_TAIL[language]}`;
}

/** The last line: urgent hand-off, the first fact we lack, or a meeting grounded in facts. */
function closer(facts: LeadFacts, language: "ru" | "en"): string {
  if (facts.timeline_days !== null && facts.timeline_days <= URGENT_TIMELINE_DAYS) {
    return CLOSER_URGENT[language];
  }
  for (const key of CLOSER_QUESTION_ORDER) {
    if (!FACT_IS_KNOWN[key](facts)) return CLOSER_QUESTION[key][language];
  }
  return groundedMeeting(facts, language);
}

/** Port of reply._pick_price_keys: at most two ranges; one per type when types differ. */
function pickPriceKeys(facts: LeadFacts): string[] {
  let keys: string[] = [];
  for (const request of facts.request_types) {
    for (const key of PRICE_KEYS[request] ?? []) if (!keys.includes(key)) keys.push(key);
  }
  if (facts.request_types.length > 1) {
    keys = [];
    for (const request of facts.request_types) {
      for (const key of PRICE_KEYS[request] ?? []) {
        if (!keys.includes(key)) {
          keys.push(key);
          break;
        }
      }
    }
  }
  return keys.slice(0, 2);
}

/** Mirrors reply.DOMINANT_SCRIPT_SHARE; the value lives in Python, this copy is cross-checked. */
const DOMINANT_SCRIPT_SHARE = 0.8;

/**
 * Language named by the DOMINANT script, with three outcomes: named; no script at all; two
 * scripts and neither dominant, where the facts decide. One polite foreign phrase changes nothing.
 */
function scriptLanguage(text: string): "ar" | "ru" | null {
  let ar = 0;
  let ru = 0;
  for (const ch of text) {
    if ((ch >= "\u0600" && ch <= "\u06ff") || (ch >= "\u0750" && ch <= "\u077f") ||
        (ch >= "\ufb50" && ch <= "\ufdff") || (ch >= "\ufe70" && ch <= "\ufeff")) ar += 1;
    else if (ch >= "\u0400" && ch <= "\u04ff") ru += 1;
  }
  const total = ar + ru;
  if (total === 0) return null;
  const [language, best] = ar >= ru ? (["ar", ar] as const) : (["ru", ru] as const);
  return best / total >= DOMINANT_SCRIPT_SHARE ? language : null;
}

/**
 * Port of reply.draft. Four outcomes, never two: a drafted reply, questions instead of
 * invented numbers, spam skipped, and "no draft, a human takes this" — the last one is what
 * Arabic gets here, because the model that writes it is not in the browser.
 */
export function draftReply(facts: LeadFacts, tier: string, text = ""): Reply {
  const byScript = scriptLanguage(text);
  const resolved = byScript ?? (facts.language === "ru" ? "ru" : "en");

  if (facts.is_spam) {
    return { body: "", language: resolved, outcome: "spam_skipped", needs_human: false, used_prices: [] };
  }

  // Hot, urgent, and the ones that could not be scored all go to a human.
  const needsHuman =
    tier === "HIGH" ||
    tier === "INVALID" ||
    (facts.timeline_days !== null && facts.timeline_days <= URGENT_TIMELINE_DAYS);

  // Why no Arabic draft: only the model writes it, and there is no model in the browser.
  if (resolved === "ar") {
    return {
      body: "",
      language: "ar",
      outcome: "no_draft_needs_human",
      needs_human: true,
      used_prices: [],
      notice:
        "The Arabic draft is written by the model inside limits set by the code, and a native " +
        "speaker must read it before sending. In demo mode there is no backend and no model, " +
        "so there is no draft — a human takes this card.",
    };
  }

  const language: "ru" | "en" = resolved;

  if (!facts.request_types.length || facts.confidence < MIN_CONFIDENCE_FOR_PRICE) {
    return {
      body: QUESTIONS[language].join("\n"),
      language,
      outcome: "questions",
      needs_human: needsHuman,
      used_prices: [],
    };
  }

  const keys = pickPriceKeys(facts);
  if (!keys.length) {
    return {
      body: QUESTIONS[language].join("\n"),
      language,
      outcome: "questions",
      needs_human: needsHuman,
      used_prices: [],
    };
  }

  const lines = [GREETING[language]];
  const known = facts.request_types.filter((request) => SUBSTANCE[request]);
  for (const request of known.slice(0, 2)) lines.push(SUBSTANCE[request][language]);
  lines.push(...keys.map((key) => priceLine(key, language)));
  lines.push(DISCLAIMER[language]);
  lines.push(closer(facts, language));
  // 4-6 lines: the middle is dropped, never the closing line.
  while (lines.length > 6) lines.splice(2, 1);

  const skipped = facts.request_types.filter((request) => !SUBSTANCE[request]);
  return {
    body: lines.join("\n"),
    language,
    outcome: "draft",
    needs_human: needsHuman || skipped.length > 0,
    used_prices: keys,
    notice: skipped.length
      ? `not covered by the draft — request types with no template: ${skipped.join(", ")}`
      : "",
  };
}
