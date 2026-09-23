# WEB-06 — Settings Page

Status: done

## Goal

Let a user set their conventions and manage the Telegram connection.

## Depends on

- WEB-01

## Context

Read completely before implementation:

- AGENTS.md
- docs/web_client_design.md (W8, W9)
- docs/telegram_client_design.md
- docs/tasks/CAPTURE-07-user-capture-preferences.md

## In scope

- An `UpdatePreferences` use case over `User.update_preferences` and the existing repository
  method.
- `GET /settings` and `POST /settings/preferences`: locale, timezone, and default currency, with
  the note that currencies are never converted.
- Telegram status; "Connect Telegram" creates a link challenge and shows the deep link;
  "Disconnect" uses the existing unlink use case after a confirmation step.

## Out of scope

- Account deletion, notification settings, and data export.

## Acceptance criteria

- Invalid values change nothing and show specific errors.
- A changed timezone changes the dashboard's default period on the next load.
- Disconnecting stops the bot from exposing account data, as in TG.

## Required tests

Unit tests must cover the use case. Integration tests must cover each preference, invalid
combinations, linking and disconnecting, CSRF, and ownership.

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
