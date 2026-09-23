# TG-02 — Telegram Connection and Link Challenge Persistence

Status: ready

## Goal

Persist Telegram connections and link challenges exactly as the approved persistence design specifies.

## Depends on

- Human approval of docs/telegram_client_design.md

## Context

Read completely before implementation:

- AGENTS.md
- docs/authentication_persistence_design.md (2.5, 2.6, 3, 5.5–5.7)
- docs/telegram_client_design.md
- docs/tasks/AUTH-05-web-sessions.md

## In scope

- Migration: `telegram_connections` and `telegram_link_challenges` with every constraint in the persistence design, including both partial unique indexes and the claim/confirmation checks.
- Repositories for: issuing a challenge; atomically claiming it; atomically confirming it and inserting the active connection; reading challenge status for its initiating session; reading the active connection by user and by Telegram user id; unlinking.
- Authentication audit event types for Telegram claim, link, and unlink (constraint updated in the migration).

## Out of scope

- Use cases, HTTP, and the bot.
- Retention deletion (TG-08).

## Acceptance criteria

- A Telegram user id can be active for at most one User, and a User can have at most one active connection, enforced by the database.
- Concurrent claims of one challenge produce exactly one winner; concurrent confirmations produce exactly one connection.
- Confirmation fails atomically on either uniqueness conflict and leaves no partial state.
- `alembic upgrade`, `downgrade -1`, `upgrade`, and `alembic check` pass.

## Required tests

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover every constraint, concurrent claims and confirmations on separate synchronized connections, conflict rollback, unlink, and relinking after unlink.

## Required checks

Run:

- focused unit, HTTP, and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for identity and ownership bypass, secret leakage,
  duplicate processing, and scope creep.

## Completion conditions

Do not commit.

Change `Status: blocked` to `Status: ready` only after the design is approved and every
dependency is `done`, then implement, then change `Status: ready` to `Status: review` only after
every criterion and check passes.
