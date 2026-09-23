# Telegram Client Design (sprint `telegram-client`)

Status: approved on 2026-09-23.

## 1. Purpose

Deliver the Telegram Client for manual capture (docs/mvp_definition.md, section 5): secure
account linking, `/start` and help, manual expense entry with review, editing, explicit
confirmation and cancellation, draft conflicts and expiry, and recent history. Everything runs
on the existing `CaptureDraft` → `ConfirmCaptureDraft` → `Expense` loop.

Receipt capture and recognition are the next sprint. This sprint must not block them: the draft
flow, review card, and conversation state are built so a receipt draft can enter the same review.

## 2. Settled inputs

These are already decided and are not reopened here:

- **Linking ceremony** (authentication_design_proposal.md, sections 6–7): the Web creates a
  five-minute, single-use challenge bound to the Web session; the bot claims it from a verified
  private chat; the same Web session confirms it; only then is the connection active. Conflicts
  fail generically and never transfer an identity. Unlinking is immediate.
- **Persistence** (authentication_persistence_design.md, 2.5, 2.6, 5.5–5.7): `TelegramConnection`
  with partial unique indexes on active `user_id` and `telegram_user_id`; `TelegramLinkChallenge`
  with a hashed 256-bit token; atomic conditional claim and confirmation; 30-day retention of
  unlinked connections and finished challenges; audit evidence for claim, link, and unlink.
- Only the verified numeric Telegram user ID is identity. Usernames and names are display data.
- Private chats only. Unlinked users can never submit financial data or see account data.
- Confirmation is always explicit; repeated confirmation never duplicates (CAPTURE-08).
- Abandoned drafts expire seven days after last activity; expiry never creates an Expense.
- Recent history shows the latest 10 confirmed Expenses.

## 3. Decisions

### T1. External service: one Telegram bot, configured by environment

A bot must be created with @BotFather by a human. The application reads three new secrets:
`MINTFLOW_TELEGRAM_BOT_TOKEN`, `MINTFLOW_TELEGRAM_BOT_USERNAME` (for deep links), and
`MINTFLOW_TELEGRAM_WEBHOOK_SECRET`. All three are optional as a group: without them the
Telegram routes are disabled, so existing environments and CI keep working. No test or CI job
calls the real Telegram API; tests use a fake Bot API client.

### T2. Updates arrive by webhook; long polling only for local development

- Deployed environments receive updates at `POST /telegram/webhook`. Telegram's
  `X-Telegram-Bot-Api-Secret-Token` header must equal the configured secret (constant-time
  comparison); anything else is rejected before parsing.
- A local command (`python -m mintflow.commands.telegram_polling`) reads updates with
  `getUpdates` and passes them to the same handler, because a webhook needs a public HTTPS
  address that a developer machine usually lacks.
- Trade-off: two transports, but one handler, so behavior cannot diverge. Polling in production
  would add a long-running worker process for no benefit at this scale.

### T3. New runtime dependency: `httpx`, and no Telegram framework

- A thin, typed Bot API client over `httpx` covers only what the MVP uses: `sendMessage`,
  `editMessageText`, `answerCallbackQuery`, `setWebhook`, `getUpdates`. Updates are parsed into
  small Pydantic models for the fields we read.
- `httpx` is already locked as a development dependency; this moves it to runtime dependencies.
- Trade-off: frameworks such as aiogram or python-telegram-bot bring their own dispatcher and
  conversation state, which would compete with our PostgreSQL-backed drafts and complicate
  idempotency. Parsing a small subset of updates ourselves is less code than adapting a
  framework to our model.

### T4. Updates are processed synchronously and deduplicated by `update_id`

- The webhook handles the update inside the request: fast database work plus one or two Bot API
  calls, well under Telegram's patience and the two-second acknowledgement target.
- A `telegram_processed_updates` table records each `update_id` in the same transaction as the
  work it caused. A redelivered update is acknowledged without repeating anything. Rows are
  deleted after seven days by the cleanup command (T9).
- Replies are sent after the transaction commits. If sending fails, the failure is logged
  without message content and the webhook still returns 200, so Telegram does not redeliver an
  update whose work is already committed.
- Trade-off: no queue this sprint. Receipt recognition will need asynchronous work, and its
  sprint will introduce a queue for that path only.

### T5. Conversation state lives in PostgreSQL

A `telegram_conversations` row per Telegram user records the active draft (if any), which field
the bot is currently asking for, and the message id of the current review card (so it can be
edited in place). Field values live only on the `CaptureDraft`. The row is owned by the active
`TelegramConnection`'s user; unlinking clears it.

### T6. Manual entry flow

1. `/add` (or the "Add expense" button) starts a `TELEGRAM_MANUAL` draft. Currency defaults to the
   user's default currency and the date to today in the user's timezone, both marked `DEFAULT`.
2. The bot asks for the amount. It accepts `12.50`, `12,50`, or `12.50 EUR`. Zero, negative
   values, and more decimals than the currency allows are rejected with a short instruction.
3. The bot asks for the merchant or description, with a "Skip" button.
4. The bot offers the system categories as an inline keyboard.
5. The review card shows merchant, date, currency, amount, and category. Defaulted values are
   marked "(default)". Buttons: edit each field, Confirm, Cancel.
6. Editing a field asks for that field only and returns to the review card. Dates accept
   "Today"/"Yesterday" buttons or typed `YYYY-MM-DD` or `DD.MM.YYYY`.
7. Confirm calls `ConfirmCaptureDraft`. The success message names the saved amount and currency
   and offers "Recent" and "Open Web" buttons. The bot never says "saved" before commit.

Trade-off: a guided flow is a few taps longer than parsing one free-text line, but it matches
the MVP's "review before confirmation" rule and handles missing fields predictably. A one-line
shortcut can be added later on top of the same draft.

### T7. Draft conflicts, cancellation, and expiry

- One active draft per conversation. `/add` while a draft is active asks "Continue current
  draft" or "Discard and start new"; discarding cancels the old draft.
- `/cancel` or the Cancel button cancels the active draft.
- A cleanup command expires drafts with no activity for seven days (`CaptureDraft.expire`) and
  clears their conversations. Buttons on an expired or confirmed draft answer with a short
  "This draft is no longer active" and change nothing.

### T8. Messages are English-only this sprint, kept in one module

All user-facing text lives in one messages module, so localization later is a translation task,
not a search through handlers. The user's `ui_language` is not used yet. Trade-off: non-English
users see English at first; building localization now would slow the core loop for a small
first audience.

### T9. One cleanup command for Telegram-related retention

Following the AUTH-15 pattern, a batch command deletes processed-update rows older than seven
days, finished link challenges and unlinked connections older than 30 days (already decided), and
expires abandoned drafts. It is meant to be run by a scheduler; scheduling itself is deployment
work outside this sprint.

### T10. Web side is API only, and "Open Web" is a plain link

- New endpoints, all authenticated and CSRF-protected where they change state:
  `POST /telegram/link-challenges`, `GET /telegram/link-challenges/{id}` (status plus safe
  Telegram display metadata for the initiating session only),
  `POST /telegram/link-challenges/{id}/confirm`, `GET /telegram/connection`, and
  `DELETE /telegram/connection`.
- "Open Web" buttons link to the configured Web origin. The user signs in there as usual.
  A Telegram-initiated automatic Web login would be a new authentication mechanism and is out of
  scope.

## 4. Out of scope

- Receipt photos, recognition, and asynchronous processing.
- Group chats, channels, inline mode, and payments.
- Localization, Web frontend, and automatic Web login from Telegram.
- Post-confirmation editing or deletion in Telegram (Web only, per MVP).
- Scheduling the cleanup command and registering the webhook in a real deployment.

## 5. Task breakdown

| Task | Summary | Depends on |
| --- | --- | --- |
| TG-01 | Telegram settings, Bot API client, update models, and a fake client for tests | — |
| TG-02 | `TelegramConnection` and `TelegramLinkChallenge` persistence | — |
| TG-03 | Linking use cases: issue, claim, confirm, unlink, status, with audit | TG-02 |
| TG-04 | Web linking endpoints, including the bot's "connected" message | TG-01, TG-03 |
| TG-05 | Webhook, update deduplication, dispatcher, `/start`, help, unlinked refusal | TG-01, TG-03 |
| TG-06 | Conversation state and the manual entry flow through confirmation | TG-05 |
| TG-07 | Recent history and draft conflict handling | TG-06 |
| TG-08 | Telegram retention and draft expiry command | TG-06 |
| TG-09 | Local long-polling command | TG-05 |
