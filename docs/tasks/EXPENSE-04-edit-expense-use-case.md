# EXPENSE-04 — Edit Expense Use Case

Status: review

## Goal

Let the owner correct a confirmed Expense, atomically recording exactly what changed.

## Depends on

- EXPENSE-03

## Context

Read completely before implementation:

- AGENTS.md
- docs/expense_management_design.md (decisions D1, D3, D4)
- docs/domain_design_proposal.md, section 5 "Expense editing" and section 6 invariants
- docs/tasks/CAPTURE-08-confirm-draft-use-case.md
- docs/tasks/EXPENSE-03-expense-change-records.md

## In scope

- An `EditExpense` application use case taking the caller, the Expense id, and a typed edit
  command in which every field is either "unchanged" or a new value (`merchant` and `note` may be
  set to `None`).
- Load the owner's active Expense with a row lock, apply the existing `Expense.edit_*` domain
  methods, update the row, and append one `edited` change record listing only fields whose values
  actually changed, all in one transaction.
- A no-op edit writes nothing, keeps `modified_at`, and returns the unchanged Expense.
- Move the "transaction date at most one day in the future in the owner's timezone" rule from
  `ConfirmCaptureDraft` into one shared function used by both confirmation and editing. Keep
  confirmation behavior identical.
- Validate that a new category key names an existing system category.
- Repository additions: lock-and-read of an active owned Expense, and an update that joins the
  caller's transaction instead of committing on its own.

## Out of scope

- HTTP endpoints (EXPENSE-05).
- Deletion and restoration (EXPENSE-06).
- Optimistic concurrency or version preconditions.
- Recognition or Receipt interactions.

## Acceptance criteria

- The owner can change any subset of merchant, note, transaction date, money, and category.
- Another owner's Expense and a soft-deleted Expense are indistinguishable "not found" outcomes.
- Invalid values (non-positive amount, invalid currency, unknown category, date beyond tolerance)
  are rejected with a dedicated exception and change nothing.
- Each effective edit produces exactly one change record with correct old and new values, even
  under two concurrent edits of the same Expense.
- If the change record insert fails, the Expense row is unchanged.
- Existing confirmation tests still pass unchanged after the shared date rule is extracted.

## Required tests

Unit tests must cover field diffing, no-op detection, clearing optional fields, every rejection
reason, and the shared date-tolerance rule at the timezone boundary.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover a persisted edit
with its change record, rollback when the record insert fails, and two concurrent edits on separate
connections with synchronization, asserting two records whose old/new values chain correctly.

## Required checks

Run:

- focused unit tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for ownership bypass, deleted-Expense editing, lost
  change records, lock ordering, confirmation regressions, and scope creep.

## Completion conditions

Do not commit.

EXPENSE-03 must be `done` first. Change `Status: blocked` to `Status: ready` only then, implement,
then change `Status: ready` to `Status: review` only after every criterion and check passes.
