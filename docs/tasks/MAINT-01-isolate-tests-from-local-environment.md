# MAINT-01 — Isolate Tests from the Local Environment

Status: ready

## Goal

Make the test suite independent of a developer's `.env` file and exported `MINTFLOW_*`
variables, so `make check` gives the same result locally and in CI.

## Depends on

- None

## Context

Read completely before implementation:

- AGENTS.md
- src/mintflow/config.py
- tests/conftest.py
- tests/test_config.py

`Settings` reads `.env` from the working directory and `MINTFLOW_*` environment variables.
Tests construct `Settings` directly, including the shared `settings` fixture, so local values
leak in. Today two tests in `tests/test_config.py` fail locally (`enable_api_docs=true` and
`email_backend=mailpit` in a developer `.env`), and exporting `.env` into the shell breaks
over a hundred tests. CI has no `.env`, so it does not notice.

## In scope

- An autouse fixture in `tests/conftest.py` that, for every test, disables the `env_file`
  source of `Settings` and removes `MINTFLOW_*` variables from the environment, keeping
  `MINTFLOW_TEST_*` variables that integration tests read.
- Tests proving the isolation: a value in a `.env` file in the working directory and an exported
  `MINTFLOW_*` variable do not reach `Settings` built in a test.

## Out of scope

- Any change to `Settings` or to how the application loads configuration outside tests.
- Changing the local `.env` file.

## Acceptance criteria

- `make check` passes locally with the developer `.env` present.
- The full suite passes with the `.env` values also exported into the environment.
- Integration tests still receive `MINTFLOW_TEST_DATABASE_URL`.

## Required checks

Run:

- full `make check` with PostgreSQL integration enabled;
- the full suite with `.env` exported into the environment;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
