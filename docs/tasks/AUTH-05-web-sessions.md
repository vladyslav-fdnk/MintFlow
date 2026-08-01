# AUTH-05 — Server-Side Web Sessions

Status: review

## Goal

Implement the approved opaque, revocable server-side WebSession.

## Depends on

AUTH-04

## Read first

- AGENTS.md
- docs/authentication_design_proposal.md
- docs/authentication_persistence_design.md

## Approved behavior

- opaque random session secret;
- at least 256 bits entropy;
- SHA-256 hash persisted;
- absolute 30-day expiry;
- no sliding expiration;
- ordinary login does not revoke other sessions;
- logout revokes current session;
- deactivation revokes every session;
- revoked or expired sessions do not authenticate.

Production cookie requirements:

- __Host- prefix;
- HttpOnly;
- Secure;
- SameSite=Lax;
- Path=/;
- no Domain.

## Scope

Implement:

- WebSession persistence;
- migration;
- token generation/hash;
- session creation after successful Magic Link consumption;
- session lookup/authentication boundary;
- session revocation;
- account-wide session revocation.

HTTP cookie wiring may be included only if required by approved architecture
and remains narrow.

## Out of scope

Do not implement:

- device management UI;
- sliding expiration;
- refresh tokens;
- JWT;
- native mobile auth;
- Telegram linking;
- MFA;
- social login.

## Tests

Include:

- session secret not persisted raw;
- creation;
- expiry;
- exact expiry boundary;
- revocation;
- account-wide revocation;
- multiple simultaneous valid sessions;
- ordinary login does not revoke others;
- deactivated User never authenticates;
- cookie security flags if HTTP boundary is introduced.

## Required checks

Run focused tests, PostgreSQL tests, make check,
Alembic lifecycle/check if schema changes,
git diff --check, and final self-review.

## Completion

Do not commit.

Change Status to review only after everything passes.
