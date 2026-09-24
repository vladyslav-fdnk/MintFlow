# WEB-09 — Dashboard Clarity

Status: done

## Goal

Make the dashboard answer "how much did I spend" at a glance, with a readable chart.

## Depends on

- WEB-08

## Context

Read completely before implementation:

- AGENTS.md
- docs/web_client_design.md (W12, W5, W6)

## In scope

- The period and a one-line answer first, with the comparison and count.
- The main chart next, switching between columns over time and a donut by category
  (`chart=columns|pie`), each with a table equivalent.
- A value axis, date labels, and a tooltip per column; a legend with amounts and percentages for
  the donut.
- Controls below the chart: period presets, custom dates, and currency choices showing their
  totals.
- Larger, stronger text for secondary information.

## Out of scope

- Other pages, analytics changes, and new metrics.

## Acceptance criteria

- The answer and the chart are visible without scrolling on a 1366 by 768 screen and on a phone
  they come first.
- Both chart views have text equivalents; donut colours are never the only signal.
- Invalid `chart` values fall back to columns; the JSON API is unchanged.

## Required tests

Unit tests for axis rounding, donut geometry, presets, and the view; integration tests for both
chart views, presets, and currency choices.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- screenshots of the changed pages (wide and phone, light and dark) reviewed before handing over;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for inline styles or scripts, contrast, keyboard access,
  and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
