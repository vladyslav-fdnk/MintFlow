# Web Accessibility Checklist (WEB-07)

Target: WCAG 2.2 AA for core Web workflows (MVP section 10). Automated page tests cover markup:
landmarks, labels, error linking, table headers, no inline scripts or styles, and focus targets
after htmx updates (web design W10). What only a person can judge is checked by hand here, once
per release that changes a page, in a current Chrome or Firefox.

## How to check

1. Run the app locally (`make run`, Mailpit for sign-in), sign in, and link a test Telegram bot
   (or skip the Telegram rows).
2. **Keyboard:** unplug the mouse. Every action is reachable with Tab, Shift+Tab, Enter, Space,
   and arrow keys in selects; the order follows the page; nothing traps focus.
3. **Focus:** the focused element always has the visible blue outline.
4. **320 px:** in responsive mode at 320 CSS pixels wide, nothing scrolls sideways and nothing
   overlaps; buttons stay at least 44 px tall.
5. **Screen reader:** with VoiceOver, NVDA, or Orca, headings, landmarks, field labels, errors,
   and status messages are announced; charts read as their text summary and table.
6. Record the date, browser, and result in the table. A problem either gets fixed or becomes a
   follow-up task, named in the Notes column.

## Pages

| Page | Automated coverage | Keyboard | Focus | 320 px | Screen reader | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Sign in (`/sign-in`) | label, field error linked, focus on error | ok | ok | ok | ok | |
| Check your email (`/sign-in/sent`) | heading | ok | ok | ok | ok | |
| Dashboard (`/dashboard`) | labelled filters, focus to region after update, chart text and table, no inline style | ok | ok | ok | ok | Charts stretch to the width; check bars stay readable at 320 px. |
| Expenses (`/expenses`) | labelled filters, column headers, focus on first new row after "Load more" | ok | ok | ok | ok | Four columns at 320 px: check wrapping. |
| Expense (`/expenses/{id}`) | description list, status message | ok | ok | ok | ok | |
| Edit expense (`/expenses/{id}/edit`) | labels, errors linked, focus on first error, unsaved-changes warning | ok | ok | ok | ok | Date input: check typing a date with the keyboard. |
| Delete expense and Undo | confirmation is a page, not a dialog | ok | ok | ok | ok | |
| Settings (`/settings`) | labels, hints, errors linked, focus on first error | ok | ok | ok | ok | The timezone list is long: check type-to-search in the select. |
| Connect Telegram (in Settings) | status in a live region, no repeated "waiting" | ok | ok | ok | ok | |
| Disconnect Telegram | confirmation page | ok | ok | ok | ok | |
| Error pages (404, 500) | heading, no navigation | ok | ok | ok | ok | |

## Runs

| Date | Browser and assistive technology | By | Result |
| --- | --- | --- | --- |
| 2026-09-24 | Not recorded | Product owner | All pages reported OK. |
