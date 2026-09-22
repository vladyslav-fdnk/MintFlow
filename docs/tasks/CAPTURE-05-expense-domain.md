# CAPTURE-05 — Expense Domain Model

Status: ready

## Goal

Model the `Expense` aggregate: the confirmed, immutable-by-default financial fact, in memory only.

## Depends on

- CAPTURE-04

## Context

Read completely before implementation:

- AGENTS.md
- docs/domain_design_proposal.md, sections 2 "Expense", 3 "Expense aggregate", 6 (invariants 1, 3,
  4, 9, 20, 26), 8 "Expense should retain"
- docs/product_decision_review.md, decisions 1, 2, 3, 6, 17

Inspect `src/mintflow/domain/user/model.py` for the established domain-model style.

Finalized MVP defaults that constrain this task directly:

- Positive amounts only; no refunds this MVP (decision 1). An `Expense`'s `Money` must be strictly
  greater than zero — this is where that rule is enforced, not in `Money` itself.
- Category is structurally required on `Expense` (decision 3); use `uncategorized` when nothing was
  explicitly selected — resolving *which* category id to use is CAPTURE-07's job, but `Expense`
  itself must not accept construction without one.
- Deletion is soft delete: excluded from ordinary history/analytics but recoverable (decision 6).
- Currency (and amount, together as `Money`) may be corrected post-confirmation, but only as one
  atomic Money replacement, never currency alone (decision 17).

## In scope

- An `Expense` frozen dataclass: identity, owner (`User.id`), `Money`, `TransactionDate`, merchant
  (`MerchantName | None`), category key (`str`, required, non-null), optional note, capture source,
  originating `CaptureDraft.id`, optional originating `Receipt.id` (reserved, always `None` this
  sprint), creation/modification timestamps, and deletion status/timestamp (`None` while active).
- `Expense.create(...)`: a factory enforcing `Money` is strictly positive and category key is
  present; this is the only way an `Expense` instance is produced by domain code in this task.
- `edit_money(new_money, *, now)`: replaces amount and currency together atomically; rejects a
  non-positive replacement the same way `create` does.
- `edit_merchant(...)`, `edit_category(...)`, `edit_note(...)`, `edit_transaction_date(...)`: each
  returns a new instance with an updated `modified_at`.
- `delete(*, now)` and `restore()`: soft-delete transition and its reversal. `delete` on an
  already-deleted `Expense` and `restore` on an active one are no-ops (return the same logical
  state) rather than errors — but must not silently accept nonsensical calls that would corrupt
  `deleted_at` (for example, calling `delete` twice must not overwrite the original deletion
  timestamp).
- Enforce invariant 26 structurally where practical: expose whether the instance `is_active` so
  callers cannot forget to filter deleted `Expense`s.

## Out of scope

- Any persistence, migration, or repository.
- Confirmation itself, i.e. constructing an `Expense` from a `CaptureDraft` (CAPTURE-07 owns that
  orchestration and calls `Expense.create` as one step within it).
- Post-confirmation audit history/edit trail persistence (decision 7 selects a lightweight audit
  record as the eventual MVP behavior, but recording *where* those records live and wiring them
  into every edit path is not required by this task; if you implement `edit_*` methods without an
  audit mechanism, note this explicitly as a known gap for a later task rather than silently
  dropping the decision).
- Any HTTP endpoint.
- Currency conversion.

## Acceptance criteria

- `Expense.create` rejects zero or negative `Money` and rejects a missing category key.
- `edit_money` rejects a non-positive replacement and otherwise atomically replaces both amount and
  currency.
- Each `edit_*` method updates `modified_at` and returns a new instance; the original is unchanged.
- `delete` sets `deleted_at` exactly once; a second `delete` call does not change an already-set
  `deleted_at`.
- `restore` clears `deleted_at`; calling it on an already-active `Expense` is a no-op.
- `is_active` reflects `deleted_at is None` and nothing else.
- Every field that domain invariant 20 ties together (expense owner, draft owner, receipt owner)
  cannot be checked by `Expense` alone (it does not have access to the draft/receipt), so this task
  must not attempt to enforce invariant 20 here — note in the diff/tests that this cross-aggregate
  check is CAPTURE-07's responsibility.

## Required tests

Unit tests must cover:

- `Expense.create` success, and rejection for zero amount, negative amount, and missing category;
- each `edit_*` method's success path and `modified_at` advancement;
- `edit_money` rejecting a non-positive replacement;
- `delete` followed by a second `delete` preserving the original `deleted_at`;
- `restore` on a deleted and on an already-active `Expense`;
- `is_active` for both states;
- immutability (original instance unchanged after every transition).

No integration tests are required; this task has no persistence.

## Required checks

Run:

- focused unit tests;
- full `make check`;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for accidental mutation, accidental acceptance of a
  non-positive `Money`, and scope creep.

## Completion conditions

Do not commit.

CAPTURE-04 must be `done` first. Change `Status: blocked` to `Status: ready` only once CAPTURE-04 is
`done`, then implement, then change `Status: ready` to `Status: review` only after every criterion
and check passes.
