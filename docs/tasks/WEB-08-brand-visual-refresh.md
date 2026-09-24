# WEB-08 — Brand Visual Refresh

Status: done

## Goal

Give the Web Client the MintFlow brand approved on the design canvas, without losing any
accessibility, security, or behaviour.

## Depends on

- WEB-07
- The design canvas approved by the product owner on 2026-09-24 (variant B, collapsible sidebar)

## Context

Read completely before implementation:

- AGENTS.md
- docs/web_client_design.md (W5, W6, W7, W11)
- docs/web_accessibility_checklist.md

## In scope

- The SVG mark and wordmark in the sidebar and on sign-in; an SVG favicon (white mark on green).
- Manrope woff2 (Latin, Latin Extended, Cyrillic) with its licence, served from `/static`.
- Palette as CSS custom properties, with a dark theme under `prefers-color-scheme: dark`.
- A sidebar collapsed to icons that expands on hover and on focus within; the hovered or focused
  item scales up slightly; no motion under `prefers-reduced-motion`; a bottom tab bar below
  900 px.
- Dashboard summary cards, chart and insight cards, bar cards; history rows as cards on narrow
  screens; restyled forms, buttons, notices, and error pages.

## Out of scope

- New pages, new behaviour, translations, and user-selectable themes.

## Acceptance criteria

- Every text and control colour pair meets WCAG 2.2 AA in both themes.
- No inline script or style; the CSP is unchanged.
- Navigation is fully usable by keyboard and screen reader in both sidebar states and on the tab
  bar; nothing depends on hover.
- Existing tests pass, updated only where markup structure changed.

## Required tests

Unit tests for the new layout markup (one navigation landmark, labels present while collapsed,
favicon and font assets served with the static headers) and a contrast check of the palette
tokens. The manual checklist is rerun for the changed pages.

## Required checks

Run:

- focused unit and integration tests for the task;
- full `make check` with PostgreSQL integration enabled;
- `git diff --check`;
- `git status --short`;
- final review of the complete task diff for inline styles or scripts, contrast, keyboard access,
  and scope creep.

## Completion conditions

Do not commit.

Change `Status: ready` to `Status: review` only after every criterion and check passes.
