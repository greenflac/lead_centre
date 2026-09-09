# HANDOFF — ветка `claude/whitelist-env-vars-3x2zo6`

Append-only журнал сессий (Ц6). Новые записи — в конец.
Предыстория проекта — в `HANDOFF_claude-sorp-group-ai-mvp-40v8h9.md`.

## Сессия 1 — 2026-09-09 — приёмка среды (вайтлист + ключ)

### Контекст
Пользователь: «добавил вайтлист в env, ключ Клод тоже там». Задача сессии — проверить,
что доехало, и записать состояние среды, чтобы спринты не упирались в сеть/ключи.

### Что измерено
- `CLAUDE_KEY` есть и рабочий: `GET api.anthropic.com/v1/models` → 200,
  негативный контроль (неверный ключ) → 401. Доступны `claude-opus-5`, `claude-sonnet-5`,
  `claude-fable-5-1`. Имени `ANTHROPIC_API_KEY` в среде НЕТ — SDK по умолчанию читает его,
  поэтому в коде ключ брать явно из `CLAUDE_KEY`.
- `ANTHROPIC_BASE_URL` = `https://api.anthropic.com` (публичный), подменять не надо.
- Ключей `SERPER_API_KEY`, `SUPABASE_*`, `HUBSPOT_TOKEN` в среде нет — сети хватает,
  ключей нет. Открытый вопрос к пользователю.
- Вайтлист применён, но `*.domain` пропускает **только поддомены**: из 114 доменов ответили
  8 apex, 106 рвутся на прокси (curl exit 56) при живом `www.`. Ломает редиректы
  `www.supabase.com → supabase.com` и т.п.

### Что сделано
- `docs/ops/check_whitelist.sh` — воспроизводимая проверка, три исхода OK/BLOCKED/ORIGIN (Р1),
  с негативным контролем (`example.com` → BLOCKED, `pypi.org` → OK).
- `docs/ops/whitelist_check.txt` — вывод прогона 2026-09-09 (артефакт замера, И2/И3).
- `docs/ops/whitelist.txt` — каждый домен теперь двумя строками: apex + wildcard (228 строк).
- `docs/ops/env_status.md` — состояние ключей и сети с пометками ИЗМЕРЕНО/НЕПРОВЕРЕНО.

### Открыто для следующей сессии
1. Пользователю перезалить обновлённый `whitelist.txt` в настройки среды; после этого
   прогнать `docs/ops/check_whitelist.sh` — apex-строки должны стать `apex=OK`.
   До перезаливки исправление НЕ проверено (И3): правка файла ≠ применённая политика.
2. Нужны ли ключи Serper / Supabase / HubSpot в среде — вопрос к пользователю.
3. Совсем закрыты (и apex, и www): `dubaipulse.gov.ae`, `bayanat.ae`, `economy.ae`,
   `growth.gov.ae`, `mohre.gov.ae`, `supabase.co`, `sorptaxaccounting.com`.
   Первые пять — это источники данных из концепции; если они нужны, политика их не пускает.
4. 403/503 от самих сайтов (антибот, не политика): `bayut.com`, `bayt.com`, `cbre.com`,
   `savills.com`, `dubai.ae`, `uaelegislation.gov.ae`, `arabianbusiness.com`.
