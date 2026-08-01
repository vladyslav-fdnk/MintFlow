# AUTH-03 — PostgreSQL Authentication Rate Limiting

Status: ready

## Goal

Implement the approved PostgreSQL-backed authentication rate limiter
required before Magic Link issuance can be exposed publicly.

## Read first

- AGENTS.md
- docs/authentication_design_proposal.md
- docs/authentication_persistence_design.md

Inspect the current LoginChallenge and Magic Link issuance implementation
before making changes.

## Approved behavior

Use PostgreSQL.

Do not introduce Redis.

Initial limits:

- maximum 3 reserved email delivery attempts per canonical email per 15 minutes;
- maximum 30 login requests per network source per 15 minutes.

Provider failure still consumes an already reserved email-delivery slot.

Public authentication responses remain generic.

## Persistence

Implement AuthenticationRateLimitBucket.

Conceptual fields:

- dimension
- key_digest
- window_started_at
- count
- expires_at

Supported dimensions:

- email_delivery
- network_request

The unique bucket identity is conceptually:

dimension + key_digest + window_started_at

Use a keyed privacy-preserving digest for:

- canonical email;
- normalized network source.

Do not store raw email or raw IP address in rate-limit records.

The secret key must come from application configuration.

Do not hardcode it.

## Concurrency

Rate-limit enforcement must be atomic in PostgreSQL.

Do not implement:

check count
then update

as two independent operations.

Use PostgreSQL behavior that correctly handles concurrent requests.

Exactly one operation should determine the new count and whether the
request remains under the threshold.

## Windows

Use fixed 15-minute UTC windows.

Rate-limit records may be retained for no more than 24 hours.

Cleanup execution itself may be postponed if it requires scheduling
infrastructure, but retention eligibility must be modeled clearly.

## Application integration

Integrate the limiter into the existing Magic Link issuance use case.

Required order:

1. Normalize/validate request.
2. Reserve/check network request limit.
3. Reserve/check email delivery limit.
4. If allowed, create LoginChallenge.
5. Persist challenge.
6. Attempt email delivery.

Provider failure does not refund the reserved email slot.

Do not expose an HTTP endpoint yet.

## Security behavior

Rate-limit rejection must map to the same generic public
MagicLinkRequestResult used by successful requests.

Do not expose:

- whether the email exists;
- which rate-limit dimension rejected the request;
- current counts;
- reset timestamp.

Internal code/tests may distinguish outcomes as needed.

## Migration

Create one new migration for AuthenticationRateLimitBucket.

Do not modify historical migrations.

Migration must support:

- upgrade;
- downgrade;
- re-upgrade;
- Alembic metadata drift checks.

## Tests

Unit tests:

- fixed-window calculation;
- privacy digest behavior;
- email threshold behavior;
- network threshold behavior;
- generic external response;
- provider failure does not refund email slot.

Real PostgreSQL integration tests:

- bucket creation;
- atomic increment;
- exact threshold;
- rejection after threshold;
- independent dimensions;
- independent keys;
- independent windows;
- concurrency at threshold;
- expiry metadata;
- raw email/IP never persisted;
- migration lifecycle.

Concurrency tests must use real separate PostgreSQL connections.

Sequential calls must not be described as concurrency tests.

## Out of scope

Do not implement:

- Redis;
- global distributed rate limiting;
- CAPTCHA;
- HTTP authentication route;
- Magic Link consumption;
- WebSession;
- Telegram linking;
- audit records;
- scheduled worker infrastructure;
- production proxy configuration.

## Required checks

Run:

- focused unit tests;
- focused PostgreSQL integration tests;
- make check with PostgreSQL integration enabled;
- Alembic upgrade;
- downgrade;
- re-upgrade;
- alembic current;
- alembic check;
- git diff --check;
- git status --short.

Perform a final self-review.

Fix valid review findings and re-run affected checks.

## Completion

Do not commit.

If all acceptance criteria pass:

Change:

Status: ready

to:

Status: review

Then stop.

If blocked by a material decision:

leave Status unchanged and report the blocker.
