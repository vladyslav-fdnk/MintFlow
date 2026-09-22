# CAPTURE-04 — CaptureDraft Persistence

Status: blocked

## Goal

Persist the `CaptureDraft` aggregate from CAPTURE-03: SQLAlchemy model, Alembic migration, and a
repository that enforces ownership scoping and supports the row-level locking the future
confirmation use case needs.

## Depends on

- CAPTURE-03

## Context

Read completely before implementation:

- AGENTS.md
- docs/domain_design_proposal.md, sections 2 "CaptureDraft" (Essential attributes), 3 "CaptureDraft
  aggregate", 6 (invariants 5, 6, 8, 16, 18)
- docs/tasks/CAPTURE-03-capture-draft-domain.md

Inspect `src/mintflow/infrastructure/persistence/web_sessions.py` and
`src/mintflow/infrastructure/persistence/models.py` for the established repository and model style:
plain SQLAlchemy `Session`-driven repositories, explicit `with self._session.begin():` transaction
boundaries for writes, and `with_for_update` for rows a concurrent operation must not race. Note
`find_session_id`'s history in `AUTH-14`'s task file and commit: a bare read auto-begins a
SQLAlchemy transaction that then conflicts with a later explicit `session.begin()` on the same
session — sequence read-then-write repository methods accordingly, or use one transaction per public
method.

## In scope

- A `CaptureDraftRecord` SQLAlchemy model matching CAPTURE-03's essential attributes: id, owner
  (`User.id`, foreign key), capture source, lifecycle state, revision, per-field value/provenance
  columns (or a structured JSON column if that is a better fit — use your judgment and document the
  choice, but keep provenance queryable enough for tests to assert it directly), category key
  (nullable, no foreign key to `Category` required yet if that complicates the migration order —
  document whichever choice is made), optional note, optional resulting Expense id (nullable,
  no foreign key yet since `Expense` does not exist until CAPTURE-06), and the timestamp columns.
- An Alembic migration for the new table, including check constraints mirroring the domain
  invariants that are cheap to express at the database level (for example: revision is
  non-negative; `confirmed` state requires a non-null resulting-expense-id and vice versa).
- A repository with at least: `create`, `get(id, owner_id)` scoped to the owner (returns nothing for
  another owner's draft, not an error that would disclose existence), `update` (persisting a new
  domain-level `CaptureDraft` snapshot, advancing revision), and a locking read intended for the
  future confirmation transaction (`SELECT ... FOR UPDATE`) distinct from the plain scoped `get`.
- Mapping between the CAPTURE-03 domain dataclass and the persistence model in both directions.

## Out of scope

- The `Expense` table or any foreign key to it beyond a nullable id column reserved for later use.
- The confirmation transaction itself (CAPTURE-07).
- Any HTTP endpoint.
- Draft expiration cleanup jobs.

## Acceptance criteria

- A draft created by one owner is never returned by `get` when queried with a different owner id.
- Persisting and reloading a draft round-trips every field, including provenance, without loss.
- The locking read acquires a row lock usable inside a later multi-step transaction (verified with a
  concurrency test, not just a single-connection call).
- The check constraint(s) reject a row claiming `confirmed` state without a resulting-expense-id (or
  vice versa) at the database level, not only in application code.
- The migration supports upgrade, downgrade, and re-upgrade without residue.

## Required tests

Unit tests must cover:

- domain-to-persistence-model mapping and back, for a draft with every field populated and for one
  with only mandatory-eligible fields set.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover:

- create and scoped get, including the negative case (wrong owner gets nothing);
- update advancing revision and persisting new field values/provenance;
- the locking read serializing two concurrent attempts to update the same draft, using real separate
  connections and a synchronization primitive — not a sequential call described as concurrent;
- the database check constraint(s) rejecting an inconsistent `confirmed`/resulting-expense-id
  combination;
- migration upgrade/downgrade/re-upgrade (`alembic current`, `alembic check`).

## Required checks

Run:

- focused unit and PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- Alembic upgrade, downgrade, re-upgrade, `alembic current`, `alembic check`;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for ownership-scoping bypass, transaction-ordering bugs
  (per the CAPTURE-04 Context note above), and scope creep.

## Completion conditions

Do not commit.

CAPTURE-03 must be `done` first. Change `Status: blocked` to `Status: ready` only once CAPTURE-03 is
`done`, then implement, then change `Status: ready` to `Status: review` only after every criterion
and check passes.
