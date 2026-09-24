# OPS-02 — Production Image, Compose, Caddy, Image Registry

Status: ready

## Goal

Production Image, Compose, Caddy, Image Registry.

## Depends on

- Human approval of docs/operations_design.md
- OPS-01

## Context

Read completely before implementation:

- AGENTS.md
- docs/operations_design.md (O1, O2, O8)

## In scope

- The Dockerfile runs as a non-root user and has a health check on `/health/ready`.
- `deploy/compose.production.yaml`: `caddy`, `app`, `worker` (the receipt worker from the same
  image), and `postgres` 17 with a named volume and no published port; `json-file` log rotation (10
  MB, 5 files).
- `deploy/Caddyfile`: automatic TLS for the domain, HTTP to HTTPS, HSTS, and a reverse proxy to
  `app`.
- CI builds the image on every push to `main` and pushes it to GitHub Container Registry tagged with
  the commit SHA.
- The migration rule (backward compatible with the previous release) recorded in `AGENTS.md`.

## Out of scope

- Cron jobs and the webhook command (OPS-03); backups (OPS-04); the real server (OPS-05).

## Acceptance criteria

- `docker compose -f deploy/compose.production.yaml config` validates with an example environment
  file.
- The image starts as a non-root user and reports healthy against a local PostgreSQL.
- The CI workflow pushes only from `main` and only with the repository's token.

## Required tests

A CI or script check that builds the image, runs it as non-root, and waits for the health check; a
Compose config validation.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for container privileges, published ports, secrets in images or CI logs, and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
