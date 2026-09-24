# OPS-01 — Production Settings, SMTP Email, Trusted Proxy

Status: ready

## Goal

Production Settings, SMTP Email, Trusted Proxy.

## Depends on

- Human approval of docs/operations_design.md

## Context

Read completely before implementation:

- AGENTS.md
- docs/operations_design.md (O2, O3, O4)

## In scope

- `email_backend: "smtp"` with host, port, TLS mode (implicit TLS or STARTTLS), username, password,
  and sender; a generic SMTP sender used for Resend (`smtp.resend.com:465`). Mailpit stays for
  development.
- Startup validation for `environment=production`: API docs off, an `https://` Web origin, the SMTP
  backend, every Telegram setting, and a receipt recognizer other than `fake` (none is allowed:
  receipts then use the manual fallback until RCPT-07).
- The real client address behind Caddy: uvicorn proxy headers trusted only from a configured proxy
  address, so login throttling sees each user.
- `.env.example` documents every new variable.

## Out of scope

- The Compose file, Caddy, and deployment (OPS-02).

## Acceptance criteria

- The SMTP sender delivers a magic-link message over implicit TLS and STARTTLS against a local test
  server, with credentials, and maps every failure to the existing delivery error without logging
  the link or the full address.
- A production configuration with any of the O4 mistakes refuses to start with a clear message;
  development and test configurations are unaffected.
- Behind a trusted proxy the network source is the forwarded client address; a spoofed `X-Forwarded-
  For` from an untrusted peer is ignored.

## Required tests

Unit tests for the SMTP sender (TLS modes, authentication, failures, redaction) and settings
validation; HTTP tests for the client address with trusted and untrusted peers.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for secret leakage in logs and errors, TLS verification, and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
