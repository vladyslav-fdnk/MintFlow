# TG-01 — Telegram Settings and Bot API Client

Status: done

## Goal

Give the application a typed, testable way to talk to the Telegram Bot API and to read the updates it sends.

## Depends on

- Human approval of docs/telegram_client_design.md

## Context

Read completely before implementation:

- AGENTS.md
- docs/telegram_client_design.md (T1, T2, T3)
- src/mintflow/config.py

## In scope

- Optional settings `telegram_bot_token`, `telegram_bot_username`, `telegram_webhook_secret` (secrets as `SecretStr`), valid only all together; absent means Telegram is disabled.
- Move `httpx` from the development group to runtime dependencies.
- A Bot API client protocol with `send_message`, `edit_message_text`, `answer_callback_query`, `set_webhook`, and `get_updates`, and an `httpx` implementation with timeouts that never logs the token or message text.
- Pydantic models for the update subset MintFlow reads: message text, `/start` payload, callback queries, sender id, chat id and chat type; everything else parses as an ignorable update.
- An in-memory fake client for tests that records calls.

## Out of scope

- Any handler, endpoint, or persistence.
- Real network calls in tests.

## Acceptance criteria

- Partial Telegram configuration fails validation with a message that contains no secret.
- Updates from non-private chats are identified as such by the models.
- Unknown update kinds parse without error and are marked ignorable.
- The bot token never appears in logs, exceptions, or `repr`.

## Required tests

Unit tests must cover settings validation, update parsing for every supported and several unsupported update kinds, and request construction of the `httpx` client against a mock transport.

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
