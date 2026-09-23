# EXPENSE-07 — Reject Re-confirming a Draft Whose Expense Was Deleted

Status: done

## Goal

Stop a repeated confirmation from presenting a soft-deleted Expense as if it were part of
financial history.

## Depends on

- EXPENSE-06

## Context

Read completely before implementation:

- AGENTS.md
- docs/expense_management_design.md (decisions D2, D6)
- docs/tasks/CAPTURE-08-confirm-draft-use-case.md
- docs/tasks/CAPTURE-10-confirm-and-expense-endpoints.md
- docs/tasks/EXPENSE-06-delete-and-restore.md

Confirmation is idempotent: confirming an already confirmed draft returns the Expense it
produced. Since EXPENSE-06, that Expense may be soft-deleted. The response carries no deletion
state, so a client would believe the Expense is in history when it is not. Approved decision:
reject this case instead of returning the deleted Expense.

## In scope

- In `ConfirmCaptureDraft`, when the draft is already confirmed and its Expense is soft-deleted,
  raise `CaptureDraftNotConfirmable`. `POST /capture/drafts/{id}/confirm` therefore returns the
  existing generic 409.
- Keep the duplicate confirmation of a draft whose Expense is active returning `200` with that
  Expense, unchanged.

## Out of scope

- Restoring the Expense through the confirm route.
- Exposing deletion state in any response.
- Any change to delete or restore.

## Acceptance criteria

- Re-confirming a draft whose Expense is deleted returns 409 and writes nothing.
- After the Expense is restored, re-confirming returns 200 with the Expense again.
- Re-confirming a draft whose Expense was never deleted is unchanged.

## Required tests

Unit tests must cover the deleted, restored, and active cases in the use case.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover confirm → delete →
re-confirm (409) → restore → re-confirm (200) through the real HTTP routes.

## Required checks

Run:

- focused unit and HTTP tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for confirmation regressions and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
