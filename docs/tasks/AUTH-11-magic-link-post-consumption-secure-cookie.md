# AUTH-11 — Magic Link POST Consumption and Secure Cookie

Status: review

## Goal

Complete Magic Link login through POST and issue the approved opaque server-side session cookie
only after the authentication transaction commits.

## Depends on

- AUTH-06
- AUTH-08
- AUTH-10

## Context

Read completely before implementation:

- AGENTS.md
- docs/authentication_design_proposal.md
- docs/authentication_persistence_design.md
- docs/tasks/AUTH-04-magic-link-consumption.md
- docs/tasks/AUTH-05-web-sessions.md
- docs/tasks/AUTH-06-authentication-audit-persistence.md
- docs/tasks/AUTH-08-http-authentication-composition-boundary.md
- docs/tasks/AUTH-10-non-mutating-magic-link-confirmation-page.md

The existing application operation atomically consumes a LoginChallenge and creates a WebSession.
The HTTP adapter must consume only on POST, emit the raw session secret only after commit, and
redirect only to an approved internal destination.

### Human-approved login-CSRF contract

Human approval is recorded for the following contract:

- The non-mutating confirmation GET renders a first-party form carrying the Magic Link token.
- The POST consumption request must carry that Magic Link token from the first-party confirmation
  form.
- The POST must validate the `Origin` header strictly against the configured first-party
  authentication Web origin.
- Missing, malformed, opaque/`null`, mismatched, cross-origin, and otherwise unapproved `Origin`
  values fail closed.
- Forwarded headers do not influence `Origin` validation.
- No pre-authentication cookie, nonce cookie, temporary browser session, or other separate browser
  state is introduced.
- The Magic Link token is never an authenticated WebSession credential. Successful authentication
  creates a fresh opaque WebSession secret and issues only that fresh secret in the authenticated
  cookie.

Existing requirements for transaction ordering, secure cookie attributes, replay protection,
generic failures, approved return targets, secret-safe logging, and atomic persistence remain
unchanged.

## In scope

- Add the fixed Magic Link confirmation POST route.
- Apply the human-approved pre-authentication/login-CSRF defense exactly as approved.
- Invoke existing atomic Magic Link consumption and session creation.
- Emit the raw session secret in the production host-only cookie only after commit.
- Use an approved production cookie name with the `__Host-` prefix and exact attributes:
  `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, no `Domain`, and explicit 30-day maximum age.
- Do not introduce separate pre-authentication browser state, and never promote or reuse any
  existing browser credential as the authenticated session.
- Redirect only to the normalized return target stored on the consumed challenge.
- Render one non-sensitive failure result for invalid, expired, consumed, and unknown tokens.
- Apply no-store/no-referrer behavior and secret-safe logging.

## Out of scope

- Revisiting or expanding the approved pre-authentication/login-CSRF mechanism.
- Session-authentication dependency.
- Authenticated session-bound CSRF protection.
- Logout.
- Device management, sliding expiry, JWT, refresh tokens, or revoking other sessions.
- Arbitrary redirect URLs or client-selected absolute destinations.
- Web Client implementation.

## Acceptance criteria

- GET cannot consume or authenticate; only the fixed POST route may do so.
- Successful consumption, first-login identity creation where needed, WebSession creation, and
  login-success audit evidence commit atomically.
- The cookie is emitted only after a successful commit; any rollback emits no authenticated cookie.
- The cookie contains only the opaque raw session secret and has exactly the approved security
  attributes, including no `Domain`.
- PostgreSQL contains only the session-secret hash.
- Invalid, expired, reused, and unknown tokens produce the same non-sensitive failure result and no
  cookie.
- Concurrent/repeated POSTs for one challenge create exactly one authenticated session.
- An existing authenticated or pre-authentication cookie is never promoted; successful login uses
  a fresh secret.
- Ordinary login leaves other authenticated sessions active.
- Redirects cannot leave the approved internal destination set.
- Token, query/form credential, cookie value, session secret/hash, and full Magic Link are absent
  from logs, traces, errors, and audit records.
- The approved login-CSRF behavior is covered exactly and fails closed.

## Required tests

Unit tests must cover HTTP-result mapping, cookie construction, approved login-CSRF validation, and
return-target behavior.

HTTP/security tests must cover:

- first login and returning login;
- invalid, expired, reused, and unknown tokens with equivalent failure responses;
- GET non-consumption and POST consumption;
- every exact cookie attribute and absence of `Domain`;
- no cookie after application or persistence failure;
- replacement/non-promotion of existing browser state;
- approved and rejected Origin/login-CSRF cases;
- open-redirect attempts;
- no-store/no-referrer behavior;
- absence of token, cookie, session secret/hash, and full URL from captured logs.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover:

- endpoint-level first and returning login;
- exact expiry boundary;
- concurrent POST consumption using separate connections and synchronization, proving one session
  and one success audit record;
- rollback of challenge, User/EmailIdentity, WebSession, and audit changes when the transaction
  fails;
- ordinary login preserving other sessions;
- deactivated User refusal.

## Required checks

Run:

- focused unit and HTTP/security tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for login CSRF, transaction/cookie ordering, fixation,
  replay, enumeration, open redirects, secret leakage, and scope creep.

## Completion conditions

The human approval gate is recorded above. Change `Status: ready` to `Status: review` only after the
implementation satisfies all criteria and checks.
