# FX-03 — Web: Converted Dashboard and History

Status: blocked

## Goal

Web: Converted Dashboard and History.

## Depends on

- FX-02

## Context

Read completely before implementation:

- AGENTS.md
- docs/exchange_rates_design.md (X4, X5)

## In scope

- The dashboard shows the main currency by default when the user has one and rates exist, marked
  "≈", with the rate date and sources; the per-currency view stays available.
- History rows in another currency show "≈ amount" in the main currency.
- Russian translations for the new strings.

## Out of scope

- Rates on expense dates.

## Acceptance criteria

- Without a default currency or rates, pages behave as before and point to Settings.
- Converted values are never shown without "≈" and a note on their source.

## Required tests

Integration tests for the converted dashboard, the per-currency switch, missing rates, and history
rows; screenshots reviewed.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for rounding, missing rates, secrets in logs, and
  scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
