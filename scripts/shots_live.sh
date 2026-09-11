#!/usr/bin/env bash
# Live-mode screenshots: the dashboard built against the real API, not the mock.
#
# Why a script of its own: Next bakes NEXT_PUBLIC_API_URL into the build, so live mode
# needs a build of its own -- running with the variable set is not enough. Shot by hand,
# 08/09 once caught an API started with OFFLINE=1, showing a "Live backend" label over
# the built-in stub. This script checks that /health reports "offline": false and refuses
# to shoot otherwise.
#
# Needs a model key in the environment (CLAUDE_KEY). Live calls cost money: two requests.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Not port 8000: an API started by hand often sits there, and then the health check
# answers about someone else's process. If it is taken, refuse rather than pretend.
API_PORT="${API_PORT:-8123}"
WEB_PORT="${WEB_PORT:-4500}"
API="http://127.0.0.1:${API_PORT}"

# A clean local store for the shoot, not the shared demo database: leftovers from past
# runs would put duplicate leads in the inbox. The file is always restored afterwards.
STORE="$ROOT/data/store_offline.json"
STORE_BACKUP="$(mktemp)"

cleanup() {
  # Kill the child processes by name, not just the wrapping subshell: `kill $PID` takes
  # down the subshell while node and uvicorn keep holding the ports, and the next run
  # then health-checks someone else's process and shoots its data.
  [[ -n "${WEB_PID:-}" ]] && kill "$WEB_PID" 2>/dev/null || true
  pkill -f "next start -p ${WEB_PORT}" 2>/dev/null || true
  pkill -f "next-server" 2>/dev/null || true
  # Kill our uvicorn itself, or the port stays taken and the next run health-checks a
  # different process.
  [[ -n "${API_PID:-}" ]] && kill "$API_PID" 2>/dev/null || true
  pkill -f "leadcentre.api" 2>/dev/null || true
  if [[ -f "$STORE_BACKUP" ]]; then
    cp "$STORE_BACKUP" "$STORE"
    rm -f "$STORE_BACKUP"
    echo "хранилище возвращено на место"
  fi
  echo "пересобираю дашборд обратно в режим демо-данных"
  (cd "$ROOT/web" && npm run build >/dev/null 2>&1) || echo "ПРЕДУПРЕЖДЕНИЕ: обратная сборка не прошла, соберите руками"
}
trap cleanup EXIT

if curl -sf -o /dev/null --max-time 2 "$API/health" 2>/dev/null; then
  echo "ОТКАЗ: на порту ${API_PORT} уже кто-то отвечает. Проверка здоровья рассказала бы"
  echo "про чужой процесс, а кадры сняли бы с чужими данными. Остановите его или задайте"
  echo "другой порт: API_PORT=8124 make shots-live"
  exit 2
fi

cp "$STORE" "$STORE_BACKUP"
python3 -c 'import json,sys; json.dump({"leads": [], "scores": [], "replies": [], "companies": [], "disagreements": []}, open(sys.argv[1], "w"))' "$STORE"

echo "1/5 поднимаю API без OFFLINE, на чистом локальном хранилище"
(cd "$ROOT" && LEADCENTRE_STORE=local PORT="$API_PORT" PYTHONPATH=. python3 -m leadcentre.api >/tmp/shots_live_api.log 2>&1) &
API_PID=$!
for _ in $(seq 1 30); do curl -sf -o /dev/null "$API/health" && break; sleep 1; done

OFFLINE_FLAG=$(curl -sf "$API/health" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("offline"))')
if [[ "$OFFLINE_FLAG" != "False" ]]; then
  echo "ОТКАЗ: /health отвечает offline=$OFFLINE_FLAG — это заглушка, а не живая модель."
  echo "Снимать такие кадры нельзя: получится ярлык «Live backend» над встроенным ответом."
  exit 2
fi
echo "      /health → offline: false, модель живая"

echo "2/5 наполняю живой инбокс (два обращения, живые вызовы модели)"
for text in \
  "Переезжаем командой 9 человек в Дубай, нужен офис и визы на всех, лицензию тоже оформляем. Срочно, хотим закрыть в этом месяце. Бюджет есть." \
  "our licence expires in 21 days and we must move to a bigger unit at the same time. 14 staff. urgent, who can call me today"
do
  curl -sf --max-time 120 -X POST "$API/leads" -H 'content-type: application/json' \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1], "channel": "whatsapp"}))' "$text")" \
    >/dev/null
done

echo "2/5 наполняю вкладку «Найдено» из кэша реестра (сеть не нужна)"
curl -sf --max-time 60 -X POST "$API/discover/run" -H 'content-type: application/json' \
  -d '{"mode": "lapsed", "limit": 20}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("      проверено", d["checked"], "записано", d["storage"]["written"])'

echo "3/5 собираю дашборд против API"
(cd "$ROOT/web" && NEXT_PUBLIC_API_URL="$API" npm run build >/dev/null)

echo "4/5 запускаю дашборд"
if curl -sf -o /dev/null --max-time 2 "http://127.0.0.1:$WEB_PORT/" 2>/dev/null; then
  echo "ОТКАЗ: порт ${WEB_PORT} уже занят — кадры сняли бы с чужой сборки."
  exit 2
fi
(cd "$ROOT/web" && NEXT_PUBLIC_API_URL="$API" npx next start -p "$WEB_PORT" >/tmp/shots_live_web.log 2>&1) &
WEB_PID=$!
for _ in $(seq 1 30); do curl -sf -o /dev/null "http://127.0.0.1:$WEB_PORT/" && break; sleep 1; done

# Prove the dashboard talks to our API rather than another one or the mock.
PROXY=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$WEB_PORT/api/backend/leads")
if [[ "$PROXY" != "200" ]]; then
  echo "ОТКАЗ: дашборд не достучался до API через прокси (HTTP $PROXY)."
  echo "Снимать нечего: на кадре был бы экран ошибки под ярлыком «Live backend»."
  exit 2
fi
echo "      прокси дашборд → API отвечает 200"

echo "5/5 снимаю кадры"
(cd "$ROOT" && PYTHONPATH=. python3 web/scripts/shots.py "http://127.0.0.1:$WEB_PORT" 08)
(cd "$ROOT" && PYTHONPATH=. python3 web/scripts/shots.py "http://127.0.0.1:$WEB_PORT" 09)
