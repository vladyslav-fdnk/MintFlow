# CAPTURE-03 — CaptureDraft Domain Model (Manual Capture Only)

Status: blocked

## Goal

Model the `CaptureDraft` aggregate and its manual-capture lifecycle and invariants in memory, with
no persistence yet. Establish field provenance so a later delayed-recognition sprint cannot silently
overwrite a user correction, even though recognition itself is not implemented now.

## Depends on

- CAPTURE-02

## Context

Read completely before implementation:

- AGENTS.md
- docs/domain_design_proposal.md, sections 2 "CaptureDraft", 3 "CaptureDraft aggregate", 5 "Manual
  capture", 6 (invariants 1–2, 5–6, 8, 10, 14, 16–19, 23), 8 "CaptureDraft should retain"
- docs/product_decision_review.md, decisions 3 and 4
- docs/tasks/CAPTURE-01-domain-value-objects.md
- docs/tasks/CAPTURE-02-category-domain-and-persistence.md

Inspect `src/mintflow/domain/user/model.py` for the established domain-model style (frozen
`slots=True` dataclass, invariants in `__post_init__`, transitions return a new instance via
`dataclasses.replace`).

Finalized MVP defaults:

- Mandatory-for-confirmation fields: amount, currency (together as `Money`), and transaction date.
  Category is always stored but user selection is optional (defaults to `uncategorized` at
  confirmation, not on the draft itself). Merchant and note are optional (decision 3).
- Today's date may be proposed as a visible default with `default` provenance; it is never silently
  invented without provenance (decision 4).

## In scope

- A `CaptureDraft` frozen dataclass: identity, owner (`User.id`), capture source (`web_manual` only
  is exercised this sprint), lifecycle state (`CaptureDraftState`), revision counter, per-field
  current value and provenance for amount+currency (`Money | None`), transaction date
  (`TransactionDate | None`), merchant (`MerchantName | None`), category key (`str | None`,
  referencing `Category` by its stable key, not validated against persistence here), optional note,
  optional resulting `Expense.id` once confirmed, and creation/modification/confirmation timestamps.
- Field-level provenance (`recognition | user | default`) per CAPTURE-01, tracked at minimum for
  amount, transaction date, merchant, and category; only `user` and `default` are produced by any
  operation in this task.
- Manual-capture lifecycle transitions only: `start()` (creates a `collecting` draft),
  `set_field(...)`-style operations that update a field's value, mark its provenance `user`, and
  advance the revision, `mark_ready_for_review()`, `cancel()`, `expire()`. Do not implement any
  `awaiting_recognition` transition or any operation that accepts `recognition` provenance.
- `is_confirmable()` (or equivalent): true only when every mandatory field
  (amount+currency, transaction date) is present and the draft is in a state from which
  confirmation is reachable (`ready_for_review`).
- `confirm(expense_id=...)`: an in-memory transition recording the resulting `Expense.id` and moving
  the draft to `confirmed`; this method does not create the `Expense` itself (that is CAPTURE-07's
  job) — it only represents "this draft has been confirmed and produced this Expense" as a pure
  state transition, callable exactly once.
- Enforce ownership at the type level where practical (every mutating method requires the caller to
  already be the owner; do not implement authorization here — that belongs to the application/HTTP
  layers — but the domain model must make "only the owner can act" structurally easy to enforce
  there, per invariant 8).
- Enforce: terminal drafts (`confirmed`, `cancelled`, `expired`) cannot be edited, cannot be
  cancelled again, and cannot be expired (invariant 18); one draft can be confirmed at most once
  (invariant 16); a category set on a draft must be a syntactically valid stable key (format only —
  existence against the `Category` table is a persistence/application concern for a later task).

## Out of scope

- Any persistence, migration, or repository.
- `Receipt`, `RecognitionResult`, or any `awaiting_recognition` transition.
- The `Expense` entity itself.
- Draft expiration scheduling/cleanup (the domain method `expire()` exists; deciding *when* to call
  it is a later application concern).
- The future-date and zero/negative-amount confirmation rules (they depend on User timezone/clock
  and are enforced by the confirmation use case in CAPTURE-07, not here).
- Authorization/ownership enforcement at the HTTP or repository boundary (later tasks).

## Acceptance criteria

- A new draft starts in `collecting` with no fields set.
- Setting a field updates its value, sets its provenance to `user`, and advances the revision;
  setting the same field twice keeps only the latest value and provenance.
- `mark_ready_for_review()` succeeds only from a state where it is reachable and fails otherwise.
- `is_confirmable()` is true only when amount+currency and transaction date are both present and the
  draft is `ready_for_review`.
- `confirm()` fails if the draft is not confirmable, and cannot be called twice on the same draft
  instance (a second call on an already-`confirmed` draft fails rather than silently succeeding —
  the *idempotent-return-existing-Expense* behavior is an application-layer concern in CAPTURE-07,
  not this raw domain transition).
- `cancel()` and `expire()` fail on a draft that is already `confirmed`, `cancelled`, or `expired`.
- No operation in this task ever produces `awaiting_recognition` state or `recognition` provenance.
- Every transition method returns a new `CaptureDraft` instance; the original instance is never
  mutated (the dataclass is frozen).

## Required tests

Unit tests must cover:

- draft creation in `collecting`;
- setting each mandatory and optional field, including provenance and revision advancement;
- overwriting a field value and provenance on a second edit;
- `mark_ready_for_review()` success and failure paths;
- `is_confirmable()` true/false combinations for each mandatory field missing;
- `confirm()` success, `confirm()` on a non-confirmable draft, and `confirm()` on an
  already-terminal draft;
- `cancel()` and `expire()` success and failure on terminal drafts;
- immutability (asserting the original instance is unchanged after every transition).

No integration tests are required; this task has no persistence.

## Required checks

Run:

- focused unit tests;
- full `make check`;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for accidental mutation, accidental
  `awaiting_recognition`/`recognition` reachability, and scope creep.

## Completion conditions

Do not commit.

CAPTURE-02 must be `done` first. Change `Status: blocked` to `Status: ready` only once CAPTURE-02 is
`done`, then implement, then change `Status: ready` to `Status: review` only after every criterion
and check passes.
