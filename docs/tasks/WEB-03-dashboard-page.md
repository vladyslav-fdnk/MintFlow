# WEB-03 — Dashboard Page

Status: ready

## Goal

Show the dashboard of MVP section 8 on the Web.

## Depends on

- WEB-01

## Context

Read completely before implementation:

- AGENTS.md
- docs/web_client_design.md (W4, W6, W7, W9)
- docs/dashboard_design.md
- docs/mvp_definition.md, section 8

## In scope

- `GET /dashboard` with date-range and currency filters (htmx updates the dashboard region; the
  URL reflects the filters, so it can be bookmarked and works without JavaScript).
- Summary: total, count, and the change from the previous period when it exists.
- Spending over time (columns), spending by category, and top merchants (horizontal bars) as
  inline SVG, each with a text summary and a data table.
- The two insights as sentences, and drill-through links to the filtered history.
- A currency choice when the period has several currencies; the first-use message when there are
  no expenses.

## Out of scope

- New analytics, caching, or client-side charting.

## Acceptance criteria

- No page ever adds amounts in different currencies.
- Empty, single-expense, and sparse periods render sensibly, with no broken charts.
- Every chart value is available as text; colour is never the only signal.

## Required tests

Unit tests must cover the SVG geometry (scaling, zero and single values) and the insight
sentences. Integration tests must cover the default period in the user's timezone, filters,
several currencies, empty state, and another user's data never appearing.

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
