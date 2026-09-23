# RCPT-04 — Telegram Receipt Intake

Status: blocked

## Goal

Accept a receipt photo in Telegram, queue it, and acknowledge it immediately.

## Depends on

- RCPT-02

## Context

Read completely before implementation:

- AGENTS.md
- docs/receipt_recognition_design.md (R5)
- docs/telegram_client_design.md
- docs/tasks/TG-06-telegram-manual-capture-flow.md

## In scope

- Update models for photos and documents; `getFile` and file download in the Bot API client, never logging the file URL (it contains the token).
- For a linked user: validate type and size from the update metadata, create a queued receipt and a `TELEGRAM_RECEIPT` draft in `awaiting_recognition` in the update's transaction, and reply with the acknowledgement after commit.
- Albums, PDFs, oversized or unsupported files: a clear instruction and nothing stored.
- A photo while a draft is active: the Continue / Discard choice from TG-07.

## Out of scope

- Downloading and recognizing (RCPT-05).

## Acceptance criteria

- The webhook does no download and no recognition.
- Unlinked users' photos are refused and nothing is stored.
- A redelivered photo update creates one receipt.

## Required tests

Unit tests must cover update parsing and validation. Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover intake through the webhook, redelivery, and the conflict path.

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
