# CAPTURE-02 — System Category Domain and Persistence

Status: done

## Goal

Establish the `Category` aggregate and persist the MVP's fixed system-category catalogue,
including the always-available `Uncategorized` fallback that `Expense` confirmation will depend on.

## Depends on

- CAPTURE-01

## Context

Read completely before implementation:

- AGENTS.md
- docs/domain_design_proposal.md, sections 2 "Category", 3 "Category aggregate", 9 "Category model"
- docs/product_decision_review.md, decisions 12 and 13

Inspect `src/mintflow/infrastructure/persistence/models.py` and the existing Alembic migrations
under `src/mintflow/infrastructure/persistence/migrations/` (or the project's configured migrations
directory) for the established SQLAlchemy model and migration style before adding new models.

Finalized MVP defaults:

- System categories only; user-defined categories are out of scope (decision 12).
- Category is structurally required on `Expense`, but user selection is optional — an Expense with
  no explicit user selection uses the system `Uncategorized` category (decision 13).
- The initial category set (decision 12): Groceries, Food & Dining, Transport, Shopping, Housing,
  Utilities, Health, Entertainment, Travel, Education, Gifts, Other, Uncategorized.
- The stored identity must represent stable meaning, not a translated display label. Do not use the
  English label as the primary key or foreign-key target.
- No category hierarchy.

## In scope

- A `Category` domain model: identity, a stable semantic key (for example `groceries`,
  `uncategorized`), an active/inactive flag, and optional presentation metadata (name, and
  optionally color/icon) separate from the stable key.
- Persistence for `Category` (SQLAlchemy model, Alembic migration, minimal repository able to fetch
  by stable key and list active categories).
- A migration or startup-safe seed step that inserts the initial 13 system categories with stable
  keys, including `uncategorized`, marked active. Seeding must be idempotent — re-running it must
  not create duplicates or fail.
- Enforce that `uncategorized` always exists and is always active; nothing in this task may
  deactivate or remove it.

## Out of scope

- User-defined categories.
- Category hierarchy or reparenting.
- An HTTP endpoint to list categories (deferred to the HTTP-boundary task later in this sprint).
- Any reference from `Category` back to `Expense` or `CaptureDraft`.
- Deleting a category that has ever been used (the domain doc notes hard deletion is only ever safe
  for a never-used category, and nothing in this sprint deletes categories at all).

## Acceptance criteria

- All 13 system categories exist after migration, each with a stable, lowercase, semantic key
  distinct from its display label.
- `uncategorized` is always present and active; no code path in this task can deactivate or remove
  it.
- Deactivating a category (if implemented) does not delete it and does not affect its key.
- Fetching an unknown key returns a clear "not found" outcome rather than raising an unrelated
  database error.
- Re-running the seed step is a no-op on an already-seeded database (no duplicate-key errors, no
  duplicate rows).
- The migration supports upgrade, downgrade, and re-upgrade without leaving orphaned or duplicate
  category rows.

## Required tests

Unit tests must cover:

- `Category` construction and its active/inactive transition;
- rejection of an attempt to deactivate `uncategorized`, if deactivation is exposed at the domain
  level at all in this task.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover:

- the seed step producing exactly the 13 expected categories with the expected keys;
- `uncategorized` present and active after seeding;
- idempotent re-seeding;
- fetch-by-key for an existing and an unknown key;
- listing only active categories after one category is deactivated (if deactivation is exposed);
- migration upgrade/downgrade/re-upgrade cleanliness (`alembic current`, `alembic check`).

## Required checks

Run:

- focused unit and PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- full `make check` with PostgreSQL integration enabled;
- Alembic upgrade, downgrade, re-upgrade, `alembic current`, `alembic check`;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for stable-key stability, seed idempotency, and scope
  creep.

## Completion conditions

Do not commit.

CAPTURE-01 must be `done` first. Change `Status: blocked` to `Status: ready` only once CAPTURE-01 is
`done`, then implement, then change `Status: ready` to `Status: review` only after every criterion
and check passes.
