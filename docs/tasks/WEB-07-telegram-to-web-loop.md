# WEB-07 — Telegram-to-Web Loop and Accessibility Checklist

Status: ready

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

## Completion conditions

Do not commit.

Change `Status: blocked` to `Status: ready` only after the design is approved and every
dependency is `done`, then implement, then change `Status: ready` to `Status: review` only after
every criterion and check passes.
