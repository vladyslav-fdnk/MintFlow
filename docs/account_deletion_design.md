# Account Deletion Design (sprint `account-deletion`)

Status: approved on 2026-09-24.

## 1. Purpose

Let a user permanently delete their MintFlow account and everything MintFlow keeps about them,
from Settings (MVP section 4, "Account deletion suitable for an initial commercial service", and
section 10, "a permanent account-deletion process"). This is the separate policy that
`authentication_persistence_design.md` postponed; its deactivation remains unchanged.

## 2. Settled inputs

Chosen by the product owner on 2026-09-24:

- Deletion is immediate and final once confirmed. There is no grace period and no undo.
- The user confirms by typing the account's email address on the deletion page.

## 3. Decisions

### A1. What is deleted, in one transaction

Everything owned by the user is deleted in a single database transaction. It either all goes or
nothing does:

| Data | Action |
| --- | --- |
| Expenses and their change records | deleted |
| Capture drafts, the Telegram conversation state | deleted |
| Receipts, receipt images, recognition results | deleted |
| The Telegram connection and link challenges | deleted (not merely unlinked) |
| Web sessions (all devices, the current one included) | deleted |
| The email identity | deleted |
| Pending and used sign-in links for that email (they hold the address) | deleted |
| The user row with its preferences | deleted |
| Authentication audit records | kept, with `user_id` set to empty (see A2) |
| Rate-limit buckets | left to expire within 24 hours; they hold only a keyed digest |
| Processed Telegram update ids | left to their 7-day expiry; they identify no one |

- The deletion takes a lock on the user row first. Concurrent requests from the same account,
  such as a receipt being processed or an edit on another device, either finish before it or
  find the user gone.
- The receipt worker already re-reads its receipt inside its own transaction. A receipt deleted
  meanwhile is skipped, not an error.
- The existing foreign keys stay `RESTRICT`. The deletion removes rows explicitly and in
  dependency order, so nothing is ever removed by an accidental cascade.

### A2. Audit records are kept but no longer point at anyone

- Audit records hold no email or content, only event types, outcomes, and times. They serve
  security (for example, counting failed logins) for their 90-day retention.
- Deletion sets their `user_id` to empty and adds one anonymous `account_deleted` event.
- A migration changes that foreign key to `ON DELETE SET NULL` as a safety net. It is additive
  and compatible with the previous release.

### A3. Confirmation and the flow on the Web

1. Settings gets a "Delete account" section with a link to `/settings/delete-account`.
2. That page says plainly what will be deleted and that it cannot be undone. It also says that
   encrypted backups keep the data for up to 30 days (A5). The user types their email address
   and presses "Delete my account permanently".
3. The POST is CSRF-protected like every change. It is accepted only for a live session, and
   only when the typed address matches the account's email after the usual normalization.
4. A mismatch shows a field error and deletes nothing.
5. On success, the session cookies are cleared and the browser lands on a public "Your account
   has been deleted" page with a link to sign in again.
6. The Telegram bot is not messaged. If the user writes to it later, it answers as for any
   unlinked chat.

### A4. After deletion

- The same email address can sign in again later. That creates a brand-new, empty account.
- The same Telegram account can be linked to a new MintFlow account.
- Nothing about the old account is recoverable by the user or by support.

### A5. Backups and restores

- Encrypted nightly backups (operations O7) still hold the deleted data until they expire after
  30 days. The deletion page and the privacy text say so.
- A tombstone table `deleted_accounts` keeps only the user id and the deletion time, for
  35 days, longer than any backup lives. After a restore, the authentication retention cleanup
  (already a required post-restore step) deletes every account listed there again.
- **Trade-off:** a disaster restore brings back the last backup, and a deletion made after that
  backup is not in its tombstones. Such an account can reappear (at most 24 hours of deletions,
  the RPO). The restore runbook says to check for it. An off-database deletion log would close
  this gap, at the cost of a new write path. That is postponed until the product runs for real.

## 4. Out of scope

- A grace period, account export, reactivation, and deletion requested through support or the
  bot.
- Deleting data inside Telegram's own chat history (MintFlow cannot).

## 5. Task breakdown

| Task | Summary | Depends on |
| --- | --- | --- |
| DEL-01 | `DeleteAccount` use case and persistence: one-transaction deletion in dependency order, the audit `SET NULL` migration, the `account_deleted` event, and the `deleted_accounts` tombstone | — |
| DEL-02 | Web: the Settings section, the confirmation page with email match, sign-out, and the public "deleted" page, with Russian text | DEL-01 |
| DEL-03 | Tombstones after a restore: the retention cleanup re-deletes listed accounts and prunes tombstones after 35 days; the privacy text and the restore note | DEL-01 |
