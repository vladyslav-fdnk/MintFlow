# WEB-02 — Sign-in and Sign-out Pages

Status: done

## Goal

Let a user sign in from the Web with a magic link and sign out.

## Depends on

- WEB-01

## Context

Read completely before implementation:

- AGENTS.md
- docs/web_client_design.md (W2, W3)
- docs/authentication_design_proposal.md
- docs/tasks/AUTH-09-magic-link-request-endpoint.md

## In scope

- `GET /sign-in`: an email form. `POST /sign-in`: calls `RequestMagicLink` with return target
  `dashboard`, under the same rate limits and approved-Origin check as the JSON endpoint, and
  always shows the same "check your email" page.
- `/` redirects to `/dashboard` when signed in and to `/sign-in` otherwise; a signed-in visit to
  `/sign-in` goes to the dashboard.
- Sign out posts to `/auth/logout` through htmx and then shows `/sign-in`.

## Out of scope

- Changes to the magic-link emails, the confirmation page, or session rules.

## Acceptance criteria

- The response never reveals whether an account exists, including under rate limiting.
- A malformed email gets a specific, accessible field error; nothing is sent.
- After sign-out, pages redirect to `/sign-in` and the old session cookie no longer works.

## Required tests

Integration tests with Mailpit disabled (a recording email sender) must cover known and unknown
emails, rate limiting, a wrong Origin, and the full sign-in, page visit, sign-out cycle.

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
