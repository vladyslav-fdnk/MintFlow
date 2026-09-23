# RCPT-02 — Receipt Persistence

Status: done

## Goal

Persist receipts as a work queue, receipt images separately from metadata, recognition results, and the draft's receipt link.

## Depends on

- RCPT-01

## Context

Read completely before implementation:

- AGENTS.md
- docs/receipt_recognition_design.md (R2, R3, R4)
- docs/tasks/RCPT-01-receipt-domain.md
- docs/tasks/CAPTURE-04-capture-draft-persistence.md

## In scope

- Migration: `receipts` (owner, state, attempt id, lease expiry, Telegram file id, timestamps, image-removed marker), `receipt_images` (receipt id, bytes up to 10 MiB, media type), `recognition_results` (receipt, attempt, selected values as JSONB, created at), and `capture_drafts.receipt_id` with a foreign key; states and sizes enforced by constraints.
- Repositories: create a queued receipt; claim the oldest claimable receipt (queued, or processing with an expired lease) with `FOR UPDATE SKIP LOCKED`; complete or fail an attempt only if it is still current; store and read an image; store a result.
- Draft repository support for the receipt link and recognition provenance.

## Out of scope

- Retention deletion (RCPT-08).
- Object storage.

## Acceptance criteria

- Two workers never claim the same receipt; an expired lease becomes claimable again.
- Completing a stale attempt changes nothing.
- Image bytes are never loaded by receipt or draft queries.
- `alembic upgrade`, `downgrade -1`, `upgrade`, and `alembic check` pass.

## Required tests

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover every constraint, concurrent claims on separate synchronized connections, lease expiry, stale completion, and image size limits.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for ownership bypass, overwriting user input, receipt
  contents or secrets in logs, duplicate processing, and scope creep.

## Completion conditions

Do not commit.

Change `Status: blocked` to `Status: ready` only after the design is approved and every
dependency is `done`, then implement, then change `Status: ready` to `Status: review` only after
every criterion and check passes.
