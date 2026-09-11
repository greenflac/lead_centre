# Security Policy

## What this project is

Lead Centre is a **demonstration project**, not a production service. It was built in three
days to show how inbound messages and public-registry records can be qualified by one
engine. It is not operated, not hosted for users, and carries no uptime, support or
security-response commitment. Do not put real customer data, real credentials or real
traffic through it without reviewing the code yourself first.

Two consequences worth stating plainly:

- **The engine never contacts anyone.** It produces a card and a draft reply; a human
  presses send. There is no outbound email, chat or phone channel in this repository.
- **Secrets live outside git.** `.env.example` lists every variable the code reads; the
  real `.env` is git-ignored. If you find a real key, token or password committed here,
  that is a bug — report it (see below) and treat the value as compromised: rotate first,
  clean history second.

## Supported versions

Only the tip of `main` is looked at. There are no maintained release branches.

| Version | Supported |
| ------- | --------- |
| `main`  | yes       |
| older tags / branches | no |

## Reporting a vulnerability

Report privately, not in a public issue:

1. Preferred — GitHub **private vulnerability reporting**:
   <https://github.com/greenflac/lead_centre/security/advisories/new>
2. If that is unavailable, open a public issue that says *only* "security report, please
   open a private channel" with no details, and wait to be contacted.

Please include what you did, what happened, and what you expected — a reproduction
command or a saved input file is worth more than a description. Because this is a
demonstration project maintained on a best-effort basis, expect an acknowledgement rather
than a schedule: there is no SLA, and a fix may be "documented, not fixed".

## Out of scope

Findings that depend on a deployment this repository does not ship — your own hosting,
your own Supabase project, your own API keys, your own reverse proxy — belong to that
deployment, not here.
