# EXPENSE-05 — Edit Expense Endpoint

Status: blocked

## Goal

Expose the EXPENSE-04 use case as `PATCH /capture/expenses/{id}`.

## Depends on

- EXPENSE-04

## Context

Read completely before implementation:

- AGENTS.md
- docs/expense_management_design.md (decisions D4, D7)
- docs/tasks/EXPENSE-04-edit-expense-use-case.md
- docs/tasks/CAPTURE-09-http-composition-and-draft-endpoints.md (draft PATCH to mirror)
- docs/tasks/AUTH-13-session-bound-csrf-protection.md

## In scope

- `PATCH /capture/expenses/{id}`, authenticated and CSRF-protected.
- JSON body with optional `merchant`, `note`, `transaction_date`, `amount_minor_units` +
  `currency` (both or neither), and `category_key`. An absent field is unchanged; explicit `null`
  clears `merchant` or `note` and is rejected for other fields. Unknown fields are rejected.
- Enforce the existing request body size limit used by capture routes.
- Response: the updated Expense representation, `200`.
- Map use-case outcomes: not found / not yours / deleted → the same 404 as `GET`; invalid value →
  the same generic 422 as the draft PATCH.

## Out of scope

- Deletion and restoration.
- Optimistic concurrency headers.
- Frontend.

## Acceptance criteria

- A valid edit returns the updated Expense, and a following `GET` returns the same values.
- Absent fields remain unchanged; explicit `null` behaves as specified.
- A CSRF or Origin failure leaves the Expense unchanged and never invokes the use case.
- Cross-owner and deleted-Expense edits return a response identical to not found.
- Error responses never echo submitted values.

## Required tests

Unit tests must cover body parsing, including absent versus `null`, paired money fields, unknown
fields, and outcome-to-status mapping.

HTTP/security tests must cover success, every rejection, CSRF/Origin failure with a recording
use-case stand-in, cross-owner and deleted cases, and unauthenticated access.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover an end-to-end edit
through the real route, verifying the persisted Expense and its change record.

## Required checks

Run:

- focused unit and HTTP/security tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for CSRF bypass, ownership bypass, null-handling errors,
  input echoing, and scope creep.

## Completion conditions

Do not commit.

EXPENSE-04 must be `done` first. Change `Status: blocked` to `Status: ready` only then, implement,
then change `Status: ready` to `Status: review` only after every criterion and check passes.
