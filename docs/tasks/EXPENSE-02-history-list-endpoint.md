# EXPENSE-02 — Expense History List Endpoint

Status: done

## Goal

Expose the EXPENSE-01 history query as `GET /capture/expenses` for the Web Client's Expense
history page.

## Depends on

- EXPENSE-01

## Context

Read completely before implementation:

- AGENTS.md
- docs/expense_management_design.md (decisions D5, D6, D7)
- docs/mvp_definition.md, section 6 "Expense history"
- docs/tasks/EXPENSE-01-history-listing-query.md
- docs/tasks/CAPTURE-10-confirm-and-expense-endpoints.md

## In scope

- `GET /capture/expenses`, authenticated, owner-scoped.
- Query parameters: `date_from`, `date_to` (ISO dates), repeatable `category`, repeatable
  `currency`, `limit` (default 50, maximum 100), `cursor`.
- Response: `{"items": [<Expense representation>], "next_cursor": <string | null>}`, reusing the
  existing single-Expense serialization.
- Validation failures (bad date, `date_from` after `date_to`, bad currency code, unknown limit
  range, malformed cursor) return the same generic 422 as other capture routes, without echoing
  input.

## Out of scope

- Totals, counts, analytics.
- Merchant search.
- Editing, deletion, restoration.
- Frontend.

## Acceptance criteria

- The endpoint returns only the caller's active Expenses, in the documented order.
- Following `next_cursor` until it is `null` returns every matching Expense exactly once.
- Unknown category keys filter to an empty result; they are not an error.
- Unauthenticated requests get the standard 401.
- Responses carry the standard authentication security headers (`Cache-Control: no-store`).

## Required tests

Unit tests must cover query-parameter parsing and validation, and the response envelope.

HTTP/security tests must cover success, each validation failure, unauthenticated access, and that
a cursor taken from one user's listing cannot reveal another user's Expenses.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover a full paginated
walk through the real route with filters, and exclusion of soft-deleted Expenses.

## Required checks

Run:

- focused unit and HTTP/security tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for owner-scoping bypass, deleted-row leakage, input
  echoing, and scope creep.

## Completion conditions

Do not commit.

EXPENSE-01 must be `done` first. Change `Status: blocked` to `Status: ready` only then, implement,
then change `Status: ready` to `Status: review` only after every criterion and check passes.
