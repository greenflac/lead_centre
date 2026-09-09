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

## Where the mock data comes from

`web/mock/*.json` is **generated from real repository files**, never hand-written:

```bash
python3 web/scripts/gen_mock.py
```

* `data/inbound_seed.csv` → 70 requests. Priorities, reasons, evidence and draft replies
  come from the actual engine — `leadcentre.engine.score.score_inbound` and
  `leadcentre.engine.reply.draft` with `data/pricelist_demo.yaml`.
* `data/gleif_ae_lapsed_sample.json` → 60 companies through `GleifAdapter(offline=True)`
  and `leadcentre.engine.score.score`.
* The one thing the generator does itself is fact extraction: `engine/extract.py` calls an
  LLM provider, and the mock must build with no network and no API key. That substitute is
  labelled `facts_source: offline_heuristic` in every record and shown in the card as
  "offline heuristic (mock mode)".

Requests typed into the **New request** tab in mock mode are scored in the browser by
`lib/mockEngine.ts` and `lib/mockReply.ts`, which mirror `engine/rubric.py`, `score.py` and
`reply.py`. Both files carry a `DEBT(2026-09-09)` note: that is knowledge duplicated in two
languages, kept only because a browser cannot run the Python engine with no backend
attached. In live mode neither file is executed.

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

`screenshots/` — inbox, lead card, new-request form and its result, discovered tab, error
state and empty state; all taken from the running production build.

## Language

Interface and this README are in English. Request texts are shown exactly as received (RU,
EN, mixed), and draft replies are written in the customer's language. Priority reasons come
from the Python engine and are shown verbatim, in Russian, so that what the manager reads is
literally what the engine produced.
