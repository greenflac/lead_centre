# Состояние среды сессии — замер 2026-09-09

Всё ниже, кроме явных пометок, — ИЗМЕРЕНО в этом контейнере командами, вывод которых
приведён в `docs/ops/whitelist_check.txt` и в отчёте сессии.

## 1. Ключи

| Переменная | Есть | Проверка |
|---|---|---|
| `CLAUDE_KEY` | да | `GET https://api.anthropic.com/v1/models` с этим ключом → **200**; негативный контроль (заведомо неверный ключ) → **401**. Ключ рабочий, доступны `claude-opus-5`, `claude-sonnet-5`, `claude-fable-5-1` и др. |
| `ANTHROPIC_API_KEY` | **нет** | имени в среде нет |
| `SERPER_API_KEY` | нет | host `google.serper.dev` доезжает (403 без ключа), ключа нет |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` | нет | — |
| `HUBSPOT_TOKEN` | нет | — |
| `GITHUB_TOKEN` / `GH_TOKEN` | да (выдан платформой) | — |

Следствия для кода MVP:

1. SDK `anthropic` по умолчанию читает **`ANTHROPIC_API_KEY`**, а не `CLAUDE_KEY`.
   В коде брать ключ явно: `os.environ["CLAUDE_KEY"]` (или продублировать имя в среде).
2. В среде задан `ANTHROPIC_BASE_URL`; ИЗМЕРЕНО — он равен `https://api.anthropic.com`,
   то есть публичному API, подменять его в коде не нужно.
3. Ключ в код/логи/чат не попадает: читается из среды, в отчётах только имя.

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
