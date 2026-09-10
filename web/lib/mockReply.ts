// Draft replies for requests typed into the demo form, mock mode only.
//
// DEBT(2026-09-09): mirrors leadcentre/engine/reply.py and the ranges of
// data/pricelist_demo.yaml in TypeScript. Same reason as mockEngine.ts: the browser cannot
// run the Python drafter with no backend attached. With NEXT_PUBLIC_API_URL set, the live
// adapter returns the Python draft and this file is never called.
// The numbers below are a DEMO price list, not SORP's price list.
// Re-synced 2026-09-10 against reply.py: renewal_year, the "Ориентир по рынку:" price line,
// the closer that asks only about facts we do NOT have, the two-range cap, and the Arabic
// outcome (the browser has no model, so ar ends in no_draft_needs_human — the same third
// outcome the Python drafter produces when the model is unavailable).

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

// Неразрывные пробелы внутри суммы и между валютой и числом — как в reply.py: обычный
// пробел даёт перенос строки посреди диапазона, и «AED» уезжает от своей суммы
// (ИЗМЕРЕНО глазами на арабской карточке 02d, снимок от 2026-09-10).
const NBSP = "\u00a0";

function amount(value: number): string {
  return value.toLocaleString("en-US").replace(/,/g, NBSP);
}

// Изоляты направления вокруг латинско-цифровой вставки (reply.ltr_run): без них сумма
// внутри арабской строки визуально распадается. В ru/en они невидимы — одна ветка на все
// языки, как в Python (Е1).
const LRI = "\u2066";
const PDI = "\u2069";

function priceFragment(item: PriceItem): string {
  return `${LRI}AED${NBSP}${amount(item.min)}–${amount(item.max)}${PDI}`;
}

function priceLine(key: string, language: "ru" | "en"): string {
  const item = PRICES[key];
  const label = language === "ru" ? item.label_ru : item.label_en;
  const unit = language === "ru" ? item.unit_ru : item.unit_en;
  return language === "ru"
    ? `Ориентир по рынку: ${label} — ${priceFragment(item)} ${unit}.`
    : `Market range: ${label} — ${priceFragment(item)} ${unit}.`;
}

// Порт reply.CLOSER_QUESTION_ORDER / FACT_IS_KNOWN / GROUNDING: последняя строка
// спрашивает только про то, чего в фактах НЕТ. Спросить про уже сказанное — показать
// клиенту, что обращение не прочитали.
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

/** Port of reply.detect_script_language: the script of the text outweighs the flag (Е2). */
function scriptLanguage(text: string): "ar" | "ru" | null {
  for (const ch of text) {
    if ((ch >= "\u0600" && ch <= "\u06ff") || (ch >= "\u0750" && ch <= "\u077f") ||
        (ch >= "\ufb50" && ch <= "\ufdff") || (ch >= "\ufe70" && ch <= "\ufeff")) return "ar";
  }
  for (const ch of text) if (ch >= "\u0400" && ch <= "\u04ff") return "ru";
  return null;
}

export function draftReply(facts: LeadFacts, tier: string, text = ""): Reply {
  const byScript = scriptLanguage(text);
  const resolved = byScript ?? (facts.language === "ru" ? "ru" : "en");

  if (facts.is_spam) {
    return { body: "", language: resolved, outcome: "spam_skipped", needs_human: false, used_prices: [] };
  }

  // Порт _needs_human: горячие, срочные и те, кого не смогли оценить.
  const needsHuman =
    tier === "HIGH" ||
    tier === "INVALID" ||
    (facts.timeline_days !== null && facts.timeline_days <= URGENT_TIMELINE_DAYS);

  // Арабский пишет модель, а не наш шаблон (reply.py: шаблон не носителя читается как
  // неуважение). Модели в браузере нет, поэтому исход — NO_DRAFT и человек, а не тихая
  // подмена языка на английский.
  if (resolved === "ar") {
    return {
      body: "",
      language: "ar",
      outcome: "no_draft_needs_human",
      needs_human: true,
      used_prices: [],
      // Служебный текст английского интерфейса — по-английски. Русская пометка на
      // английской карточке читается как недоделка; движок по той же причине перевёл
      // NATIVE_REVIEW_NOTICE.
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
  // 4-6 строк: режем середину, а не концовку.
  while (lines.length > 6) lines.splice(2, 1);

  const skipped = facts.request_types.filter((request) => !SUBSTANCE[request]);
  return {
    body: lines.join("\n"),
    language,
    outcome: "draft",
    needs_human: needsHuman || skipped.length > 0,
    used_prices: keys,
    // То же самое: пометка адресована менеджеру, читающему английский экран.
    notice: skipped.length
      ? `not covered by the draft — request types with no template: ${skipped.join(", ")}`
      : "",
  };
}
