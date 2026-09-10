"""Сверка двух путей движка: Python (leadcentre) против TypeScript (web/lib/mock*).

Зачем: браузер без бэкенда не умеет запускать Python, поэтому в `web/lib/mockEngine.ts` и
`web/lib/mockReply.ts` живёт порт извлечения, приоритета и черновика. Два способа узнать
одно и то же — дефект, и единственная защита от расхождения — регулярная сверка
числами. Прибор сравнивает поле в поле на выборке обращений из data/inbound_seed.csv.

Как: TS-модули собираются одноразовым скриптом через node (esbuild не нужен — файлы
переписываются в CommonJS штатным tsc из web/node_modules).

Негативный контроль прибора: в набор добавлены четыре подложные строки — подменённый
приоритет, подменённый текст черновика, подменённая английская причина и подменённая
связка «причина — цитаты»; прибор обязан назвать все четыре расхождениями. Без этого
«0 расхождений» не отличить от «прибор ничего не сравнивает».

Причины сверяются на обоих языках каталога `engine/reasons.py`: карточка дашборда идёт
по-английски, измерительный стенд и логи — по-русски.

Три исхода: СОШЛОСЬ / РАСХОЖДЕНИЕ / НЕ СМОГЛИ СВЕРИТЬ (node или tsc недоступны).

Запуск: PYTHONPATH=. python3 web/scripts/crosscheck.py [сколько обращений]
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "web"
sys.path.insert(0, str(REPO))

from leadcentre.engine import reply as reply_mod
from leadcentre.engine.facts_rules import rules_facts
from leadcentre.engine.reasons import Language
from leadcentre.engine.score import score_inbound
from leadcentre.models import InboundMessage

# Связку «причина — цитаты» строит генератор демо-данных, и он же единственное место, где
# она живёт на стороне Python: сверять копию было бы сверкой копии с копией.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_mock import link_reasons

# Выборка: по одному обращению каждого вида плюс те, на которых движок менялся
# (renewal — gen-20, продление лицензии — urg-13, длинное — edge-03, спам — spam-01).
SAMPLE_IDS = ("urg-01", "urg-13", "gen-20", "gen-19", "prc-01",
              "edge-03", "spam-01", "visa-06", "acct-02", "urg-02")

DRIVER = r"""
const path = require("path");
const { extractFacts, scoreInbound } = require(path.join(process.argv[2], "mockEngine.js"));
const { draftReply } = require(path.join(process.argv[2], "mockReply.js"));
const rows = JSON.parse(require("fs").readFileSync(process.argv[3], "utf8"));
const { renderReasons, linkReasons } = require(path.join(process.argv[2], "mockEngine.js"));
const out = rows.map((row) => {
  const received = new Date(row.received_at + "T00:00:00Z");
  const { facts, quotes } = extractFacts(row.text, received);
  const scored = scoreInbound(row.text, facts, quotes);
  const reply = draftReply(facts, scored.tier, row.text);
  return { id: row.id, facts, quotes, tier: scored.tier,
           reasons_ru: renderReasons(scored.reasonItems, "ru"),
           reasons_en: renderReasons(scored.reasonItems, "en"),
           violations: scored.violations, reply,
           reason_links: linkReasons(scored.reasonItems, quotes, facts, received)
             .map((l) => ({ code: l.code, quotes: l.quotes })) };
});
process.stdout.write(JSON.stringify(out));
"""


def build_ts(workdir: Path) -> Path:
    """Компилирует lib/*.ts в CommonJS. Возвращает каталог с .js."""
    out = workdir / "js"
    tsc = WEB / "node_modules" / ".bin" / "tsc"
    if not tsc.exists():
        raise FileNotFoundError("нет web/node_modules/.bin/tsc — npm install не выполнен")
    cmd = [str(tsc), "--module", "commonjs", "--target", "es2020", "--outDir", str(out),
           "--skipLibCheck", "--lib", "es2020",
           str(WEB / "lib" / "mockEngine.ts"), str(WEB / "lib" / "mockReply.ts")]
    # check=False намеренно: tsc может ругаться на типы и всё же собрать модули —
    # успех проверяется наличием файла, а не кодом возврата.
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if not (out / "mockEngine.js").exists():
        raise RuntimeError(f"tsc не собрал модули: {result.stdout}{result.stderr}")
    return out


def read_rows() -> list[dict]:
    import csv
    rows = {r["external_id"]: r for r in
            csv.DictReader((REPO / "data" / "inbound_seed.csv").open(encoding="utf-8"))}
    return [{"id": i, "text": rows[i]["text"], "channel": rows[i]["channel"],
             "received_at": rows[i]["received_at"][:10]} for i in SAMPLE_IDS if i in rows]


def python_side(row: dict) -> dict:
    message = InboundMessage(external_id=row["id"], channel=row["channel"],
                             text=row["text"], received_at=date.fromisoformat(row["received_at"]))
    facts = rules_facts(message)
    result = score_inbound(message, facts)
    drafted = reply_mod.draft(message, facts, result.tier, reply_mod.load_prices())
    return {
        "id": row["id"],
        "request_types": [r.value for r in facts.request_types],
        "headcount": facts.headcount,
        "timeline_days": facts.timeline_days,
        "budget_hint": facts.budget_hint,
        "is_spam": facts.is_spam,
        "confidence": facts.confidence,
        "quotes": list(facts.quotes),
        "tier": result.tier.value,
        # Причины сверяются на ОБОИХ языках: карточка идёт по-английски, а измерительный
        # стенд и логи — по-русски, и разъехаться они не имеют права.
        "reasons_ru": list(result.reasons_in(Language.RU)),
        "reasons_en": list(result.reasons_in(Language.EN)),
        "reply_language": drafted.language,
        "reply_outcome": drafted.outcome,
        "used_prices": list(drafted.used_prices),
        "reply_body": drafted.body,
        # Какая цитата доказывает какую причину: на карточке это подсветка под причиной,
        # и разъехавшийся отбор показал бы менеджеру доказательство не той причины.
        "reason_links": [{"code": link["code"], "quotes": link["quotes"]}
                         for link in link_reasons(result, list(facts.quotes), facts,
                                                  date.fromisoformat(row["received_at"]))],
    }


def ts_side(item: dict) -> dict:
    facts = item["facts"]
    return {
        "id": item["id"],
        "request_types": facts["request_types"],
        "headcount": facts["headcount"],
        "timeline_days": facts["timeline_days"],
        "budget_hint": facts["budget_hint"],
        "is_spam": facts["is_spam"],
        "confidence": facts["confidence"],
        "quotes": item["quotes"],
        "tier": item["tier"],
        "reasons_ru": item["reasons_ru"],
        "reasons_en": item["reasons_en"],
        "reply_language": item["reply"]["language"],
        "reply_outcome": item["reply"]["outcome"],
        "used_prices": item["reply"]["used_prices"],
        "reply_body": item["reply"]["body"],
        "reason_links": item["reason_links"],
    }


FIELDS = ("request_types", "headcount", "timeline_days", "budget_hint", "is_spam",
          "confidence", "quotes", "tier", "reasons_ru", "reasons_en", "reply_language",
          "reply_outcome", "used_prices", "reply_body", "reason_links")

# Арабский черновик пишет модель, и в браузере её нет — это заявленное различие путей,
# а не расхождение реализации. Сравниваются все поля, кроме тела и исхода черновика.
ARABIC_EXEMPT = ("reply_body", "reply_outcome", "used_prices")


def main() -> int:
    rows = read_rows()
    if not rows:
        print("НЕ СМОГЛИ СВЕРИТЬ: в data/inbound_seed.csv не нашлось ни одного id выборки")
        return 2
    workdir = Path(tempfile.mkdtemp(prefix="crosscheck-"))
    try:
        js_dir = build_ts(workdir)
        payload = workdir / "rows.json"
        payload.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        driver = workdir / "driver.js"
        driver.write_text(DRIVER, encoding="utf-8")
        # check=False намеренно: ненулевой код — это исход «не смогли сверить», который
        # печатается числом, а не исключение, обрывающее прогон.
        proc = subprocess.run(["node", str(driver), str(js_dir), str(payload)],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            print(f"НЕ СМОГЛИ СВЕРИТЬ: node вернул {proc.returncode}\n{proc.stderr[:800]}")
            return 2
        ts_items = {item["id"]: ts_side(item) for item in json.loads(proc.stdout)}
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"НЕ СМОГЛИ СВЕРИТЬ: {exc}")
        return 2
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    py_items = {row["id"]: python_side(row) for row in rows}

    # Негативный контроль: подложные строки.
    controls: list[tuple[str, dict, dict, str]] = []
    for probe, field, value in (("контроль-приоритет", "tier", "LOW"),
                                ("контроль-черновик", "reply_body", "подменённый текст"),
                                ("контроль-причины-en", "reasons_en", ["подменено"]),
                                ("контроль-связки-цитат", "reason_links",
                                 [{"code": "подменено", "quotes": [99]}])):
        victim = dict(py_items[rows[0]["id"]])
        victim[field] = value
        controls.append((probe, victim, ts_items[rows[0]["id"]], field))

    print(f"{'обращение':<12}{'полей':>7}{'сошлось':>9}{'разошлось':>11}  что разошлось")
    total_same = total_diff = 0
    details: list[str] = []
    for lead_id in [row["id"] for row in rows]:
        py, ts = py_items[lead_id], ts_items[lead_id]
        arabic = any("؀" <= ch <= "ۿ" for ch in
                     next(r["text"] for r in rows if r["id"] == lead_id))
        same = diff = 0
        bad_fields = []
        for field in FIELDS:
            if arabic and field in ARABIC_EXEMPT:
                continue
            if py[field] == ts[field]:
                same += 1
            else:
                diff += 1
                bad_fields.append(field)
                details.append(f"  {lead_id}.{field}:\n    py = {py[field]!r}\n    ts = {ts[field]!r}")
        total_same += same
        total_diff += diff
        mark = ", ".join(bad_fields) if bad_fields else ("арабский: черновик пишет модель"
                                                         if arabic else "—")
        print(f"{lead_id:<12}{same + diff:>7}{same:>9}{diff:>11}  {mark}")

    if details:
        print("\nрасхождения по полям:")
        for line in details:
            print(line)

    caught = 0
    for probe, py, ts, field in controls:
        if py[field] != ts[field]:
            caught += 1
        else:
            print(f"негативный контроль {probe} НЕ сработал — прибор не сравнивает {field}")
    print(f"\nнегативный контроль: подложек {len(controls)}, поймано {caught}")
    print(f"сверено обращений {len(rows)}, полей {total_same + total_diff}, "
          f"сошлось {total_same}, расхождений {total_diff}, не смогли 0")
    return 1 if total_diff or caught != len(controls) else 0


if __name__ == "__main__":
    raise SystemExit(main())
