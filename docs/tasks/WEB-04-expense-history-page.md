# WEB-04 — Expense History Page

Status: blocked

## Goal

Let a user browse and filter confirmed expenses.

## Depends on

- WEB-01

## Context

Read completely before implementation:

- AGENTS.md
- docs/web_client_design.md (W1, W6)
- docs/expense_management_design.md
- docs/tasks/EXPENSE-02-history-list-endpoint.md

## In scope

- `GET /expenses` listing date, merchant, category, amount with currency, newest first, from the
  existing history query.
- Date-range, category, and currency filters in the URL; "Load more" through htmx using the
  existing keyset position.
- Empty, loading, and error states; each row links to its detail page.

## Out of scope

- Merchant search, sorting options, and bulk actions.

## Acceptance criteria

- The filters and pages match the JSON history endpoint exactly.
- Load more never repeats or skips an expense, including after an edit between loads.
- Invalid filters show a clear message instead of an error page.

## Required tests

Integration tests must cover every filter, pagination boundaries, deleted expenses being hidden,
and ownership.

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
