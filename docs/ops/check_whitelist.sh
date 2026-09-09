#!/usr/bin/env bash
# Проверка вайтлиста доменов сессии. Четыре исхода на хост (Р1: «не смогли» не сводится
# ни к «годно», ни к «не годно»):
#   OK      — хост ответил 2xx/3xx: политика пропускает, сайт живой
#   POLICY  — прокси вернул `CONNECT tunnel failed, response 403`: домена нет в вайтлисте.
#             Сигнатура ИЗМЕРЕНА 2026-09-09 на example.com, wikipedia.org, ycombinator.com.
#   NODNS   — та же сигнатура прокси, но у хоста нет A-записи. Прокси отвечает 403 и на
#             несуществующий домен, поэтому без проверки DNS «закрыто» и «такого хоста нет»
#             неразличимы; разделено явно, чтобы не чинить вайтлистом отсутствующий хост.
#   ORIGIN  — доехали, отказал сам сайт (4xx/5xx, антибот). Вайтлист тут ни при чём.
#   NETFAIL — доехали до TLS/DNS и не договорились (битый сертификат, apex без SNI, таймаут).
#             Это «не смогли проверить», а не «закрыто»: правкой вайтлиста не лечится.
# Для каждой записи проверяются apex и www отдельно.
# Использование: docs/ops/check_whitelist.sh [вайтлист] > docs/ops/whitelist_check.txt
set -u
WL="${1:-$(dirname "$0")/whitelist.txt}"
probe() { # host -> "CLASS деталь"
  local h=$1 out code
  out=$(curl -sS -o /dev/null -w '%{http_code}' -m 20 "https://$h/" 2>&1)
  code=$(printf '%s' "$out" | tail -c 3)
  case "$out" in
    *"CONNECT tunnel failed"*)
      if getent hosts "$h" >/dev/null 2>&1; then echo "POLICY -"; else echo "NODNS -"; fi ;;
    *"curl: ("*) echo "NETFAIL $(printf '%s' "$out" | sed -n 's/.*curl: (\([0-9]*\)).*/\1/p' | head -1)" ;;
    2??|3??) echo "OK $code" ;;
    *) echo "ORIGIN $code" ;;
  esac
}
export -f probe
row() { printf '%-34s apex=%-14s www=%s\n' "$1" "$(probe "$1")" "$(probe "www.$1")"; }
export -f row
echo "# Прогон: $(date -u +%Y-%m-%dT%H:%M:%SZ) UTC, вайтлист: $WL"
tmp=$(mktemp)
grep -v '^#' "$WL" | grep -v '^[[:space:]]*$' | sed 's/^\*\.//' | sort -u \
  | xargs -P 8 -I{} bash -c 'row {}' | sort | tee "$tmp"
echo "# --- итог по apex (Р2: числами, а не флагом) ---"
for c in OK POLICY ORIGIN NETFAIL NODNS; do
  printf '# %-8s %s\n' "$c" "$(grep -c "apex=$c" "$tmp")"
done
printf '# всего проверено %s\n' "$(grep -c . "$tmp")"
rm -f "$tmp"
