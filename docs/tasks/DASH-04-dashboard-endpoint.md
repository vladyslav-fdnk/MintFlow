# DASH-04 — Dashboard Endpoint

Status: done

## Goal

Expose the dashboard as `GET /analytics/dashboard` for the Web Client.

## Depends on

- DASH-03

## Context

Read completely before implementation:

- AGENTS.md
- docs/dashboard_design.md (decisions D1, D2, D3, D10)
- docs/tasks/DASH-03-build-dashboard-use-case.md
- docs/tasks/EXPENSE-02-history-list-endpoint.md (hand-parsed, non-echoing query parameters)

## In scope

- A new `analytics` router registered in the application, with
  `GET /analytics/dashboard`, authenticated and owner-scoped.
- Query parameters `date_from` and `date_to` (both or neither) and `currency`, parsed by hand
  like the history endpoint. Unknown or repeated parameters, malformed dates, an inverted or
  over-long range, and unsupported currencies return a generic 422 that does not echo input.
- A JSON response with the D1–D8 shape: money as integer minor units plus currency code,
  dates as ISO strings, shares as integer basis points.
- The standard authentication security headers.

## Out of scope

- Frontend, caching, and any new analytics.

## Acceptance criteria

- The default request returns the current month in the user's timezone.
- Multi-currency periods return separate totals and never a combined total.
- Other users' Expenses and soft-deleted Expenses never affect any number.
- Unauthenticated requests get the standard 401.

## Required tests

Unit tests must cover query parsing and response serialization.

HTTP/security tests must cover success, each validation failure without echoing input,
unauthenticated access, and currency-selection-required responses.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover the default
request and a multi-currency request end to end through the real route.

## Required checks

Run:

- focused unit and HTTP/security tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for owner-scoping bypass, currency mixing, input
  echoing, and scope creep.

## Completion conditions

Do not commit.

DASH-03 must be `done` first. Change `Status: blocked` to `Status: ready` only then, implement,
then change `Status: ready` to `Status: review` only after every criterion and check passes.
