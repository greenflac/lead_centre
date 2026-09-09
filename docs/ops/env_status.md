# Состояние среды сессии — замер 2026-09-09

Всё ниже, кроме явных пометок, — ИЗМЕРЕНО в этом контейнере командами, вывод которых
приведён в `docs/ops/whitelist_check.txt` и в отчёте сессии.

## 1. Ключи — прогон 2026-09-09 (после «добавил все ключи»)

Каждый ключ проверен живым запросом и негативным контролем (заведомо неверный ключ
на тот же эндпоинт), иначе 200 не отличить от «эндпоинт пускает всех» (И5).
Значения ключей нигде не печатаются — только имена и коды ответов.

| Переменная | Запрос | Реальный ключ | Негативный контроль | Вывод |
|---|---|---|---|---|
| `CLAUDE_KEY` | `GET api.anthropic.com/v1/models` | **200** | 401 | рабочий |
| `SERPER_KEY` | `POST google.serper.dev/search` | **200** | 403 | рабочий |
| `SUPABASE_PUBLISHABLE_KEY` | `GET $SUPABASE_URL/rest/v1/<нет таблицы>` | **404** «table not found» — авторизация пройдена | 401 «Invalid API key» | рабочий |
| `SUPABASE_SECRET_KEY` | то же + `GET /rest/v1/` | **404 / 200** | 401 | рабочий |
| `SUPABASE_JWKS_URL` | `GET` | **200** | — | рабочий |
| `HUBSPOT_PERSONAL_KEY` | `GET api.hubapi.com/crm/v3/objects/contacts?limit=1` | **401** | 401 | **НЕ работает** |

Про `/rest/v1/` (корень): с publishable-ключом он отдаёт 401 «Only secret API keys can be
used for this endpoint» — это ограничение самого эндпоинта, а не плохой ключ; на обычном
запросе к таблице ключ проходит. Поэтому проверять publishable корнем нельзя.

### HubSpot: ключ мёртвый

Ответ API: `EXPIRED_AUTHENTICATION`, «The OAuth token used to make this call expired
20705 day(s) ago», expire time `1970-01-01T00:00:00Z` — то есть HubSpot не признаёт токен
вообще. Тот же 401 даёт и заведомо неверный токен, так что негативный контроль не
различает их: работоспособность НЕ подтверждена ни одним запросом.
Формат тоже не тот: в переменной лежит строка вида `CiR…` (OAuth access token), а для
серверных вызовов нужен токен **Private App** вида `pat-naX-…`
(HubSpot → Settings → Integrations → Private Apps → Create private app, скоупы
`crm.objects.contacts.read/write`, `crm.objects.companies.read/write`).
Устаревший способ `?hapikey=` тоже даёт 401 — HubSpot их отключил.

Замечание по имени: SDK `anthropic` по умолчанию читает `ANTHROPIC_API_KEY`, которого в
среде нет. В коде MVP ключ брать явно из `CLAUDE_KEY`. `ANTHROPIC_BASE_URL` = публичный
`https://api.anthropic.com`, подменять не нужно.

## 2. Вайтлист доменов — ПОСЛЕ перезаливки (прогон 2026-09-09, `whitelist_check.txt`)

Исправление подтверждено прогоном: **apex-хосты открылись**. Было 106 apex, обрубленных
политикой, стало **0**. Итог по 114 доменам (apex): `OK 96`, `POLICY 0`, `ORIGIN 9`,
`NETFAIL 7`, `NODNS 2`. По `www.`: `OK 88`, `POLICY 0`, `ORIGIN 16`, `NETFAIL 5`, `NODNS 5`.

Ключевая проверка: `api.anthropic.com/v1/models` с `CLAUDE_KEY` → **200**;
`example.com` (вне списка) → **POLICY**, то есть политика по-прежнему действует и
проверка её видит — это не «всё открыли».

Пять исходов проверки (Р1 — «не смогли» не сворачивается ни в «годно», ни в «не годно»):

- `OK` — 2xx/3xx;
- `POLICY` — прокси вернул `CONNECT tunnel failed, response 403`: домена нет в вайтлисте;
- `NODNS` — та же сигнатура прокси, но у хоста нет A-записи. Прокси отвечает 403 и на
  несуществующий домен, поэтому без проверки DNS «закрыто» и «хоста не существует»
  неразличимы. Разделено явно: `www.hubapi.com`, `www.supabase.co`, `www.pythonhosted.org`,
  `githubusercontent.com`, `my.site.com` — это `NODNS`, чинить вайтлистом нечего,
  рабочие `api.hubapi.com`, `files.pythonhosted.org`, `raw.githubusercontent.com` доезжают;
- `ORIGIN` — доехали, отказал сам сайт (403/503 антибот): `bayut.com`, `bayt.com`,
  `cbre.com`, `savills.com`, `dubai.ae`, `uaelegislation.gov.ae`, `arabianbusiness.com`,
  `tass.com`, `jamanetwork.com`. Вайтлист тут ни при чём, нужен другой способ доступа;
- `NETFAIL` — доехали до TLS и не договорились: `dubaipulse.gov.ae`, `mohre.gov.ae`,
  `economy.ae`, `moet.gov.ae` (TLS `unrecognized name` — apex не обслуживается),
  `growth.gov.ae` (сертификат не проверяется), `supabase.co`/`chilipiper.com` (таймаут).
  Это «не смогли проверить», а не «закрыто»; для госисточников ОАЭ нужен рабочий хост
  (конкретный поддомен/путь), а не строка в вайтлисте.

Негативный контроль прибора: на контрольном наборе выдаются все пять классов
(`example.com` → POLICY, `pypi.org` → OK, `growth.gov.ae` → NETFAIL, `www.bayt.com` → ORIGIN,
`www.hubapi.com` → NODNS), то есть проверка не печатает всегда один и тот же исход.

Рабочие эндпоинты для спринтов (ИЗМЕРЕНО): `api.anthropic.com` (200 с ключом),
`api.hubapi.com` 302, `api.hubspot.com` 302, `google.serper.dev` 403 (жив, нужен ключ),
`app.supabase.com` 308, `api.vercel.com` 308, `registry.npmjs.org` 200,
`files.pythonhosted.org` 200, `raw.githubusercontent.com` 301.

Перепроверка в любой момент: `docs/ops/check_whitelist.sh > docs/ops/whitelist_check.txt`.
