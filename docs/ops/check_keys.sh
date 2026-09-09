#!/usr/bin/env bash
# Проверка ключей среды: живой запрос + негативный контроль (заведомо неверный ключ на
# тот же эндпоинт). Без контроля 200 не отличить от «эндпоинт пускает всех» (И5).
# Значения ключей не печатаются — только имена, коды ответов и вердикт.
# Исходы (Р1): OK / FAIL / NOKEY («переменной нет» — это не «ключ плохой»).
# Использование: docs/ops/check_keys.sh
set -u
code() { curl -s -o /dev/null -w '%{http_code}' -m 25 "$@"; }
verdict() { # имя real bogus
  local n=$1 r=$2 b=$3
  if [ "$r" = "$b" ]; then printf '%-26s real=%-4s bogus=%-4s FAIL (не отличается от неверного)\n' "$n" "$r" "$b"
  else printf '%-26s real=%-4s bogus=%-4s OK\n' "$n" "$r" "$b"; fi
}
need() { [ -n "${!1:-}" ] || { printf '%-26s NOKEY (переменной нет в среде)\n' "$1"; return 1; }; }

if need CLAUDE_KEY; then
  verdict CLAUDE_KEY \
    "$(code https://api.anthropic.com/v1/models -H "x-api-key: $CLAUDE_KEY" -H 'anthropic-version: 2023-06-01')" \
    "$(code https://api.anthropic.com/v1/models -H 'x-api-key: sk-ant-bogus' -H 'anthropic-version: 2023-06-01')"
fi
if need SERPER_KEY; then
  verdict SERPER_KEY \
    "$(code -X POST https://google.serper.dev/search -H "X-API-KEY: $SERPER_KEY" -H 'Content-Type: application/json' -d '{"q":"test"}')" \
    "$(code -X POST https://google.serper.dev/search -H 'X-API-KEY: bogus' -H 'Content-Type: application/json' -d '{"q":"test"}')"
fi
if need HUBSPOT_PERSONAL_KEY; then
  verdict HUBSPOT_PERSONAL_KEY \
    "$(code 'https://api.hubapi.com/crm/v3/objects/contacts?limit=1' -H "Authorization: Bearer $HUBSPOT_PERSONAL_KEY")" \
    "$(code 'https://api.hubapi.com/crm/v3/objects/contacts?limit=1' -H 'Authorization: Bearer bogus')"
fi
# Supabase: корень /rest/v1/ пускает только secret-ключ, поэтому проверяем запросом к
# несуществующей таблице — 404 «table not found» означает, что авторизация пройдена.
T='zzz_no_such_table?select=*'
if need SUPABASE_URL; then
  for k in SUPABASE_PUBLISHABLE_KEY SUPABASE_SECRET_KEY; do
    need "$k" || continue
    verdict "$k" \
      "$(code "$SUPABASE_URL/rest/v1/$T" -H "apikey: ${!k}" -H "Authorization: Bearer ${!k}")" \
      "$(code "$SUPABASE_URL/rest/v1/$T" -H 'apikey: bogus' -H 'Authorization: Bearer bogus')"
  done
fi
need SUPABASE_JWKS_URL && printf '%-26s http=%s (негативного контроля нет: эндпоинт публичный)\n' \
  SUPABASE_JWKS_URL "$(code "$SUPABASE_JWKS_URL")"
