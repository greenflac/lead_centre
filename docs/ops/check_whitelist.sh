#!/usr/bin/env bash
# Проверка вайтлиста доменов сессии: три исхода на домен (Р1).
#   OK       — хост ответил HTTP-кодом (политика пропускает)
#   BLOCKED  — curl exit 56/35/7 без кода: обрезал прокси (политика не пропускает)
#   ORIGIN   — хост ответил 401/403/404/5xx: доехали, отказал сам сайт, не политика
# Для каждой записи вайтлиста проверяются apex и www отдельно: замер 2026-09-09 показал,
# что `*.domain` пропускает только поддомены, apex остаётся закрытым.
# Использование: docs/ops/check_whitelist.sh [путь_к_вайтлисту] > docs/ops/whitelist_check.txt
set -u
WL="${1:-$(dirname "$0")/whitelist.txt}"
probe() { # host -> "CLASS code exit"
  local h=$1 code exit
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 20 "https://$h/" 2>/dev/null); exit=$?
  if [ "$exit" -ne 0 ]; then echo "BLOCKED - $exit"
  elif [ "$code" -ge 200 ] && [ "$code" -lt 400 ]; then echo "OK $code 0"
  else echo "ORIGIN $code 0"; fi
}
export -f probe
row() {
  local d=$1
  printf '%-34s apex=%-16s www=%s\n' "$d" "$(probe "$d")" "$(probe "www.$d")"
}
export -f row
echo "# Прогон: $(date -u +%Y-%m-%dT%H:%M:%SZ) UTC, вайтлист: $WL"
grep -v '^#' "$WL" | grep -v '^[[:space:]]*$' | sed 's/^\*\.//' | sort -u \
  | xargs -P 8 -I{} bash -c 'row {}' | sort
