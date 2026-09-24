# OPS-05 — Runbook and First Production Deploy

Status: ready

## Goal

Runbook and First Production Deploy.

## Depends on

- Human approval of docs/operations_design.md
- OPS-03
- OPS-04

## Context

Read completely before implementation:

- AGENTS.md
- docs/operations_design.md (sections 3 and 4)

## In scope

- `docs/runbook.md`: server setup (O9), first deploy, deploy, rollback, restore rehearsal, rotating
  secrets, and every manual step in section 4 with exact values.
- The first production deploy with the product owner: domain, TLS, Resend domain verification,
  webhook, cron, backups, and monitoring confirmed working.
- The restore rehearsal recorded in the runbook with its date.

## Out of scope

- Staging, dashboards, correlation identifiers, and the cohort release (the next sprint).

## Acceptance criteria

- A person following the runbook alone can deploy, roll back, and restore.
- Production passes: sign-in email arrives from the domain with SPF, DKIM, and DMARC passing; the
  Telegram webhook answers; `/health/ready` is monitored; a backup exists and was restored on a
  scratch server.

## Required tests

Manual verification recorded in the runbook; `make check` stays green.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for secrets in the repository or runbook, and every manual step being reproducible.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
