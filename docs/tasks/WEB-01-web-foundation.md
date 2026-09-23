# WEB-01 — Web Foundation

Status: ready

## Goal

Add the server-rendered Web layer every page builds on.

## Depends on

- Human approval of docs/web_client_design.md

## Context

Read completely before implementation:

- AGENTS.md
- docs/web_client_design.md (W1, W3, W5, W6, W7)
- docs/tasks/AUTH-12-authenticated-web-session-dependency.md
- docs/tasks/AUTH-13-session-bound-csrf-protection.md

## In scope

- Dependencies `jinja2`, `python-multipart`, and `babel`, added with approval recorded in this
  sprint.
- A `mintflow.web` package with a router, a Jinja2 environment, a `_()` string function, and Babel
  formatting helpers for dates and money (ISO code always shown).
- A base layout: header with navigation (Dashboard, Expenses, Settings, Sign out), main landmark,
  skip link, `aria-live` status region, responsive CSS from 320 pixels.
- Static files: CSS, vendored and pinned htmx with its licence, and one script that sends the CSRF
  cookie as `X-CSRF-Token` on htmx requests.
- A page-authentication dependency that redirects to `/sign-in` with `303`, and generic 404 and
  500 pages.
- The security headers of W5 on every Web response.

## Out of scope

- Any product page content beyond a placeholder dashboard route used by tests.

## Acceptance criteria

- An unauthenticated page request redirects to `/sign-in`; JSON endpoints still answer `401`.
- Every Web response carries the CSP and the other W5 headers; no template contains an inline
  script or style.
- An htmx request carries the CSRF header; a mutation without it is rejected by the existing
  dependency.
- Money is formatted per locale with its currency code; dates per locale.

## Required tests

Unit tests must cover the formatting helpers (locales, currencies with 0 and 3 decimals) and the
layout's landmarks and labels. Integration tests must cover the redirect, the headers, and CSRF on
a test mutation route.

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
