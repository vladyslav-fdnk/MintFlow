# AUTH-14 — Logout Endpoint

Status: blocked

## Goal

Implement CSRF-protected logout that revokes only the current server-side WebSession and expires
the browser cookie.

## Depends on

- AUTH-12
- AUTH-13

## Context

Read completely before implementation:

- AGENTS.md
- docs/authentication_design_proposal.md
- docs/authentication_persistence_design.md
- docs/tasks/AUTH-06-authentication-audit-persistence.md
- docs/tasks/AUTH-12-authenticated-web-session-dependency.md
- docs/tasks/AUTH-13-session-bound-csrf-protection.md

Logout is a state-changing browser action. It must use POST, validate the authenticated session and
CSRF contract, revoke server-side state, append minimal audit evidence, and expire the cookie.

## In scope

- Add the fixed logout POST route.
- Require the AUTH-12 authenticated principal and AUTH-13 CSRF/Origin protection.
- Revoke only the current WebSession.
- Append the approved logout/session-revocation audit evidence.
- Expire the exact host-only session cookie with matching path and security attributes.
- Make repeated logout safe and non-disclosing.

## Out of scope

- GET logout.
- Logout-all endpoint or UI.
- Device/session management.
- Account deactivation or deletion.
- Revoking other valid sessions.
- Telegram unlinking.

## Acceptance criteria

- GET and other safe methods cannot log out.
- A valid logout revokes the current server-side session and records audit evidence.
- Other valid sessions belonging to the same User remain active.
- Repeated logout is safe and cannot revoke another session.
- The cookie is expired even when the presented server-side session is already invalid, without
  leaking the reason.
- CSRF or Origin failure occurs before revocation and audit success evidence.
- The expired cookie uses matching name/path and does not broaden Domain scope.
- Cookie values, session secrets/hashes, CSRF tokens, and authorization details are absent from
  logs and audit records.

## Required tests

Unit tests must cover current-session selection, idempotent outcome mapping, and cookie-expiration
construction.

HTTP/security tests must cover:

- successful POST logout;
- GET cannot log out;
- missing/invalid CSRF and Origin leave the session active;
- repeated logout;
- invalid/expired/revoked session behavior;
- exact cookie expiration attributes;
- no secret or CSRF data in logs/errors;
- other sessions remain authenticated.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover current-session
revocation, preservation of other sessions, repeated/concurrent logout behavior, audit evidence,
and absence of partial commit when audit insertion fails. Concurrency assertions must use separate
connections and synchronization.

## Required checks

Run:

- focused unit and HTTP/security tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for CSRF, session targeting, transactionality, cookie
  deletion, secret leakage, and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes. AUTH-13's
approved browser CSRF contract must be implemented first; do not bypass it to expose logout.
