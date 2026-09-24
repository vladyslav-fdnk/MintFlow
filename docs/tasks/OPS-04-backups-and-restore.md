# OPS-04 — Encrypted Backups and Rehearsed Restore

Status: ready

## Goal

Encrypted Backups and Rehearsed Restore.

## Depends on

- Human approval of docs/operations_design.md
- OPS-02

## Context

Read completely before implementation:

- AGENTS.md
- docs/operations_design.md (O7)

## In scope

- `deploy/backup.sh`: `pg_dump --format=custom` in the `postgres` container, encrypted with `age` to
  a public key, uploaded to S3-compatible object storage, and a Healthchecks.io ping on success.
- `deploy/restore.sh`: download and decrypt a chosen backup, restore it into a fresh database, run
  the migrations check, and print row counts.
- A lifecycle rule of 30 days, documented with the retention statement; after a full restore both
  cleanup commands run before traffic returns.

## Out of scope

- The real bucket and the rehearsal on the real server (OPS-05).

## Acceptance criteria

- A local round trip (backup, encrypt, decrypt, restore into a scratch database) reproduces every
  table's row count.
- The private key is never needed on the server; the script refuses to run without the public key or
  storage settings.
- Nothing secret is printed.

## Required tests

A script test of the local round trip against a throwaway PostgreSQL database.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for key handling, data loss on failure paths, secret leakage, and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
