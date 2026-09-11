# Contributing

Lead Centre is a demonstration project (see [`SECURITY.md`](SECURITY.md)). Contributions
are welcome, and the bar is simple: **a claim is only done when a command printed it.**

## Getting set up

Python 3.11 or newer, Node 22 for the dashboard.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # engine, API and the test/lint tooling
cd web && npm ci && cd ..      # dashboard only
```

Copy `.env.example` to `.env` and fill in what you need. `.env.example` lists every
variable the code reads; `.env` itself is git-ignored and must stay that way. Nothing in
this repository requires a live key to run the checks below — they run offline.

## Running the checks

Every command lives in the [`Makefile`](Makefile); CI runs exactly these, nothing extra.

| Command | What it does |
| --- | --- |
| `make test-ci` | The test suite with network access blocked by the machine, not by convention (`ci/sitecustomize.py` is injected through `PYTHONPATH`). |
| `make test-ci-selfcheck` | Negative control for that block: a request to the network **must** fail, and fail *because of the block*. Without it a silently missing block looks identical to a working one. |
| `make lint` | `ruff` over `leadcentre`, `tests`, `ci`, `eval`, `scripts`, `web/scripts`. |
| `make demo` | End-to-end run of the promise in the first paragraph of the README: four inbound messages, the checks around them. |
| `make check-web` | Dashboard instruments: contrast, design-system properties, and a cross-check of the TypeScript port against the Python engine. |
| `make test` | The plain suite, without the network block. Use `make test-ci` before opening a PR. |
| `make mutate` | Mutation run, without the bytecode-cache trap. Not part of CI — it edits sources, so it is a manual technique. |

Run `make test-ci && make lint && make demo && make check-web` before you push. If
something cannot be run, say so in the PR — "written, not verified, because …" is an
acceptable answer; silently claiming green is not.

## How the tree is laid out

- `leadcentre/` — the engine, the HTTP layer and the stores.
- `web/` — the Next.js dashboard, including a TypeScript port of some engine rules. When
  you change a rule in the engine, change the port too — `make check-web` fails when they
  drift apart.
- `prompts/` — versioned extraction prompts.
- `eval/` — the evaluation harness and its fixtures.
- `tests/` — pytest suite.
- `docs/` — product documentation, indexed by `docs/README.md`.
- `ci/` — the machine-enforced network block used by `make test-ci`.

## Branches and commits

- `main` is the default branch, and CI must be green on it.
- Work happens on a topic branch (`fix/…`, `feat/…`, `docs/…`); nothing is pushed straight
  to `main`. Session journals (`HANDOFF_*.md`) are git-ignored and live on `worklog/…`
  branches, not in the release tree.
- **Cleanup and behaviour changes go in separate commits.** A mixed diff can neither be
  reviewed nor reverted: reverting the cleanup takes the fix with it.
- Commit subjects follow `type(scope): what changed` and say what changed for the reader,
  not what you typed — see `git log` for the house style.

## What a pull request needs

The template ([`.github/pull_request_template.md`](.github/pull_request_template.md)) asks
for three things, and they are the whole review:

1. **What changes** — and why, in one or two sentences.
2. **How it was verified** — the commands you ran and their output, not a prediction.
3. **What was not covered** — the parts you could not check, and why.

Two habits that save reviewer time:

- When you fix a defect, first reproduce it observably (a failing test, a saved input
  file), then fix it. A fix without an observation fixes a hypothesis.
- After fixing, `grep` for the same shape elsewhere and say in the PR how many places you
  found. The expensive bugs in this repository each lived in five places.

## Code of Conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
