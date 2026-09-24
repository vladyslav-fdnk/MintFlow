# DEL-01 — Delete Account: Use Case and Persistence

Status: ready

## Goal

Delete Account: Use Case and Persistence.

## Depends on

- Human approval of docs/account_deletion_design.md

## Context

Read completely before implementation:

- AGENTS.md
- docs/account_deletion_design.md (A1, A2, A5)
- docs/authentication_persistence_design.md

## In scope

- `DeleteAccount` use case: lock the user row, delete every owned row in dependency order in one
  transaction (expenses and change records, drafts, the Telegram conversation, receipts with images
  and recognition results, the Telegram connection and link challenges, web sessions, the email
  identity, login challenges for that email, the user), and record an anonymous `account_deleted`
  audit event.
- A migration (the next revision) adding `account_deleted` to the audit event types, changing
  `authentication_audit_records.user_id` to `ON DELETE SET NULL`, and creating `deleted_accounts`
  (user id primary key, `deleted_at`).
- The use case clears `user_id` on the user's audit records before deleting the user and writes a
  `deleted_accounts` row.
- The receipt worker skips a receipt whose row disappeared, without an error.

## Out of scope

- The Web pages (DEL-02); re-deletion after a restore and tombstone pruning (DEL-03).

## Acceptance criteria

- After deletion no table holds a row that references the user or the user's email, except the
  tombstone and the anonymized audit records.
- Another user's data is untouched, including an identical merchant, amount, and Telegram history.
- A failure part way leaves every row in place (one transaction).
- Deleting the same account twice, or concurrently, deletes it once and never raises an integrity
  error.
- The migration upgrades and downgrades cleanly and `alembic check` shows no drift.

## Required tests

PostgreSQL integration tests seeding every kind of owned row for two users, then deleting one; a
rollback test with an injected failure; a concurrency test with two deletions; a migration round
trip; a worker test for a vanished receipt.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for data left behind, other users' data, transaction boundaries, lock order, and migration safety.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
