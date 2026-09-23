# TG-09 — Telegram Local Long-Polling Command

Status: done

## Goal

Let developers run the bot locally without a public webhook address.

## Depends on

- TG-05

## Context

Read completely before implementation:

- AGENTS.md
- docs/telegram_client_design.md (T2)
- docs/tasks/TG-05-telegram-webhook-and-dispatcher.md

## In scope

- A command that deletes any webhook, then loops on `getUpdates` with an offset and passes each update to the TG-05 handler; it refuses to run outside development and test environments.

## Out of scope

- Production use.

## Acceptance criteria

- Updates are handled by exactly the same code path as the webhook, including deduplication.
- The command stops cleanly on interrupt.

## Required tests

Unit tests must cover the offset loop and the environment guard with the fake client.

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
