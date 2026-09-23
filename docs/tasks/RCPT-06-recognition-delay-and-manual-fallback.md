# RCPT-06 — Recognition Delay Message and Manual Fallback

Status: ready

## Goal

Keep the user in control when recognition is slow.

## Depends on

- RCPT-05

## Context

Read completely before implementation:

- AGENTS.md
- docs/receipt_recognition_design.md (R7)
- docs/mvp_definition.md (sections 5, 10)

## In scope

- After 30 seconds without a result, one "taking longer" message with an "Enter manually" button.
- "Enter manually" moves the draft to collecting and starts the manual questions for missing fields.
- A late result fills only untouched fields and then shows the review card.

## Out of scope

- Explicit retry of recognition.

## Acceptance criteria

- The delay message is sent at most once per receipt.
- User input during processing is never lost or overwritten.

## Required tests

Unit tests must cover timing and every ordering of user input and late results. Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover a late result after manual entry.

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
