# RCPT-05 — Receipt Worker

Status: blocked

## Goal

Process queued receipts in the background and put the result in front of the user.

## Depends on

- RCPT-02
- RCPT-03
- RCPT-04

## Context

Read completely before implementation:

- AGENTS.md
- docs/receipt_recognition_design.md (R2, R6, R7)
- docs/tasks/RCPT-03-recognition-boundary.md
- docs/tasks/TG-09-telegram-local-polling.md

## In scope

- `python -m mintflow.commands.receipt_worker`: claim, download from Telegram, store the image, recognize with a 60-second limit, store the result, apply it to the draft, and send the review card or the missing-field question.
- Failures and timeouts turn the draft into a manual draft and tell the user; a crashed worker's lease expires and the receipt is claimed again.
- Logs record outcome categories and durations only, never receipt contents.

## Out of scope

- The 30-second delay message (RCPT-06).
- A real provider (RCPT-07).

## Acceptance criteria

- A result never overwrites a field the user already set.
- A result for a cancelled or expired draft is stored but changes nothing and sends nothing.
- No receipt is processed twice concurrently.

## Required tests

Unit tests with the fake recognizer and bot must cover success, partial results, failure, timeout, and cancelled drafts. Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover intake to review card to confirmed Expense end to end, and two workers racing.

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
