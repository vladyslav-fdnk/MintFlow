# TG-08 — Telegram Retention and Draft Expiry Command

Status: done

## Goal

Remove expired Telegram data and expire abandoned drafts on a schedule.

## Depends on

- TG-06

## Context

Read completely before implementation:

- AGENTS.md
- docs/telegram_client_design.md (T4, T9)
- docs/authentication_persistence_design.md (retention)
- docs/tasks/AUTH-15-authentication-retention-cleanup-command.md

## In scope

- A batch command in the AUTH-15 style that deletes processed-update rows older than seven days, finished link challenges and unlinked connections older than 30 days, and expires drafts without activity for seven days, clearing their conversations.

## Out of scope

- Scheduling the command.

## Acceptance criteria

- Expiry never creates an Expense and never touches confirmed drafts.
- Active connections and unfinished challenges are never deleted.
- The command is safe to run concurrently and repeatedly.

## Required tests

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover every retention rule at its boundary and concurrent runs.

## Required checks

Run:

- focused unit, HTTP, and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for identity and ownership bypass, secret leakage,
  duplicate processing, and scope creep.

## Completion conditions

Do not commit.

Change `Status: blocked` to `Status: ready` only after the design is approved and every
dependency is `done`, then implement, then change `Status: ready` to `Status: review` only after
every criterion and check passes.
