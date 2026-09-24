# FX-02 — Dashboard in the Main Currency

Status: done

## Goal

Dashboard in the Main Currency.

## Depends on

- FX-01

## Context

Read completely before implementation:

- AGENTS.md
- docs/exchange_rates_design.md (X1, X3, X4)

## In scope

- Analytics aggregates grouped by currency as well.
- `BuildDashboard` option to convert into a target currency with a rates snapshot: summary,
  comparison, time buckets, categories, merchants, and insights.
- Currencies without a rate reported separately.

## Out of scope

- The JSON API and the Web page.

## Acceptance criteria

- Converted totals equal the sum of converted currency groups; nothing is converted without a
  rate.
- The single-currency dashboard is unchanged.

## Required tests

Unit tests for the conversion of each dashboard part; PostgreSQL tests with several currencies and
a missing rate.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for rounding, missing rates, secrets in logs, and
  scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
