# TG-07 — Telegram Recent History and Draft Conflicts

Status: blocked

## Goal

Show recent confirmed expenses and handle a new capture while a draft is active.

## Depends on

- TG-06

## Context

Read completely before implementation:

- AGENTS.md
- docs/telegram_client_design.md (T7, T10)
- docs/tasks/EXPENSE-01-history-listing-query.md
- docs/tasks/TG-06-telegram-manual-capture-flow.md

## In scope

- `/recent` and the "Recent" button: the latest 10 active Expenses with date, merchant or fallback, amount, currency, and category, plus an "Open Web" link.
- `/add` while a draft is active offers "Continue current draft" or "Discard and start new".

## Out of scope

- Post-confirmation editing or deletion in Telegram.

## Acceptance criteria

- History uses the shared active-record rule and never shows deleted or foreign Expenses.
- Discarding cancels the old draft before starting the new one, atomically.

## Required tests

Unit tests must cover formatting of amounts in each minor-unit exponent and the conflict prompt. Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover history contents and the discard path.

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
