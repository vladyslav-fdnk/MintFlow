# CAPTURE-09 — HTTP Composition Boundary and Manual Draft Endpoints

Status: ready

## Goal

Expose manual capture over HTTP: start a draft, edit its fields, view it, mark it ready for review,
and cancel it. Establish the capture HTTP composition boundary the way `AUTH-08` established it for
authentication.

## Depends on

- CAPTURE-08

## Context

Read completely before implementation:

- AGENTS.md
- docs/domain_design_proposal.md, section 12 "Manual capture", "Draft management"
- docs/tasks/AUTH-08-http-authentication-composition-boundary.md
- docs/tasks/AUTH-12-authenticated-web-session-dependency.md
- docs/tasks/AUTH-13-session-bound-csrf-protection.md
- docs/tasks/CAPTURE-03-capture-draft-domain.md
- docs/tasks/CAPTURE-04-capture-draft-persistence.md

Inspect `src/mintflow/http/authentication.py` for the established composition style: a
`Runtime`-shaped dataclass built once per app in `main.py`, request-scoped dependencies built from
`request.app.state`, and typed `Annotated[..., Depends(...)]` aliases exported for route functions
and tests to use. This task adds an analogous `src/mintflow/http/capture.py` module and its own
runtime composition, following the same shape, not modifying the authentication module beyond
importing its existing `AuthenticatedPrincipalDependency` and `CsrfProtectedPrincipalDependency`.

Every route in this task is authenticated and every unsafe (`POST`/`PATCH`/`DELETE`) route is
CSRF-protected, reusing `AUTH-12`/`AUTH-13` exactly as built — do not invent a second authentication
or CSRF mechanism.

## In scope

- `src/mintflow/http/capture.py` with a `CaptureRuntime` (or similarly named) composition dataclass
  and its builder, mirroring `AuthenticationRuntime`/`build_authentication_runtime`.
- Bounded, typed request DTOs for: starting a draft (capture source; the HTTP layer always sends
  `web_manual` for now), editing one or more draft fields (amount, currency, transaction date,
  merchant, category key, note — all optional per request so a client can send only what changed),
  marking a draft ready for review, and cancelling a draft.
- Routes, all requiring `AuthenticatedPrincipalDependency` and scoping every read/write to
  `principal.user_id`:
  - `POST /capture/drafts` — start a manual draft (CSRF-protected).
  - `GET /capture/drafts/{id}` — view one draft owned by the caller; `404`-equivalent generic
    response for another owner's draft (no existence disclosure), matching the ownership-scoping
    pattern already established for orders/sessions in this project's sibling `ludora` project and
    this project's own `AuthenticatedPrincipalDependency` usage.
  - `PATCH /capture/drafts/{id}` — edit one or more fields (CSRF-protected); reject edits to a
    terminal draft with a clear, non-5xx error.
  - `POST /capture/drafts/{id}/ready` — mark ready for review (CSRF-protected).
  - `POST /capture/drafts/{id}/cancel` — cancel (CSRF-protected).
- Map domain-level rejections (wrong owner, terminal draft, invalid field value) to appropriate,
  non-disclosing HTTP status codes and generic bodies — do not leak internal exception text.
- Request size and field-length bounds on every DTO, consistent with the existing
  `MAX_MAGIC_LINK_REQUEST_BODY_BYTES`-style bounding in `src/mintflow/http/authentication.py`.

## Out of scope

- The confirm endpoint and the view-Expense endpoint (`CAPTURE-10`).
- Receipt upload or any Telegram-facing endpoint.
- Listing all of a User's drafts.
- Category-listing endpoint (needed for a real client, but not required to exercise this task's
  acceptance criteria with direct category keys; add it here only if it is essentially free given
  `CAPTURE-02`'s repository — otherwise leave it for a later task and say so in the completion
  report).

## Acceptance criteria

- Every route requires authentication; an unauthenticated request receives the same generic
  response `AUTH-12` already established.
- Every unsafe route requires a valid session-bound CSRF token and approved Origin; a CSRF/Origin
  failure leaves the draft unchanged (no partial edit).
- A caller can never read, edit, ready, or cancel another owner's draft; the response for "not
  found" and "found but not yours" is identical.
- Editing a terminal (`confirmed`/`cancelled`/`expired`) draft is rejected without creating a new
  revision.
- Starting a draft, editing every optional field across two separate `PATCH` calls, marking it
  ready, then viewing it, reflects the accumulated edits and correct provenance.
- Malformed or oversized request bodies are rejected with a bounded, generic error, not a stack
  trace or unbounded echo of the input.

## Required tests

Unit tests must cover request DTO validation/bounding and the domain-rejection-to-HTTP-status
mapping.

HTTP/security tests must cover:

- full authenticated happy path across start → edit → edit → ready → view;
- unauthenticated access to each route;
- CSRF/Origin failure on each unsafe route, verifying the draft is unchanged afterward;
- cross-owner access to each route returning the same response as "not found";
- editing/readying/cancelling a terminal draft;
- malformed/oversized request bodies.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover the same happy path
and ownership-scoping end to end against real persistence, plus concurrent `PATCH` requests to the
same draft (two real connections, synchronized) leaving the draft in a consistent, non-corrupted
state with a well-defined final revision.

## Required checks

Run:

- focused unit and HTTP/security tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for authentication bypass, CSRF bypass, ownership-scoping
  bypass, and scope creep.

## Completion conditions

Do not commit.

CAPTURE-08 must be `done` first. Change `Status: blocked` to `Status: ready` only once CAPTURE-08 is
`done`, then implement, then change `Status: ready` to `Status: review` only after every criterion
and check passes.
