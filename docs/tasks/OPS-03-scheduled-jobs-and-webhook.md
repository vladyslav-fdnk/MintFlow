# OPS-03 — Scheduled Jobs, Telegram Webhook, Dead Man's Switches

Status: done

## Goal

Scheduled Jobs, Telegram Webhook, Dead Man's Switches.

## Depends on

- Human approval of docs/operations_design.md
- OPS-02

## Context

Read completely before implementation:

- AGENTS.md
- docs/operations_design.md (O5, O6, O8)

## In scope

- `register_telegram_webhook`: `setWebhook` with `<web origin>/telegram/webhook` and the secret
  token, idempotent, run as a deploy step.
- `deploy/crontab`: `refresh_exchange_rates` at 15:30 and 06:00 UTC,
  `authentication_retention_cleanup` at 03:10, `telegram_retention_cleanup` at 03:20, and the backup
  at 02:30, each through `docker compose run --rm`.
- Each job pings its Healthchecks.io check only after it succeeds; the ping URLs come from the
  server's environment file.
- A daily disk-space check that pings only while usage is below 80%.

## Out of scope

- The backup script itself (OPS-04).

## Acceptance criteria

- The webhook command registers the expected URL and secret with a recording Bot API and fails
  clearly when Telegram settings are missing.
- The crontab lists every job with the documented schedule; a failing job does not ping.
- No ping URL or bot token appears in logs.

## Required tests

Unit tests for the webhook command; a test that parses `deploy/crontab` against the documented
schedule; a script test for ping-on-success.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for secret leakage, idempotency, and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
