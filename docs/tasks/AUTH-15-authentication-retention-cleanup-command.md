# AUTH-15 — Authentication Retention Cleanup Command

Status: done

## Goal

Implement bounded, idempotent cleanup operations and an invocable command for authentication data
whose approved retention period has elapsed.

## Depends on

- AUTH-06

## Context

Read completely before implementation:

- AGENTS.md
- docs/authentication_design_proposal.md
- docs/authentication_persistence_design.md
- docs/tasks/AUTH-03-rate-limiting.md
- docs/tasks/AUTH-05-web-sessions.md
- docs/tasks/AUTH-06-authentication-audit-persistence.md

The approved retention policy deletes consumed LoginChallenges 30 days after consumption,
unconsumed LoginChallenges 30 days after expiry, revoked WebSessions 30 days after revocation,
expired WebSessions 30 days after expiry, rate-limit buckets at their configured expiry and never
after 24 hours, and AuthenticationAuditRecords after 90 days. Cleanup must be safe under repeated or
concurrent execution and must not delete User, EmailIdentity, or financial data.

## In scope

- Define deterministic UTC cutoff calculations with an injected clock.
- Add bounded PostgreSQL deletion operations for:
  - consumed and expired/unconsumed LoginChallenges;
  - revoked and expired WebSessions;
  - expired AuthenticationRateLimitBuckets;
  - expired AuthenticationAuditRecords.
- Provide an application service and small CLI/command entry point suitable for invocation by an
  external scheduler.
- Make execution idempotent, bounded by configurable batch size, and safe under concurrent runs.
- Report only aggregate counts and non-sensitive operational outcomes.
- Use existing retention indexes or add a new forward migration only if PostgreSQL query evidence
  shows an approved record lacks a necessary cleanup index.

## Out of scope

- Adding cron, a scheduler service, worker framework, daemon, or deployment automation.
- TelegramConnection or TelegramLinkChallenge cleanup.
- User, EmailIdentity, account, Expense, CaptureDraft, Receipt, RecognitionResult, object-storage,
  or backup deletion.
- Changing approved retention periods or increasing sensitive-data retention.
- Account hard deletion or anonymization.
- Modifying historical migrations.

## Acceptance criteria

- Eligibility follows the exact approved boundaries and uses timezone-aware UTC values.
- Records exactly at the deletion cutoff are handled consistently and documented by tests.
- Each database operation deletes no more than the configured positive batch size.
- Repeated execution is idempotent; concurrent execution is safe and does not fail or over-report
  in a way that affects correctness.
- Cleanup cannot cascade to or otherwise delete User, EmailIdentity, or any financial ownership
  data.
- Rate-limit buckets are removable at `expires_at` and cannot be retained by this command beyond
  their approved maximum.
- Command output and logs contain aggregate counts only, with no email, IP, token/hash, session
  secret/hash, cookie, full link, or subject identifiers.
- A failure in one transaction does not report uncommitted deletions as successful.
- No scheduling infrastructure is introduced.

## Required tests

Unit tests must cover:

- every retention cutoff calculation;
- before, exactly at, and after each boundary;
- timezone normalization;
- valid and invalid batch sizes;
- aggregate result reporting without sensitive values.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover:

- eligible and ineligible consumed LoginChallenges;
- eligible and ineligible unconsumed/expired LoginChallenges;
- eligible and ineligible revoked and expired WebSessions;
- active WebSessions remaining untouched;
- expired and live rate-limit buckets;
- expired and live AuthenticationAuditRecords;
- exact cutoff boundaries;
- bounded batches across multiple invocations;
- idempotent repeated execution;
- concurrent cleanup using separate connections and explicit synchronization;
- foreign-key/cascade assurance that User and EmailIdentity rows remain untouched;
- transaction rollback and accurate committed counts;
- migration lifecycle and metadata drift only if a new migration is justified.

## Required checks

Run:

- focused unit tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- direct command smoke test against the dedicated test database;
- full `make check` with PostgreSQL integration enabled;
- Alembic upgrade, downgrade, re-upgrade, current, and check if a migration is added;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for retention boundaries, destructive scope, boundedness,
  concurrency, secret leakage, migration necessity, and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes. If cleanup
would require scheduling infrastructure or a change to approved retention policy, leave status
unchanged and report the exact approval required.
