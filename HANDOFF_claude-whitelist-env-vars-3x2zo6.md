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

## Сессия 1, продолжение — 2026-09-09 — вайтлист перезалит, исправление проверено

- Пользователь залил обновлённый `whitelist.txt` (apex + wildcard) в настройки среды.
  Политика применилась к текущему контейнеру, новая сессия не понадобилась.
- Прогон `docs/ops/check_whitelist.sh`: **apex-хостов, обрубленных политикой, стало 0**
  (было 106). apex: OK 96 / POLICY 0 / ORIGIN 9 / NETFAIL 7 / NODNS 2.
  Пункт 1 из «открыто для следующей сессии» закрыт.
- Проверялка переписана: пять исходов вместо трёх. Оказалось, прокси отдаёт
  `CONNECT tunnel failed, response 403` и на закрытый домен, и на несуществующий, —
  прежняя версия называла бы `www.hubapi.com` заблокированным, хотя у него просто нет
  A-записи. Теперь классы `POLICY` и `NODNS` разделены проверкой DNS, а `NETFAIL`
  (битый TLS у госсайтов ОАЭ) отделён от «закрыто».
- Осталось открытым: ключи Serper / Supabase / HubSpot в среде так и не заданы;
  госисточники `dubaipulse.gov.ae`, `mohre.gov.ae`, `economy.ae`, `moet.gov.ae`,
  `growth.gov.ae` доезжают по сети, но рвут TLS на apex — нужен рабочий поддомен/путь,
  вайтлистом это не чинится.

## Сессия 1, продолжение — 2026-09-09 — ключи

Пользователь добавил ключи. Имена в среде: `CLAUDE_KEY`, `SERPER_KEY`,
`HUBSPOT_PERSONAL_KEY`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`,
`SUPABASE_SECRET_KEY`, `SUPABASE_JWKS_URL`. Значения нигде не печатались.

Прогон `docs/ops/check_keys.sh` (живой запрос + негативный контроль на тот же эндпоинт),
вывод — `docs/ops/keys_check.txt`:
- `CLAUDE_KEY` 200 / bogus 401 — рабочий;
- `SERPER_KEY` 200 / bogus 403 — рабочий;
- `SUPABASE_PUBLISHABLE_KEY` и `SUPABASE_SECRET_KEY` 404 «table not found» / bogus 401 —
  рабочие (корень `/rest/v1/` пускает только secret, поэтому проверка идёт запросом к
  несуществующей таблице: 404 = авторизация пройдена);
- `SUPABASE_JWKS_URL` 200;
- **`HUBSPOT_PERSONAL_KEY` не работает**: 401 `EXPIRED_AUTHENTICATION`, «expired 20705
  days ago», expire time 1970-01-01 — то же, что у заведомо неверного токена. В переменной
  лежит OAuth access token (`CiR…`), а нужен Private App token (`pat-naX-…`) со скоупами
  `crm.objects.contacts.*` / `crm.objects.companies.*`. `?hapikey=` тоже 401, HubSpot его
  отключил.

Открыто: заменить HubSpot-токен. Остальное для спринтов 1–3 готово — сеть и три ключа.
