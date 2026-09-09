# SORP Lead Centre

Inbound request triage and a registry watchlist, both running through one qualification
engine. A proactive test project for the SORP Group "AI developer (vibe coding)" vacancy
(`docs/brief/01_vacancy.md`); it is not a production system and is not connected to any
SORP data.

## The problem it addresses

An inbound request from the site form, WhatsApp or a chat widget sits in a queue until a
manager gets to it. The request was already paid for with advertising. What the engine does
is turn the raw text into a card a manager can act on in seconds: what the customer is
asking for, how many people, what deadline, which language, a priority with the reasons for
it, quoted evidence from the text itself, and a draft reply in the customer's language.
A human presses every button; nothing is sent to a customer by the system.

The second half is lead discovery: the same engine reads a public company registry and puts
companies with a datable, provable reason on a watchlist — no contact details, deliberately.

## Architecture

```
inbound text (form / WhatsApp / chat)  ─┐
                                        ├─►  scrub PII  ─►  extract facts (LLM, JSON schema)
GLEIF registry adapter (lapsed / fresh) ─┘                        │
                                                                  ▼
                                                    score  (rubric as data, code decides)
                                                                  │
                                            ┌─────────────────────┴──────────────────┐
                                            ▼                                        ▼
                                    draft reply (RU/EN) ─► lint            store (local / Supabase)
                                            │                                        │
                                            └──────────►  FastAPI  ◄─────────────────┘
                                                             │
                                                    Next.js dashboard ─► Approve → CRM sink
```

Three properties are deliberate:

**One engine, two intakes.** An inbound message and a registry company reach the same
`score` module and the same rubric. The registry axes are address type × event; an inbound
request adds an intent-and-urgency axis on top. Adding a source means implementing the
`SourceAdapter` protocol (`leadcentre/sources/base.py`) — GLEIF is one implementation, a
paid provider or a client's own export would be another, and the engine knows nothing about
either.

**The model proposes facts, the code decides the priority.** Extraction (`engine/extract.py`)
returns structured facts and nothing else. The tier comes from `engine/rubric.py` — thresholds
and the matrix are data in one file — plus invariants in `engine/score.py`: HIGH requires at
least one verbatim evidence quote, low extraction confidence caps the tier, a broken invariant
returns `INVALID` rather than a guess. A reason line is produced for every tier, and the
dashboard shows it verbatim.

**Three outcomes, not two.** Every check returns `ok` / `not ok` / `could not check`, and
prints `checked N, violations M, could not K`. Zero violations out of zero checks is not a
pass. The same rule holds for the store (`success` / `rejected` / `unavailable`), the CRM
sink (`sent` / `rejected` / `unavailable` / `skipped`) and the reply linter.

Model routing is by message length: requests up to 600 characters go to Haiku 4.5, longer
ones to Opus 5 (`engine/extract.py`). The threshold is a chosen constant with its rationale
next to it; the card records which model actually served the lead, taken from the API
response rather than from intent.

Repository map:

| Path | What is there |
|---|---|
| `leadcentre/engine/` | extract, rubric, score, reply, lint, shared fact heuristics |
| `leadcentre/sources/` | `SourceAdapter` protocol + GLEIF adapter |
| `leadcentre/store/`, `leadcentre/crm/` | local JSON / Supabase store, Null and HubSpot sinks |
| `leadcentre/api.py` | FastAPI, 9 routes; logic in functions, handlers only parse |
| `eval/` | measurement bench: Cohen's kappa, confusion matrix, stability, negative controls |
| `prompts/` | versioned extraction prompts (v1, v2, v3), loaded by the code, never inlined |
| `web/` | Next.js dashboard, mock mode and live mode (`web/README.md`) |
| `data/` | 70 synthetic requests, GLEIF samples, demo price list |
| `docs/` | blueprint, data notes, environment measurements, research |

## Running it

Python 3.11. `pip install -e ".[dev]" fastapi uvicorn httpx`.

```bash
make test-ci            # tests with network blocked by the machine, not by agreement
make test-ci-selfcheck  # negative control: a network call in that mode must fail
make run                # score registry companies from the cached sample, no network
make discover           # same, against the live GLEIF API
make mutate             # test run with the bytecode cache cleared (see the defect story)
python eval/run_eval.py --labels eval/labels_synthetic.csv     # the bench, no model calls
python eval/run_eval.py --engine=llm --limit 10 --labels ...   # with live extraction, costs money
OFFLINE=1 python -m leadcentre.api                             # API on :8000
cd web && npm install && npm run dev                           # dashboard on :3000
```

`OFFLINE=1` means cached data and no network anywhere. The dashboard runs on generated mock
data with no backend attached, and switches to the API with a single environment variable
(`NEXT_PUBLIC_API_URL`); the header says which mode is on.

Keys are read from the environment: `CLAUDE_KEY` for the model, `SUPABASE_URL` plus keys for
the store, `HUBSPOT_PERSONAL_KEY` for the CRM sink. Without them the code falls back to the
local store and the null CRM sink — explicitly, never silently: an unknown value of
`LEADCENTRE_STORE` or `LLM_PROVIDER` is an error, not a default.

## What is measured

Every number below came out of this repository. The command that produced it is named.

| Measurement | Value | Where from |
|---|---|---|
| Tests | 275 passed | `make test-ci` |
| Agreement with the author's labels, Cohen's kappa | 0.787 (po 0.867, pe 0.373, 30 pairs) | `python eval/run_eval.py --labels eval/labels_synthetic.csv` |
| Stability, 3 runs of the same input | 1.0000 on 70 requests | same run |
| Negative controls | 6 of 6 | same run |
| Priority distribution on the 70-request seed | 13 HIGH / 40 MEDIUM / 17 LOW, 0 invariant violations | `web/mock/stats.json`, generated by `web/scripts/gen_mock.py` |
| Cost per lead over the seed | $0.0033 cold cache, $0.0030 warm | token counts of the whole set, Haiku/Opus prices |
| Extraction latency | ~5 s per lead (8 edge-case requests, 35.1 s total, Haiku 4.5) | run recorded in `HANDOFF_claude-whitelist-env-vars-3x2zo6.md` |
| Registry volume, UAE | 9 362 legal entities with an LEI, of which 3 937 have a lapsed LEI registration | `docs/data/gleif_schema.md` |

Reading of the agreement number, in the bench's own words (`eval/README.md`): it is
**agreement between the author's labels and the engine on synthetic requests**, not accuracy.
The requests are invented, and the person who wrote the labels also wrote the rubric, so the
figure shows that the engine reproduces the stated rubric — not that it reads customer intent
correctly. Accuracy needs a live SORP stream: two weeks of real requests, a manager's labels,
a parallel manual reply, and time-to-first-answer as the metric. `eval/labels_template.csv`
(30 rows) is waiting for the owner's labels; `eval/labels_synthetic.csv` is a stand-in by the
bench author and is marked as such in the file itself.

The bench is checked against itself, because a metric that never moves measures nothing: with
all labels forced to one class it prints `DEGENERATE` and exits "could not check" (code 2)
rather than 0; with labels shuffled on six seeds the kappa falls to between −0.2308 and
+0.1282 (measured against the 0.7436 baseline the bench had at the time). Decision constants are checked by mutation in both directions — 20 of 20
engine mutations killed (thresholds, matrix cells, target city, registrar code, the axis-C
ladder). One is honestly not covered: `URGENT_DEFAULT_DAYS` survives mutation on the current
labels, and that is recorded as debt in `eval/README.md` rather than papered over.

Tests do not reach the network, and that is enforced by `ci/sitecustomize.py` loaded through
`PYTHONPATH`, not by convention. `make test-ci-selfcheck` is the negative control for the ban
itself: a silent ban is indistinguishable from a missing one.

## Data boundaries

- **The 70 inbound requests are synthetic.** SORP has no exported request log available here.
  Every row of `data/inbound_seed.csv` was written for this project (`docs/data/inbound_seed.md`):
  channels jivo 24 / whatsapp 20 / telegram 16 / form 10, languages ru 47 / en 16 / mixed 6 /
  ar 1, text length median 98 and max 1332 characters, including empty text, emoji-only and a
  long mixed-language message. Every card in the dashboard is tagged **synthetic data**. The
  distribution is our hypothesis about the channel mix, not a measurement of SORP's traffic;
  phone numbers and e-mail addresses in the seed are reserved fictional patterns only.
- **GLEIF is a real public registry, but only companies that hold an LEI.** That is roughly
  9 362 UAE entities, a slice skewed towards financial, trading and fund structures — not the
  whole Dubai licence base, and the flow of new entries is dozens per month, not thousands.
  Registry rows in the dashboard are tagged **public registry record**: the fields are the
  registry's, the priority and the reason are ours.
- **The price list is a demo.** `data/pricelist_demo.yaml` holds public market ranges and
  author-chosen values, marked in the file, in the draft replies and in the UI. These are not
  SORP's prices. Drafts only ever state a range, and the linter cross-checks every AED figure
  in a draft against the price list, so a hallucinated number fails the check.
- **Personal data is cut before the model call.** Phone numbers and e-mail addresses are
  stripped from the text before it is sent anywhere, and `has_contact` is derived from what
  the scrubber actually removed rather than from the model's opinion.

## What this system does not do

It does not send anything to a customer — no e-mail, no WhatsApp message, no call. It does
not collect contact details from the registry: the Discovered tab has no phone or e-mail
column by design, and no "write to them" button. It is a triage and drafting tool with a
human on every outgoing action.

## What is not done, and why

- **No owner labels yet.** `eval/labels.csv` does not exist; the kappa above is against the
  author's stand-in labels. Until the owner fills in the 30-row template, the number says the
  engine follows the rubric, nothing more.
- **No n8n workflow.** It was planned for day 3 of `docs/BLUEPRINT.md` and did not get built;
  there is no `n8n/` directory in this repository, and this line is here instead of a claim.
- **HubSpot is partly unverified.** Company creation was exercised live against the portal
  and the record deleted straight afterwards. Deals return 403 `MISSING_SCOPES` on the
  available token, so deals, contacts and v4 associations are written to the documented API
  shape but never confirmed by a live call. The default sink is `null`, so demo runs do not
  fill anyone's CRM; the `NullSink` reports `skipped`, not `sent`.
- **Model quality is measured on a small sample.** The Haiku/Opus comparison (5.9 s and
  $0.00299 per lead versus 8.1 s and $0.01598) rests on 3 requests, and one instability is
  known and unfixed: on the longest request Haiku returned a relative deadline as 7 days four
  times and 38 days twice over six runs, which is why long messages are routed to Opus.
- **Prompt caching does not pay off on the cheap model.** Measured: with a ~2.9k-token prompt
  Haiku reports zero cache reads and zero cache writes — the prefix is below the model's
  threshold. On Opus the cache works (input 5.4× cheaper on a warm read) but with a 5-minute
  TTL, so it only helps above one lead per five minutes. Recorded as a negative result rather
  than dropped.
- **The screenshots in `web/screenshots/` predate the HIGH-threshold fix** described below:
  they show 30/20/20 in the header where the current engine produces 13/40/17. They were not
  retaken, and this note is cheaper than a screenshot that quietly disagrees with the code.
- **The API sends no CORS headers** (measured), so the dashboard proxies live calls through
  its own origin instead. Adding the middleware is a decision for whoever owns the API.

## One defect, in full

**Symptom.** On a live run, short price questions were coming out HIGH. `prc-01` — the whole
message is "скок стоит фриз зона?" — should not outrank a team relocation.

**Cause.** The extractor has a `budget_hint` field, meaning "the customer named a budget".
The model was filling it with the customer's *question* about price: `prc-01` produced
`budget_hint = 'скок стоит'`, `acct-01` produced `'сколько в месяц выйдет'`. The scoring
code reads that field as "the customer signalled willingness to pay" and raised the tier.
The model was not hallucinating; it was answering a question the prompt had not asked
precisely enough — a question about a value is not a value.

**Search before fix.** The defect has a shape: *a customer's question recorded as a fact*.
Grepping for that shape rather than for that field found a second instance nobody had
reported — `jurisdiction_hint`, where a customer asking "mainland or freezone?" produced
`jurisdiction_hint = 'mainland, freezone'`, i.e. the engine believed the customer had already
chosen both. Both fields were fixed, not just the reported one.

**Fix.** `prompts/extract_v2.md`: `budget_hint` is filled only when a money figure is named
("no number, no budget_hint"), `jurisdiction_hint` is null when the customer is asking which
jurisdiction to pick, plus six examples sitting exactly on those boundaries. The prompt is a
versioned file; v1 stayed next to it for comparison.

**Measured, v1 → v2, three requests on Haiku 4.5.** `prc-01` budget_hint `'скок стоит'` → `None`;
`acct-01` `'сколько в месяц выйдет'` → `None`; `edge-03`, where a real figure was named,
kept its value — the negative control that the fix did not simply blank the field. `edge-03`
jurisdiction_hint `'mainland, freezone'` → `None`.

**What it cost.** The prompt grew from 1927 to 2934 input tokens per request (+52%), moving
the lead from about $0.0027 to about $0.0031. That was accepted: the growth bought three
boundaries, each of which had a defect behind it. It also had a side effect caught later —
compressing the examples in v3 lost the hint that kept `prc-01` classified as a company-setup
request, and the field started flapping until the hint was restored. Trimming examples is
only safe if you re-check the fields those examples were holding.

## Related notes in the repository

- `docs/BLUEPRINT.md` — the plan this was built against, including what was to be cut first.
- `docs/data/gleif_schema.md` — registry schema, volumes and the two discovery signals.
- `docs/data/inbound_seed.md` — how the synthetic set is built and which edges it covers.
- `eval/README.md` — what each measured number means and where the bench is weak.
- `web/README.md` — dashboard modes, screenshots, and where the mock data comes from.
- `HANDOFF_claude-whitelist-env-vars-3x2zo6.md` — the working journal: every run, every
  measurement, and the failures that did not make it into this file.
