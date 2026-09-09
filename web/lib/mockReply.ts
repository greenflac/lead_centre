// Draft replies for requests typed into the demo form, mock mode only.
//
// DEBT(2026-09-09): mirrors leadcentre/engine/reply.py and the ranges of
// data/pricelist_demo.yaml in TypeScript. Same reason as mockEngine.ts: the browser cannot
// run the Python drafter with no backend attached. With NEXT_PUBLIC_API_URL set, the live
// adapter returns the Python draft and this file is never called.
// The numbers below are a DEMO price list, not SORP's price list.

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
};

const PRICE_KEYS: Record<string, string[]> = {
  office: ["office_mini_year", "flexi_desk_year"],
  setup: ["setup_mainland_package", "setup_freezone_package"],
  visa: ["visa_employment"],
  accounting: ["accounting_month"],
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

function amount(value: number): string {
  return value.toLocaleString("en-US").replace(/,/g, " ");
}

function priceLine(key: string, language: "ru" | "en"): string {
  const item = PRICES[key];
  const label = language === "ru" ? item.label_ru : item.label_en;
  const unit = language === "ru" ? item.unit_ru : item.unit_en;
  return language === "ru"
    ? `${label} — AED ${amount(item.min)}–${amount(item.max)} ${unit}.`
    : `${label} — AED ${amount(item.min)}–${amount(item.max)} ${unit}.`;
}

export function draftReply(facts: LeadFacts, tier: string): Reply {
  const language: "ru" | "en" = facts.language === "ru" || facts.language === "mixed" ? "ru" : "en";

  if (facts.is_spam) {
    return { body: "", language, outcome: "spam_skipped", needs_human: false, used_prices: [] };
  }

  const needsHuman =
    tier === "HIGH" ||
    (facts.timeline_days !== null && facts.timeline_days <= URGENT_TIMELINE_DAYS);

  if (!facts.request_types.length || facts.confidence < MIN_CONFIDENCE_FOR_PRICE) {
    return {
      body: QUESTIONS[language].join("\n"),
      language,
      outcome: "questions",
      needs_human: needsHuman,
      used_prices: [],
    };
  }

  const keys = facts.request_types.flatMap((r) => PRICE_KEYS[r] ?? []).slice(0, 3);
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
  for (const request of facts.request_types.slice(0, 2)) {
    lines.push(SUBSTANCE[request]?.[language] ?? SUBSTANCE.other[language]);
  }
  lines.push(...keys.map((key) => priceLine(key, language)));
  lines.push(DISCLAIMER[language]);
  lines.push(
    facts.timeline_days !== null && facts.timeline_days <= URGENT_TIMELINE_DAYS
      ? CLOSER_URGENT[language]
      : CLOSER_MEETING[language],
  );
  while (lines.length > 6) lines.splice(2, 1);

  return { body: lines.join("\n"), language, outcome: "draft", needs_human: needsHuman, used_prices: keys };
}
