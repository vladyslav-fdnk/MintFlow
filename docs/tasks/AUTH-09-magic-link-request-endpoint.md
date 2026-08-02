# AUTH-09 — Magic Link Request Endpoint

Status: review

## Goal

Expose the existing Magic Link issuance use case through a secure, non-enumerating HTTP POST
endpoint.

## Depends on

- AUTH-08

## Context

Read completely before implementation:

- AGENTS.md
- docs/authentication_design_proposal.md
- docs/authentication_persistence_design.md
- docs/tasks/AUTH-03-rate-limiting.md
- docs/tasks/AUTH-06-authentication-audit-persistence.md
- docs/tasks/AUTH-08-http-authentication-composition-boundary.md

The issuance use case already validates email, reserves PostgreSQL rate-limit capacity, persists a
LoginChallenge, invokes `EmailSender`, and returns a generic result. The HTTP boundary must not
reveal account existence, rate-limit state, delivery state, or internal failure categories.

## In scope

- Add a bounded, typed request DTO containing submitted email and an approved return-target
  identifier.
- Add the Magic Link request POST route.
- Supply the normalized direct-peer network source from AUTH-08.
- Invoke the existing request use case and local email adapter.
- Return one generic public status and body for syntactically valid, invalid, rate-limited, and
  expected email-delivery-failure cases.
- Emit minimal approved authentication audit evidence without sensitive input.

## Out of scope

- GET confirmation page or POST consumption.
- WebSession cookie issuance.
- CSRF for authenticated browser operations.
- Account lookup or existence-specific behavior.
- CAPTCHA, global distributed rate limiting, Redis, or production proxy configuration.
- Production email-provider behavior.
- Arbitrary redirect URLs.

## Acceptance criteria

- Known and unknown accounts cannot be distinguished through response status, body, headers, or
  explicit error category.
- Invalid email, email-limit rejection, network-limit rejection, and expected delivery failure map
  to the same public response as an accepted request.
- Only allowlisted return targets reach challenge persistence; absolute/external redirects are
  rejected safely.
- Network limiting occurs before email limiting, and email limiting occurs before challenge
  creation and delivery.
- Client-supplied forwarding headers cannot bypass or partition rate limits.
- Raw email and raw IP/network source are absent from rate-limit and audit records.
- Request bodies, email addresses, full Magic Links, tokens, token hashes, and delivery content are
  absent from routine and exception logs.
- Request size and field lengths are bounded.
- Expected provider failure does not refund a reserved email slot.

## Required tests

Unit tests must cover request mapping, bounded validation, generic result mapping, and approved
return-target handling.

HTTP/security tests must cover:

- accepted and invalid email submissions;
- known and previously unseen emails returning the same external response;
- exact email and network rate-limit thresholds;
- expected delivery failure;
- malformed/oversized input;
- absolute, scheme-relative, encoded, and otherwise unapproved redirect attempts;
- spoofed `Forwarded` and `X-Forwarded-For` headers;
- response equivalence across non-disclosing outcomes;
- absence of raw email, IP, token, full link, and request body from captured logs.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must verify endpoint-level
challenge persistence, both rate-limit dimensions, provider-failure accounting, audit evidence,
and absence of raw email/IP in operational persistence.

## Required checks

Run:

- focused unit and HTTP/security tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for enumeration, open redirects, proxy spoofing, secret
  logging, rate-limit order, and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes. The
production trusted-proxy topology must remain unresolved until separately approved; do not trust
forwarded headers as a workaround.
