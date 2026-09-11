# Lead Centre — dashboard

Next.js (App Router) front end for the Lead Centre engine — inbound-request triage for an
international consultancy in Dubai. It runs against the Python backend, or entirely on
generated demo data with no backend at all.

| Tab | What it is |
|---|---|
| **Inbox** | Incoming requests with priority, time since arrival, channel and first words. Opening one shows the full text, the extracted facts, the reasons behind the priority with the quote behind each reason, extraction confidence, a draft reply in the customer's language, and the **Approve → CRM** / **Disagree** buttons. |
| **New request** | Paste any text, pick a channel, submit — a card appears seconds later. This is what a reviewer drives: they invent the request themselves, it is not a prepared example. |
| **Discovered** | Companies from the public GLEIF LEI registry, one row each: priority, company, address type (*Registrar address* / *Business centre* / *Own office* / *Not stated*), city, the dated reason, and the evidence it rests on — *Renewal due 4 Sep 2026 · LEI 984500D91E8NC3EF6836*, with the licence number and the registration date in the cell's tooltip. Field names and codes are never shown: the row reads as English, not as a record dump. A field the registry does not carry reads *not recorded*, and a value the screen cannot name reads *not recognised (…)* — neither is silently blank. **No contact details, by design** — a watchlist for a manager, not a mailing list, and the interface says so on the tab. |

Nothing is ever sent to a customer: a human presses every button.

## Running it

```bash
npm install
npm run dev                    # http://localhost:3000
npm run build && npm start     # production build
```

Deploys to Vercel as-is (`web/` as the project root, default Next.js preset).

## Mock mode and live mode — one environment variable

```
NEXT_PUBLIC_API_URL=            # empty or unset → mock mode, reads web/mock/*.json
NEXT_PUBLIC_API_URL=https://…   # live mode, all six calls go to the backend
```

The active mode is shown in a strip under the header (`DEMO DATA` / `LIVE BACKEND`), so nobody mistakes
demo numbers for production ones. `lib/api.ts` is the only module that knows which mode is on;
components call six functions and nothing else:

| Function | Live endpoint |
|---|---|
| `getLeads()` | `GET /leads` |
| `postLead(text, channel)` | `POST /leads` |
| `approve(id)` | `POST /leads/{id}/approve` |
| `disagree(id, reason)` | `POST /leads/{id}/disagree` |
| `getCompanies()` | `GET /companies` |
| `getStats()` | `GET /stats` |

### In mock mode

`web/mock/*.json` is generated from real repository files, never hand-written:

```bash
PYTHONPATH=. python3 web/scripts/gen_mock.py
```

* `data/inbound_seed.csv` → **70 requests** (13 HIGH, 41 MEDIUM, 16 LOW, 0 invariant
  violations). Priorities, reasons, evidence and draft replies come from the actual engine —
  `leadcentre.engine.score.score_inbound` and `leadcentre.engine.reply.draft` with
  `data/pricelist_demo.yaml`.
* `data/gleif_ae_lapsed_sample.json` → **60 companies** (12 HIGH, 48 MEDIUM) through
  `GleifAdapter(offline=True)` and `leadcentre.engine.score.score`.
* Facts come from `leadcentre.engine.facts_rules.rules_facts`, the same deterministic
  extractor the eval bench uses, because the model extractor needs a provider and a key and
  the mock must build with neither. Records carry `facts_source: offline_heuristic`, shown in
  the card as "offline heuristic (mock mode)"; `confidence` there means "markers matched"
  (1.00 / 0.00), not a model's probability.

Requests typed into **New request** in mock mode are scored in the browser by
`lib/mockEngine.ts` and `lib/mockReply.ts` — TypeScript ports of the Python scoring rules and
reply builder, needed because a browser cannot run Python with no backend attached. They are
checked against Python rather than trusted: `PYTHONPATH=. python3 web/scripts/crosscheck.py`
runs a sample of seed requests through both paths and compares field by field in both
languages — facts, tier, reasons, draft, and which quote is offered as proof of which
reason — with planted mismatches as a negative control. Last run (2026-09-11): 12 requests,
174 fields, 174 matched, 0 mismatches, 4 of 4 planted mismatches caught. In live mode
neither file is executed.

`budget_hint` is the customer's own words, not the list of markers that fired: a figure in
the text displaces the markers, and only when there is no figure does the first matching
marker stand in. That rule lives in `facts_rules.rules_facts`, and the TypeScript copy is
held to it by the cross-check above — reverting the copy to the old marker join reddens it on
5 fields.

### In live mode

Live calls go through this app, not straight to the backend. `leadcentre/api.py` returns no
CORS headers, so a browser calling it from another origin gets a bare "Failed to fetch".
`next.config.mjs` therefore rewrites `/api/backend/*` to `NEXT_PUBLIC_API_URL`, and the
browser only ever calls its own origin.

`lib/live.ts` translates the backend envelopes (`{outcome, leads: [...]}` with
`{lead, score, reply}` cards) into the flat `Lead` the components render. An
`outcome: "unavailable"` answer becomes a visible error, never an empty list.

**Reasons arrive in the interface language, and where they cannot, the card says so.** The
API sends `reasons_by_language`, which holds only the languages it could actually assemble
from the stored reason codes. `lib/live.ts::uiReasons` keeps three outcomes apart: an `en`
list is rendered as is; no reasons at all is not a failure; and a lead scored before the
engine kept reason codes (`reasons_outcome: "no_codes"`) has no English text in existence, so
the card shows the stored Russian **with a line saying it is shown as stored and has to be
rescored**. Russian is never relabelled as English.

To run both halves locally:

```bash
python -m leadcentre.api                                  # terminal 1, port 8000
echo 'NEXT_PUBLIC_API_URL=http://127.0.0.1:8000' > web/.env.local
cd web && npm run build && npm start                      # terminal 2
```

## Honest labels

* Every request card carries a **synthetic data** tag: the seed is invented — the
  consultancy has no exported request log yet.
* Registry rows carry **public registry record** instead — they are real GLEIF entries from a
  cached sample; the priority and the reason are ours, the fields are the registry's.
* Prices in draft replies are ranges from the demo price list, marked as such. They are not
  the consultancy's real prices.
* Arabic drafts have not been read by a native speaker, and the card says so — in English,
  because that notice is for the manager, not for the customer.

**Known, not fixed:** in the narrow Arabic column a price range can still break across lines,
now between the two figures (`AED 15 000–` / `35 000`, visible in `02d`; in the wider column of
`02e` the same range holds together). The order stays correct — the isolate holds it — but the
break is ugly. The fix belongs to `leadcentre/engine/reply.py::price_fragment`: a non-breaking
hyphen inside the range, the same move that already fixed the break between `AED` and its
digits.

**The Arabic draft is not reproducible.** The model writes it on every `gen_mock.py` run: in
3 observed runs on 2026-09-10 the `edge-03` draft passed the reply linter twice and was
rejected once (7 lines against a maximum of 6). A rejected draft is the honest "no draft,
needs a human" outcome, but its notice is still Russian — only `NATIVE_REVIEW_NOTICE` was
translated. Regenerate, then look at the card before shooting it.

## States you can actually see

* **Loading** — static skeleton rows, not a blank page and not an endless shimmer.
* **Empty** — "Nothing matches this filter", with what to do about it.
* **Error** — a provider out of budget (HTTP 402/429) is reported as *"Model provider
  unavailable"* with the raw server detail underneath and a retry button, never a white screen.

## Screenshots

All taken from the running production build, Chromium at 1440 px. Ten come from
`python3 web/scripts/shots.py [base_url]` in mock mode; `06` needs a stub backend answering
402 and `08`/`09` a build pointed at a running `leadcentre/api.py`, so those three are shot
against those backends by hand.

| File | What it shows |
|---|---|
| `01-inbox.png` | Inbox list and the open card |
| `02-lead-card.png`, `02b-…-closeup.png` | An urgent HIGH request |
| `02c-lead-card-low.png` | Emoji-only edge case: LOW, confidence 0.00, no evidence, questions instead of prices |
| `02d-lead-card-arabic.png` | Arabic request and Arabic draft: right-to-left base, Noto Naskh Arabic, ranges still reading left to right, and the "not proofread by a native speaker" notice in English — it is addressed to the manager, not to the customer |
| `02e-lead-card-long-request.png` | The `edge-03` case, the longest request in the set: seven numbered questions, five services asked about. The budget reason reads `budget named: 150 тысяч` — the customer's own words, with that same fragment quoted underneath as its evidence, and the quoted sentence highlighted in the request text on the left |
| `03-new-request-form.png` | The form and the pipeline explainer |
| `04-new-request-result.png` | Card produced from a request typed into the form |
| `05-discovered.png` | Registry watchlist: human evidence (`Renewal due 4 Sep 2026 · LEI 984500D91E8NC3EF6836`) and spelled-out address types, one line per row |
| `06-error-provider-budget.png` | Backend answering 402: readable error, raw detail, retry |
| `07-empty-state.png` | Search matching nothing |
| `08-live-backend.png`, `09-live-new-request.png` | Live mode against `leadcentre/api.py` |

## Language and right-to-left text

The interface is in English. Request texts are shown exactly as received (RU, EN, AR, mixed),
and draft replies are written in the customer's language, with that language marked next to
the draft.

Request texts and drafts carry `dir="auto"`, so an Arabic message lays itself out
right-to-left while the Russian message next to it in the same list does not; the interface
chrome stays left-to-right on purpose. Layout uses logical CSS properties throughout, so
nothing has to be mirrored by hand. The Arabic face is **Noto Naskh Arabic** (SIL Open Font
License, `public/fonts/OFL.txt`), self-hosted because the demo browser has no internet and a
Google Fonts link would have silently fallen back to a system face.

Priority reasons come from the Python engine's own bilingual catalogue
(`leadcentre/engine/reasons.py`): the engine stores a reason as a code plus its parameters and
renders it on demand, so the card shows the English rendering of the very reason the engine
produced — a translation by the engine, not by the dashboard.

## Three scripts that keep this half honest

They print numbers rather than a verdict, and CI runs all three on every push
(`make check-web`) — an unchecked promise is not a check:

* `python3 web/scripts/contrast.py` — WCAG contrast of every colour pair in both schemes, with
  a negative control on the instrument itself (white on white must give 1.00, black on white
  21.00). Last run (2026-09-11): 38 pairs checked, 0 below threshold, 2 with no threshold.
* `python3 web/scripts/css_audit.py` — counts font sizes, weights, spacing values off the 4 px
  grid, radii, shadows and physical (non-logical) properties. Last run (2026-09-11): 4 sizes
  (12 / 14 / 15 / 16 px, 15 px for Arabic blocks only), 2 weights, 0 off-grid spacings,
  0 shadows, 0 physical properties.
* `python3 web/scripts/crosscheck.py` — runs the same 12 requests through the Python engine
  and through this TypeScript port and compares every field of both results, including the
  reasons rendered in both languages. 174 fields, 174 matched, 0 disagreements, and 4 planted
  disagreements the instrument has to catch — without them a run of zeros would prove nothing.
