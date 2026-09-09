# Ресерч 3: что ждут от тестового AI-MVP, архитектура, стоимость

> Агент-ресерч, 2026-09-09. Прочитаны напрямую: platform.claude.com (цены, structured outputs),
> github.com (n8n LICENSE.md, ai-engineering-field-guide), arxiv.org. Домены supabase.com,
> airtable.com, n8n.io, make.com, retool.com, vercel.com, streamlit docs, hubspot/bitrix/pipedrive
> docs, sorp.ae, anthropic.com — закрыты прокси; лимиты по ним из вторичных обзоров, НЕПРОВЕРЕНО.

## 1. Что оценивают в take-home / проактивных MVP (2025–2026)
- ai-engineering-field-guide (100+ репо с домашками): end-to-end работа, edge cases, модульность,
  error handling; **«red flag — кандидат не начал с evals»**; production-мышление (кэш,
  мониторинг, cost, PII, rate limits); победители: код + документ trade-offs + тесты + демо-видео.
  https://github.com/alexeygrigorev/ai-engineering-field-guide/blob/main/interview/questions/06-home-assignments.md
- Rootly «transcripts over code»: приватный репо с README, Loom < 5 мин, транскрипты работы с AI
  (как направляли модель). НЕПРОВЕРЕНО (сниппет).
- Louis Bouchard: умение измерить, что система работает; README с допущениями и «что дальше»;
  «одна интересная поломка и как починил» ставит выше 90% кандидатов. НЕПРОВЕРЕНО.
- Anthropic engineering, AI-resistant evaluations: отличает то, как человек работает с AI в цикле —
  вкус, итерации, суждение. НЕПРОВЕРЕНО (домен закрыт).
- jobsbyculture 2026: узкий реальный продукт + ~50 размеченных eval-примеров + детерминированные
  проверки + LLM-judge + метрики согласия с разметкой + README. НЕПРОВЕРЕНО.
- n8nlab: микро-воркфлоу с error handling, naming, n8n-терминология. НЕПРОВЕРЕНО.
- Vibe coding роли: живой деплой + история итераций с AI; «2–3 законченных > 10 клонов».

**Консенсус:** рабочее демо + Loom + README с trade-offs + eval-набор с числами + цена/латентность
+ один честно описанный провал. Guardrails/PII упоминаются часто; prompt versioning — редко.

## 2. Архитектура: n8n vs code-first vs гибрид
| Критерий | n8n self-hosted | Code-first (Claude tool calling) | Гибрид |
|---|---|---|---|
| Впечатление на фаундеров | Максимум: видимый граф | Слабое без UI | Высокое |
| Передача коллегам | Легко, совпадает с JD | Нужны dev-навыки | Хорошо |
| Тестируемость | Слабая (ручные тесты, JSON-экспорт) | Сильная (unit, eval, CI, промпты в git) | Средняя: скоринг вынесен в модуль |

n8n Sustainable Use License (прочитано в LICENSE.md): внутреннее бизнес-использование разрешено;
embed/хостинг для клиентов — платно. Для внутреннего лид-гена SORP — ок.
n8n Cloud: 14 дней trial без карты; Starter ≈ $20–24/мес (НЕПРОВЕРЕНО). Make Free: 1000 оп/мес,
2 сценария, интервал 15 мин (НЕПРОВЕРЕНО).

**Рекомендация:** гибрид. Скоринг + письма — Python/Node модуль с Claude structured outputs
(тестируемо, промпты в git); n8n — триггеры, CRM, уведомления, «картинка» для фаундера.

## 3. Хранилище для демо «через 2 недели»
- Supabase Free (НЕПРОВЕРЕНО): 2 проекта, 500 MB, **пауза после 7 дней неактивности**, ручное
  восстановление. Риск «Project paused» у рецензента.
- Airtable Free (НЕПРОВЕРЕНО): 1000 записей/базу, 1000 API-вызовов/мес, не паузится.
- Вывод: Supabase (совпадает с JD) + keep-alive cron раз в 3–5 дней (GitHub Actions/n8n) +
  строка в README «если проект в паузе».

## 4. Дашборд
- Streamlit Community Cloud: бесплатно, **спит после 12 ч**, выглядит как ноутбук.
- Retool Free: 5 пользователей, нет внешних — рецензенту нужен аккаунт (НЕПРОВЕРЕНО).
- Vercel Hobby: не спит, только некоммерческое (НЕПРОВЕРЕНО).
- Lovable Free: 5 кредитов/день, бейдж, публичные проекты (НЕПРОВЕРЕНО).
- Вывод: Next.js на Vercel (Lovable/v0 + доводка руками) — не спит, выглядит как продукт,
  совпадает с JD. Streamlit — только внутренний eval-отчёт.

## 5. LLM-скоринг: практики
- Structured outputs (официально): `output_config.format` с JSON Schema — валидный JSON, но не
  корректность; `enum` для HIGH/MEDIUM/LOW, `description` у полей, `messages.parse()`.
  https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Рубрика + verdict/confidence/evidence/reason; покритериальная оценка снижает шум (arXiv 2603.00077).
- Калибровка: эксперт размечает 30–50 примеров; цель Cohen's κ > ~0.6.
- Consistency: «Reliability without Validity» (arXiv 2606.19544): 85% agreement ≈ κ 0.48;
  ≥3 независимых прогона с выключенным кэшем, отчёт по κ. Haiku 4.5 при T=0: ICC 0.977
  (arXiv 2603.06836, НЕПРОВЕРЕНО).
- Few-shot: 3–5 разнообразных примеров (края + середина, Т3).

## 6. Холодные письма 2025–2026
- 4–6 строк, одна мысль, один конкретный сигнал (найм, лицензия, новость), бинарный CTA.
- Reply rate: 5.1% (2024) → 3.43% (2026). AI-only: 4.1% ответов, 7.8% спам; человеческие
  10.4%/2.9%; **гибрид AI-черновик + правка человека — 14.7%** (firstsales.io, НЕПРОВЕРЕНО).
- AI-slop: «I came across your company and was impressed…», «I hope this finds you well»,
  одинаковая структура, персонализация только именем.
- MVP: письмо — черновик «needs human review», поле `observation_source` (URL факта),
  линтер стоп-фраз.

## 7. CRM для демо
| CRM | Доступ | API |
|---|---|---|
| HubSpot Free | private app token; 100 req/10 s (НЕПРОВЕРЕНО) | POST /crm/v3/objects/{contacts,companies,deals} |
| Bitrix24 Free | REST на бесплатном; inbound webhook — статический URL с токеном; `crm.item.add` (crm.lead.add устарел) (НЕПРОВЕРЕНО) | Самый простой: один POST без OAuth |
| Pipedrive | 14 дней trial | истечёт до «2 недель спустя» |

CRM SORP: НЕПРОВЕРЕНО. Косвенно русскоязычный рынок Дубая массово на Bitrix24.
Рекомендация: интерфейс `CrmSink` с Bitrix24 webhook (основная демо) и HubSpot.

## 8. Стоимость (цены — ИЗМЕРЕНО по platform.claude.com/docs/en/about-claude/pricing, 2026-09-09)
Haiku 4.5 $1/$5; Sonnet 5 $2/$10; Sonnet 4.6 $3/$15 за MTok. Batch −50%, cache-hit 0.1× input.
Допущения (ВЫБРАНО): скоринг 1.5k in + 0.5k out; письмо 2k in + 0.6k out; 200 компаний/нед,
письмо всем 200 (верхняя граница). Токены/нед: input 700k, output 220k.

| Конфигурация | Расчёт | $/нед | $/мес |
|---|---|---|---|
| Haiku 4.5 везде | 0.7×1 + 0.22×5 | $1.80 | ~$7.8 |
| Sonnet 5 везде | 0.7×2 + 0.22×10 | $3.60 | ~$15.6 |
| Haiku скоринг + Sonnet 5 письма | 0.80 + 2.00 | $2.80 | ~$12.1 |
| Haiku + Batch | 1.80×0.5 | $0.90 | ~$3.9 |

Тройной прогон скоринга на Haiku: +$1.60/нед. Оговорка: токенизатор моделей 4.7+ (Sonnet 5)
даёт до ~1.3× больше токенов; Haiku 4.5 — старый токенизатор.

## Топ-10, что выделит тест-MVP
1. Eval-набор 30–50 компаний UAE, размеченных руками, с κ и confusion matrix в README.
2. Consistency-отчёт: 3 прогона на компанию, % расхождений, κ между прогонами.
3. Structured outputs с enum + rationale + confidence + evidence-URL; низкий confidence → ручная очередь.
4. Таблица стоимости и латентности из реальных `usage` в логах, переключатель Haiku/Sonnet/Batch.
5. Гибрид: n8n-граф + тестируемый код-модуль с pytest и промптами в git (v1/v2 + changelog).
6. Guardrails: PII-политика, линтер AI-slop, письмо всегда черновик, лимит писем/день.
7. Демо, которое не умрёт через 2 недели: Next.js на Vercel + keep-alive Supabase.
8. Loom ≤ 5 мин + README с trade-offs, «что не сделано и почему», один провал и починка.
9. CRM-адаптер Bitrix24 + HubSpot.
10. Письма по правилам 2026 + мини-eval «AI-only vs AI+правка».
