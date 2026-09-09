# Ресерч 2: источники данных о компаниях ОАЭ для outbound-лидогенерации

> Агент-ресерч, 2026-09-09. Официальные сайты (u.ae, ner.economy.ae, difc.com, dmcc.ae, apify.com,
> resend.com и др.) закрыты прокси, поэтому поля реестров и точные формулировки ToS — из вторичных
> источников, НЕПРОВЕРЕНО. Free-лимиты — из обзоров 2026; перепроверить на страницах вендоров
> перед стартом (Ц10).

## 1. LinkedIn — не годно как источник (кроме cookieless-скрейпинга компаний/вакансий, рискованно)
- Официальные API: Sales Navigator Application Platform не принимает новых партнёров; Job Posting
  API только для ATS-партнёров. За 2 недели партнёрство не получить.
- Право: hiQ v. LinkedIn — 9-й округ (2022): скрейпинг публичных страниц не CFAA-нарушение, но
  итог — consent judgment, hiQ признана нарушившей user agreement, $500k, постоянный запрет.
  «Не уголовно» ≠ «законно» для залогиненного.
- Proxycurl закрыт 04.07.2025 после иска LinkedIn. LinkedIn активно судится с посредниками.
- Phantombuster — session-cookie → нарушение §8.2 User Agreement, риск бана; $69–439/мес.
- Apify cookieless-акторы (Company ~$4/1000, Jobs ~$1/1000): технически работают, аккаунт не
  затрагивают; серая зона. **Вердикт:** компании/вакансии — «рискованно, допустимо для демо
  с оговоркой»; профили людей — не использовать.

## 2. Apify — годно
Free: $5 кредитов/мес без карты, блокировка при исчерпании. Google Maps Scraper ≈ $3.90/1000
мест → ~1000–1250 мест бесплатно. Поля: название, категория, адрес, телефон, сайт, число отзывов
(0–5 отзывов = proxy «молодости»). Есть акторы Bayt, GulfTalent, LinkedIn Jobs.

## 3. Search API — годно; лучший бесплатный «двигатель»
| Сервис | Free | Примечание |
|---|---|---|
| SerpAPI | 250/мес бессрочно | Google News с `tbs=qdr:m`/`cdr` — фильтр по дате |
| Serper.dev | 2500 разово | `/search`, `/news`, `/places`; самый щедрый для 2 недель |
| Tavily | 1000 кредитов/мес | `topic="news"` + `days=N`; LLM-friendly выдержки |
| Brave | free убран 02.2026, $5/1000, нужна карта | не брать |

Запросы: `"opens Dubai office" site:khaleejtimes.com`, `"expands to UAE" 2026`,
`"new office" "Barsha Heights" OR "Media City" OR "Internet City"` — HIGH-лиды с явным намерением.

## 4. Официальные реестры — верификатор, не источник обнаружения
- NER (ner.economy.ae): публичный поиск по названию/номеру/виду деятельности; массового листинга,
  экспорта, API нет (НЕПРОВЕРЕНО; сайт отдавал ошибку).
- Invest in Dubai / DET: Trade Licence Verification — статус, срок, имя, деятельность, адрес.
  Только по имени/номеру.
- DIFC Public Register: бесплатно, дата инкорпорации, статус, директора. Но DIFC-компании ищут
  офис в DIFC — низкая релевантность.
- DMCC: directory «с согласия членов», скрейпить нельзя (НЕПРОВЕРЕНО). География JLT.
- DSO — есть directory; IFZA, DAFZA, RAKEZ, Meydan — публичных директорий с датами не найдено.
  **Важно:** IFZA/Meydan/RAKEZ дают лицензии с flexi-desk без офиса — именно они через 6–18 мес
  ищут реальный офис в TECOM, но списка резидентов нет.
**Вердикт:** реестры — шаг verify+enrich на 20–50 лидов вручную/единичными запросами.

## 5. Google Places API — годно с ограничениями
С 01.03.2025 free-пороги по SKU: 10 000/мес Essentials, 5000 Pro (Text/Nearby Search),
1000 Enterprise; сверх — Text Search Pro $32/1000. **Даты основания нет.** Есть `businessStatus`,
`openingDate` только для FUTURE_OPENING. Косвенные сигналы новизны: `userRatingCount` ≤ 5,
нет сайта/фото. Нужна карта (GCP billing).

## 6. Стартап-базы — условно годно (ручная работа на free)
- Crunchbase: фильтры founded/HQ/headcount и до 1000 результатов — только Pro $99/мес; free API
  убран; есть Pro-триал → одна выгрузка 200–500 строк «HQ Dubai, founded 2024–2026» для seed.
- Dealroom: от €12 500/год — не годно.
- Magnitt: free research-аккаунт; экспорт — НЕПРОВЕРЕНО, вероятно платно. Ручной просмотр.
- Wamda: медиа, базы нет — только новости.

## 7. Job boards как intent-сигнал — годно, лучший бесплатный proxy
- API нет: Indeed Publisher API закрыт 2023; LinkedIn Jobs partner-only; Bayt/GulfTalent — только
  Apify-акторы (HTTP, без логина). LinkedIn Jobs cookieless ~$1/1000.
- Proxy: «нанимает 3+ офисных ролей в Дубае» + «нет физического адреса на Maps» = сильный сигнал.
  Слабость: крупные компании тоже нанимают — фильтр ≤50 чел. и «свежесть». Валидации proxy нет —
  в скоринге помечать `ВЫБРАНО`, не `ИЗМЕРЕНО`.

## 8. Новости — годно
RSS: Khaleej Times /business, Gulf Business `/feed`, Zawya `/sitemaps/en/rss`, Arabian Business.
Gulf News RSS — НЕПРОВЕРЕНО. RSS даёт ~20–50 последних; ретроспектива 3 мес — через Serper `/news`.
LLM-классификация заголовков: «opens Dubai office», «expands to UAE», «relocates HQ» → HIGH.

## 9. Property Finder / Bayut — не для спроса
Это предложение. Польза только как бенчмарк цены за sq ft для персонализации письма.

## 10. Обогащение и e-mail — годно с free-лимитами; PDPL требует минимализма
- Hunter.io 25 поисков + 50 верификаций/мес; Apollo до 10 000 email-кредитов/мес с корпоративным
  доменом (100 с Gmail), 10 экспортов; Snov.io 50 кредитов без API; Lusha 40/мес; Clearbit →
  Breeze, бесплатного нет с 30.04.2025.
- Право: PDPL (Decree-Law 45/2021) допускает legitimate interest, право возражать против direct
  marketing; TDRA anti-spam: идентификация отправителя + opt-out. Практика MVP: ролевые/корпоративные
  адреса, отправитель и unsubscribe, хранить источник контакта, не тянуть телефоны.
  **Для демо — не отправлять реальным людям вообще.**

## 11. Отправка
- Resend AUP запрещает cold outreach и скрейпленные списки; SendGrid — то же, блокируют.
- Instantly — под cold email; lemlist — допускает при соблюдении privacy-норм.
- Для MVP: письма в draft/preview; отправка через Instantly/lemlist-триал или mock-адаптер.

## Рекомендация: топ-3 для демо-pipeline
1. **Search API (Serper.dev, резерв Tavily) по новостям** — единственный источник явного намерения,
   HIGH-лиды с цитируемым подтверждением.
2. **Job boards через Apify** (Bayt/GulfTalent/LinkedIn Jobs cookieless) — масштабируемый
   MEDIUM-сигнал: ≥3 офисных вакансий в Дубае у компании ≤50 чел.
3. **Google Places + Apify Google Maps** — покрытие и обогащение (адрес, сайт, отзывы как proxy
   возраста). Плюс ручная верификация 20–50 лидов через NER/DET/DIFC.

Исключить: LinkedIn-профили людей, Crunchbase API, Dealroom, Phantombuster, Resend/SendGrid
для рассылки.

## Fallback: seed-датасет
- `data/seed_companies.csv` 150–300 строк: 50–100 реальных компаний из новостей + Crunchbase-триал
  + Maps с `source_url` и датой сбора; остальное — синтетика по тем же полям (name, industry,
  size_band, founded, uae_presence ∈ {none, freezone-flexi, mainland-office}, signals[],
  contact_role), помеченная `is_synthetic=true`.
- Режим `--offline`: адаптеры читают закэшированные JSON; в отчёте прогона печатается
  `live N / cached M / synthetic K` (Е3, Р2), чтобы кэш не выдавался за live.
- Скоринг и письма — на seed; live-запросы — на 3–5 компаниях в конце как «расширение».
