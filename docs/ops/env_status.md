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

## 2. Вайтлист доменов

Политика сети применена: `api.anthropic.com`, `google.serper.dev` и остальные рабочие
поддомены доезжают, `example.com` (вне списка) — нет.

Главная находка (ИЗМЕРЕНО): **`*.domain` пропускает только поддомены, apex — нет.**
Из 114 доменов вайтлиста ответили только 8 apex — в основном те, что и так в дефолтном
списке платформы (`pypi.org`, `pythonhosted.org`, `npmjs.org/com`, `github.com`,
`arxiv.org`, `microsoft.com`; плюс `invest.dubai.ae`, почему — не выяснял).
Остальные 106 apex-хостов рвутся на прокси (curl exit 56), при этом их `www.` работает. Ломается это на редиректах `www.supabase.com → supabase.com`,
`www.vercel.com → vercel.com`, `www.claude.com → claude.com`.

Исправление: в `docs/ops/whitelist.txt` каждый домен теперь двумя строками — apex и
wildcard. **Файл нужно перезалить в настройки среды**; до этого apex-хосты закрыты.
Проверка после перезаливки: `docs/ops/check_whitelist.sh > docs/ops/whitelist_check.txt`,
строки должны стать `apex=OK`.

Три исхода проверки (Р1), а не два:

- `OK` — хост ответил HTTP-кодом 2xx/3xx;
- `BLOCKED` — соединение оборвано без кода (политика; так же выглядит несуществующий домен);
- `ORIGIN` — доехали, отказал сам сайт (401/403/404/5xx) — это не вина политики.

Негативный контроль прибора: `example.com` (не в списке) → `BLOCKED`,
`pypi.org` → `OK`. То есть проверка различает оба исхода, а не всегда печатает один.

Что нужно для спринтов и уже доезжает (по поддоменам):
`api.anthropic.com`, `api.hubapi.com`, `api.hubspot.com`, `google.serper.dev`,
`app.supabase.com`, `api.vercel.com`, `registry.npmjs.org`, `files.pythonhosted.org`,
`raw.githubusercontent.com`.

Закрыто и apex, и www (нужны отдельно, если пойдём в эти источники):
`dubaipulse.gov.ae`, `bayanat.ae`, `economy.ae`, `growth.gov.ae`, `mohre.gov.ae`,
`supabase.co` (apex; проектные `<ref>.supabase.co` — поддомены, отдельно не проверялись,
т.к. проекта ещё нет — НЕПРОВЕРЕНО), `sorptaxaccounting.com`.

Отдают 403/503 сам сайт (антибот, не политика): `bayut.com`, `bayt.com`, `cbre.com`,
`savills.com`, `arabianbusiness.com`, `dubai.ae`, `uaelegislation.gov.ae`, `wordstream.com`,
`amazon.com`. Для них нужен не вайтлист, а другой способ доступа.
