# AUTH-06 — Authentication Audit Persistence

Status: done

## Goal

Implement the approved minimal, append-only authentication audit record and integrate audit
evidence into the authentication operations that already exist.

## Depends on

- AUTH-05

## Context

Read completely before implementation:

- AGENTS.md
- docs/authentication_design_proposal.md
- docs/authentication_persistence_design.md
- docs/tasks/AUTH-04-magic-link-consumption.md
- docs/tasks/AUTH-05-web-sessions.md

The approved persistence design requires successful Magic Link consumption, WebSession creation,
and login-success audit evidence to commit atomically. Audit data is operational evidence only. It
must never become authentication state, a generic event store, or a source of authorization truth.

## In scope

- Add `AuthenticationAuditRecord` to the SQLAlchemy metadata.
- Add one new reversible Alembic migration; do not modify historical migrations.
- Define a closed, strongly typed event-type and outcome vocabulary covering only currently
  implemented authentication operations:
  - login challenge requested;
  - login succeeded or failed;
  - current session revoked;
  - all sessions revoked.
- Store only the approved minimal fields: ID, UTC occurrence time, constrained event type,
  constrained outcome, optional User ID, and optional non-secret subject record ID.
- Add an application port and PostgreSQL adapter for appending audit evidence.
- Append login-success evidence in the same transaction as challenge consumption and WebSession
  creation.
- Append appropriate evidence for Magic Link requests, failed consumption, current-session
  revocation, and account-wide session revocation without weakening their generic public results.
- Model 90-day cleanup eligibility for later use by AUTH-15.
- Keep User foreign keys restrictive and audit records append-only through application interfaces.

## Out of scope

- HTTP routes or cookies.
- Telegram audit events.
- Account-deactivation orchestration.
- Expense edit or deletion audit history.
- Generic JSON payloads, arbitrary metadata, event sourcing, audit export, or an outbox.
- A scheduled cleanup runner or deletion execution.
- Raw or canonical email, raw network addresses, request bodies, browser/user-agent history, or
  full redirect URLs.
- Changes to existing historical migrations.

## Acceptance criteria

- A successful login cannot commit its consumed LoginChallenge and WebSession without its
  login-success audit record.
- A rollback of audit insertion rolls back challenge consumption, first-login User/EmailIdentity
  creation where applicable, and WebSession creation.
- Audit records never contain raw tokens, token hashes, session secrets or hashes, cookie values,
  full Magic Links, email content, raw/canonical email, arbitrary request bodies, or full redirect
  URLs.
- Event types and outcomes are database constrained and represented by strong application types.
- `user_id` is nullable for pre-identity/failure evidence and uses a restrictive foreign key when
  present.
- Deleting audit records cannot cascade to User, EmailIdentity, WebSession, or future financial
  data.
- Audit records are never queried to authenticate or authorize a request.
- Existing generic Magic Link request and consumption results remain non-disclosing.
- A clear, indexed 90-day retention boundary can be selected by AUTH-15.
- Alembic metadata has no drift after upgrade.

## Required tests

Unit tests must cover:

- allowed and rejected event/outcome values;
- mapping existing authentication outcomes to minimal audit evidence;
- omission of forbidden sensitive fields;
- 90-day retention-cutoff calculation and exact boundary behavior.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover:

- migration upgrade, downgrade, and re-upgrade;
- required fields and constrained event/outcome values;
- nullable and valid User correlation;
- restrictive User foreign-key behavior;
- append behavior and immutable application interface;
- atomic login-success, challenge-consumption, and WebSession creation;
- rollback of the complete login transaction when audit insertion fails;
- concurrent Magic Link consumption still producing exactly one successful session and one
  login-success record;
- session-revocation and all-session-revocation audit evidence;
- retention eligibility at, before, and after the exact cutoff;
- assurance that forbidden secrets and identifiers are not persisted.

Concurrency tests must use separate PostgreSQL connections and explicit synchronization.

## Required checks

Run:

- focused unit tests;
- focused PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- Alembic upgrade, downgrade, re-upgrade, current, and check;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for security, transactionality, retention, schema drift,
  secret leakage, unnecessary abstraction, and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every acceptance criterion and required check
passes. If a material decision is missing, leave the status unchanged and report the exact human
approval required.
