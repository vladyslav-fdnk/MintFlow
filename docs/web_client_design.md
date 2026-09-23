# Web Client Design (sprint `web-client`)

Status: approved on 2026-09-23.

## 1. Purpose

Deliver the Web Client: the "understand on the Web" half of the product loop
(docs/mvp_definition.md, sections 3 and 6). A user signs in with a magic link, sees the
dashboard, browses, corrects, and deletes expenses, links Telegram, and sets their conventions.

The backend already provides every use case the pages need except changing preferences:
magic-link sign-in and sessions (AUTH), the dashboard (DASH), history, edit, delete, and restore
(EXPENSE), and Telegram linking (TG). This sprint adds the pages, one small use case, and the
tests that prove the Telegram-to-Web loop.

## 2. Settled inputs

These are already decided and are not reopened here:

- The stack is chosen by the product owner: server-rendered Jinja2 templates with htmx, inside
  the existing FastAPI application. No Node toolchain and no separate frontend deployment.
- Sign-in is by magic link only. The session cookie (`__Host-mintflow_session`, HttpOnly) and the
  session-bound CSRF token (`__Host-mintflow_csrf`, readable by scripts, checked against the
  `X-CSRF-Token` header together with the request Origin) stay exactly as built in AUTH-11..13.
- The dashboard has exactly the summary, three charts, and two insights of MVP section 8, from
  `BuildDashboard`. Currencies are never added together or converted.
- Only confirmed, non-deleted Expenses appear, from the existing history query.
- System categories only; account deletion needs a separate policy (domain design) and is not in
  this sprint.

## 3. Decisions

### W1. A `mintflow.web` package renders pages from application use cases

- Pages are FastAPI routes returning Jinja2 templates. They call the same application use cases
  and dependencies as the JSON API, never the JSON API over HTTP.
- New dependencies: `jinja2` (templates), `python-multipart` (form bodies), and `babel` (locale
  formatting, W7). htmx is vendored as one pinned, minified static file with its licence; nothing
  is loaded from a CDN, so pages work under a strict CSP and send no data to third parties.
- The JSON API stays. It serves tests and any later client.
- Trade-off: a SPA would give richer interactions, but duplicates validation and state, needs a
  second toolchain, and is slower to reach a usable page on 4G. htmx partial updates cover the
  interactions the MVP needs (filters, load more, inline edit errors, confirmations).

### W2. Routes and sign-in

| Page | Path |
| --- | --- |
| Sign in, "check your email" | `GET/POST /sign-in` |
| Dashboard | `GET /dashboard` (and `/` redirects here) |
| Expense history | `GET /expenses` |
| Expense detail, edit, delete | `GET /expenses/{id}`, `GET/POST /expenses/{id}/edit`, `POST /expenses/{id}/delete`, `POST /expenses/{id}/restore` |
| Settings | `GET /settings`, `POST /settings/preferences`, Telegram link and disconnect |

- An unauthenticated request for a page gets `303` to `/sign-in`; JSON endpoints keep their
  `401`. Another user's expense is `404`, never `403`.
- `/sign-in` is an HTML form that calls `RequestMagicLink` with return target `dashboard` and
  always shows the same "check your email" page (no account enumeration). It goes through the
  same use case, so rate limits and audit records match the JSON endpoint, and it additionally
  requires the approved Origin (the JSON request endpoint does not check Origin; the form, being
  a browser form, can). The magic-link confirmation page already redirects to `/dashboard`.
- Sign out is a button posting to the existing `/auth/logout`, then going to `/sign-in`.

### W3. Mutations use htmx with the existing CSRF header

- Every state-changing request is sent by htmx. One static script copies the CSRF cookie into the
  `X-CSRF-Token` header of each htmx request; the server reuses the existing CSRF dependency
  unchanged. There is no second CSRF mechanism.
- Consequence: reading pages works without JavaScript; changing data requires it. This is
  accepted for the MVP and stated here, because a form-field token would be a second path to
  secure and test.

### W4. Charts are server-rendered SVG with table equivalents

- The column chart and the two horizontal bar charts are inline SVG built in templates from the
  `Dashboard` data. There is no chart library and no client-side rendering.
- Each chart has a visible text summary and a data table (hidden behind a disclosure on small
  screens). Bars carry values as text, never colour alone. The time and category charts link
  through to the filtered history (dashboard design D10).

### W5. Security headers on every page

- `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self';
  img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'`. No inline
  scripts or styles; htmx is configured not to inject its indicator styles.
- `Cache-Control: no-store` on authenticated pages, `X-Content-Type-Options: nosniff`, and
  `Referrer-Policy: same-origin`. Static files get long cache lifetimes with versioned names.

### W6. Accessibility and responsiveness are part of every task

- Semantic landmarks and headings, labelled form controls, specific error messages tied to their
  fields, visible focus, and WCAG 2.2 AA contrast. Nothing depends on hover or colour alone.
- htmx updates go into regions with `aria-live`; after a partial update, focus moves to the
  updated content or the first error. The delete confirmation is a page section, not a modal,
  so there is no focus trap to manage.
- Layouts work from 320 CSS pixels up, with touch targets of at least 44 by 44 pixels.

### W7. Strings are externalizable; formatting follows the user's locale

- The first release is in English, like the bot. Template strings go through a `_()` function
  (identity for now), so translation later only adds catalogues.
- Dates and amounts are formatted with Babel using the user's locale (or `en` when unset), and
  every amount shows its ISO currency code. Dates are the stored transaction dates; "today" and
  the default period come from the user's timezone.

### W8. Preferences get one application use case

- `UpdatePreferences` changes locale, timezone, and default currency through the existing
  `User.update_preferences` (all values validated before any is applied) and the existing
  repository method. The settings page explains that totals in different currencies are not
  converted.
- Telegram status, link, and disconnect reuse the TG use cases. Linking shows the `t.me` deep
  link as a button.

### W9. First-use guidance

- A dashboard with no expenses shows one onboarding message: capture in Telegram, understand on
  the Web, with "Connect Telegram" (or "Open the bot" once linked) and a pointer to settings for
  timezone and default currency (MVP section 3, first use).

### W10. Testing without a browser

- Page tests use the ASGI test client and parse HTML with a small helper over the standard
  library parser: status, redirects, headers, landmarks, labels, links, and the data shown.
- PostgreSQL integration tests cover ownership, CSRF rejection, and one end-to-end loop: an
  expense confirmed through the Telegram webhook appears in Web history and the dashboard, and a
  Web edit changes the dashboard.
- Browser-driven tests (for example Playwright) and automated accessibility audits are deferred
  to the deployment sprint; this sprint documents a manual keyboard and 320-pixel check per page.

## 4. Out of scope

- Account deletion and its policy, privacy notice, and legal pages.
- Receipt images on the Web (a Should Have in design R4).
- Translations beyond English, merchant search, user-defined categories.
- A production email sender (only Mailpit exists) and deployment; they belong to a later
  operations sprint.
- Browser automation and automated accessibility audits (W10).

## 5. Task breakdown

| Task | Summary | Depends on |
| --- | --- | --- |
| WEB-01 | Web foundation: templates, layout, static files, htmx and CSRF script, security headers, page authentication | — |
| WEB-02 | Sign-in and sign-out pages | WEB-01 |
| WEB-03 | Dashboard page: filters, summary, three SVG charts with tables, two insights, first-use guidance | WEB-01 |
| WEB-04 | Expense history page: filters and "load more" | WEB-01 |
| WEB-05 | Expense detail, edit, delete, and restore | WEB-04 |
| WEB-06 | Settings: preferences use case and page, Telegram link and disconnect | WEB-01 |
| WEB-07 | End-to-end Telegram-to-Web loop test and the manual accessibility checklist | WEB-02..06 |
