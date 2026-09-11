# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The project is three days old: the first commit is dated 2026-09-09 and `0.1.0` describes
the state of `main` on 2026-09-11. There is deliberately no invented release history
before that. No git tag has been cut yet — `v0.1.0` is the next step, and until it exists
the entry below describes `main`, not a published release.

## [Unreleased]

Nothing yet.

## [0.1.0] - 2026-09-11

First working version: one qualification engine serving both an inbound inbox and
registry-based discovery, with a dashboard, an HTTP layer and a test suite.

### Added

- **Qualification engine** (`leadcentre/`): deterministic scoring over a rubric held as
  data, fact extraction from free-form messages, priority reasons stored as codes and
  rendered in two languages, and per-request evidence as verbatim quotes.
- **Inbound path**: messages are parsed into a card — what the person wants, headcount,
  deadline, language, priority — with a draft reply in the sender's language. Arabic
  drafts are written by the model; company names are transliterated.
- **Discovery path**: a GLEIF adapter reads the public LEI registry and surfaces companies
  whose licence, visa or lease renewal is due, each with a reason, a date and a link to
  the registry record.
- **HTTP layer and stores** (`leadcentre/api.py`, `leadcentre/store/`): FastAPI service,
  a local JSON store and a Supabase-backed store with a migrated schema, plus a HubSpot
  CRM receiver.
- **Dashboard** (`web/`): Next.js inbox and discovery views, with a TypeScript port of the
  engine rules and `make check-web` cross-checking that port against the Python original.
- **Evaluation harness** (`eval/`) including negative controls, and versioned extraction
  prompts (`prompts/extract_v1..v5.md`).
- **CI** (`.github/workflows/ci.yml`): tests, lint, the end-to-end demo and the dashboard
  instruments all run as gates, on every push and pull request.
- `make demo` — a single command that runs the promise from the README's first paragraph
  end to end.
- `make shots-live` — reproducible screenshots of live mode.

### Changed

- Priority reasons and lint violations moved from free text to a code catalogue, so the
  dashboard and the engine can no longer drift apart silently.
- Extraction routes to a model chosen by message length.
- Documentation was rewritten to explain why the program exists rather than how it is
  wired; release docs are down to five documents, and session journals moved to a
  `worklog/` branch.

### Fixed

- Urgency: the word "urgent" no longer becomes an invented date, and a deadline named in
  words is computed by the code, not guessed.
- Facts: budgets are quoted whole — range, "from"/"up to" and currency; duplicate quotes
  are no longer presented as separate evidence; headcount and reply language are no longer
  taken from the first match.
- Registry: company names and cities come from the registry spelling instead of being
  guessed, and registry statuses resolve to three outcomes rather than a silent "no
  reason".
- Replies: the model's internal markup no longer reaches the customer-facing letter, money
  amounts do not pass through the model, and a money insert is not broken by a line wrap.
- Rules that had been copied into a data generator were deleted; a test now guards against
  the fork returning.

### Security

- **Tests cannot reach the network, and a machine enforces it** — `ci/sitecustomize.py` is
  injected through `PYTHONPATH` and cannot be bypassed from a test. `make
  test-ci-selfcheck` is the negative control: an attempt to reach the network must fail,
  and fail *because of the block*.
- `.env.example` lists every variable the code reads; the local MCP configuration and the
  offline run artifact were removed from the tree and git-ignored.
- `/health` no longer discloses the Supabase project address, and the stored schema no
  longer carries a project identifier.
- Personal data stripping and the CRM receiver's default are covered by tests.
