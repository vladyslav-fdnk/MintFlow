# CAPTURE-08 — Confirm CaptureDraft Use Case

Status: review

## Goal

Implement the single atomic, idempotent operation that converts one `CaptureDraft` into exactly one
`Expense`. This is the most safety-critical operation in the sprint: it is the only path by which
unconfirmed information may enter financial history.

## Depends on

- CAPTURE-06
- CAPTURE-07

## Context

Read completely before implementation:

- AGENTS.md
- docs/domain_design_proposal.md, section 3 "Confirmation transaction" (the six numbered steps),
  section 5 "Duplicate confirmation", section 6 (invariants 10, 16, 17, 19, 20, 25)
- docs/product_decision_review.md, decisions 3, 5, 13, 19
- docs/tasks/CAPTURE-03-capture-draft-domain.md
- docs/tasks/CAPTURE-04-capture-draft-persistence.md
- docs/tasks/CAPTURE-05-expense-domain.md
- docs/tasks/CAPTURE-06-expense-persistence.md
- docs/tasks/CAPTURE-07-user-capture-preferences.md

This mirrors a pattern already implemented and reviewed in a sibling project on this account:
order-level reservation authority in `ludora`'s `docs/architecture/ADR-001-license-reservation.md`
and its `backend/apps/orders/services.py` (`authorize_order_reservation`, `complete_payment`). The
same discipline applies here: lock or version-check the aggregate first, verify authority/ownership,
detect an already-completed outcome before doing any work, and make the terminal transition and its
side effect atomic in one transaction.

Finalized MVP defaults this use case must enforce directly:

- Mandatory fields at confirmation: amount+currency and transaction date. Category is always stored
  on the resulting `Expense`; if the draft has no category key, default to `uncategorized`
  (decisions 3, 13).
- Future-date tolerance: reject a transaction date more than one calendar day ahead of the User's
  current local date; today and the immediate next local day are allowed (decision 5). Use an
  injected clock, never wall time, so this is testable.
- Confirmation evidence (decision 19, Option B): the operation must be traceable via the
  `CaptureDraft` id, the resulting `Expense` id, the confirming User id (the draft's owner — do not
  accept a separate caller-supplied id), a UTC confirmation timestamp, the confirmation channel
  (`CaptureSource`), and the confirmed draft revision. All of this evidence already exists as fields
  on the confirmed `CaptureDraft` and the resulting `Expense` after this task — do not add a new
  audit table for it.

## In scope

- One application-service function/class, in one database transaction, that:
  1. locks the `CaptureDraft` row (the CAPTURE-04 locking read);
  2. verifies the caller is the draft's owner;
  3. if the draft is already `confirmed`, returns the existing linked `Expense` unchanged and
     performs no writes (idempotent duplicate confirmation — domain-doc section 5);
  4. otherwise verifies `is_confirmable()` and the future-date rule, using the injected clock and the
     owning User's timezone;
  5. resolves the category key to use (the draft's chosen key, or `uncategorized` if none);
  6. creates exactly one `Expense` via `Expense.create(...)`, persists it;
  7. transitions the `CaptureDraft` to `confirmed` recording the new `Expense.id`, persists it;
  8. returns the `Expense`.
- A concurrent-request guard: two near-simultaneous confirmation requests for the same draft must
  not create two `Expense` rows. The database unique constraint from CAPTURE-06 is the final
  backstop; the application-level row lock from step 1 is the primary mechanism that should prevent
  the race from reaching that backstop at all in the common case.
- Ownership cross-check per invariant 20: the draft's owner, and the resulting Expense's owner, must
  be identical to the authenticated caller. There is no separate Receipt owner to check this sprint.

## Out of scope

- Receipt/RecognitionResult involvement of any kind.
- Any HTTP endpoint (CAPTURE-10 wires this use case to `POST`).
- Draft expiration.
- Post-confirmation `Expense` editing or deletion use cases.
- Analytics.

## Acceptance criteria

- A confirmable draft produces exactly one `Expense`, correctly reflecting every draft field,
  falling back to `uncategorized` when no category was selected.
- A non-confirmable draft (missing amount/currency or missing date) is rejected without creating an
  `Expense` or mutating the draft.
- A transaction date more than one local day in the future is rejected at confirmation time even if
  it was accepted when originally set on the draft (`TransactionDate` itself does not enforce this
  per CAPTURE-01/03).
- Confirming an already-`confirmed` draft a second time returns the same `Expense` and performs no
  additional writes (verified by asserting `Expense` and `CaptureDraft` row counts/values are
  unchanged after the second call).
- Confirming a `cancelled` or `expired` draft is rejected without creating an `Expense`.
- A caller who is not the draft's owner cannot confirm it (uniform rejection with no existence
  disclosure, consistent with the pattern established in `AUTH-12`).
- Two concurrent confirmation attempts for the same draft, from separate connections, result in
  exactly one `Expense` row and both callers ultimately observing the same `Expense` (whichever
  arrives second waits on the lock, then hits the idempotent-return path).
- The resulting `Expense`'s owner equals the `CaptureDraft`'s owner in every successful case.

## Required tests

Unit tests must cover:

- the future-date rule at exactly the boundary (today, next day, and the first rejected day) using
  an injected clock and a fixed User timezone;
- category fallback to `uncategorized` when the draft has no category key;
- the six-step sequence's branching (confirmable vs. not, already-confirmed vs. not, owner match vs.
  not) using fakes/stubs for the repositories, mirroring the style of `AuthenticateWebSession`'s
  unit tests.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover:

- end-to-end confirmation producing one persisted `Expense` and one `confirmed` `CaptureDraft`;
- duplicate confirmation returning the same `Expense` with no additional row created;
- rejection of a non-confirmable, `cancelled`, and `expired` draft, each leaving no `Expense` row;
- rejection for a non-owner caller;
- the concurrent-confirmation race using two real separate connections and a synchronization
  primitive (`Barrier`/`Event`, per the pattern in `ludora`'s
  `test_competing_orders_cannot_reserve_the_same_limited_key` and this project's own
  `test_endpoint_authentication_serializes_with_security_transition`), asserting exactly one
  `Expense` row exists afterward;
- the future-date rejection using a fixed, injected clock, not wall time.

## Required checks

Run:

- focused unit and PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for double-confirmation, ownership bypass, non-atomic
  writes, and scope creep.

## Completion conditions

Do not commit.

CAPTURE-06 and CAPTURE-07 must both be `done` first. Change `Status: blocked` to `Status: ready`
only once both are `done`, then implement, then change `Status: ready` to `Status: review` only
after every criterion and check passes.
