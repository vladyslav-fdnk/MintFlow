# TG-04 — Telegram Linking Web Endpoints

Status: ready

## Goal

Expose the linking use cases to the Web Client.

## Depends on

- TG-01
- TG-03

## Context

Read completely before implementation:

- AGENTS.md
- docs/telegram_client_design.md (T10)
- docs/tasks/TG-01-telegram-bot-api-client.md
- docs/tasks/TG-03-telegram-linking-use-cases.md
- docs/tasks/AUTH-13-session-bound-csrf-protection.md

## In scope

- `POST /telegram/link-challenges` (CSRF) returning the challenge id, deep link, and expiry.
- `GET /telegram/link-challenges/{id}` returning status and safe metadata to the initiating session only.
- `POST /telegram/link-challenges/{id}/confirm` (CSRF). After the connection commits, the bot sends the linked Telegram user a short "connected" message through the TG-01 client; a send failure is logged and does not undo the link.
- `GET /telegram/connection` and `DELETE /telegram/connection` (CSRF).
- All routes return 404 when Telegram is not configured.

## Out of scope

- Frontend.
- Bot handlers.

## Acceptance criteria

- Other sessions, including other sessions of the same User, get the same response as for an unknown challenge.
- CSRF or Origin failure never invokes a use case.
- Responses never contain the raw token after creation, and never contain another account's data.

## Required tests

HTTP/security tests must cover every route, CSRF failures with recording stand-ins, foreign-session access, and disabled configuration. Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover the full ceremony through the routes with a simulated bot claim.

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
