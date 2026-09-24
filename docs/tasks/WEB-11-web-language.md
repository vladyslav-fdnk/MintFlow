# WEB-11 — Web Interface in English and Russian

Status: done

## Goal

Offer the Web Client in Russian as well as English.

## Depends on

- WEB-09
- WEB-10

## Context

Read completely before implementation:

- AGENTS.md
- docs/web_client_design.md (W14, W5, W6)

## In scope

- Babel extraction from templates and Python; `ru` catalogue; compiled catalogues shipped with the
  package.
- The language comes from `ui_language`; a choice in Settings and a quick switch in the sidebar.
- Formatting follows `locale`, else the interface language.

## Out of scope

- The Telegram bot, and languages other than English and Russian.

## Acceptance criteria

- Every user-visible Web string is translated; switching takes effect on the next page.
- Plural forms follow Russian rules.

## Required tests

Unit tests that every catalogue entry is translated, plural forms, and switching; integration
tests that pages render in Russian.

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
