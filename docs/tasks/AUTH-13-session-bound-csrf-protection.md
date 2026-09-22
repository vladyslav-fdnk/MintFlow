# AUTH-13 — Session-Bound CSRF Protection

Status: review

## Goal

Protect authenticated state-changing browser requests with CSRF tokens bound to the active
server-side WebSession and with Origin validation.

## Depends on

- AUTH-12

## Context

Read completely before implementation:

- AGENTS.md
- docs/authentication_design_proposal.md
- docs/authentication_persistence_design.md
- docs/tasks/AUTH-11-magic-link-post-consumption-secure-cookie.md
- docs/tasks/AUTH-12-authenticated-web-session-dependency.md

The approved architecture requires session-bound CSRF tokens for every state-changing browser
request. `SameSite=Lax` is defense in depth and is not sufficient on its own.

### Human approval gate

**Approved 2026-09-22:** double-submit cookie plus a custom header. The server derives the CSRF
token as `HMAC-SHA256(authentication_csrf_signing_key, session_secret)` and exposes it to the
browser only through a non-`HttpOnly`, `Secure`, `SameSite=Lax`, `__Host-`-prefixed cookie
(`__Host-mintflow_csrf`) set alongside the session cookie at login. The Web Client reads that
cookie with JavaScript and echoes it back on every unsafe request as the `X-CSRF-Token` header.
The server never persists the token: it is stateless, so it rotates automatically whenever the
session secret rotates (new login) and is automatically invalid whenever the session it was
derived from is (the session-authentication check that must run before CSRF validation already
rejects revoked, expired, and deactivated-user sessions).

(Superseded by the approval recorded above: before that approval, the contract was unresolved and
this task could not become `ready` or be implemented autonomously.)

## In scope

- Implement the human-approved CSRF token generation and session binding.
- Expose the token to the browser only through the approved integration contract.
- Validate it on authenticated unsafe HTTP methods before invoking application use cases.
- Validate request Origin against the configured approved Web origin for sensitive actions.
- Invalidate or make tokens unusable after logout, session revocation, session expiry, or account
  deactivation.
- Provide a narrow reusable dependency for authenticated browser routes.

## Out of scope

- Choosing the browser integration contract.
- Pre-authentication/login-CSRF behavior from AUTH-11.
- CAPTCHA or bot protection.
- Cross-origin public APIs.
- Telegram webhook authentication.
- Logout or other product endpoints except a minimal test-only protected handler if needed.
- A general security middleware framework.

## Acceptance criteria

- Every protected unsafe request requires both an authenticated session and a valid token bound to
  that exact session.
- Missing, malformed, cross-session, stale, and invalid tokens fail before application use cases
  execute.
- Invalid or absent Origin fails closed for protected browser actions according to the approved
  contract; unapproved cross-origin requests fail.
- Safe methods do not perform state changes and are not accidentally made mutating by CSRF setup.
- SameSite remains configured but is not treated as the sole defense.
- Tokens cannot be reused after logout, revocation, expiry, or account deactivation.
- CSRF secrets are not exposed in logs, errors, URLs, analytics, or audit records.
- The implementation introduces no new server-side session lifetime or device-management state.

## Required tests

Unit tests must cover token generation/binding, validation, exact session matching, and invalidation
rules from the approved contract.

HTTP/security tests must cover:

- valid same-origin request with the correct session-bound token;
- missing and malformed token;
- token from another session;
- missing, malformed, and unapproved Origin;
- token use after logout/revocation/expiry/deactivation;
- safe-method behavior;
- application use case is not invoked after CSRF failure;
- token is absent from URLs, redirects, logs, errors, and audit evidence;
- cookie SameSite behavior remains defense in depth.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` are required wherever session
revocation, expiry, or deactivation persistence participates in CSRF validity. They must verify that
persisted session state remains authoritative.

## Required checks

Run:

- focused unit and HTTP/security tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set where required;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for CSRF bypass, Origin validation, cross-session use,
  token leakage, unsafe-method coverage, and scope creep.

## Completion conditions

Do not mark this task ready and do not implement it until the browser CSRF integration contract is
approved and recorded.

After approval, change `Status: ready` to `Status: review` only when every criterion and check
passes. If the contract is absent or ambiguous, leave status unchanged and report the exact human
decision required.
