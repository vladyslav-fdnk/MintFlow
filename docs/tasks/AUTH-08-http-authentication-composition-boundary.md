# AUTH-08 — HTTP Authentication Composition Boundary

Status: review

## Goal

Create the narrow FastAPI composition boundary needed to expose authentication use cases safely,
without adding user-facing authentication endpoints yet.

## Depends on

- AUTH-06
- AUTH-07

## Context

Read completely before implementation:

- AGENTS.md
- docs/authentication_design_proposal.md
- docs/authentication_persistence_design.md
- docs/tasks/AUTH-06-authentication-audit-persistence.md
- docs/tasks/AUTH-07-local-mailpit-email-adapter.md

The current application exposes only health routes. Authentication HTTP handlers need consistent
request-scoped database lifecycle, use-case construction, safe response behavior, and a network
source that cannot be spoofed through untrusted forwarding headers.

## In scope

- Add an authentication HTTP/router module and register it without adding public authentication
  operations.
- Define request-scoped database Session lifecycle and cleanup.
- Compose existing authentication repositories, rate limiter, audit adapter, email adapter, clock,
  token generator, link builder, and use cases from validated settings.
- Define a typed normalized network-source boundary for future request rate limiting.
- Use the direct request peer by default and ignore `Forwarded` and `X-Forwarded-For` unless a later
  approved trusted-proxy configuration explicitly enables them.
- Add narrow helpers for approved authentication security headers and generic HTTP outcomes where
  they eliminate duplication in AUTH-09 through AUTH-14.

## Out of scope

- Magic Link request, confirmation, consumption, authentication, CSRF, or logout routes.
- Trusting or configuring a production reverse proxy.
- Global middleware redesign or a generic dependency framework.
- Changes to authentication persistence schemas or migrations.
- Web Client implementation.

## Acceptance criteria

- Authentication infrastructure is composed at the application boundary, not imported into domain
  code.
- Every request-scoped database Session is closed after success, handled failure, or unexpected
  exception.
- Transaction ownership remains explicit; composition does not introduce hidden commits that break
  approved atomic operations.
- Untrusted forwarding headers cannot spoof the network source used by future rate limiting.
- Network-source normalization is deterministic and never logs or persists the raw source through
  this boundary.
- Configuration errors fail closed and do not expose secret values.
- No new user-facing authentication route is introduced.
- Existing health routes continue to work.

## Required tests

Unit tests must cover:

- dependency/use-case composition;
- direct-peer network-source normalization;
- malformed or missing peer information;
- ignored untrusted `Forwarded` and `X-Forwarded-For` values;
- configuration failure without secret disclosure.

HTTP tests must cover:

- request-scoped Session cleanup after success and exception;
- spoofed forwarding headers not changing the normalized source;
- existing health routes and route inventory;
- exception responses and captured logs containing no database URL, rate-limit key, token, cookie,
  or authorization header.

## Required checks

Run:

- focused unit and HTTP tests;
- full `make check`;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for dependency direction, transaction lifetime, proxy
  spoofing, secret leakage, unnecessary abstraction, and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after all criteria and checks pass. Production proxy
trust remains a deployment approval gate and must not be resolved in this task.
