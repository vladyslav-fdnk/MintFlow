# CAPTURE-10 — Confirm Draft and View Expense Endpoints

Status: review

## Goal

Expose the CAPTURE-08 confirmation use case over HTTP, and let a caller view the resulting
`Expense`. This closes the first end-to-end loop: manual capture → confirmation → financial history
record, reachable entirely over HTTP.

## Depends on

- CAPTURE-09

## Context

Read completely before implementation:

- AGENTS.md
- docs/domain_design_proposal.md, section 5 "Manual capture", "Duplicate confirmation"
- docs/tasks/CAPTURE-08-confirm-draft-use-case.md
- docs/tasks/CAPTURE-09-http-composition-and-draft-endpoints.md

This is the sprint's most safety-critical HTTP surface: it is the only route that can create
financial history. Apply the same rigor `AUTH-13`/`AUTH-14` applied to CSRF and session handling.

## In scope

- `POST /capture/drafts/{id}/confirm` — CSRF-protected, authenticated, scoped to the caller's own
  draft. Invokes the CAPTURE-08 use case. Returns the resulting `Expense` (id, money, transaction
  date, merchant, category key, note, capture source, timestamps).
- A duplicate/repeated confirmation of the same draft returns `200` with the same `Expense`, not an
  error — consistent with CAPTURE-08's idempotent behavior.
- Map CAPTURE-08's rejection reasons (not confirmable, terminal draft, future-date violation, wrong
  owner) to clear, non-5xx, non-disclosing HTTP responses. A wrong-owner attempt must return the
  same response as "not found," identical to every other route in `CAPTURE-09`.
- `GET /capture/expenses/{id}` — authenticated, scoped to the caller's own, non-deleted `Expense`.
  Same not-found-vs-not-yours non-disclosure pattern as the draft routes.

## Out of scope

- Editing or deleting a confirmed `Expense`.
- Listing all of a User's Expenses, filtering, or any analytics.
- Receipt-related confirmation paths.
- Any Telegram-facing endpoint.

## Acceptance criteria

- Confirming a confirmable draft returns `200` (or another success code the diff documents
  consistently) with a complete, correct `Expense` representation.
- Confirming the same draft again returns the same `Expense` unchanged, and no second `Expense` is
  created (verified end to end, not only at the use-case layer already covered by `CAPTURE-08`).
- Confirming a non-confirmable, terminal, or future-dated-beyond-tolerance draft returns a clear
  rejection and creates no `Expense`.
- A CSRF/Origin failure on the confirm route leaves the draft and any prior `Expense` state
  unchanged, and the use case is never invoked (mirroring the pattern `AUTH-13`/`AUTH-14`
  established and tested for their own CSRF-protected routes).
- A caller cannot confirm or view another owner's draft/Expense; the response matches the
  not-found case exactly.
- `GET /capture/expenses/{id}` never returns a soft-deleted `Expense` (there is no deletion path yet
  in this sprint, but the query must already exclude `deleted_at IS NOT NULL` rows on principle, per
  domain invariant 26, so a later deletion task does not have to re-audit every read path).

## Required tests

Unit tests must cover the confirm-rejection-to-HTTP-status mapping and the Expense response
serialization.

HTTP/security tests must cover:

- successful confirm and its response shape;
- duplicate confirm returning the same Expense;
- rejection for non-confirmable, terminal, and future-dated-beyond-tolerance drafts;
- CSRF/Origin failure leaving state unchanged and never invoking the use case (a recording
  stand-in for the use case, per the pattern in `AUTH-13`'s
  `test_csrf_protected_route_rejects_every_invalid_case_and_skips_the_use_case`);
- cross-owner confirm and cross-owner view, each matching the not-found response;
- unauthenticated access to both routes.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover the full manual
capture-to-confirmation loop end to end: `POST /capture/drafts` → `PATCH` every mandatory field →
`POST .../ready` → `POST .../confirm` → `GET /capture/expenses/{id}`, asserting the persisted
`Expense` matches every submitted field, plus a concurrent double-confirm through the real HTTP
routes (two real connections, synchronized) asserting exactly one `Expense` is created.

## Required checks

Run:

- focused unit and HTTP/security tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for double-confirmation, CSRF bypass, ownership-scoping
  bypass, soft-deleted-Expense leakage, and scope creep.

## Completion conditions

Do not commit.

CAPTURE-09 must be `done` first. Change `Status: blocked` to `Status: ready` only once CAPTURE-09 is
`done`, then implement, then change `Status: ready` to `Status: review` only after every criterion
and check passes.

This task's completion closes the `capture-foundation` sprint's core loop. Receipt capture,
recognition, expense editing/deletion, listing, and analytics are explicitly deferred to a
follow-on sprint and must not be started under this task's scope.
