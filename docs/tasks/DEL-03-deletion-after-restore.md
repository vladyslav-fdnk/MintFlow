# DEL-03 — Deleted Accounts Stay Deleted After a Restore

Status: ready

## Goal

Deleted Accounts Stay Deleted After a Restore.

## Depends on

- Human approval of docs/account_deletion_design.md
- DEL-01

## Context

Read completely before implementation:

- AGENTS.md
- docs/account_deletion_design.md (A5)
- docs/authentication_persistence_design.md

## In scope

- `authentication_retention_cleanup` also deletes, through `DeleteAccount`, every account listed in
  `deleted_accounts` that exists again, and prunes tombstones older than 35 days.
- The deletion page, `docs/operations_design.md` (O7), and the restore reminder in
  `deploy/restore.sh` describe backup retention and the post-restore step, including the trade-off
  for deletions after the last backup.

## Out of scope

- An off-database deletion log.

## Acceptance criteria

- After restoring a backup taken before a deletion, one cleanup run removes the account again, with
  the same result as the original deletion.
- Tombstones older than 35 days are removed; younger ones stay.
- The cleanup stays safe under repeated and concurrent runs.

## Required tests

PostgreSQL integration tests that recreate a deleted user's rows, run the cleanup, and check the
result; tombstone boundary tests; a concurrent-run test.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for idempotency, data left behind, and documentation accuracy.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
