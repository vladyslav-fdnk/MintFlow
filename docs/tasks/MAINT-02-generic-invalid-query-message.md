# MAINT-02 — Accurate Generic Message for Invalid Query Parameters

Status: done

## Goal

Stop `GET /capture/expenses` from calling invalid query parameters an invalid "request body".

## Depends on

- None

## Context

Read completely before implementation:

- AGENTS.md
- docs/tasks/EXPENSE-02-history-list-endpoint.md
- docs/tasks/DASH-04-dashboard-endpoint.md

EXPENSE-02 reused the capture routes' body message ("The request body is invalid.") for query
parameter errors. DASH-04 introduced "The request is invalid." for the same case on the
dashboard.

## In scope

- One shared generic message for invalid query parameters, used by both
  `GET /capture/expenses` and `GET /analytics/dashboard`.
- Body-validation errors on capture routes keep their existing message.

## Out of scope

- Any other change to status codes, messages, or parsing.

## Acceptance criteria

- Invalid history queries return 422 with "The request is invalid." and still do not echo input.
- The dashboard's message is unchanged, and both endpoints use the same constant.
- Malformed capture request bodies still return "The request body is invalid."

## Required checks

Run:

- focused HTTP tests;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
