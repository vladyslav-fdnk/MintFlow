# MintFlow MVP Definition

**Product principle:** Capture in Telegram. Understand on the Web.

**Target:** A commercially credible product for the first 100 users, focused only on recording personal expenses and turning them into understandable spending summaries.

# 1. Purpose

The MintFlow Platform MVP exists to prove that people who spend across everyday, international contexts will repeatedly use the Telegram Client to record expenses and then gain enough clarity from the Web Client dashboard to understand where their money went. It must validate the complete product loop—fast capture, trustworthy review, and useful reflection—not the recognition technology in isolation. The central question is whether this combination is meaningfully easier and more valuable than a spreadsheet, notes app, or conventional expense tracker.

# 2. MVP Success

The MVP is successful when all of the following are true during first-user testing and early commercial use:

- A new user can connect Telegram, record a first expense, and find it on the Web without assistance.
- A returning user can submit a manual expense in about 30 seconds and a clear receipt in about 60 seconds, including review.
- Receipt capture produces a reviewable draft even when recognition is incomplete; the user never has to restart the flow merely because recognition failed.
- Users can confidently correct every field before confirmation and can tell whether an expense was saved.
- Confirmed expenses appear consistently in both the Telegram Client and the Web Client.
- The dashboard answers three practical questions: “How much did I spend?”, “What did I spend it on?”, and “How is my spending changing over time?”
- Multiple currencies are never silently combined or converted; totals remain understandable and trustworthy.
- Users can correct or delete erroneous records without support intervention.
- Core capture, confirmation, history, and dashboard workflows work reliably on supported mobile and desktop browsers.
- In qualitative follow-up, target users report that the capture-to-understanding loop is useful enough to continue using weekly; observed repeat capture and dashboard return behavior supports that claim.

Success is judged by completed workflows, user comprehension, repeated use, and trust—not registrations, receipt upload volume, recognition accuracy in isolation, or social reach.

The product succeeds when users repeatedly capture expenses, return to the dashboard, trust their stored financial data, and prefer MintFlow over manual notes or spreadsheets. The MVP validates this product behavior; technical excellence alone does not constitute success.

# 3. User Journey

## First use

1. The user creates or signs into a MintFlow account on the Web.
2. The user connects their Telegram account through a clear, secure linking flow.
3. The user selects locale, timezone, and a default currency.
4. The product explains the core loop in one short onboarding message: capture in Telegram, understand on the Web.
5. The user is invited to record their first expense manually or with a receipt.

## Receipt journey

Open Telegram

↓

Send a clear receipt photo

↓

MintFlow acknowledges receipt and performs recognition

↓

MintFlow presents a draft containing merchant, date, currency, total, and category

↓

User reviews and edits any field

↓

User explicitly confirms

↓

Expense is saved and a concise success message is shown

↓

User opens the Web Client

↓

Dashboard and expense history include the confirmed expense

↓

User understands total spending, category distribution, and trend over time

## Manual journey

If no receipt is available—or recognition is slow or unsuccessful—the user starts manual entry, supplies the required fields, reviews the same draft, and explicitly confirms it. Manual capture is a first-class path, not an error fallback.

## Correction journey

The user can open a confirmed expense on the Web, correct it, save the change, and immediately see updated history and analytics. The user can also delete an erroneous expense after a clear confirmation step.

# 4. Functional Scope

## Must Have

- Secure account access and one-to-one linking of a MintFlow account with a Telegram account.
- First-use guidance for linking, capturing, reviewing, and opening the Web Client.
- Telegram Client manual expense capture.
- Telegram Client receipt-photo capture and recognition.
- A unified draft review flow with mandatory explicit confirmation.
- Draft editing and cancellation in the Telegram Client.
- Clear handling of missing fields, recognition failure, duplicate actions, timeouts, and unsupported input.
- Recent confirmed-expense history in the Telegram Client.
- Web Client dashboard with the minimal analytics defined in Section 8.
- Web Client expense history with filtering and pagination or incremental loading.
- Web Client expense detail, editing, and deletion.
- A small, useful system category set.
- Support for ISO 4217 fiat currencies without exchange-rate conversion.
- User settings for locale, timezone, default currency, and Telegram connection.
- Consistent financial data across the Telegram Client and Web Client.
- Account deletion suitable for an initial commercial service.
- Operational safeguards described in Section 9.

## Should Have

These are desirable for launch but may be deferred if they threaten the reliability or clarity of the Must Have loop:

- A receipt thumbnail or original image accessible from expense details while retained.
- Reuse of the most recently selected date range on the dashboard.
- A small set of contextual empty states and sample explanations for charts.
- User-initiated deletion of a retained receipt image while keeping the confirmed expense.
- Lightweight in-product feedback submission.

## Won't Have

The MVP includes only the Must Have items and any Should Have items explicitly accepted into the release. Everything else is outside the MVP. In particular, all capabilities listed in Section 11 are postponed.

# 5. Telegram Client

## Required features

### Account connection and guidance

- `/start` explains what MintFlow does and provides the secure account-linking route.
- The Telegram Client clearly identifies an unlinked user and does not accept financial data into an unknown account.
- A short help response explains supported inputs, how confirmation works, and how to open the Web Client.
- The user can disconnect Telegram from Web Client settings; the Telegram Client then stops exposing account information.

### Manual entry

- The user can intentionally start a manual expense.
- The flow collects amount, currency, transaction date, merchant or description, and category.
- Amount must be greater than zero; refunds, income, and transfers are rejected as unsupported.
- The default currency and today's date in the user's timezone may be proposed, but both remain visible and editable.
- The user can move backward or edit any field before confirmation.

### Receipt capture

- The Telegram Client accepts a single receipt image as a photo or supported image file.
- It immediately acknowledges that processing has begun.
- One receipt creates at most one expense draft.
- Recognition fills only the fields defined in Section 7.
- The user can continue manually when recognition is incomplete, uncertain, times out, or fails.
- Unsupported files and unreadable images receive a clear recovery instruction.

### Review, editing, and confirmation

- Every draft displays merchant, date, currency, total, and category in one concise review.
- Recognized, defaulted, and missing values are distinguishable enough to prompt careful review.
- The user can edit each field without resending the receipt.
- Confirmation is always an explicit action; viewing a draft or accepting a suggested edit never saves it.
- Confirmation is allowed only when total, currency, date, and a stored category are valid. Merchant may use a neutral user-visible fallback when absent.
- Repeated confirmation actions do not create duplicate expenses.
- Success states identify the saved amount and currency and offer links or actions for recent history and Web analytics.

### Draft control

- The user can cancel the active draft.
- When a new capture conflicts with an active draft, the Telegram Client asks the user to continue or discard the existing draft.
- Abandoned drafts expire after a documented short retention period, recommended as seven days after last activity.
- Expiration never creates an expense.

### History

- The Telegram Client shows a compact list of recent confirmed expenses, recommended as the latest 10.
- Each item shows date, merchant or description, amount, currency, and category.
- History is informational; full browsing and post-confirmation editing belong on the Web.
- A direct, authenticated route takes the user to Web expense history.

### Essential communication behavior

- Commands and buttons use consistent wording and avoid financial or technical jargon.
- The Telegram Client never claims an expense is saved before confirmation succeeds.
- Processing, recoverable error, cancellation, expiration, and saved states are unambiguous.
- Sensitive receipt contents and account data are never exposed in group chats; the MVP supports private bot chats only.

# 6. Web Client

## Dashboard

**Why it exists:** This is where captured records become financial understanding. It gives a compact summary of spending for a chosen period without requiring spreadsheet work.

It contains only the metrics, charts, filters, and insights defined in Section 8, plus a clear link to the underlying expense list.

## Expense history

**Why it exists:** Users need to verify completeness, locate mistakes, and inspect the transactions behind analytics.

It shows a readable list or table with date, merchant or description, category, amount, and currency. It supports date-range, category, and currency filters, predictable sorting by newest transaction date, and empty/loading/error states. Merchant search is postponed until after the MVP.

## Expense details

**Why it exists:** A list cannot safely present all context or destructive actions.

It shows all confirmed expense fields, capture source, confirmation time, and the receipt image when retained and available. It provides clear edit and delete actions. Raw recognition diagnostics are not user-facing.

## Expense editing

**Why it exists:** Trustworthy analytics require users to correct recognition and entry mistakes after confirmation.

The user can edit merchant or description, date, amount, currency, and category. The UI validates values, warns about unsaved changes, confirms successful saving, and refreshes affected analytics. It does not provide allocations, line items, refund handling, or bulk edit.

## Categories

**Why it exists:** A small category system makes the dashboard useful with stable, understandable classifications.

The MVP ships with system categories only: Groceries, Food & Dining, Transport, Shopping, Housing, Utilities, Health, Entertainment, Travel, Education, Gifts, Other, and Uncategorized. Their identities remain stable and their display labels may be localized. User-defined categories are intentionally postponed. Category hierarchy, icons, rules, and automatic assignment are excluded.

## Settings

**Why it exists:** International spending summaries depend on explicit personal conventions and users need control over connection and data.

Settings includes locale, timezone, default currency, Telegram Client connection status and disconnect action, and account deletion. It explains that currency totals are not converted. Full data export, profile customization, billing, teams, notification rules, and complex privacy controls are not included.

## Minimal account screens

Authentication, account linking, password or sign-in recovery, and legal/privacy acknowledgement screens are enabling screens rather than product destinations. They are required only to the extent needed for safe access to the six product pages above.

# 7. Recognition Pipeline MVP

The recognition pipeline attempts to extract exactly four receipt fields:

- **Merchant:** the best readable merchant or store name, preserving meaningful original text rather than performing advanced normalization.
- **Date:** the transaction date printed on the receipt, interpreted using the user's locale and reasonable receipt context.
- **Currency:** an ISO 4217 fiat currency inferred only from an explicit code, unambiguous symbol plus context, or supported locale evidence.
- **Total:** the final amount paid, excluding subtotal, tax, change, discounts, loyalty balances, and payment-card fragments.

Recognition does not assign a category in the MVP. A valid stored fallback category such as Uncategorized is proposed until the user selects another category.

## Required pipeline behavior

- Recognition returns partial results when only some fields can be extracted.
- Each extracted value has enough confidence or provenance information for the product to decide whether to prefill it or leave it missing.
- Recognition never saves an expense and never bypasses review.
- User edits take precedence over later or retried recognition results.
- A timeout or service failure produces a usable manual draft.
- The original receipt is associated with the draft and resulting expense only according to the documented retention policy.

## Acceptable failure modes

- Merchant is missing, contains harmless extra text, or needs user correction.
- Date is missing or ambiguous and requires user selection.
- Currency is missing when a symbol or locale is ambiguous.
- Total is missing when multiple plausible totals exist.
- A low-quality, cropped, handwritten, unusually formatted, or unsupported-language receipt cannot be recognized.
- Processing takes long enough that the user is offered manual completion.

These failures are acceptable only when they are explicit, recoverable without restarting, and never result in an unreviewed expense. Incorrect confident values are more harmful than missing values. User confirmation is mandatory for every receipt, regardless of confidence.

# 8. Analytics MVP

The dashboard uses one shared date-range filter and one optional currency filter. The default range is the current calendar month in the user's timezone. When more than one currency exists in the selected period, the dashboard requires a single currency selection or presents separate totals; it never adds unlike currencies together.

## Summary

- **Total spent:** sum of confirmed positive expenses for the selected period and currency.
- **Expense count:** number of confirmed expenses in the same selection.
- **Change from previous comparable period:** percentage and absolute difference in total spending, shown only when the comparison is mathematically meaningful and has data.

## Exactly three charts

1. **Spending over time — column chart.** Daily totals for ranges up to 31 days and monthly totals for longer supported ranges. It reveals spikes and direction without forecasting.
2. **Spending by category — horizontal bar chart.** Ranked category totals and percentages, with Uncategorized visible. It answers where money went more legibly than a crowded pie chart.
3. **Top merchants — horizontal bar chart.** The top five user-confirmed merchant or description values by total spend, with the remaining merchants grouped as “Other.” It highlights spending concentration without advanced normalization.

Every chart displays the currency, has an accessible textual/table equivalent, handles sparse and empty data, and links or filters through to the contributing expenses where practical.

## Exactly two generated insights

- **Largest category:** “Your largest category was X at Y, representing Z% of spending,” shown when the period contains categorized spending.
- **Largest expense:** “Your largest expense was X at Merchant on Date,” shown when the period contains expenses.

Insights are deterministic summaries of stored data, not AI advice, predictions, anomaly detection, or moral judgments. No other charts or generated insights are required for the MVP.

# 9. Product Metrics

The MintFlow Platform internally measures the following minimum product metrics to improve the capture-to-understanding loop:

- Average capture time.
- Draft completion rate.
- Draft abandonment rate.
- Recognition completion rate.
- Manual correction rate.
- Dashboard revisit rate.

These are internal product-improvement metrics, not user-facing analytics. Collection must follow the privacy, retention, and data-minimization requirements below.

# 10. Non-functional Requirements

## Performance

- Telegram acknowledges a message or button action within two seconds under normal operating conditions, even when recognition continues asynchronously.
- Recognition should normally return a draft within 15 seconds; after 30 seconds the user receives a clear delay state and can continue manually.
- Core Web pages should become usable within three seconds on a typical mobile 4G connection for first-100-user data volumes.
- Expense saves and edits should normally confirm within two seconds, with protection against repeated submission.

These are product targets measured at realistic percentiles, not guarantees for third-party outages.

## Reliability

- Confirmed expenses are not silently lost, duplicated, or partially saved.
- Confirmation, edit, and delete operations are safe to retry.
- Temporary Telegram, recognition, or network failures provide a recovery path.
- The product distinguishes pending, failed, cancelled, expired, and saved states.
- Core service availability has an initial target of 99.5% per calendar month, excluding announced maintenance, with third-party degradation communicated honestly.

## Privacy and security

- Collect only account, expense, receipt, preference, and operational data required for the MVP.
- Encrypt data in transit and use appropriate encryption at rest for persistent stores and backups.
- Restrict financial and receipt access to the authenticated owner and authorized operations personnel on a need-to-know basis.
- Never place receipt contents, tokens, or sensitive personal data in routine logs.
- Define and disclose retention for receipts, abandoned drafts, recognition artifacts, backups, and deleted accounts.
- Provide a clear privacy notice, receipt deletion where included, and a permanent account-deletion process.
- Telegram capture works only in private bot chats.

## Accessibility

- Target WCAG 2.2 AA for core Web workflows.
- All actions are keyboard-operable, focus is visible, forms have programmatic labels, errors are specific, and color is never the sole signal.
- Charts have meaningful text summaries or data-table equivalents.
- Mobile controls have practical touch targets and dialogs manage focus correctly.

## Internationalization

- User-facing strings are externalizable even if the first release launches in one language.
- Dates, decimal separators, amount formatting, and currency display respect the selected locale.
- Store and process Unicode merchant and category names.
- Use ISO 4217 fiat currency codes and always display a code when a symbol could be ambiguous.
- Analytics periods use the user's configured timezone.
- Do not convert currencies or imply that totals in different currencies are comparable.

## Responsiveness

- All core Web workflows work from 320 CSS pixels wide through common desktop widths.
- Mobile Web supports reviewing dashboard summaries and fully browsing, editing, and deleting expenses.
- No required interaction depends on hover, a large screen, or precision pointing.

## Backup expectations

- Automated encrypted backups cover confirmed expenses, users, categories, and settings at least daily.
- Backups use access controls separate from normal application access and have a documented retention period.
- A restore procedure is documented and successfully rehearsed before commercial launch and at least quarterly thereafter.
- Initial recovery targets are a maximum 24-hour recovery point and an eight-hour recovery time for core financial records.
- Receipt-image recovery expectations are explicitly documented; the product must not promise recoverability beyond its actual backup and retention behavior.

## Logging and observability

- Structured logs cover authentication, account linking, capture state transitions, recognition outcome categories, confirmations, edits, deletions, and failures without storing receipt contents or secrets.
- Logs include correlation identifiers sufficient to trace one workflow across Telegram, recognition, and Web.
- Operational metrics cover latency, error rate, recognition completion, queue age, and confirmation failure.
- Alerts exist for sustained capture failure, confirmation failure, backlog growth, and unavailable core dependencies.
- Security-sensitive user actions have a minimal audit record appropriate to the first 100 users.

## Error handling

- Errors state what happened, whether data was saved, and the user's next safe action.
- Validation occurs close to the relevant field and preserves valid input.
- Unexpected failures show a reference identifier for support without leaking internals.
- Third-party outages degrade to manual capture wherever possible.
- Destructive actions require confirmation and provide an unambiguous completion state.

# 11. Definition of Done

## Product

- Every Must Have capability is implemented; any included Should Have capability meets the same quality bar.
- The end-to-end Telegram-to-Web loop works for manual and receipt capture.
- At least five representative target users complete first capture, correction, and dashboard-understanding tasks; critical confusion is resolved.
- Success criteria have instrumentation or a documented qualitative evaluation method.
- Scope, supported inputs, currency behavior, retention, and known limitations are approved and communicated.

## Engineering

- Core workflows meet the performance, reliability, privacy, and retry-safety requirements in this document.
- Production configuration, secrets, access controls, migrations, backups, restores, monitoring, and rollback are operationally verified.
- No known critical or high-severity security defect remains open.
- No known defect can silently lose, duplicate, misattribute, or incorrectly aggregate a confirmed expense.
- Dependency and data-retention ownership is documented.

## UX

- A first-time user can link Telegram and save an expense without staff guidance.
- All capture states and save outcomes are understandable.
- Web core flows are responsive, keyboard-operable, and meet the stated accessibility target.
- Empty, loading, validation, recognition-failure, dependency-failure, and deletion states have reviewed copy and behavior.
- Multiple-currency analytics cannot be mistaken for converted totals.

## Testing

- Automated tests cover core product rules and the critical manual-capture, receipt-capture, confirmation, editing, deletion, and analytics paths.
- End-to-end tests verify that a confirmed Telegram expense appears correctly on the Web and changes update analytics.
- Tests cover recognition failure, partial recognition, ambiguous currency, retry, duplicate confirmation, abandoned drafts, and dependency outage behavior.
- Supported browsers and representative mobile sizes pass a documented smoke-test matrix.
- Accessibility checks combine automated scanning with manual keyboard and screen-reader smoke testing.
- Backup restoration and production rollback have been rehearsed.

## Documentation

- User help covers connection, manual entry, receipt quality, confirmation, corrections, currencies, privacy, deletion, and support.
- Internal runbooks cover deployment, rollback, monitoring, incident response, data restoration, third-party failure, and user support lookup.
- Product limitations and all postponed capabilities are recorded.
- Privacy notice, terms, retention policy, and support contact are published and consistent with actual behavior.

## Deployment

- A production-like staging environment passes the release checklist.
- Production uses controlled deployment with health checks and a tested rollback route.
- Monitoring, alerts, backups, domain/TLS, Telegram configuration, and recognition-provider limits are verified.
- A named owner can respond to launch incidents and user reports.
- A small controlled cohort completes smoke testing before access expands to the first 100 users.

# 12. Explicitly NOT Part of MVP

The following are intentionally postponed:

- Generative AI features, conversational financial advice, predictions, anomaly detection, and personalized recommendations.
- Receipt line items, item-level categories, quantities, tax breakdown, tips, discounts, and warranty tracking.
- Budgets, goals, envelopes, savings plans, and spending limits.
- Bank, card, open-banking, e-wallet, or accounting-platform synchronization.
- Income, refunds, reimbursements, transfers, assets, liabilities, investments, crypto, and net-worth tracking.
- Family, household, shared, team, accountant, role-based, or multi-user accounts.
- Recurring expenses, subscriptions, reminders, and scheduled transactions.
- Native iOS, Android, React Native, desktop, browser-extension, voice, email, or WhatsApp clients.
- Premium plans, subscriptions, trials, paywalls, coupons, invoices, and self-service billing.
- Custom recognition training, model fine-tuning, user-specific recognition learning, and recognition benchmarking as a standalone product.
- Receipt splitting, multi-expense receipts, split payments, shared bills, and category allocations.
- Advanced merchant normalization, merchant enrichment, logos, addresses, maps, and merchant rules.
- Automatic categorization, category rules, category hierarchy, tags, projects, and cost centers.
- User-defined categories and category management.
- Merchant search.
- Exchange-rate lookup, conversion, base-currency totals, historical rates, and foreign-exchange gains or fees.
- Bulk import, spreadsheet import, email forwarding, bulk edit, bulk delete, and migration from other products.
- Data export, custom reports, scheduled reports, tax reports, accounting reports, and printable statements.
- Custom dashboards, additional chart libraries, cohort comparisons, forecasts, cash-flow reports, and natural-language analytics.
- Receipt PDF parsing, multi-page receipts, handwritten-receipt guarantees, barcode scanning, and broad document ingestion.
- Offline mode and capture from Telegram group chats or channels.
- Multiple Telegram accounts per user or multiple MintFlow accounts per Telegram identity.
- Public API, webhooks, third-party integrations, plugin systems, and developer platform capabilities.
- Enterprise SSO, SCIM, organization administration, granular roles, custom compliance controls, and enterprise audit reporting.
- Localization into many launch languages unless commercially required; the MVP remains localization-ready.
- Custom themes, social features, gamification, streaks, leaderboards, advertising, and referrals.
- Full edit-history UI, event sourcing, legal-grade immutable audit trails, and undo for all actions.
- Advanced support tooling, automated support agents, and a full internal administration suite; minimal safe operational support is sufficient.

# 13. MVP Risks

## Product risks

### Capture is not materially faster than existing habits

If review requires too many steps, Telegram becomes another form rather than a capture advantage.

**Mitigation:** Time real first-use and repeat workflows; keep one active draft and one compact review; propose safe defaults; treat manual entry as first-class; remove any field that does not improve analytics integrity.

### Analytics do not justify returning to the Web

Three charts may still feel like a static record rather than useful understanding.

**Mitigation:** Test whether users can answer the three dashboard questions from Section 2; make every summary traceable to expenses; interview users after real spending history accumulates; add no chart unless repeated evidence identifies an unanswered high-value question.

### Users do not build a complete-enough record

Partial capture produces misleading insights and weakens trust.

**Mitigation:** Clearly describe analytics as based on recorded expenses; optimize repeat capture; show transaction count and Uncategorized visibly; avoid claims of complete financial position.

### International positioning exceeds MVP capability

No exchange conversion and limited language support may disappoint users who expect a unified global total.

**Mitigation:** Make per-currency behavior explicit in onboarding and dashboards; recruit early users whose primary need is multi-currency recording, not consolidated net worth; validate demand before building conversion.

## Technical risks

### Receipt recognition is inconsistent

Poor images, formats, languages, and ambiguous totals can create wrong confident drafts.

**Mitigation:** Prefer missing values over guesses, require confirmation, preserve manual completion, monitor field correction patterns, and narrow documented receipt support based on evidence.

### Asynchronous and repeated Telegram actions create duplicates or stale drafts

Network retries and delayed recognition can race with user edits or confirmation.

**Mitigation:** Make confirmation retry-safe, use explicit draft states, prevent late recognition from overwriting user edits, and test duplicated/out-of-order events.

### Third-party dependency outage breaks the core loop

Telegram or recognition-provider failures are outside MintFlow's control.

**Mitigation:** Acknowledge quickly, expose honest states, allow manual completion when recognition fails, monitor dependencies, and document outage handling.

### Financial or receipt data is exposed or lost

The sensitivity of purchase history raises the impact of access-control, logging, backup, or deletion defects.

**Mitigation:** Minimize data, enforce owner-scoped access, redact logs, encrypt transport and storage, rehearse restoration, test deletion, and limit staff access.

## UX risks

### Mandatory confirmation feels like friction

Confirmation is necessary for trust but can undermine the speed promise.

**Mitigation:** Present all fields in one concise review, make edits direct, keep the confirm action obvious, and measure end-to-end time rather than recognition time.

### Currency and date interpretation is confusing

Symbols, locale formats, and timezone boundaries can make correct-looking data wrong.

**Mitigation:** Display ISO currency codes, format using explicit locale, keep date editable, avoid silent inference when ambiguous, and state the analytics timezone.

### Users cannot tell pending from saved

Telegram delays or retries can cause duplicate attempts and loss of confidence.

**Mitigation:** Use distinct processing, draft, saving, saved, and failed language; never use a success-looking message before persistence completes.

## Commercial risks

### The product is useful but not valuable enough to pay for

Early engagement does not necessarily demonstrate commercial demand.

**Mitigation:** Recruit a narrow target segment with frequent receipt capture and international spending; discuss willingness to pay after sustained real use; test price expectations manually without building billing or Premium.

### Privacy concerns block adoption

Users may hesitate to send receipts through Telegram or store financial history with a new service.

**Mitigation:** Explain data flow and retention plainly, support account deletion, minimize stored receipt duration, publish credible policies, and avoid exaggerated security claims.

### Support cost overwhelms the first-100-user team

Recognition edge cases and account-linking issues can create high-touch support.

**Mitigation:** Instrument failure categories, provide actionable recovery text, maintain concise help content and support runbooks, and roll out in controlled cohorts.

# 14. First Release Checklist

## Scope and product decisions

- [ ] Approve this MVP definition and assign an owner for scope changes.
- [ ] Confirm the initial target-user segment and recruitment plan.
- [ ] Freeze mandatory expense fields, category set, supported currencies, locale, timezone, and positive-expense rules.
- [ ] Confirm draft, receipt, recognition-artifact, backup, and deleted-account retention periods.
- [ ] Confirm supported receipt input formats and launch language.
- [ ] Define the account-deletion promise.
- [ ] Record every accepted Should Have item; leave all others out of the release.

## Telegram capture milestone

- [ ] Complete secure account linking and disconnect behavior.
- [ ] Complete `/start`, help, unsupported-input, and unlinked-user experiences.
- [ ] Complete manual expense entry and validation.
- [ ] Complete single-image receipt capture and immediate acknowledgement.
- [ ] Complete partial, delayed, failed, and timed-out recognition paths.
- [ ] Complete unified review and every field edit.
- [ ] Complete explicit, retry-safe confirmation and success response.
- [ ] Complete draft conflict, cancellation, and expiration behavior.
- [ ] Complete recent-expense history and authenticated Web route.
- [ ] Verify private-chat-only handling and safe group-chat response.

## Web Client milestone

- [ ] Complete authentication, recovery, and first-use connection guidance.
- [ ] Complete Dashboard with exactly the Section 8 scope.
- [ ] Complete expense history and required filters.
- [ ] Complete expense details, editing, validation, and deletion.
- [ ] Complete system category behavior.
- [ ] Complete settings for locale, timezone, default currency, Telegram Client, and account deletion.
- [ ] Complete mobile layouts and all empty/loading/error states.

## Trust and international behavior milestone

- [ ] Verify ISO currency codes and locale-aware amounts and dates everywhere.
- [ ] Verify analytics never combine unlike currencies.
- [ ] Verify analytics periods use the configured timezone.
- [ ] Verify Telegram and Web show consistent confirmed records.
- [ ] Verify edits and deletion update every affected view and metric.
- [ ] Publish privacy notice, terms, retention details, limitations, and support contact.

## Quality milestone

- [ ] Pass automated tests for product rules and critical workflows.
- [ ] Pass end-to-end manual and receipt capture through Web analytics.
- [ ] Pass retry, duplicate, race, dependency-outage, and recognition-failure tests.
- [ ] Pass supported-browser and responsive-layout checks.
- [ ] Pass accessibility scan plus keyboard and screen-reader smoke tests.
- [ ] Resolve all critical and high-severity security findings.
- [ ] Confirm no open defect can silently lose, duplicate, expose, or misaggregate expense data.
- [ ] Complete usability sessions with at least five representative users and resolve critical findings.

## Operations and release milestone

- [ ] Verify production secrets, access controls, TLS, and Telegram configuration.
- [ ] Verify monitoring, correlation identifiers, dashboards, and alerts.
- [ ] Verify encrypted backups and rehearse restoration against recovery targets.
- [ ] Rehearse deployment rollback and third-party outage response.
- [ ] Publish user help and internal support/incident runbooks.
- [ ] Name the launch incident and user-support owner.
- [ ] Pass staging release checks with production-like dependencies.
- [ ] Release to a small controlled cohort and run production smoke tests.
- [ ] Review real workflow completion, correction patterns, dashboard comprehension, repeat use, and support load before expanding toward 100 users.
- [ ] Capture post-MVP requests in a backlog without adding them to the release unless they block the core promise.
