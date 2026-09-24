# WEB-10 — Theme Switch

Status: done

## Goal

Let the user switch between light and dark from the sidebar.

## Depends on

- WEB-08

## Context

Read completely before implementation:

- AGENTS.md
- docs/web_client_design.md (W13, W5, W6)

## In scope

- A `role="switch"` button in the sidebar (and the tab bar on phones).
- A `mintflow_theme` cookie (`light` or `dark`); without it the system setting applies.
- The server sets `data-theme` on `<html>` from the cookie; the stylesheet honours it over the
  media query.

## Out of scope

- Per-account storage of the theme.

## Acceptance criteria

- No flash of the wrong theme on load; works with the keyboard; announced as a switch with its
  state.
- Both themes keep AA contrast (the palette test covers them).

## Required tests

Unit tests for reading the cookie into `data-theme` and the switch markup; the palette test runs
for both themes.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- screenshots of the changed pages (wide and phone, light and dark) reviewed before handing over;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for inline styles or scripts, contrast, keyboard access,
  and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
