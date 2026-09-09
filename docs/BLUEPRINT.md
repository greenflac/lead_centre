# Блюпринт: SORP Lead Centre — AI-агент исходящей лидогенерации арендаторов

Версия 1, 2026-09-09. Три спринта по одному дню. Проактивное тестовое под вакансию
«AI-разработчик (vibe coding), SORP Group». Исходники: `docs/brief/`, ресерч: `docs/research/`.

## 0. Согласованные решения

| Вопрос | Решение | Кем |
|---|---|---|
| Формат | Проактивно, без ТЗ от SORP; дедлайн — 3 дня | пользователь, раунд 1–2 |
| Аудитория | HR-скрининг → технический оценщик; два слоя упаковки | пользователь |
| Аутрич | Только черновики. Отправки нет. Кнопка «approve» кладёт лид в HubSpot | пользователь |
| Стек | Python (логика) + Next.js на Vercel (дашборд) + Supabase (данные) + один n8n-воркфлоу (день 3) | пользователь |
| LLM | Anthropic API; ключ заводит пользователь | пользователь |
| Источники | Seed-датасет (реальные + помеченная синтетика) + Serper free (новости) | пользователь |
| CRM | HubSpot free | пользователь |
| Язык | UI/README EN; письма RU или EN по языку лида | пользователь |
| Eval | 30 компаний размечает пользователь вручную | пользователь |

## 1. Допущения, принятые мной (ВЫБРАНО; поправьте, если не так)

1. **Модель.** По умолчанию `claude-opus-5` (адаптивное мышление, effort `low` для скоринга):
   так рекомендует справочник Anthropic; переключение через `LLM_MODEL` в .env. В день 3 — таблица
   «Opus 5 vs Sonnet 5 vs Haiku 4.5» на eval-наборе по κ, цене и латентности. Расчётный расход
   на весь MVP при 60 компаниях × 3 прогона × 2 задачи даже на Opus 5 < $5 (РАСЧЁТ по ценам
   $5/$25 за MTok; 1.5k in + 0.5k out на вызов).
2. **Хостинг Python.** FastAPI-сервис деплоится на Vercel как serverless (python runtime) —
   один аккаунт, ноль новых регистраций. Если упрёмся в 10-секундный лимит функции, батч-скоринг
   гоняем локально/через GitHub Actions, а в Vercel остаются только лёгкие эндпоинты.
3. **Supabase keep-alive.** GitHub Actions cron раз в 3 дня делает `select 1`, чтобы free-проект
   не заснул к моменту, когда оценщик откроет ссылку.
4. **Seed-датасет.** 40–60 реальных компаний из новостей ОАЭ 2025–2026 (с `source_url`, датой
   сбора) + 40–60 синтетических с `is_synthetic=true`. Реальные — только публичные факты о
   компаниях, никаких персональных данных сотрудников.
5. **Имя проекта** — `lead_centre` (по репозиторию). Продуктовое имя в UI: «SORP Lead Centre».
6. **Loom** записывает пользователь по моему сценарию (день 3, ≤ 4 мин).

## 2. Definition of Done (конец дня 3) и стоп-условие (Ц9)

Сдано, если есть все пять:
1. Живой URL дашборда на Vercel: таблица лидов, фильтр по HIGH/MEDIUM/LOW, карточка лида с
   причинами и evidence-URL, превью письма, кнопка Approve → лид появляется в HubSpot.
2. Репозиторий: `README.md` (EN) с архитектурой, trade-offs, eval-результатами (κ, confusion
   matrix, consistency), таблицей цена/латентность из реальных `usage`, «что не сделано и почему»,
   один описанный провал и починка.
3. `docs/onepager.md` для HR: проблема → решение → результат в цифрах → скриншот, 1 экран.
4. Loom ≤ 4 мин по сценарию `docs/loom_script.md`.
5. Прогоны воспроизводимы: `make eval` и `make run` печатают отчёт с числами
   (`проверено N / нарушений M / не смогли K`, `live/cached/synthetic`).

**Стоп-условие.** Если к 18:00 дня 3 пункт 1 не работает end-to-end — замораживаем фичи,
оставшиеся часы уходят на README с честным статусом и Loom по тому, что работает. Записываем
в HANDOFF, что именно не доехало.

## 3. Архитектура

```
 seed CSV ─┐
           ├─► sources/ ─► normalize ─► score (Claude, JSON schema) ─► email draft (RU/EN)
 Serper ───┘        │                        │                              │
                    ▼                        ▼                              ▼
                Supabase.companies     Supabase.scores                Supabase.emails
                                             │                              │
                                     Next.js dashboard  ──Approve──►  HubSpot (company+contact+deal)
                                             ▲
                                   n8n (день 3): cron → POST /run → уведомление
```

**Python-пакет `leadgen/`**
- `sources/seed.py`, `sources/serper_news.py` — каждый адаптер возвращает `list[RawCompany]`
  и счётчик `{live, cached, synthetic}`; сетевые вызовы кэшируются в `data/cache/*.json`,
  режим `OFFLINE=1` читает только кэш (Т4: тесты не ходят в сеть).
- `normalize.py` — единая схема `Company` (pydantic).
- `scoring/rubric.py` — критерии как данные (пороги и списки — в одном месте, Е1).
- `scoring/score.py` — вызов Claude через `messages.parse()` со схемой `ScoreResult`:
  `tier ∈ {HIGH, MEDIUM, LOW}`, `confidence 0–1`, `reasons[]`, `evidence_urls[]`,
  `detected_language ∈ {ru, en, other}`, `next_action`.
- `outreach/draft.py` — письмо 4–6 строк на языке лида, `observation_source` обязателен;
  `outreach/lint.py` — стоп-фразы AI-slop (список — данные, Т1-мутируемый).
- `crm/hubspot.py` — `CrmSink` протокол + HubSpot-реализация + `NullSink` для тестов.
- `store/supabase.py` — запись/чтение.
- `report.py` — печать отчёта прогона числами (Е3, Р2).
- `api.py` — FastAPI: `POST /run`, `GET /leads`, `POST /leads/{id}/approve`, `GET /stats`.

**Eval `eval/`**
- `eval/labels.csv` — 30 компаний, ручная разметка пользователя.
- `eval/run_eval.py` — κ Коэна, confusion matrix, per-tier precision/recall; 3 прогона →
  consistency (% совпадений, κ между прогонами); стоимость и латентность из `usage`.
- Негативный контроль (И5): 3 компании «заведомо LOW» (не в ОАЭ) и 3 «заведомо HIGH» — если
  модель ошибается на них, eval краснеет отдельной строкой.

**Данные (Supabase)**
`companies(id, name, website, industry, size_band, founded_year, jurisdiction, uae_presence,
signals jsonb, source, source_url, is_synthetic, collected_at)`;
`scores(company_id, model, tier, confidence, reasons, evidence_urls, prompt_version, usage jsonb,
latency_ms, run_id)`; `emails(company_id, language, subject, body, observation_source,
lint_ok, status ∈ {draft, approved, rejected})`; `runs(id, started_at, counts jsonb, cost_usd)`.

## 4. Рубрика скоринга v1 (пороги — ВЫБРАНО из ресерча 01; калибруются по eval в день 1)

| Признак | HIGH | MEDIUM | LOW |
|---|---|---|---|
| Присутствие в ОАЭ | mainland-лицензия или анонс открытия офиса в Дубае | free-zone flexi-desk / «планирует выход в ОАЭ» | нет присутствия и планов |
| Размер | ≥ 6 сотрудников или ≥ 3 открытых вакансии в Дубае | 2–5 | solo / неизвестно и нет сигналов |
| Стадия | регистрация ≤ 12 мес, продление лицензии, рост штата | > 12 мес без сигналов роста | закрывается / неактивна |
| Сфера | tech, finance, consulting, trading, crypto, logistics | прочие B2B | retail/производство с потребностью в складе |
| Бюджет-fit | может платить ≥ AED 40k/год за мини-офис | shared / flexi-desk | не соответствует |
| Язык | RU — доп. бонус к приоритету (ЦА SORP) | — | — |

Правило агрегации — в промпте и продублировано детерминированной проверкой на «заведомо LOW»
(не в ОАЭ → никогда не HIGH), чтобы Т1-мутация порога ловилась тестом.

## 5. Спринт 1 (день 1): данные + скоринг + eval

**Цель дня:** `make eval` печатает κ на 30 размеченных компаниях.

| Часы | Задача | Приёмка |
|---|---|---|
| 0–1 | Скелет репо: `pyproject`, `Makefile`, `.env.example`, pre-commit (ruff), CI (pytest, `OFFLINE=1`) | `make test` зелёный на пустом наборе |
| 1–3 | Seed-датасет: 40–60 реальных компаний из новостей (Serper `/news`, запросы из ресерча 02) + 40–60 синтетических | `data/seed_companies.csv`, отчёт `real N / synthetic M`, у каждой реальной — `source_url` |
| 3–5 | `scoring/` с `messages.parse()` и схемой; промпт `prompts/score_v1.md` | 5 компаний с краёв и середины (Т3) скорятся, JSON валиден |
| 5–6 | **Пользователь размечает `eval/labels.csv` (30 строк, ~40 мин)**; я в это время пишу `run_eval.py` | файл с 30 метками в репо |
| 6–8 | Eval: κ, confusion matrix, 3 прогона consistency, негативные контроли; калибровка промпта до v2 при κ < 0.6 | вывод `make eval` в README-черновике; И6: зафиксированы отрицательные итерации промпта |

Тесты дня 1: схема результата (литералы, Т2); негативный контроль; мутация порога «≥ 6
сотрудников» → 4 и 8 красит тест (Т1, в отчёт — что подменили).
**Режется первым:** синтетика сокращается до 20; consistency — до 2 прогонов.

## 6. Спринт 2 (день 2): письма + HubSpot + Supabase + API

**Цель дня:** `make run` прогоняет seed end-to-end, черновики лежат в Supabase, Approve через API создаёт запись в HubSpot.

| Часы | Задача | Приёмка |
|---|---|---|
| 0–2 | `outreach/draft.py` (RU/EN по `detected_language`), `lint.py` со стоп-фразами | 10 писем, 0 слоп-фраз, каждое ≤ 6 строк, есть `observation_source` |
| 2–3.5 | Supabase: схема, миграция, `store/` | `make run` пишет companies/scores/emails/runs |
| 3.5–5 | HubSpot: private app, `CrmSink`, создание company+contact(role-based)+deal с tier | лид виден в HubSpot UI, скриншот в `docs/img/` |
| 5–6.5 | FastAPI `api.py` + деплой на Vercel | `curl GET /leads` отдаёт JSON с живого URL |
| 6.5–8 | `report.py`: `проверено/нарушений/не смогли`, `live/cached/synthetic`, cost из `usage`; keep-alive cron | вывод в README |

Тесты дня 2: линтер (мутация списка стоп-фраз, Т1); `NullSink`; API-контракт; `OFFLINE=1`
в CI (Т4). **Режется первым:** deal в HubSpot (оставить company+contact); Vercel-деплой API →
запуск локально с ngrok только для Loom.

## 7. Спринт 3 (день 3): дашборд + n8n + упаковка

**Цель дня:** оценщик открывает URL, видит лиды, жмёт Approve, лид в HubSpot; README и Loom готовы.

| Часы | Задача | Приёмка |
|---|---|---|
| 0–3 | Next.js дашборд (Lovable/v0 → доводка): таблица, фильтр tier, карточка (reasons, evidence, confidence), превью письма, Approve | живой Vercel URL, П3: открыть глазами, скриншот |
| 3–4 | n8n в Docker, один воркфлоу: cron → `POST /run` → Slack/Telegram-уведомление «N HIGH»; экспорт JSON + скриншот в репо | `n8n/workflow.json`, `docs/img/n8n.png` |
| 4–5 | Сравнение моделей на eval (Opus 5 / Sonnet 5 / Haiku 4.5): κ, $/компания, p50 латентность | таблица в README, ИЗМЕРЕНО |
| 5–7 | README (EN), `docs/onepager.md` (HR), `docs/loom_script.md`, раздел «провал и починка», «не сделано и почему», backlog | П3: README прочитан целиком |
| 7–8 | Пользователь пишет Loom; финальный `make eval && make run`, вывод — в README; HANDOFF | всё в ветке, стоп-условие проверено |

**Режется первым:** n8n → абзац «как перенести в n8n» + backlog; сравнение моделей → только
две модели.

## 8. Что сдаём и кому

- **HR:** ссылка на дашборд, `docs/onepager.md`, Loom.
- **Технарь:** репо (README, тесты, eval, промпты с версиями `prompts/score_v1.md` → `v2`,
  `CHANGELOG` промптов), n8n JSON, таблицы κ/цена/латентность.
- **Общее:** первое сообщение в отклике — 5 строк: проблема SORP BC → что построено → цифры
  (κ, $/лид, время прогона) → ссылка → «готов показать за 15 минут».

## 9. Вне MVP (backlog, честно в README)

LinkedIn (юридический риск, см. ресерч 02); Apify job boards и Google Places; реальная отправка
(Instantly/lemlist + PDPL opt-out); Bitrix24-адаптер; RAG по базе услуг SORP для кросс-селла;
автоповтор LOW через 3 месяца; мультиагентная верификация через реестры NER/DET.

## 10. Метрики MVP (замена недостижимым за 3 дня из концепции v0)

| Метрика | Как измеряем | Цель |
|---|---|---|
| Согласие с ручной разметкой | κ Коэна на 30 компаниях | ≥ 0.6 |
| Стабильность | 3 прогона, % идентичных tier | ≥ 90% |
| Негативные контроли | 6 компаний | 6 из 6 |
| Стоимость | $/компания из `usage` | печатается, цель — знать число |
| Латентность | p50 полного цикла на компанию | печатается |
| HIGH с evidence-URL | доля HIGH, у которых ≥ 1 проверяемая ссылка | 100% |
| Слоп в письмах | нарушений линтера | 0 |

Конверсию в показы и аренду измерить нельзя — в README так и написано, с планом измерения
после пилота (2 недели, 50 отправок с согласия SORP).
