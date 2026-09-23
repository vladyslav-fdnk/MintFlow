# DASH-02 — Analytics Read Repository

Status: review

## Goal

Provide the aggregate reads the dashboard needs, computed in PostgreSQL over active Expenses
only.

## Depends on

- Human approval of docs/dashboard_design.md

## Context

Read completely before implementation:

- AGENTS.md
- docs/dashboard_design.md (decisions D3, D6, D7, D8, D9)
- docs/expense_management_design.md (decision D6)
- docs/tasks/EXPENSE-01-history-listing-query.md

## In scope

- A read-only SQLAlchemy analytics repository whose every query starts from
  `select_active_expenses(owner_id)`, returning typed application values:
  - totals and counts per currency for a date range;
  - totals per transaction date for a date range and currency;
  - totals and counts per category, with the category display name, for a range and currency;
  - totals and counts per merchant name, with Expenses without a merchant reported as one
    `None` group, for a range and currency;
  - the largest Expense for a range and currency, using the D8 tie-break order.
- Sums are returned as exact integers in minor units.

## Out of scope

- Bucketing, ranking, top-five selection, shares, and currency resolution (DASH-03).
- HTTP.
- New indexes, unless a failing plan assertion shows one is needed; if so, stop and report.

## Acceptance criteria

- Soft-deleted Expenses and other owners' Expenses never contribute to any aggregate.
- Date bounds are inclusive. Currencies are never mixed in one sum.
- Sums stay exact for large totals (no floating point).
- An empty range returns empty results, not errors.
- The largest-expense tie-break is deterministic.

## Required tests

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover each aggregate
with several owners, currencies, categories, merchants (including none and names differing only
in case), deleted Expenses, inclusive bounds, empty ranges, large sums, and the tie-break.

## Required checks

Run:

- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for owner-scoping bypass, deleted-row leakage,
  currency mixing, and scope creep.

## Completion conditions

Do not commit.

Change `Status: blocked` to `Status: ready` only after the design is approved, then implement,
then change `Status: ready` to `Status: review` only after every criterion and check passes.
