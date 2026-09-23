# TG-05 — Telegram Webhook, Deduplication, and Dispatcher

Status: blocked

## Goal

Receive Telegram updates safely and route them to handlers, starting with `/start`, help, and the unlinked refusal.

## Depends on

- TG-01
- TG-03

## Context

Read completely before implementation:

- AGENTS.md
- docs/telegram_client_design.md (T2, T4, T8)
- docs/mvp_definition.md (section 5)
- docs/tasks/TG-01-telegram-bot-api-client.md

## In scope

- `POST /telegram/webhook` verifying the secret header in constant time before parsing.
- `telegram_processed_updates` table; each update's work and its `update_id` commit together; redeliveries are acknowledged without repeating work; replies are sent after commit and send failures are logged without content.
- A dispatcher that ignores non-private chats and unsupported updates.
- `/start` without a payload: what MintFlow is and how to link from Web settings.
- `/start <token>`: claim through TG-03 and reply with a generic outcome; the "connected" message is sent only after Web confirmation, by TG-04.
- `/help`: supported inputs, how confirmation works, and the Web link.
- Any other input from an unlinked user: a refusal that points to linking and stores nothing.
- A messages module holding all user-facing text (T8).

## Out of scope

- Manual capture (TG-06).
- Long polling (TG-09).

## Acceptance criteria

- A wrong or missing secret returns 401 and touches nothing.
- The same `update_id` delivered twice does its work once.
- Group, supergroup, and channel updates are ignored and never claim a challenge.
- Unlinked users can never create a draft or see account data.

## Required tests

Unit tests must cover the dispatcher and messages. HTTP/security tests must cover secret verification and non-private chats. Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover deduplication under concurrent redelivery and a claim through the webhook.

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
