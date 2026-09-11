"""Compares the two engine paths field by field: Python against the TypeScript port.

The browser cannot run Python, so web/lib/mockEngine.ts and mockReply.ts carry a port of
extraction, scoring and drafting. Two places that know the same thing drift, and the only
defence is to compare them with numbers on requests from data/inbound_seed.csv.

Four planted rows act as the negative control -- a swapped tier, draft, English reason
and reason-to-quote link -- and the instrument must name all four, otherwise "0
disagreements" cannot be told apart from "nothing was compared".

Three outcomes: agree / disagree / could not compare (node or tsc unavailable).
Usage: PYTHONPATH=. python3 web/scripts/crosscheck.py [how many requests]
"""
from __future__ import annotations

import json
import re
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
from leadcentre.engine.reasons import VIOLATION_CATALOGUE, Language, ViolationCode
from leadcentre.engine.score import score_inbound
from leadcentre.models import InboundMessage

# The reason-to-quote link lives only in the demo generator on the Python side; a local
# copy would mean comparing a copy with a copy.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_mock import link_reasons

# One request of each kind, plus the ones the engine changed on.
#
# Measured 2026-09-11: with ten requests the instrument missed two port mutations -- a
# weekday shift in the "by friday" rule and the dropped invariant that a stated deadline
# cancels worded urgency. Neither form was in the sample, so "0 disagreements" meant
# "nothing to measure". urg-05 and urg-11 close one each.
SAMPLE_IDS = ("urg-01", "urg-13", "gen-20", "gen-19", "prc-01",
              "edge-03", "spam-01", "visa-06", "acct-02", "urg-02",
              "urg-05", "urg-11")

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
    """Compiles lib/*.ts to CommonJS and returns the directory holding the .js."""
    out = workdir / "js"
    tsc = WEB / "node_modules" / ".bin" / "tsc"
    if not tsc.exists():
        raise FileNotFoundError("нет web/node_modules/.bin/tsc — npm install не выполнен")
    cmd = [str(tsc), "--module", "commonjs", "--target", "es2020", "--outDir", str(out),
           "--skipLibCheck", "--lib", "es2020",
           str(WEB / "lib" / "mockEngine.ts"), str(WEB / "lib" / "mockReply.ts")]
    # check=False on purpose: tsc may complain about types and still emit the modules,
    # so success is judged by the file, not the exit code.
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
        # Reasons are compared in both languages: the card renders English, the harness
        # and logs Russian, and the two must not drift.
        "reasons_ru": list(result.reasons_in(Language.RU)),
        "reasons_en": list(result.reasons_in(Language.EN)),
        "reply_language": drafted.language,
        "reply_outcome": drafted.outcome,
        "used_prices": list(drafted.used_prices),
        "reply_body": drafted.body,
        # Which quote proves which reason: a drifted selection would show the manager
        # evidence belonging to a different reason.
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

# The Arabic draft is written by the model, which the browser has no access to: a
# declared difference between the paths, so body and outcome are left out.
ARABIC_EXEMPT = ("reply_body", "reply_outcome", "used_prices")



VIOLATION_TEXTS_RE = re.compile(
    r"export const VIOLATION_TEXTS = \{(.*?)\} as const;", re.DOTALL
)
VIOLATION_PAIR_RE = re.compile(r"(\w+):\s*\"([^\"]+)\"")


def check_violation_texts() -> tuple[int, int, list[str]]:
    """Compares the port's invariant-violation texts against the engine catalogue.

    A check of its own rather than a field: the demo set has zero violations, so these
    strings are invisible to a request run. While nobody compared them, the port wrote
    them in Russian and they reached the English card.

    Returns: compared, disagreed, disagreement lines.
    """
    source = (WEB / "lib" / "mockEngine.ts").read_text(encoding="utf-8")
    block = VIOLATION_TEXTS_RE.search(source)
    if not block:
        return 0, 0, ["НЕ СМОГЛИ: в mockEngine.ts не нашлось VIOLATION_TEXTS"]
    ts_texts = dict(VIOLATION_PAIR_RE.findall(block.group(1)))
    checked = diff = 0
    details: list[str] = []
    for code, text in ts_texts.items():
        want = VIOLATION_CATALOGUE[ViolationCode(code)].texts[Language.EN]
        checked += 1
        if want != text:
            diff += 1
            details.append(f"  нарушение {code}:\n    py = {want!r}\n    ts = {text!r}")
    return checked, diff, details


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
        # check=False on purpose: a non-zero code is the "could not compare" outcome,
        # printed as a number rather than raised as an exception.
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

    viol_checked, viol_diff, viol_details = check_violation_texts()

    # Negative control: planted rows.
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

    if viol_details:
        print("\nтексты нарушений инвариантов:")
        for line in viol_details:
            print(line)
    print(f"тексты нарушений инвариантов: сверено {viol_checked}, расхождений {viol_diff}")

    print(f"сверено обращений {len(rows)}, полей {total_same + total_diff + viol_checked}, "
          f"сошлось {total_same + viol_checked - viol_diff}, "
          f"расхождений {total_diff + viol_diff}, не смогли 0")
    return 1 if total_diff or viol_diff or caught != len(controls) else 0


if __name__ == "__main__":
    raise SystemExit(main())
