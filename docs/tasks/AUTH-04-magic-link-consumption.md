# AUTH-04 — Magic Link Consumption

Status: done

## Goal

Implement secure POST consumption of an existing LoginChallenge.

## Depends on

AUTH-03

## Read first

- AGENTS.md
- docs/authentication_design_proposal.md
- docs/authentication_persistence_design.md

## Scope

Implement:

- lookup by SHA-256 token hash;
- exact expiration behavior;
- atomic single-use consumption;
- first-login User + EmailIdentity creation;
- returning-login User resolution;
- generic invalid/expired/reused result;
- concurrent consumption protection.

Do not create WebSession yet.

Return an authenticated application result that a later WebSession task
can consume.

## Important behavior

GET must never consume a LoginChallenge.

POST consumption only.

Validity:

now < expires_at

At:

now == expires_at

the challenge is expired.

Consumption must be atomic in PostgreSQL.

Concurrent consumption of the same LoginChallenge must produce exactly
one successful result.

## First registration

When the canonical email has no EmailIdentity:

atomically:

- consume LoginChallenge;
- create User;
- create EmailIdentity.

Prevent duplicate Users during concurrent first login.

Respect the approved canonical-email uniqueness.

## Returning login

When EmailIdentity exists:

- consume LoginChallenge;
- resolve User;
- require User active;
- return authenticated User identity.

Do not modify EmailIdentity.

Do not create another User.

## Out of scope

Do not implement:

- WebSession;
- cookies;
- CSRF;
- GET confirmation page;
- HTTP route;
- Telegram;
- account recovery;
- email change;
- audit record;
- session revocation.

## Tests

Include real PostgreSQL concurrency tests for:

- same challenge consumed simultaneously;
- two valid challenges for same previously unseen email;
- one User and one EmailIdentity created;
- reused challenge;
- exact expiry boundary;
- deactivated User;
- returning login.

## Required checks

Run full project checks, PostgreSQL integration tests,
Alembic validation if schema changes, git diff --check,
and final self-review.

## Completion

Do not commit.

Change Status to review only when completely green.
