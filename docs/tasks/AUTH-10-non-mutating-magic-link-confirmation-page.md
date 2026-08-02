# AUTH-10 — Non-Mutating Magic Link Confirmation Page

Status: ready

## Goal

Provide the scanner-safe GET page for a Magic Link without consuming the challenge, creating an
account, or authenticating a browser.

## Depends on

- AUTH-08

## Context

Read completely before implementation:

- AGENTS.md
- docs/authentication_design_proposal.md
- docs/authentication_persistence_design.md
- docs/tasks/AUTH-08-http-authentication-composition-boundary.md

Magic Link secrets necessarily appear in the initial URL, and email scanners commonly follow GET
links. The approved architecture therefore requires a first-party confirmation page and a separate
POST consumption action. The page must not load third-party resources or leak the URL through
referrers, caches, analytics, or logs.

## In scope

- Add the Magic Link GET confirmation route at the approved path.
- Render a minimal first-party confirmation form for a later POST.
- Preserve the presented token and approved return-target representation only as needed for that
  form submission.
- Apply `Referrer-Policy: no-referrer` and no-store response headers.
- Render one generic safe failure presentation for malformed input.
- Ensure HTTP access logging/configuration does not record the credential-bearing query string.

## Out of scope

- LoginChallenge lookup or consumption.
- User, EmailIdentity, or WebSession creation.
- POST confirmation behavior or cookie issuance.
- Pre-authentication/login-CSRF policy resolution.
- Third-party scripts, fonts, images, analytics, or other resources.
- Full Web Client styling or framework integration.

## Acceptance criteria

- GET never mutates LoginChallenge, User, EmailIdentity, WebSession, audit, or rate-limit state.
- Repeated GET requests are safe and leave PostgreSQL unchanged.
- The page has no third-party resource references.
- Responses include effective no-store and no-referrer protection.
- Unknown, malformed, expired, and already-consumed-looking inputs are not distinguished by a
  database lookup or account-specific content.
- The token and credential-bearing URL are absent from application, access, exception, and test
  logs.
- The form submits only by POST to the fixed internal consume route and cannot select an external
  action.
- Viewing the page never emits an authenticated session cookie.

## Required tests

HTTP/security tests must cover:

- a valid-looking token renders a confirmation form;
- malformed and missing input render the approved generic safe state;
- repeated GET requests do not consume or authenticate;
- no `Set-Cookie` authenticated-session header is emitted;
- exact no-store and no-referrer response behavior;
- no third-party resources or external form actions;
- token and complete query URL absence from captured logs;
- GET, HEAD where supported, and scanner-like repeated requests remain non-mutating.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must prove that GET requests do
not change LoginChallenge consumption, User/EmailIdentity counts, WebSession rows, or audit records.

## Required checks

Run:

- focused HTTP/security tests;
- focused PostgreSQL non-mutation integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for GET mutation, token leakage, caching, referrer leakage,
  third-party resources, and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes. Do not make
or imply a decision about the unresolved pre-authentication/login-CSRF mechanism required by
AUTH-11.
