# CAPTURE-01 — Capture Domain Value Objects and Enums

Status: review

## Goal

Establish the provider-neutral value objects and enums that `CaptureDraft` and `Expense` will be
built from: `Money`, `CurrencyCode`, `TransactionDate`, `MerchantName`, `CaptureSource`,
`CaptureDraftState`, and a draft-field provenance source. No persistence, no HTTP, no use case in
this task.

## Depends on

None. This is the first task of the `capture-foundation` sprint.

## Context

Read completely before implementation:

- AGENTS.md
- docs/domain_design_proposal.md (sections 1, 2 "CaptureDraft", 4, 6)
- docs/product_decision_review.md, decisions 1, 2, 14, 15

Inspect `src/mintflow/domain/user/model.py` for the established domain-model style: frozen
`slots=True` dataclasses, invariants raised in `__post_init__`, and transitions returned as new
instances via `dataclasses.replace` rather than mutation.

The finalized MVP defaults from `product_decision_review.md` apply directly:

- Refunds are not supported: `Money` never represents a negative amount (decision 1).
- `Money` may technically represent zero; only `Expense` confirmation rejects zero, not `Money`
  itself (decision 2). Do not bake the "> 0" rule into `Money` — it belongs to a later confirmation
  task.
- Currency scope is active ISO 4217 fiat currencies only, with minor-unit metadata per currency
  (decision 14). No crypto.
- Use a large representational cap on amounts rather than no cap at all (decision 15), for example
  999,999,999 major units interpreted through the currency's minor-unit definition. Make the cap a
  named constant, not a narrow database numeric type, so it can change without a migration.

## In scope

- `Money`: integer minor-unit amount plus `CurrencyCode`. Reject negative amounts. Reject amounts
  above the configured cap. Provide same-currency addition and comparison. Cross-currency
  arithmetic or comparison must raise, not silently coerce.
- `CurrencyCode`: a validated wrapper around an ISO 4217 alphabetic code. Normalize to uppercase.
  Validate against a small static currency catalogue that records each currency's minor-unit
  exponent (0, 2, or 3 decimal places). Reject unknown or non-fiat codes.
- A minimal currency catalogue covering at least USD, EUR, GBP, PLN, UAH, JPY (0 decimals), and one
  3-decimal currency (for example BHD), so `Money` behavior is exercised across differing
  minor-unit exponents.
- `TransactionDate`: a named wrapper around a calendar date (not a datetime/instant). Reject
  obviously invalid values a date type cannot already reject on its own only if there is a concrete
  domain reason (for example, refuse an absurdly distant past/future bound as a sanity check); do
  **not** implement the "today or tomorrow only" business rule here — that depends on the User's
  current timezone and clock, and belongs to the future confirmation task.
- `MerchantName`: a non-empty, normalized text wrapper (trimmed, internal whitespace collapsed,
  bounded length). Used only as `MerchantName | None` since merchant is optional on both
  `CaptureDraft` and `Expense`.
- `CaptureSource` enum: `telegram_manual`, `telegram_receipt`, `web_manual`. Only `web_manual` is
  reachable by any code in this sprint; the others are reserved so the stored identity never needs
  to change meaning later (see domain doc section 9's category-identity-stability reasoning, which
  applies equally here).
- `CaptureDraftState` enum: `collecting`, `awaiting_recognition`, `ready_for_review`, `confirmed`,
  `cancelled`, `expired`. Only `collecting`, `ready_for_review`, `confirmed`, `cancelled`, and
  `expired` are reachable this sprint; `awaiting_recognition` is reserved for the receipt-capture
  sprint and must not be produced or accepted by any code added in this sprint.
- A draft-field provenance source enum: `recognition`, `user`, `default`. Only `user` and `default`
  are reachable this sprint.

## Out of scope

- `CaptureDraft`, `Receipt`, `RecognitionResult`, `Expense`, or `Category` entities.
- Any persistence, migration, or repository.
- Any HTTP endpoint or use case.
- The future-date confirmation rule, the zero/negative-amount confirmation rule, and any other rule
  that depends on request-time context (User timezone, clock, confirmation state) rather than the
  value alone.
- Currency conversion or exchange rates.

## Acceptance criteria

- `Money` rejects negative amounts and amounts above the configured cap, for currencies with 0, 2,
  and 3 decimal places.
- `Money` addition and comparison across different `CurrencyCode`s raises instead of coercing or
  silently comparing minor units.
- `Money` equality and comparison for the same currency behave correctly at the cap boundary and at
  zero.
- `CurrencyCode` normalizes lowercase/mixed-case input to uppercase and rejects codes absent from
  the catalogue.
- The currency catalogue exposes each supported currency's minor-unit exponent, and `Money`
  construction uses it consistently (for example, cap comparison honors the exponent).
- `TransactionDate` wraps a calendar date and is distinguishable in type from any datetime/instant
  value elsewhere in the codebase.
- `MerchantName` normalizes whitespace, rejects an empty/whitespace-only string, and enforces a
  bounded length.
- All enums are `StrEnum` with stable lowercase string identities suitable for direct database
  storage.

## Required tests

Unit tests must cover:

- `Money` construction success and rejection (negative, over-cap, at-cap, zero) across at least one
  0-decimal, one 2-decimal, and one 3-decimal currency;
- `Money` same-currency addition and comparison;
- `Money` cross-currency addition and comparison raising;
- `CurrencyCode` normalization and rejection of unknown/malformed codes;
- `TransactionDate` construction and its distinctness from a plain datetime;
- `MerchantName` normalization, empty rejection, and length bounding;
- enum membership and string values for `CaptureSource`, `CaptureDraftState`, and the provenance
  source enum.

No integration tests are required; this task has no persistence.

## Required checks

Run:

- focused unit tests;
- full `make check`;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for accidental persistence coupling, accidental exposure
  of unreachable enum values in any reachable code path, and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
