# DASH-03 — Build Dashboard Use Case

Status: ready

## Goal

Assemble the complete dashboard for one user, period, and optional currency from the DASH-01
rules and DASH-02 aggregates.

## Depends on

- DASH-01
- DASH-02

## Context

Read completely before implementation:

- AGENTS.md
- docs/dashboard_design.md (all decisions)
- docs/mvp_definition.md, section 8
- docs/tasks/DASH-01-dashboard-period-rules.md
- docs/tasks/DASH-02-analytics-read-repository.md

## In scope

- A `BuildDashboard` use case taking the caller, an optional period (default per D2), and an
  optional currency, returning a typed `Dashboard`:
  - the period and the chart granularity;
  - `currencies` with per-currency totals and counts (D3);
  - the selected currency or `currency_selection_required` (D3);
  - summary: total, count, and comparison with absolute and basis-point change (D4);
  - spending over time with every bucket present (D5);
  - spending by category with shares (D6);
  - top five merchants and `other` (D7);
  - the two insights (D8).
- Uses the user's timezone and default currency from the user repository and an injected clock.

## Out of scope

- HTTP and response serialization.
- Any chart or insight not listed.

## Acceptance criteria

- Currency resolution follows the four D3 steps exactly and never combines currencies.
- Comparison, buckets, shares, top five, "Other", and insights follow D4–D8, including ties and
  empty or sparse data.
- An unknown user is a dedicated not-found outcome.

## Required tests

Unit tests with in-memory fakes must cover every D3 branch, comparison present, absent, and
zero-baseline cases, daily and monthly buckets with zeros, share rounding, exactly five and more
than five merchants with and without unnamed Expenses, only-uncategorized spending, and
largest-expense ties.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover one realistic
multi-currency month end to end against the real repository.

## Required checks

Run:

- focused unit tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for currency mixing, off-by-one periods, rounding, and
  scope creep.

## Completion conditions

Do not commit.

DASH-01 and DASH-02 must be `done` first. Change `Status: blocked` to `Status: ready` only then,
implement, then change `Status: ready` to `Status: review` only after every criterion and check
passes.
