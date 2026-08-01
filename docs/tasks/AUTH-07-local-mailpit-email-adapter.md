# AUTH-07 — Local Mailpit Email Adapter

Status: ready

## Goal

Provide a concrete local-development email adapter for the existing `EmailSender` port so Magic
Links can be inspected in Mailpit without sending mail to real recipients.

## Depends on

- AUTH-05

## Context

Read completely before implementation:

- AGENTS.md
- docs/authentication_design_proposal.md
- docs/authentication_persistence_design.md
- docs/tasks/AUTH-03-rate-limiting.md
- docs/tasks/AUTH-05-web-sessions.md

The application already owns a typed `EmailSender` boundary. Local development must use a local
mail-capture service or clearly marked development sink and must never log full Magic Links. This
task must preserve that boundary rather than coupling authentication use cases to Mailpit.

## In scope

- Add Mailpit as a local Docker Compose service.
- Implement the smallest concrete adapter for the existing typed `EmailSender` port using Mailpit's
  local SMTP interface.
- Add typed configuration required for the local SMTP connection.
- Map expected connection/provider failures to the existing `EmailDeliveryError`.
- Wire the adapter only for an explicitly allowed local/test environment.
- Document the minimal local workflow for finding a captured Magic Link in Mailpit.

## Out of scope

- Production email-provider selection or production delivery adapter.
- Paid or externally hosted email services.
- Sending-domain, SPF, DKIM, DMARC, bounce, complaint, suppression, retry, alerting, or delivery
  metrics implementation.
- An outbox, worker, or provider-specific persistence.
- Marketing email, reusable templating systems, or elaborate HTML design.
- HTTP authentication routes.
- Logging full Magic Links or raw tokens as a development delivery mechanism.

## Acceptance criteria

- A locally issued Magic Link is delivered to and inspectable in Mailpit.
- The authentication application continues to depend only on the typed `EmailSender` port.
- Recipient and link mapping is correct, with transport details contained in infrastructure.
- Expected SMTP/Mailpit failure becomes `EmailDeliveryError`; the existing generic public result
  remains unchanged and the reserved email-delivery rate-limit slot is not refunded.
- Full Magic Links and raw tokens are absent from routine and exception logs.
- Production and staging cannot silently select Mailpit or a development sink through defaults.
- Configuration values are validated and secrets are represented using secret-aware settings where
  applicable.
- Existing Compose PostgreSQL and application behavior remains intact.

## Required tests

Unit tests must cover:

- typed message-to-SMTP mapping;
- expected delivery failure conversion;
- no exception text or log record exposes the full Magic Link or raw token;
- environment/configuration rules preventing accidental production use.

Integration tests must cover:

- delivery of a representative Magic Link to local Mailpit;
- Mailpit unavailability returning the approved expected delivery failure;
- existing provider-failure behavior still consuming the email rate-limit reservation.

No new PostgreSQL schema is expected. If persistence is changed unexpectedly, stop and obtain
approval rather than broadening this task.

## Required checks

Run:

- focused adapter and configuration tests;
- the scoped Mailpit integration test;
- full `make check`;
- relevant Docker Compose configuration/status validation;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for environment isolation, secret leakage, dependency
  scope, and accidental production behavior.

## Completion conditions

Do not commit.

This task may become ready after the preceding ready task is approved, preserving queue order.
Change `Status: ready` to `Status: review` only when all criteria and checks pass. If implementation
would require choosing a production email provider or external service, leave status unchanged and
report the blocker.
