# Ресерч 6: размер пула, русскоязычный приток, правила, триггер-фиды, адреса, датасеты

> Агент-ресерч, 2026-09-09. Прокси закрыл dubaipulse, growth.gov.ae, dubaichambers, gulfnews,
> kommersant, forbes.ru, dmcc, bayt, gulftalent и др. Всё — из сниппетов, НЕПРОВЕРЕНО;
> официальные цифры — «НЕПРОВЕРЕНО по сниппету официального источника».

## 1. Размер пула
| Источник | Цифра |
|---|---|
| DET, H1 2026 (mainland + фризоны) | 58 337 новых лицензий, 173 652 продления за полгода → ≈9700 новых/мес (Gulf News) |
| Активные лицензии Дубая | >215 000; DUL выдано >900 000 (mediaoffice.ae) |
| Dubai Chamber (обязательно для mainland ≈ прокси mainland) | 35 532 новых H1 2025; 70 500 за 2024 → ≈5900/мес |
| DMCC 2025 | +2300 (≈190/мес), всего >26 000 |
| Meydan FZ | «>30 000» vs «83 000» — расходятся |
| RAKEZ | >30 000; SHAMS >6000; IFZA — свежих чисел нет |

Доля flexi-desk vs физический офис: официальной статистики нет; единственная цифра — «80%
solo-founders на flexi» (маркетинг консультанта, нерепрезентативно).

## 2. Русскоязычный приток
- 2024: новых российских компаний в ОАЭ в 2.5× больше, ≈4000 за год (Forbes.ru по данным Uppercase).
- Конец 2025: 13 500 российских компаний в ОАЭ, ≈2000 новых лицензий за 2025; консультанты
  фиксируют замедление из-за нехватки площадей (Коммерсантъ, Business Emirates). Ряд
  3.5k → 13.5k внутренне не согласован.
- Россия не в топ-10 национальностей новых членов Dubai Chamber (Индия 9038 в H1 2025).

**Тест русскоязычных запросов (WebSearch; Serper напрямую не вызывался — ключа нет):**
«открывает офис в Дубае», «выходит на рынок ОАЭ» 2026, «релокация в Дубай» → почти целиком
SEO-контент сетапщиков и гайды vc.ru; 0–2 свежих новости. Целевые источники лучше:
Business Emirates, Russian Emirates, UPPERNEWS (uppersetup), Коммерсантъ, ComNews; Telegram
не тестировался.

## 3. Правила (вторичные источники)
- Mainland (MOHRE): ~1 виза на 9 кв. м (100 sq ft); квота обновляется после загрузки Ejari.
- Ejari обязателен для выдачи и ежегодного продления mainland-лицензии; DET проверяет
  автоматически; договор должен действовать ≥1 мес после истечения лицензии. Существует
  рынок «virtual office Ejari».
- IFZA: пакеты 0/1/3/6 виз, потолок 6; flexi обычно 1–3. Meydan: визы отдельно (AED 1850/шт.,
  до 6 на flexi). DMCC: виртуального нет, flexi до 3 виз, физический 1 виза/100 sq ft.
  RAKEZ: Flexi Desk BC 0 виз, Flexi Desk 1, flexi office 2, далее по площади до 6–8.
- Продление ежегодно и на mainland, и во фризонах.

## 4. Триггер-фиды
**(a) Вакансии.** LinkedIn «13 000+ jobs in Dubai»; GulfTalent 7694 в Дубае. Объявление
показывает город и работодателя, но не тип адреса — тип выводится join'ом с профилем
компании/Google Maps. Kaggle «Job Posting Data in UAE» — годится для демо.

**(b) Адреса-регистраторы фризон (классификатор «flexi/virtual»):**
- IFZA — IFZA Business Park, Building A2/A1, Dubai Digital Park, Dubai Silicon Oasis.
- Meydan FZ — Meydan Grandstand, 6th floor, Meydan Road, Nad Al Sheba, P.O. Box 9305.
- DMCC — JLT: Almas Tower / One JLT / DMCC Business Centre.
- RAKEZ — Compass Coworking Centre, Al Hamra, RAK; Boulevard Plaza Tower 2, Floor 22, Downtown.
- SHAMS — Al Messaned, Al Bataeh, Sharjah. Dubai South — Business Centre, P.O. Box 282228.
- DSO/DTEC — Dtec, Dubai Silicon Oasis. DAFZA — Al Quds St, P.O. Box 491.
- DWTC FZ — Podium Building 3rd Floor, Za'abeel, SZR.

**(c) Бизнес-центры (serviced/virtual):**
- Regus: DWTC C1, Marina Gate, Barsha Heights, The Offices 1, Downtown, Business Bay, JLT.
- Servcorp: Emirates Towers L41–42; Boulevard Plaza 2 L23; Almas Tower L54; Al Habtoor L21.
- Nook (Standard Chartered Tower L5); AstroLabs (JLT Cluster R); WeWork (One Central).
- Media City: Concord Tower, Arenco Tower, Business Central, Shatha Tower, Loft Offices.

**(d) Англоязычные новости за 90 дней (3 запроса):** Mishcon de Reya, SCC, SharpMinds,
Blackstone (DIFC), Staffbase (DIC), Carmignac (DIFC), Oppenheim Group — ≈7 релевантных,
почти все крупные фирмы в DIFC/DIC (офис уже есть). Для SME-кросс-сейла точность низкая,
как сигнал «входит в ОАЭ → визы/бухгалтерия» пригодно.

## 5. Датасеты
- **Dubai Pulse (DET, только mainland):** `ded_license_master`, `ded_license_activities`,
  `ded_license_partners`, `ded_business_activities`; CSV + API. Сниппет: лицензия с обязательной
  атрибуцией и разрешением коммерческого использования; поля (дата выдачи, адрес) и точные
  условия — НЕПРОВЕРЕНО (домен закрыт). `partners` даёт имена партнёров → прокси
  «русскоязычный» по транслитерации.
  https://www.dubaipulse.gov.ae/data/ded-licenses/ded_license_master-open
- **bayanat.ae:** CC BY 4.0, коммерческое использование с атрибуцией; группа business-registration.
- **growth.gov.ae/G2C** (НЭР, ~1.4M компаний, поиск без логина, bulk неизвестен). НЕПРОВЕРЕНО.
- Kaggle/GitHub: датасета лицензий нет; Gigasheet UAE Business List — происхождение неясно,
  не в прод (Ц5).

## Итог
**(1) Адресуемый пул/мес (РАСЧЁТ на НЕПРОВЕРЕННЫХ цифрах; доли — ВЫБРАНО):**
новых лицензий ≈9700/мес; mainland ≈5900, фризоны ≈3800. Flexi среди фризонных — ВЫБРАНО 70%
→ ≈2700/мес. Дорастают до офиса за 12–18 мес — ВЫБРАНО 10–15% → **270–400/мес** «горячих»
под офис + визы; остальные — кросс-сейл бухгалтерии/продления. Mainland на «virtual Ejari»/БЦ —
ВЫБРАНО 30% → ≈1800/мес кандидатов на апгрейд. Русскоязычные: ≈170/мес новых → **12–25/мес**
«горячих», все 170 — цель для setup/визы/бухгалтерии. Наблюдаемых сигналами — 5–10% пула.

**(2) Топ-5 триггеров (доступность × точность):**
1. Дельта Dubai Pulse `license_master`/`partners`: новые mainland-лицензии + дата выдачи →
   продление через 11 мес; имена партнёров → русскоязычные. Доступность высокая, точность по
   новизне высокая; тип адреса — проверять.
2. Вакансия в Дубае у компании, чей адрес совпадает с адресом-регистратором фризоны (b).
3. Число сотрудников/вакансий > визового потолка пакета (IFZA 6, DMCC flexi 3, RAKEZ 1–2).
4. Англоязычные новости «opens Dubai office» — 2–3/нед, легко, но крупные фирмы.
5. Русскоязычные новости/Telegram — средняя доступность, низкая точность; Google засорён SEO.

## Проверка доступности из среды сессии (2026-09-09, Ц10)
`curl` к dubaipulse.gov.ae (страница датасета и CSV) и bayanat.ae → HTTP 000 (соединение не
установлено через прокси). Датасет НЕПРОВЕРЕН отсюда; скачивать нужно с машины пользователя.
