# EXPENSE-01 — Active-Record Rule and History Listing Query

Status: done

## Goal

Give the persistence layer one centralized active-record rule and an owner-scoped, filtered,
keyset-paginated Expense history query.

## Depends on

- Human approval of docs/expense_management_design.md

## Context

Read completely before implementation:

- AGENTS.md
- docs/expense_management_design.md (decisions D5, D6)
- docs/product_decision_review.md, decision 6
- docs/tasks/CAPTURE-06-expense-persistence.md
- docs/tasks/CAPTURE-10-confirm-and-expense-endpoints.md

## In scope

- One repository-level helper that applies `deleted_at IS NULL`. Refactor the existing
  `SqlAlchemyExpenseRepository.get` path used by `GET /capture/expenses/{id}` to use it.
- A listing method that takes the owner, an optional inclusive transaction-date range, an optional
  set of category keys, an optional set of currency codes, a page size, and an optional keyset
  position. It returns the page and the next keyset position, or none on the last page.
- Order: `transaction_date DESC, created_at DESC, id DESC`.
- A typed keyset position value, plus encoding to and decoding from an opaque base64url cursor
  string. Decoding rejects malformed input with a dedicated exception.
- Migration `11`: a partial index
  `(owner_id, transaction_date DESC, created_at DESC, id DESC) WHERE deleted_at IS NULL`.

## Out of scope

- HTTP endpoints.
- Totals, counts, or any analytics aggregation.
- Merchant or text search.
- Reading deleted Expenses (restore is EXPENSE-06).

## Acceptance criteria

- Soft-deleted Expenses never appear in the listing or in single-Expense reads.
- Another owner's Expenses never appear, whatever the filters or cursor.
- Paging through the full result with any page size returns every matching active Expense exactly
  once, in the documented order, including ties on transaction date and creation time.
- Expenses inserted or deleted between page requests do not cause duplicates or skips among
  the rows that existed throughout.
- Filters combine with AND. Date bounds are inclusive.
- A malformed cursor raises the dedicated exception, never a database error.
- `alembic upgrade head`, `downgrade -1`, `upgrade head`, and `alembic check` pass with no drift.

## Required tests

Unit tests must cover cursor encoding round-trips and rejection of malformed, truncated,
wrong-type, and wrong-shape cursors.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover ordering with ties,
exhaustive pagination, each filter and their combination, owner scoping, deleted-row exclusion,
insertion and deletion between pages, and that the query plan can use the new index (an `EXPLAIN`
assertion is optional; do not make it brittle).

## Required checks

Run:

- focused unit tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- Alembic upgrade/downgrade/check;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for owner-scoping bypass, deleted-row leakage, unstable
  ordering, migration drift, and scope creep.

## Completion conditions

Do not commit.

Change `Status: blocked` to `Status: ready` only after the design is approved, then implement,
then change `Status: ready` to `Status: review` only after every criterion and check passes.
