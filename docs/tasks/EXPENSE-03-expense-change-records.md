# EXPENSE-03 — Expense Change Records

Status: done

## Goal

Add the restrained, immutable change record for confirmed Expenses (product decision 7) so later
edit, delete, and restore operations can record what changed in the same transaction.

## Depends on

- Human approval of docs/expense_management_design.md

## Context

Read completely before implementation:

- AGENTS.md
- docs/expense_management_design.md (decision D1)
- docs/product_decision_review.md, decision 7
- docs/tasks/AUTH-06-authentication-audit-persistence.md (append-only pattern to mirror)
- docs/tasks/CAPTURE-06-expense-persistence.md

## In scope

- Migration `12`: the `expense_change_records` table exactly as specified in D1, with a check
  constraint on `change_type`, a check that `changes` is present only for `edited`, and an index on
  `(expense_id, occurred_at)`.
- A typed application-level representation of a change record and of a field change (old and new
  values serialized to JSON-safe primitives: minor units as integers, currency and category as
  strings, dates as ISO strings, nullable merchant and note).
- An append-only SQLAlchemy repository with a single `append` method that joins the caller's
  transaction and never commits by itself.
- Resolve the "Known gap" note in the `Expense` docstring by pointing to this mechanism.

## Out of scope

- Any use case that edits, deletes, or restores an Expense (EXPENSE-04 and EXPENSE-06).
- Reading or displaying change history.
- Retention or purge of change records.

## Acceptance criteria

- Records can only be appended; the repository exposes no update or delete.
- An `edited` record without `changes`, or a `deleted`/`restored` record with `changes`, is rejected
  by both the application type and the database constraint.
- `append` does not commit: rolling back the surrounding transaction leaves no record.
- Deleting an Expense row cascades to its change records.
- `alembic upgrade head`, `downgrade -1`, `upgrade head`, and `alembic check` pass with no drift.

## Required tests

Unit tests must cover construction rules and JSON serialization of every field type.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover append, the check
constraints, rollback without partial commit, and cascade on Expense deletion.

## Required checks

Run:

- focused unit tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- Alembic upgrade/downgrade/check;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for mutability, transactional coupling, migration drift,
  and scope creep.

## Completion conditions

Do not commit.

Change `Status: blocked` to `Status: ready` only after the design is approved, then implement,
then change `Status: ready` to `Status: review` only after every criterion and check passes.
