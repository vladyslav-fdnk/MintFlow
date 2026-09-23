# WEB-05 — Expense Detail, Edit, Delete, and Restore

Status: blocked

## Goal

Let a user inspect, correct, and delete a confirmed expense on the Web.

## Depends on

- WEB-04

## Context

Read completely before implementation:

- AGENTS.md
- docs/web_client_design.md (W2, W3, W6)
- docs/expense_management_design.md
- docs/mvp_definition.md, section 6 (details, editing)

## In scope

- `GET /expenses/{id}`: every confirmed field, source, and confirmation time.
- Edit form for merchant, date, amount, currency, and category using `EditExpense`, with per-field
  errors, a warning before leaving with unsaved changes, and a success message.
- Delete with an explicit confirmation step, then an "Undo" that calls restore.

## Out of scope

- Receipt images, change history on the page, and bulk edit.

## Acceptance criteria

- Another user's expense is `404` for every route.
- An edit that fails validation keeps what the user typed and changes nothing.
- After an edit or delete, the history and the dashboard reflect it on the next load.

## Required tests

Integration tests must cover valid and invalid edits (each field), currency precision, future-date
tolerance, delete and restore, stale forms after a concurrent change, CSRF rejection, and
ownership.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for ownership bypass, CSRF bypass, secrets or personal
  data in logs, inaccessible markup, and scope creep.

## Completion conditions

Do not commit.

Change `Status: blocked` to `Status: ready` only after the design is approved and every
dependency is `done`, then implement, then change `Status: ready` to `Status: review` only after
every criterion and check passes.
