# FX-01 — Exchange Rates: Storage, Sources, Refresh

Status: done

## Goal

Exchange Rates: Storage, Sources, Refresh.

## Depends on

- Human approval of docs/exchange_rates_design.md

## Context

Read completely before implementation:

- AGENTS.md
- docs/exchange_rates_design.md (X2, X3)

## In scope

- An `exchange_rates` table (migration 20260924_19): currency, units per EUR, rate date, source,
  fetch time.
- ECB and NBU adapters with httpx, timeouts, and strict parsing; only catalogue currencies are
  kept.
- `ExchangeRates`, a snapshot that converts minor units between currencies with half-even
  rounding, or reports a missing rate.
- The `refresh_exchange_rates` command: fetch both, store the latest rate per currency, exit non-
  zero on a failed source without dropping stored rates.

## Out of scope

- Using the rates anywhere else.

## Acceptance criteria

- Recorded ECB and NBU responses parse into the expected rates; malformed or partial responses are
  rejected.
- Conversions round half-even to the target currency's minor units, including JPY and BHD.
- Tests never call the real services.

## Required tests

Unit tests for parsing recorded responses, the NBU-to-EUR derivation, and conversion arithmetic;
integration tests for storing and reading rates and for the command with mocked sources.

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
