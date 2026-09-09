# SORP Lead Centre — dashboard

Next.js (App Router) front end for the Lead Centre engine. Three tabs:

| Tab | What it is |
|---|---|
| **Inbox** | Incoming requests with priority, time since arrival, channel and first words. Opening one shows the full text, extracted facts, the reasons behind the priority, evidence quotes, extraction confidence, a draft reply in the customer's language, and the **Approve → CRM** / **Disagree** buttons. |
| **New request** | Paste any text, pick a channel, submit — a card appears seconds later. This is what a reviewer drives: they invent the request themselves. |
| **Discovered** | Companies from the public GLEIF LEI registry: name, city, registration authority, licence number, priority, reason (`регистрация LEI просрочена 41 дн.`) and evidence. **No contact details, by design** — it is a watchlist for a manager, not a mailing list, and the interface says so on the tab. |

Nothing is ever sent to a customer: a human presses every button.

## Running it

```bash
npm install
npm run dev          # http://localhost:3000
npm run build && npm start
```

Deploys to Vercel as-is (`web/` as the project root, default Next.js preset).

## Mock and live data — one environment variable

```
NEXT_PUBLIC_API_URL=            # empty or unset → mock mode, reads web/mock/*.json
NEXT_PUBLIC_API_URL=https://…   # live mode, all six calls go to the backend
```

`lib/api.ts` is the only module that knows which mode is on. Components call six functions
and nothing else:

| Function | Live endpoint |
|---|---|
| `getLeads()` | `GET /leads` |
| `postLead(text, channel)` | `POST /leads` |
| `approve(id)` | `POST /leads/{id}/approve` |
| `disagree(id, reason)` | `POST /leads/{id}/disagree` |
| `getCompanies()` | `GET /companies` |
| `getStats()` | `GET /stats` |

The mode is shown in the header (`MOCK DATA` / `LIVE DATA`), so nobody mistakes demo
numbers for production ones.

**Live calls go through this app, not straight to the backend.** `leadcentre/api.py`
returns no CORS headers (measured 2026-09-09: `curl -D- -H "Origin: http://localhost:3100"
http://127.0.0.1:8000/leads` shows no `access-control-*`), so a browser calling it from
another origin gets a bare "Failed to fetch". `next.config.mjs` therefore rewrites
`/api/backend/*` to `NEXT_PUBLIC_API_URL`, and the browser only ever calls its own origin.
The alternative — adding `CORSMiddleware` on the backend — is the API owner's call; the
proxy works either way.

`lib/live.ts` translates the backend envelopes (`{outcome, leads: [...]}` with
`{lead, score, reply}` cards) into the flat `Lead` the components render. The shapes there
are measured, not guessed: they come from `OFFLINE=1 python -m leadcentre.api` running
locally, and the whole live path was exercised through the browser — list, score a new
request, approve — see `screenshots/08-live-backend.png` and `09-live-new-request.png`.
An `outcome: "unavailable"` answer becomes a visible error, never an empty list.

## Where the mock data comes from

`web/mock/*.json` is **generated from real repository files**, never hand-written:

```bash
PYTHONPATH=. python3 web/scripts/gen_mock.py
```

Its own output is the check: `leads: checked 70, tiers {HIGH: 13, MEDIUM: 40, LOW: 17,
INVALID: 0}, violations 0` / `companies: checked 60, tiers {HIGH: 12, MEDIUM: 48}, skipped
0`. Those are the numbers on the screenshots; if they and the header ever disagree, the
mock is stale — regenerate and reshoot.

* `data/inbound_seed.csv` → 70 requests. Priorities, reasons, evidence and draft replies
  come from the actual engine — `leadcentre.engine.score.score_inbound` and
  `leadcentre.engine.reply.draft` with `data/pricelist_demo.yaml`.
* `data/gleif_ae_lapsed_sample.json` → 60 companies through `GleifAdapter(offline=True)`
  and `leadcentre.engine.score.score`.
* Facts come from `leadcentre.engine.facts_rules.rules_facts` — the same deterministic
  extractor the eval bench uses, because `engine/extract.py` needs a provider and a key and
  the mock must build with neither. Records carry `facts_source: offline_heuristic`, shown
  in the card as "offline heuristic (mock mode)"; `confidence` there means "markers
  matched" (1.00 / 0.00), not a model's probability.

Requests typed into the **New request** tab in mock mode are scored in the browser by
`lib/mockEngine.ts` (a port of `engine/facts_rules.py` + `score.score_inbound` with the
thresholds of `engine/rubric.py`) and `lib/mockReply.ts` (a port of `engine/reply.py` and
the demo price ranges). Both carry a `DEBT(2026-09-09)` note: knowledge duplicated in two
languages, kept only because a browser cannot run Python with no backend attached. In live
mode neither file is executed.

**They are checked against Python, not trusted.** Ten seed requests covering all seven
categories were scored by `rules_facts` + `score_inbound` in Python and then typed into the
form in Chromium; tier and the full list of reasons matched on 10 of 10. The check has a
negative control: mutating `SIGNALS_FOR_HIGH` from 2 to 1 in the TypeScript copy turned
`acct-08` from MEDIUM into HIGH and the comparison reported 1 mismatch, so it is capable of
failing. Redo this whenever the rubric moves.

## Honest labels

* Every request card carries a **synthetic data** tag: the seed is invented, SORP has no
  exported request log yet.
* Registry rows carry **public registry record** instead — they are real GLEIF entries
  (cached sample of 2026-09-09); the priority and the reason are ours, the fields are the
  registry's.
* Prices in draft replies are ranges from the demo price list, marked as such. They are not
  SORP's prices.

## States you can actually see

* **Loading** — shimmering rows, not a blank page.
* **Empty** — "Nothing matches this filter" with what to do about it.
* **Error** — the provider running out of budget (HTTP 402/429) is reported as *"Model
  provider unavailable — the language-model provider refused the request on budget or rate
  limits"* with the raw server detail underneath and a retry button, never a white screen.
  See `screenshots/06-error-provider-budget.png`, taken against a stub API returning 402.

## Screenshots

All taken from the running production build (`next start`), Chromium at 1440 px.

| File | What it shows |
|---|---|
| `01-inbox.png` | Inbox list and the open card |
| `02-lead-card.png`, `02b-…-closeup.png` | An urgent HIGH request |
| `02c-lead-card-low.png` | Emoji-only edge case: LOW, no evidence, questions instead of prices |
| `03-new-request-form.png` | The form and the pipeline explainer |
| `04-new-request-result.png` | Card produced from a request typed into the form |
| `05-discovered.png` | Registry watchlist |
| `06-error-provider-budget.png` | Backend answering 402: readable error, raw detail, retry |
| `07-empty-state.png` | Search matching nothing |
| `08-live-backend.png`, `09-live-new-request.png` | Live mode against `leadcentre/api.py`. That backend ran with `OFFLINE=1`, so its extractor is a stub and the cards show empty facts and confidence 0.00 — that is the backend's offline mode, not the dashboard |

## Language

Interface and this README are in English. Request texts are shown exactly as received (RU,
EN, mixed), and draft replies are written in the customer's language. Priority reasons come
from the Python engine and are shown verbatim, in Russian, so that what the manager reads is
literally what the engine produced.
