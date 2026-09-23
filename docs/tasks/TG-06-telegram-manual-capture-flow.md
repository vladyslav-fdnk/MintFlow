# TG-06 — Telegram Manual Capture Flow

Status: blocked

## Goal

Let a linked user create, review, edit, confirm, and cancel a manual expense in Telegram.

## Depends on

- TG-05

## Context

Read completely before implementation:

- AGENTS.md
- docs/telegram_client_design.md (T5, T6, T7)
- docs/mvp_definition.md (section 5)
- docs/tasks/CAPTURE-08-confirm-draft-use-case.md

## In scope

- `telegram_conversations` table and repository (T5).
- `/add` and the "Add expense" button start a `TELEGRAM_MANUAL` draft with defaulted currency and date.
- Prompts for amount, merchant (skippable), and category; amount parsing per T6.
- The review card with per-field edit buttons, Confirm, and Cancel, edited in place.
- Confirmation through `ConfirmCaptureDraft`, with the success message after commit.
- `/cancel` and the Cancel button.

## Out of scope

- Draft conflicts and history (TG-07).
- Receipt input.

## Acceptance criteria

- Every draft shows merchant, date, currency, amount, and category, with defaulted values marked.
- Zero, negative, and over-precise amounts are rejected with guidance and change nothing.
- Buttons on a confirmed, cancelled, or expired draft change nothing.
- Repeated Confirm taps create exactly one Expense, and the bot never reports success before commit.
- A Telegram-confirmed Expense appears in `GET /capture/expenses` and the dashboard.

## Required tests

Unit tests must cover amount and date parsing and every flow step with fakes. Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover a full flow through the webhook to a confirmed Expense visible on the Web routes, and concurrent duplicate Confirm taps.

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
