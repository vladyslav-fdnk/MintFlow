# CAPTURE-07 — User Capture Preferences (Timezone, Default Currency, UI Language)

Status: review

## Goal

Add the User preference fields the domain design already specifies but authentication-sprint
persistence never implemented, starting with the one the confirmation use case cannot work
correctly without: timezone. Add default currency and UI language in the same task since they share
the same table, validation style, and are equally small.

## Depends on

- CAPTURE-01

## Context

Read completely before implementation:

- AGENTS.md
- docs/domain_design_proposal.md, section 2 "User" (Essential attributes, Invariants)
- docs/tasks/CAPTURE-01-domain-value-objects.md (for `CurrencyCode`; this task adds `Locale` and
  `Timezone` value objects from the same domain doc section 4, which CAPTURE-01 did not include
  because they belong to `User`, not `CaptureDraft`/`Expense`)

Inspect `src/mintflow/domain/user/model.py` and its persistence model in
`src/mintflow/infrastructure/persistence/models.py` (the `UserRecord` table from the
`user-foundation` sprint). This task extends that existing table with new nullable/defaulted
columns via a new migration; it does not replace or duplicate `User`.

This gap was discovered while planning `CAPTURE-08` (the draft-confirmation use case), which needs
the owning User's timezone to enforce the approved future-date tolerance (`product_decision_review.md`
decision 5) and needs a default currency to make manual capture fast. Rather than have the
confirmation use case guess a placeholder value (for example UTC) without an explicit decision,
this task adds the real preference storage first.

## In scope

- A `Timezone` value object: a validated IANA timezone identifier wrapper (reject unknown zone
  names).
- A `Locale` value object: a validated BCP-47-compatible tag wrapper, used only for the optional
  locale preference — do not derive currency or country from it (domain doc section 7).
- Extend the `User` domain model with: `timezone` (`Timezone`, required, with a sensible default —
  use your judgment for what a newly created `User` gets before they explicitly choose one, and
  document the choice, for example `UTC` as a neutral starting default that the user can change
  immediately), `default_currency` (`CurrencyCode | None`, optional per the domain doc), `ui_language`
  (a small validated wrapper or constrained string; keep it minimal — do not build a full i18n
  catalogue in this task), and `locale` (`Locale | None`, optional).
- `User.update_preferences(...)`-style method(s) returning a new instance, per the established
  immutable-transition style.
- A migration adding the corresponding nullable/defaulted columns to the existing `users` table (or
  a separate `user_preferences` table if that is a cleaner fit for the existing schema — use your
  judgment, but do not duplicate the `User` aggregate root).
- Extend the existing `SqlAlchemyUserRepository` (or equivalent) to persist and load the new fields.

## Out of scope

- Any HTTP endpoint for viewing/editing preferences (deferred; `CAPTURE-08` only needs to *read*
  `User.timezone` internally, not expose it).
- UI language catalogue/translation infrastructure.
- Deriving timezone, locale, or currency from IP, Accept-Language, or any other request signal —
  preferences are explicit, stored values only.
- Changing how existing `user-foundation`-sprint authentication code reads `User`; this task must
  not alter authentication behavior.

## Acceptance criteria

- A newly created `User` has the documented default timezone and `None` for the other preferences.
- `update_preferences` validates each provided value through its value object and rejects an
  invalid timezone/locale/currency without partially applying the other fields.
- Existing authentication tests continue to pass unmodified — this task only adds columns/fields and
  must not change `User.create`'s existing signature in a way that breaks `user-foundation`-sprint
  call sites (use additive defaults, not new required constructor arguments, unless every call site
  is also updated in this task's diff).
- The migration is additive only: it does not drop or rename any existing `users` table column.

## Required tests

Unit tests must cover:

- `Timezone` and `Locale` construction success and rejection of invalid input;
- `User` default timezone on creation;
- `update_preferences` success and per-field validation rejection.

Real PostgreSQL integration tests using `MINTFLOW_TEST_DATABASE_URL` must cover:

- persisting and reloading a `User` with preferences set and with preferences left at their
  defaults;
- the migration's upgrade/downgrade/re-upgrade cleanliness (`alembic current`, `alembic check`);
- that every existing `user-foundation` PostgreSQL integration test still passes unmodified against
  the migrated schema.

## Required checks

Run:

- focused unit and PostgreSQL integration tests with `MINTFLOW_TEST_DATABASE_URL` set;
- the complete existing authentication test suite, to confirm no regression;
- full `make check` with PostgreSQL integration enabled;
- Alembic upgrade, downgrade, re-upgrade, `alembic current`, `alembic check`;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for authentication regressions and scope creep.

## Completion conditions

Do not commit.

CAPTURE-01 must be `done` first. Change `Status: blocked` to `Status: ready` only once CAPTURE-01 is
`done`, then implement, then change `Status: ready` to `Status: review` only after every criterion
and check passes.
