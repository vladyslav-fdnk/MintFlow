# RCPT-01 — Receipt Domain and Draft Recognition Provenance

Status: ready

## Goal

Model receipts, recognition results, and how a result may change a draft, as pure domain rules.

## Depends on

- Human approval of docs/receipt_recognition_design.md

## Context

Read completely before implementation:

- AGENTS.md
- docs/receipt_recognition_design.md (R6)
- docs/domain_design_proposal.md (Receipt, RecognitionResult, CaptureDraft, section 5)
- src/mintflow/domain/capture/draft.py

## In scope

- A `Receipt` aggregate with the lifecycle queued → processing → recognized / recognition_failed, an attempt id per processing run, and image removal; stale attempts cannot complete.
- An immutable `RecognitionResult` with per-field selected values (merchant, date, total with currency) and a result id; no provider types.
- CaptureDraft: a receipt id, the `awaiting_recognition` state for receipt drafts, `recognition` provenance with the result id, and one `apply_recognition` transition that fills only empty or default fields, proposes Uncategorized, and moves to review or to collecting when required fields are missing.
- A `continue_manually` transition from awaiting recognition to collecting.
- Remove the sprint guard that rejects recognition provenance.

## Out of scope

- Persistence, recognition selection rules, Telegram.

## Acceptance criteria

- User-supplied fields are never overwritten by any result, before or after review.
- A result for another owner or another receipt is rejected.
- A completed or failed attempt cannot be completed again; a newer attempt makes older results stale.
- Manual drafts and every existing capture rule behave exactly as before.

## Required tests

Unit tests must cover every lifecycle transition, stale attempts, application over empty, default, and user fields in both collecting and review states, and regressions for manual drafts.

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
