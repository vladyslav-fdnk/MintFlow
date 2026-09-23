# RCPT-07 — Recognition Provider Adapter

Status: ready

## Goal

Connect the chosen recognition provider behind the RCPT-03 contract.

## Depends on

- RCPT-03
- Human choice among the free candidates in design R1, made from this task's evaluation

## Context

Read completely before implementation:

- AGENTS.md
- docs/receipt_recognition_design.md (R1)
- docs/tasks/RCPT-03-recognition-boundary.md

## In scope

- Evaluate the free candidates in design R1 on consented sample receipts and report the results; a human then chooses one and approves its data-processing terms and any credentials. The provider must be free.
- An adapter implementing `ReceiptRecognizer`, configured by settings, with timeouts and no logging of image contents or credentials.
- A small evaluation script run manually on consented sample receipts, reporting per-field correct, missing, and confidently wrong counts.

## Out of scope

- Storing raw provider responses.

## Acceptance criteria

- Tests never call the real provider.
- The provider's types never leave the adapter.

## Required tests

Unit tests must map recorded provider responses (fixtures without real personal data) to candidates, including errors and partial responses.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for ownership bypass, overwriting user input, receipt
  contents or secrets in logs, duplicate processing, and scope creep.

## Progress

- Provider chosen by the product owner: Azure AI Document Intelligence, prebuilt receipt, F0
  (design R1).
- Done: `AzureReceiptRecognizer` (REST via httpx, deadline 45 s, key sent only to the
  configured host, no logging), `MINTFLOW_RECEIPT_RECOGNIZER=azure` with endpoint and key
  settings, and unit tests on recorded-format responses.
- Done: the evaluation script, `python -m mintflow.commands.evaluate_recognizer --samples DIR`,
  using the configured recognizer and printing counts and file names only.
- Remaining, needs the product owner: an Azure F0 resource with its endpoint and key in `.env`,
  consented sample receipts outside the repository, and the evaluation run and its report.

## Completion conditions

Do not commit.

Change `Status: blocked` to `Status: ready` only after the design is approved and every
dependency is `done`, then implement, then change `Status: ready` to `Status: review` only after
every criterion and check passes.
