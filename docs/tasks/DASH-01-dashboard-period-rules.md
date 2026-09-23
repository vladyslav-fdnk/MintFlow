# DASH-01 — Dashboard Period Rules

Status: done

## Goal

Encode the dashboard's period arithmetic as pure, fully tested application functions: the
default period, range validation, chart buckets, and the previous comparable period.

## Depends on

- Human approval of docs/dashboard_design.md

## Context

Read completely before implementation:

- AGENTS.md
- docs/dashboard_design.md (decisions D2, D4, D5)
- docs/mvp_definition.md, section 8
- docs/product_decision_review.md, decision 18

## In scope

- A new `mintflow.application.analytics` package.
- A typed `DashboardPeriod` (inclusive start and end dates) that rejects an inverted range, a
  range over 366 days, and years outside 2000–2100.
- `default_period(now, timezone)`: the first through the last day of the current month in the
  user's timezone.
- `chart_buckets(period)`: daily buckets for up to 31 days, otherwise calendar-month buckets
  clipped to the period, in order, with the granularity named.
- `comparison_windows(period, today)`: the current window clipped to `today` and its previous
  comparable window per D4, or none when the period starts after `today`.

## Out of scope

- Database access, currency handling, and HTTP.

## Acceptance criteria

- Default periods are correct across month lengths, leap years, and timezones where the local
  date differs from UTC.
- Exactly 31 days is daily; 32 days is monthly. Buckets cover the period exactly, without gaps
  or overlaps.
- Month-aligned comparisons shift by one calendar month and clamp to the shorter month;
  other ranges use the equal-length window immediately before.
- Periods partly or wholly in the future are clipped or have no comparison as specified.

## Required tests

Unit tests must cover every rule above, including 1 January (previous month in the previous
year), 29 February, 31-day months compared with 30-day months, single-day periods, and the
366-day boundary.

## Required checks

Run:

- focused unit tests;
- full `make check`;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for off-by-one errors, timezone misuse, and scope
  creep.

## Completion conditions

Do not commit.

Change `Status: blocked` to `Status: ready` only after the design is approved, then implement,
then change `Status: ready` to `Status: review` only after every criterion and check passes.
