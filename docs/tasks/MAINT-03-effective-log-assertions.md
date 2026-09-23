# MAINT-03 — Make Log Assertions in Tests Effective

Status: done

## Goal

Make every test that asserts on log output actually see the application's log records.

## Depends on

- None

## Context

Read completely before implementation:

- AGENTS.md
- src/mintflow/logging.py
- tests/test_magic_link_confirmation_http.py
- tests/test_telegram_http.py (a working workaround)

`create_app` calls `configure_logging`, which runs `logging.basicConfig(force=True)`. That removes
every root handler, including pytest's `caplog` handler. Any test that builds the application and
then asserts on `caplog.text` sees nothing, so assertions such as "the token is not in the logs"
pass even if the token is logged. This was confirmed during TG-04 with a probe: a warning logged
after `create_app` did not reach `caplog`.

## In scope

- One shared test fixture or helper that attaches `caplog`'s handler to the `mintflow` project
  logger for the duration of a test, and its use in every existing test that asserts on logs
  after building the application.
- A test proving the helper captures a record logged after `create_app`.
- Replacing the TG-04 local workaround in `tests/test_telegram_http.py` with the shared helper.

## Out of scope

- Changing `configure_logging` or runtime logging behavior.

## Acceptance criteria

- Every log assertion in the suite would fail if the asserted-absent secret were logged; verified
  for at least one existing test by temporarily logging the secret and seeing it fail.
- `make check` passes.

## Required checks

Run:

- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
