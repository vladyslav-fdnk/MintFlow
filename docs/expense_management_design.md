# Expense Management Design (sprint `expense-management`)

Status: approved on 2026-09-23.

## 1. Purpose

Deliver the backend for the Web Client's Expense history, Expense details, and Expense editing
pages (docs/mvp_definition.md, section 6): list confirmed Expenses with filters, edit them, delete
them recoverably, and record every post-confirmation change.

This sprint builds only on the existing `Expense` aggregate, `expenses` table, and HTTP composition
from `capture-foundation`. It adds no external service and no new dependency.

## 2. Settled inputs

These are already decided and are not reopened here:

- Soft deletion with web restoration (docs/product_decision_review.md, decision 6). Every history
  and analytics query must apply one centralized active-record rule.
- A restrained, immutable change record for confirmed-Expense edits and deletions, not full event
  sourcing (decision 7).
- Editable fields: merchant, transaction date, amount, currency, category (mvp_definition section
  6). The note is also editable because it is an Expense attribute set during capture.
- No bulk edit, allocations, line items, refunds, merchant search, or currency conversion.
- Only the owner may view, edit, or delete an Expense (domain invariant 9). Not-yours responses
  are identical to not-found responses.

## 3. Proposed decisions (need approval)

### D1. Change record storage — new append-only `expense_change_records` table

One row per effective change operation:

| Column | Notes |
| --- | --- |
| `id` | UUID primary key |
| `expense_id` | FK → `expenses.id`, `ON DELETE CASCADE` (the record has no meaning without its Expense; permanent account deletion will purge both) |
| `actor_user_id` | FK → `users.id`; always the owner in the MVP |
| `occurred_at` | timestamptz |
| `change_type` | constrained: `edited`, `deleted`, `restored` |
| `changes` | JSONB, only for `edited`: `{field: {"old": ..., "new": ...}}` for changed fields only |

- Written in the same transaction as the Expense update; a failed audit insert rolls back the edit.
- Append-only: the application never updates or deletes rows. It is a supporting persistence
  record, not a domain aggregate (decision 7).
- An edit that changes nothing writes no record and does not bump `modified_at`.
- Not user-facing this sprint. It is not exposed through any endpoint.

Trade-off: JSONB keeps one generic shape for five editable fields instead of one column pair per
field. Stored values are the user's own financial data, so they get the same access and retention
as the Expense itself. Nothing in the record is a secret.

### D2. Deleted Expenses are kept until account deletion

- Delete sets `deleted_at`; restore clears it. Both are idempotent.
- No automatic purge this sprint. A purge window, for example 30 days, is a retention-policy
  decision that belongs with account deletion and the privacy notice. It is recorded here as a
  known gap, not solved silently.

### D3. Concurrency — row lock, last write wins

- Edit, delete, and restore load the Expense with `SELECT ... FOR UPDATE`, apply the domain
  change, update the row, and append the change record in one transaction. The lock makes the
  recorded `old` values exact under concurrent requests.
- No optimistic version or `If-Match` check this sprint. With one owner and two tabs at worst,
  last write wins, and the change record preserves what was overwritten.
- Trade-off: a stale form can silently overwrite a newer edit. If that becomes a real problem,
  adding an `expected_modified_at` precondition later is backward-compatible.

### D4. Edit semantics — partial update with explicit clearing

- `PATCH` accepts any subset of fields. An absent field is unchanged.
- `merchant` and `note` accept explicit `null` to clear them. Required fields (amount, currency,
  transaction date, category) reject `null`.
- Amount and currency are sent together, as in the draft PATCH, because `Money` is one value.
- The transaction date obeys the same rule as confirmation: at most one day in the future
  relative to the owner's timezone. The rule moves into one shared place instead of being copied.
- Editing a soft-deleted Expense returns not found. It must be restored first.

### D5. History listing — keyset pagination

- Order: `transaction_date DESC, created_at DESC, id DESC`. This is stable and predictable.
- Filters: inclusive `date_from` / `date_to` on transaction date, `category` (one or more keys),
  `currency` (one or more ISO codes). All filters combine with AND.
- Page size defaults to 50, maximum 100, and the response returns an opaque `next_cursor`.
- New partial index: `(owner_id, transaction_date DESC, created_at DESC, id DESC) WHERE deleted_at
  IS NULL`.
- Trade-off: keyset is slightly more code than offset/limit. It stays correct when Expenses are
  added or deleted between pages, which matters for trust in history. Offset would still be fast
  at MVP data volumes, but can skip or duplicate rows while the user edits.
- The cursor is base64url JSON of the last row's sort key. It is not signed: it only narrows a
  query that is already owner-scoped, so tampering cannot expose another user's data. Malformed
  cursors return a generic 422.

### D6. Centralized active-record rule

- A single repository-level helper applies `deleted_at IS NULL` to every history read. The
  existing `GET /capture/expenses/{id}` and the new list query both use it. Analytics will reuse
  it later.
- Only restore reads deleted Expenses, through an explicitly named repository method.

### D7. Routes stay under `/capture`

- `GET /capture/expenses`, `PATCH /capture/expenses/{id}`, `DELETE /capture/expenses/{id}`,
  `POST /capture/expenses/{id}/restore`.
- This is consistent with the existing `GET /capture/expenses/{id}`. Renaming the prefix, for
  example to `/expenses`, would be a separate, deliberate API change and is not done here.
- Every mutating route uses the existing session-bound CSRF protection
  (`CsrfProtectedPrincipalDependency`).

## 4. Out of scope

- Analytics dashboard, summaries, charts, and insights.
- Displaying change history to users.
- Receipt images and receipt deletion.
- Telegram recent-history view.
- Purging soft-deleted Expenses, and account deletion.
- Merchant search, bulk edit, CSV export.
- Any frontend.

## 5. Task breakdown

| Task | Summary | Depends on |
| --- | --- | --- |
| EXPENSE-01 | Active-record rule and history listing query with keyset pagination and index | — |
| EXPENSE-02 | `GET /capture/expenses` list endpoint | EXPENSE-01 |
| EXPENSE-03 | `expense_change_records` table and append-only repository | — |
| EXPENSE-04 | Edit Expense use case: lock, validate, diff, record | EXPENSE-03 |
| EXPENSE-05 | `PATCH /capture/expenses/{id}` endpoint | EXPENSE-04 |
| EXPENSE-06 | Delete and restore use cases and endpoints | EXPENSE-03 |
