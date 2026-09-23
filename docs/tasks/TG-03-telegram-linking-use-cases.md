# TG-03 — Telegram Linking Use Cases

Status: done

## Goal

Implement the linking ceremony from authentication_design_proposal.md section 7 as application use cases.

## Depends on

- TG-02

## Context

Read completely before implementation:

- AGENTS.md
- docs/authentication_design_proposal.md (6, 7)
- docs/authentication_persistence_design.md (5.5–5.7)
- docs/tasks/TG-02-telegram-link-persistence.md

## In scope

- Issue a challenge for an authenticated Web session: 256-bit random token, SHA-256 stored, five-minute expiry, deep link `https://t.me/<bot_username>?start=<token>`.
- Claim from a verified private-chat `/start <token>` update.
- Challenge status for the initiating session only, with safe display metadata.
- Confirm by the same initiating, still-valid Web session.
- Unlink by an authenticated Web session.
- Resolve the active User for a Telegram user id, used by every bot action.
- Audit evidence for claim, link, and unlink.

## Out of scope

- HTTP endpoints and bot handlers.
- Transfer or recovery of a connection held by another account.

## Acceptance criteria

- Expired, reused, invalid, and conflicting attempts fail with one generic outcome that reveals no account data.
- A claim by a different person stays pending and expires; only the initiating session can see or confirm it.
- Unlinking immediately stops `resolve active User` for that Telegram user id.
- Raw tokens never reach logs, audit records, or errors.

## Required tests

Unit tests must cover every outcome with fakes. Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover the full ceremony, the wrong-claimer case, conflicts, unlink, and relink.

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
