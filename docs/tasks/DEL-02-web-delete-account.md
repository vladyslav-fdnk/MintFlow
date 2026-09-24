# DEL-02 — Web: Delete Account Page and Flow

Status: blocked

## Goal

Web: Delete Account Page and Flow.

## Depends on

- Human approval of docs/account_deletion_design.md
- DEL-01

## Context

Read completely before implementation:

- AGENTS.md
- docs/account_deletion_design.md (A3, A4)
- docs/authentication_persistence_design.md

## In scope

- A "Delete account" section in Settings linking to `/settings/delete-account`.
- The confirmation page: what is deleted, that it cannot be undone, that backups keep data for up to
  30 days, an email field, and a destructive button; CSRF-protected POST for a live session only.
- A typed address that does not match the account's email after normalization shows a field error
  and deletes nothing.
- On success the session and CSRF cookies are cleared and the browser lands on a public "Your
  account has been deleted" page with a link to sign in.
- English and Russian text; screenshots reviewed in both themes and on a phone.

## Out of scope

- Deleting through the bot or support.

## Acceptance criteria

- Only the signed-in owner can delete, and only with the matching email and a valid CSRF token.
- After deletion the old session cookie no longer works, and signing in with the same email creates
  a new, empty account.
- The pages meet the existing accessibility and CSP rules.

## Required tests

HTTP integration tests for mismatch, CSRF failure, success, cookie clearing, the old session
rejected afterwards, and signing in again; template tests for both languages.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for authorization, CSRF, cookie handling, accessibility, and copy accuracy.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
