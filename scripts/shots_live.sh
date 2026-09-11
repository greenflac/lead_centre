#!/usr/bin/env bash
# Кадры живого режима: дашборд, собранный против настоящего API, а не против мока.
#
# Почему отдельный сценарий. Next вшивает NEXT_PUBLIC_API_URL в сборку, поэтому живой
# режим требует своей сборки — «просто запустить с переменной» не работает. Прежние кадры
# 08/09 снимались руками, и на них попал API, поднятый с OFFLINE=1: на экране ярлык
# «Live backend», а под ним встроенная заглушка с «уверенность извлечения 0.00». Этот
# скрипт делает так, что ошибиться нечем: он сам проверяет, что API отвечает
# "offline": false, и отказывается снимать, если это не так.
#
# Нужен ключ модели в среде (CLAUDE_KEY). Живые вызовы стоят денег — два обращения.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Порт не 8000: на нём часто уже висит API, поднятый руками, и тогда проверка здоровья
# отвечает про чужой процесс. Занят — отказываемся, а не притворяемся, что подняли свой.
API_PORT="${API_PORT:-8123}"
WEB_PORT="${WEB_PORT:-4500}"
API="http://127.0.0.1:${API_PORT}"

# Хранилище на время съёмки — чистое и локальное, а не общая демо-база. Иначе в инбокс
# попадают осадки прошлых проб, и на кадре три лида, из которых два одинаковых: зритель
# прочитает это как «система дублирует обращения». Файл возвращается на место всегда.
STORE="$ROOT/data/store_offline.json"
STORE_BACKUP="$(mktemp)"

cleanup() {
  # Убиваем дочерние процессы по имени, а не только обёртки-подоболочки: `kill $PID`
  # снимает подоболочку, а node и uvicorn остаются держать порты. Тогда следующий прогон
  # проверяет здоровье ЧУЖОГО процесса и снимает кадры с чужими данными — ровно так
  # и появились прежние 08/09 с заглушкой под ярлыком «Live backend».
  [[ -n "${WEB_PID:-}" ]] && kill "$WEB_PID" 2>/dev/null || true
  pkill -f "next start -p ${WEB_PORT}" 2>/dev/null || true
  pkill -f "next-server" 2>/dev/null || true
  # Убиваем именно наш uvicorn, а не только обёртку-подоболочку: иначе порт остаётся
  # занятым, и следующий прогон проверит здоровье чужого процесса.
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

# Прибор обязан доказать, что дашборд говорит с НАШИМ API, а не с чужим и не с моком.
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
