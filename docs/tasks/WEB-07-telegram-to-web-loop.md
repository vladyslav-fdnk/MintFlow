# WEB-07 — Telegram-to-Web Loop and Accessibility Checklist

Status: done

## Goal

Prove the product loop end to end and record the manual accessibility checks.

## Depends on

- WEB-02
- WEB-03
- WEB-04
- WEB-05
- WEB-06

## Context

Read completely before implementation:

- AGENTS.md
- docs/web_client_design.md (W10)
- docs/mvp_definition.md, sections 2 and 11

## In scope

- An integration test: link Telegram from the Web, confirm an expense through the webhook, see it
  in Web history and on the dashboard, edit it on the Web, and see the dashboard change.
- A checklist in docs covering keyboard-only use, visible focus, 320-pixel width, and screen-
  reader labels for every page, filled in by a manual run.

## Out of scope

- Browser automation and automated accessibility audits.

## Acceptance criteria

- The loop test passes on real PostgreSQL.
- Every page on the checklist is marked as checked, or its problem is fixed or recorded as a
  follow-up task.

## Required tests

A real PostgreSQL integration test for the loop above.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for ownership bypass, CSRF bypass, secrets or personal
  data in logs, inaccessible markup, and scope creep.

## Progress

- Done: `tests/integration/persistence/test_web_telegram_loop.py` covers settings, linking,
  capture in the bot, Web history and dashboard, a Web edit, and the bot's `/recent` agreeing.
- Done: `docs/web_accessibility_checklist.md`, with the automated coverage per page.
- Done: the manual run on 2026-09-24 by the product owner, all pages reported OK. It found two
  defects, fixed in separate commits: sign-in from the confirmation page (709a87d) and the
  Telegram card keeping its keyboard after Confirm with a local Web origin (090509e).

## Completion conditions

Do not commit.

Change `Status: blocked` to `Status: review` only after the design is approved and every
dependency is `done`, then implement, then change `Status: review` to `Status: review` only after
every criterion and check passes.
