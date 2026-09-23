# RCPT-03 — Recognition Boundary and Candidate Selection

Status: ready

## Goal

Define the provider-independent recognizer contract and the rules that turn candidates into safe prefill values.

## Depends on

- RCPT-01

## Context

Read completely before implementation:

- AGENTS.md
- docs/receipt_recognition_design.md (R1, R6)
- docs/mvp_definition.md (section 7)
- docs/domain_design_proposal.md (sections 7, 8)

## In scope

- A `ReceiptRecognizer` protocol taking image bytes and hints (user locale, timezone, default currency) and returning candidates per field with confidence and evidence kind.
- Selection rules: per-field confidence thresholds; ambiguous totals, dates, or currencies are dropped; currency only from explicit evidence; dates beyond the future tolerance are dropped; amounts must be positive and within Money limits.
- A deterministic fake recognizer for tests and local development.

## Out of scope

- Any real provider (RCPT-07).

## Acceptance criteria

- A confident wrong-looking value (for example two different totals with similar confidence) is never selected.
- The selection output contains no provider-specific data.

## Required tests

Unit tests must cover each rule with table-driven cases, including multiple totals, subtotal-only receipts, symbol-only currencies such as `$`, locale-dependent dates such as `03/04`, and missing fields.

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
