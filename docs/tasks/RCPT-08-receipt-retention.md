# RCPT-08 — Receipt Image and Recognition Retention

Status: done

## Goal

Delete receipt images and recognition results 30 days after their draft finishes.

## Depends on

- RCPT-02

## Context

Read completely before implementation:

- AGENTS.md
- docs/receipt_recognition_design.md (R4)
- docs/product_decision_review.md (decisions 8, 10)
- docs/tasks/TG-08-telegram-retention-and-draft-expiry.md

## In scope

- Extend the retention command with batched, skip-locked deletion of images and recognition results whose draft was confirmed, cancelled, or expired more than 30 days ago, marking the receipt's image as removed.

## Out of scope

- User-initiated image deletion.

## Acceptance criteria

- Expenses and their receipt ids are untouched.
- Images of open drafts are never deleted.

## Required tests

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover every boundary and concurrent runs.

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
