# CAPTURE-06 — Expense Persistence

Status: review

## Goal

Persist the `Expense` aggregate from CAPTURE-05, including the database-level guarantee that one
`CaptureDraft` produces at most one `Expense`.

## Depends on

- CAPTURE-05

## Context

Read completely before implementation:

- AGENTS.md
- docs/domain_design_proposal.md, sections 2 "Expense" (Essential attributes), 3 "Expense
  aggregate", 6 (invariants 5, 6, 9, 16, 20, 26), 8 "Expense should retain"
- docs/product_decision_review.md, decision 16
- docs/tasks/CAPTURE-05-expense-domain.md

Inspect `src/mintflow/infrastructure/persistence/models.py` for constraint style (see
`ck_web_sessions_expiry` and the unique `secret_hash` constraint referenced in
`AUTH-13`/`AUTH-14`'s task files and tests for precedent on enforcing an invariant at the database
level, not only in application code).

Decision 16 fixes the cardinality this schema must enforce: one Receipt supports one CaptureDraft,
which creates at most one Expense.

## In scope

- An `ExpenseRecord` SQLAlchemy model matching CAPTURE-05's essential attributes: id, owner
  (`User.id`, foreign key), amount (integer minor units) + currency code, transaction date,
  merchant (nullable), category key (foreign key to the `Category` table from CAPTURE-02, not
  nullable), optional note, capture source, originating `CaptureDraft.id` (foreign key, **unique** —
  this is the database-level guarantee for invariant 16), optional originating `Receipt.id`
  (nullable, no foreign key required yet since `Receipt` does not exist this sprint), and the
  creation/modification/deletion-timestamp columns.
- An Alembic migration for the table, including: a check constraint that amount is strictly
  positive; a unique constraint on the originating-draft-id column; a foreign key to `Category`
  that a category deactivation cannot break (deactivating must not delete the row).
- Now that `Expense` exists, add the deferred foreign key from `CaptureDraftRecord`'s
  resulting-expense-id column (from CAPTURE-04) to this table, in this task's migration, along with
  the deferred check constraint tightening if CAPTURE-04 left it looser than intended.
- A repository with at least: `create`, `get(id, owner_id)` scoped to the owner, `get_by_draft_id`
  (used by the future confirmation use case to detect an existing Expense for a draft), and `update`
  for the `edit_*`/`delete`/`restore` transitions.

## Out of scope

- The confirmation transaction itself (CAPTURE-08 uses this repository as one collaborator inside
  one transaction that also touches `CaptureDraftRecord`).
- Expense listing/filtering/analytics use cases.
- Post-confirmation audit-history persistence (still deferred, as noted in CAPTURE-05).
- Any HTTP endpoint.

## Acceptance criteria

- Inserting a second `ExpenseRecord` with the same originating-draft-id as an existing row is
  rejected by the database (unique constraint), not only by application logic.
- Inserting an `ExpenseRecord` with amount `<= 0` is rejected by the database.
- An `Expense` owned by one User is never returned by `get` for a different owner id.
- `get_by_draft_id` returns the existing `Expense` for a draft that already has one, and nothing for
  a draft that does not.
- Soft-deleting and restoring round-trip correctly through the repository.
- The migration supports upgrade, downgrade, and re-upgrade without residue, and correctly adds the
  deferred foreign key onto the CAPTURE-04 table.

## Required tests

Unit tests must cover:

- domain-to-persistence-model mapping and back, including a deleted `Expense`.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover:

- the unique-draft-id constraint rejecting a second `Expense` for the same draft, including under
  concurrent insertion attempts from two separate connections synchronized to race (not a sequential
  call described as concurrent);
- the positive-amount check constraint;
- scoped `get` (positive and wrong-owner negative case);
- `get_by_draft_id` positive and negative case;
- soft delete/restore round-trip;
- migration upgrade/downgrade/re-upgrade (`alembic current`, `alembic check`), including the
  deferred foreign key added onto `CaptureDraftRecord`.

## Required checks

Run:

- focused unit and PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- Alembic upgrade, downgrade, re-upgrade, `alembic current`, `alembic check`;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for the uniqueness guarantee, ownership-scoping bypass, and
  scope creep.

## Completion conditions

Do not commit.

CAPTURE-05 must be `done` first. Change `Status: blocked` to `Status: ready` only once CAPTURE-05 is
`done`, then implement, then change `Status: ready` to `Status: review` only after every criterion
and check passes.
