# EXPENSE-06 — Delete and Restore Expense

Status: done

## Goal

Let the owner soft-delete a confirmed Expense and restore it, with both operations recorded and
safe to retry.

## Depends on

- EXPENSE-03

## Context

Read completely before implementation:

- AGENTS.md
- docs/expense_management_design.md (decisions D1, D2, D3, D6, D7)
- docs/product_decision_review.md, decision 6
- docs/tasks/EXPENSE-03-expense-change-records.md
- docs/tasks/CAPTURE-10-confirm-and-expense-endpoints.md

## In scope

- `DeleteExpense` and `RestoreExpense` use cases: lock the owner's Expense, apply the existing
  `Expense.delete` / `Expense.restore` domain methods, update the row, and append a `deleted` or
  `restored` change record in one transaction.
- Repeating an operation that is already in effect is a successful no-op that writes no record.
- A repository method that explicitly locks and reads an owned Expense regardless of deletion
  state, used only by these two use cases.
- `DELETE /capture/expenses/{id}` → `204`. `POST /capture/expenses/{id}/restore` → `200` with the
  Expense representation. Both are authenticated and CSRF-protected.
- Cross-owner and missing Expenses return the same 404 as `GET`.

## Out of scope

- Purging soft-deleted Expenses, or any retention job.
- Account deletion.
- Listing deleted Expenses ("trash" view).
- Receipt deletion.

## Acceptance criteria

- After delete, the Expense disappears from `GET /capture/expenses/{id}` and from the history
  listing; after restore, it reappears unchanged except for `modified_at`.
- Repeated delete and repeated restore return success and write no extra change record.
- Concurrent delete requests produce exactly one `deleted` record.
- A CSRF or Origin failure leaves the Expense unchanged and never invokes the use case.
- If the change record insert fails, the deletion state is unchanged.

## Required tests

Unit tests must cover no-op detection and outcome-to-status mapping.

HTTP/security tests must cover success for both routes, repeats, CSRF/Origin failure with a
recording stand-in, cross-owner access, and unauthenticated access.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover delete → hidden
from list and detail → restore → visible again, rollback on record insert failure, and concurrent
deletes on separate connections with synchronization.

## Required checks

Run:

- focused unit and HTTP/security tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for CSRF bypass, ownership bypass, deleted-row leakage,
  duplicate change records, and scope creep.

## Completion conditions

Do not commit.

EXPENSE-03 must be `done` first. Change `Status: blocked` to `Status: ready` only then, implement,
then change `Status: ready` to `Status: review` only after every criterion and check passes.
