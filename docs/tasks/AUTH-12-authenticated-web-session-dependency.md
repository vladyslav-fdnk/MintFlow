# AUTH-12 — Authenticated Web-Session Dependency

Status: ready

## Goal

Resolve the secure WebSession cookie to a typed active User identity for protected Web operations.

## Depends on

- AUTH-11

## Context

Read completely before implementation:

- AGENTS.md
- docs/authentication_design_proposal.md
- docs/authentication_persistence_design.md
- docs/tasks/AUTH-05-web-sessions.md
- docs/tasks/AUTH-08-http-authentication-composition-boundary.md
- docs/tasks/AUTH-11-magic-link-post-consumption-secure-cookie.md

Application and domain use cases must receive `User.id`, not email, cookie secrets, or session IDs as
account ownership. Revoked or expired sessions and sessions belonging to deactivated Users must
never authenticate.

## In scope

- Extract the approved host-only session cookie from a request.
- Invoke the existing WebSession authentication use case.
- Provide a typed authenticated principal containing the authenticated User ID and internal session
  ID needed by later logout/CSRF behavior.
- Add a narrow FastAPI dependency for protected Web handlers.
- Return one generic unauthenticated response for missing, malformed, unknown, expired, revoked,
  and deactivated-user sessions.
- Preserve absolute session expiry without last-seen writes or sliding renewal.

## Out of scope

- Roles, permissions, organizations, or public API authentication.
- CSRF token generation or validation.
- Logout route.
- Device history, naming, session listing, JWT, or refresh tokens.
- Telegram authorization.
- Financial owner-scoped endpoints.

## Acceptance criteria

- Protected handlers receive a trusted User ID resolved from server-side persistence.
- Missing, malformed, unknown, expired, revoked, and deactivated-user sessions are rejected
  uniformly without revealing which condition occurred.
- The session row ID alone never authenticates a request.
- Raw cookie/session secrets and hashes are absent from logs, traces, errors, and responses.
- Authentication performs no write, does not extend expiry, and does not create another session.
- Exact expiry remains `now < expires_at`; equality is unauthenticated.
- Dependency and database Session lifetimes are safe on success and failure.

## Required tests

Unit tests must cover cookie extraction, typed-principal mapping, generic rejection, and no sliding
expiration.

HTTP/security tests must cover:

- valid authenticated request;
- missing, empty, malformed, unknown, expired, and revoked cookies;
- a valid session belonging to a deactivated User;
- session row ID presented as cookie value;
- uniform unauthenticated response;
- no cookie value or hash in captured logs/errors;
- no session expiry or persistence mutation after authentication.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover endpoint-level
authentication, exact expiry, revocation, deactivated User behavior, multiple valid sessions, and
relevant authenticate/revoke or authenticate/deactivate races using separate connections where
concurrency is asserted.

## Required checks

Run:

- focused unit and HTTP/security tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for authentication bypass, secret leakage, expiry
  mutation, dependency lifetime, and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes. If AUTH-11's
human approval gate has not been resolved and implemented, leave this task blocked.
