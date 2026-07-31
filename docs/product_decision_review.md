The recommendations below record the finalized MVP defaults and their trade-offs.

------------------------------------------------

## 1. Refunds and negative expenses

### Why it matters

Refunds affect the meaning of spending totals, category analytics, merchant comparisons, and month-over-month reporting. Treating a refund as an ordinary negative Expense is simple, but it can make the domain vocabulary and analytics ambiguous.

### Option A — Do not support refunds in the MVP

Reject zero and negative amounts. Users may manually adjust or delete the original Expense.

**Pros**

- Simplest financial model.
- All Expenses represent positive spending.
- Analytics remain easy to understand.
- No refund matching or cross-period behavior.

**Cons**

- Users cannot accurately represent common refunds.
- Editing the original Expense can falsify historical timing.
- Deleting a purchase loses useful history.
- A refund received in a later month cannot be represented correctly.

### Option B — Allow negative Expense amounts

A negative amount represents money returned to the user.

**Pros**

- Minimal structural change.
- Supports partial and cross-period refunds.
- Net spending can be calculated naturally.

**Cons**

- “Expense” no longer always means spending.
- Users may enter negatives accidentally.
- Gross spending and refund totals require separate calculations.
- The Recognition Pipeline should not infer a negative value without clear evidence.
- Transfers and income could later be confused with negative Expenses.

### Option C — Add an Expense kind

Use `purchase` and `refund` kinds while storing a positive Money amount. A refund may optionally reference the original Expense later.

**Pros**

- Intent is explicit.
- Analytics can show gross spending, refunds, and net spending.
- Avoids ambiguous negative amounts.
- Supports unmatched and partial refunds.

**Cons**

- Adds an enum and branching rules.
- Capture and review need another concept.
- Linking partial refunds introduces additional behavior.
- More work than the initial workflow requires.

### Recommendation for MintFlow MVP

Choose Option A for the first release: support positive spending only and explicitly document that refunds are not yet supported. Do not encourage users to edit the original purchase as an official refund workflow.

Before a broader commercial release, Option C is the cleaner extension. Avoid establishing negative Expense amounts as permanent domain semantics merely because they are easy to store.

Technical consequence: validate that confirmed Money is strictly positive. No refund-specific schema or analytics are needed initially.

### Long-term implications

Adding an Expense kind later is moderately expensive because analytics, API contracts, UI labels, exports, and historical assumptions must be reviewed. It is still manageable if the MVP clearly treats all existing Expenses as `purchase`.

------------------------------------------------

## 2. Can confirmed expenses have zero amount?

### Why it matters

A zero-value record contributes nothing to spending analytics and is commonly caused by incomplete recognition, a default value, or user error. Some legitimate receipts may show zero after discounts, but they provide little value to the MVP’s financial purpose.

### Option A — Prohibit zero amounts

Only amounts greater than zero can be confirmed.

**Pros**

- Prevents incomplete drafts from becoming Expenses.
- Keeps spending records meaningful.
- Simplifies analytics.
- Provides a clear confirmation rule.

**Cons**

- Cannot represent fully discounted purchases or free transactions.
- May exclude rare but legitimate receipts.

### Option B — Allow zero amounts

Zero is treated as a valid confirmed amount.

**Pros**

- Accurately reflects free or fully discounted receipts.
- Avoids imposing a financial interpretation.

**Cons**

- Makes recognition failures easier to confirm accidentally.
- Creates history entries with no analytical value.
- Requires UI distinctions between missing and deliberately zero.
- Can distort transaction-count analytics.

### Recommendation for MintFlow MVP

Choose Option A. Confirmed Expenses must have an amount greater than zero.

The domain must still distinguish “missing amount” from zero while editing a CaptureDraft.

Technical consequence: Money may technically represent zero, but Expense confirmation rejects it. This keeps the Money value object reusable without making every zero value a valid Expense.

### Long-term implications

Allowing zero later is easy. It is primarily a validation and UI change, provided missing values are never encoded as zero.

------------------------------------------------

## 3. Which fields are mandatory for confirmation?

### Why it matters

Mandatory fields determine capture speed, data quality, and the usefulness of analytics. Requiring too much defeats the 20–30 second capture goal; requiring too little produces weak financial history.

### Option A — Require every structured field

Require amount, currency, date, merchant, and category. Note remains optional.

**Pros**

- Rich, consistent analytics.
- Merchant and category views are immediately useful.
- Few incomplete records.

**Cons**

- High capture friction.
- Weak recognition forces additional editing.
- Unknown merchants or ambiguous categories block confirmation.
- Users may choose incorrect values merely to proceed.

### Option B — Require only amount, currency, and date

Merchant, category, and note are optional.

**Pros**

- Fastest reliable capture.
- These three fields are sufficient for basic spending totals and trends.
- Recognition failure does not block confirmation.
- Users are not forced to invent information.

**Cons**

- Category and merchant analytics may be incomplete.
- Web dashboards need explicit uncategorized/unknown groupings.
- Users may never enrich older records.

### Option C — Require amount, currency, date, and a stored category

The user does not have to select a category. An unclassified Expense receives a system `Uncategorized` category. Merchant and note remain optional.

**Pros**

- Preserves fast capture.
- Every Expense remains categorically queryable.
- Missing categorization is visible and actionable.
- No nullable category logic in analytics.

**Cons**

- `Uncategorized` may become large.
- A default category is not meaningful classification.
- The UI must not imply the user deliberately selected it.

### Recommendation for MintFlow MVP

Choose Option C:

- Amount: required.
- Currency: required.
- Transaction date: required.
- Category identity: always stored, but explicit user selection is optional.
- Merchant: optional.
- Note: optional.

Use a system `Uncategorized` category when the user does not select one. The confirmation screen must show this clearly.

Technical consequence: Expense can require a Category reference without making category selection a capture blocker. Merchant and note must remain nullable or optional domain values.

### Long-term implications

Making optional fields mandatory later is expensive for existing incomplete data. Relaxing mandatory fields later is easy. Starting with the smallest reliable financial core is safer.

------------------------------------------------

## 4. Should today’s date be proposed automatically?

### Why it matters

Most expenses are captured near the time they occur. Defaulting the date saves interaction time, but receipts are often uploaded later, and a silent default can create incorrect monthly analytics.

### Option A — Always propose today in the User’s timezone

**Pros**

- Fast manual entry.
- Most real-time captures need no date interaction.
- Simple and predictable.

**Cons**

- Wrong for older receipts.
- Can be mistaken for recognized data.
- Timezone mistakes can place an Expense on the wrong day.

### Option B — Leave date empty until entered or recognized

**Pros**

- Never invents a date.
- Forces deliberate accuracy.

**Cons**

- Adds friction to every manual capture.
- Makes recognition failure more tedious.
- Works against the fast-capture principle.

### Option C — Propose today with explicit provenance

For manual capture, today is a visible default. For receipt capture, a credible recognition proposal takes priority; otherwise today is offered as a fallback.

**Pros**

- Fast happy path.
- The user sees that the value is a default.
- Works consistently with field provenance.
- Avoids pretending today came from the receipt.

**Cons**

- Users may still confirm without noticing an incorrect date.
- Requires the review UI to communicate source clearly.

### Recommendation for MintFlow MVP

Choose Option C. Propose the User’s current local date, mark it as a default, and expose it on the review screen.

Technical consequence: CaptureDraft must distinguish default, recognition, and user provenance. TransactionDate remains explicitly confirmed even when its initial value was automatic.

### Long-term implications

Easy to change. Default-selection policy belongs mainly in application behavior, provided provenance is preserved.

------------------------------------------------

## 5. Should future transaction dates be allowed?

### Why it matters

Future dates commonly indicate input or recognition errors. However, timezone boundaries and clock errors can make a transaction appear one day ahead.

### Option A — Reject every future date

**Pros**

- Strong protection against recognition mistakes.
- Expenses cannot appear in future analytics periods.

**Cons**

- Can reject legitimate edge cases near timezone boundaries.
- Device or account timezone errors can block capture.
- Correcting the date becomes mandatory.

### Option B — Allow any future date

**Pros**

- Maximum flexibility.
- No special validation.

**Cons**

- Recognition errors can silently corrupt analytics.
- Users may accidentally enter a year or month incorrectly.
- Future expenses overlap conceptually with planned spending, which is deferred.

### Option C — Allow a small tolerance and reject distant future dates

For example, allow tomorrow relative to the User’s timezone, but reject anything later.

**Pros**

- Handles timezone and late-night edge cases.
- Blocks obvious recognition and entry errors.
- Simple to explain.

**Cons**

- The tolerance is still a policy choice.
- A future date may remain surprising.

### Recommendation for MintFlow MVP

Choose Option C. Allow today and, at most, the next local calendar day; reject later dates during confirmation.

Technical consequence: confirmation needs the User’s timezone and current clock. Tests must use an injected clock rather than actual wall time.

### Long-term implications

Easy to change as a validation policy. Do not design future planned Expenses around this exception.

------------------------------------------------

## 6. Expense deletion

### Why it matters

Deletion affects analytics, user trust, support, accidental data loss, privacy, and auditability.

### Option A — Hard delete

The Expense is immediately and permanently removed.

**Pros**

- Simple user expectation.
- Strong data-minimization behavior.
- No deleted records in ordinary storage.
- Simple analytics queries.

**Cons**

- Accidental deletion cannot be reversed.
- Support cannot investigate what happened.
- Related receipt and audit behavior can be difficult.
- Referential cleanup must be carefully defined.

### Option B — Soft delete

The Expense remains stored with a deletion timestamp and is excluded from normal history and analytics.

**Pros**

- Recoverable.
- Supports audit and troubleshooting.
- Related records remain stable.
- Common commercial-product behavior.

**Cons**

- Every financial query must exclude deleted records.
- “Delete” may not mean immediate erasure.
- Retention and permanent purge rules are required.
- Soft-delete bugs can leak records into analytics.

### Option C — Archive

The Expense remains an active financial record but is hidden from default views.

**Pros**

- Useful for decluttering.
- Fully reversible.
- Preserves analytics if archive means hidden-only.

**Cons**

- Does not satisfy a user’s intent to delete incorrect data.
- Whether archived records count in analytics is confusing.
- Introduces a feature the MVP does not need.

### Recommendation for MintFlow MVP

Choose Option B with a simple recoverable deletion policy:

- Deleted Expenses are excluded from history and analytics.
- Restoration may be supported on the web.
- Permanent account deletion remains separate.
- Do not add archive behavior.

Technical consequence: Expense needs deletion metadata, and every history and analytics query must apply the same active-record rule. Centralizing this rule is important.

### Long-term implications

Changing from soft to hard deletion is manageable through a purge process. Changing from hard deletion to recovery is impossible for already deleted records. Archive can be added independently later if users request it.

------------------------------------------------

## 7. Should editing confirmed expenses create an audit history?

### Why it matters

Confirmed Expenses drive analytics. Without history, MintFlow cannot explain why a result changed or distinguish recognition output from later user edits.

### Option A — No edit history

Store only current values and `updated_at`.

**Pros**

- Lowest implementation complexity.
- Minimal storage.
- Simple domain and persistence.

**Cons**

- Changes cannot be explained or undone.
- Support and debugging are weaker.
- Recognition-quality evaluation may confuse later edits with capture corrections.

### Option B — Store a lightweight immutable change record

For each edit, retain who changed it, when, which fields changed, and their previous/new values.

**Pros**

- Strong traceability.
- Supports future undo or activity display.
- Helps diagnose unexpected analytics.
- Reasonable commercial-quality baseline.

**Cons**

- Adds persistence and privacy considerations.
- Sensitive historical values remain retained.
- Requires consistent recording across all edit paths.

### Option C — Full event sourcing

Reconstruct Expense state from all historical events.

**Pros**

- Complete history and temporal reconstruction.
- Powerful future analysis.

**Cons**

- Disproportionately complex.
- Makes ordinary reads, migrations, and corrections harder.
- Unnecessary for the first 100 users.

### Recommendation for MintFlow MVP

Choose Option B, but keep it restrained. Record confirmed-Expense edits and deletions, not every CaptureDraft keystroke or Telegram interaction.

Technical consequence: persistence design needs a lightweight immutable audit mechanism before implementation. This is one of the few cases where an additional supporting record may be justified, but it does not need to become a domain aggregate.

### Long-term implications

Adding history after launch cannot recover earlier edits. Therefore, postponing it loses information permanently. Expanding a lightweight audit record later is relatively easy; moving to full event sourcing would remain expensive and is unlikely to be necessary.

------------------------------------------------

## 8. Can a user delete a receipt image while keeping the expense?

### Why it matters

Receipt images can contain sensitive information and consume storage. Expense is the financial record; requiring the image to remain would contradict user ownership and couple the supporting document too tightly to the Expense.

### Option A — Yes, independently delete the image

The Expense and confirmed values remain.

**Pros**

- Strong privacy and data ownership.
- Reduces storage.
- Preserves useful financial history.
- Reinforces that Receipt is supporting evidence.

**Cons**

- The user can no longer inspect or reprocess the receipt.
- Support cannot verify the source image.
- Future item extraction becomes impossible for that Receipt.

### Option B — Delete Receipt only with Expense

**Pros**

- Simple relationship.
- Evidence is always available for existing Expenses.
- Future reprocessing remains possible.

**Cons**

- Unnecessarily retains sensitive documents.
- Couples two concepts with different purposes.
- Weakens user control.

### Option C — Hide the Receipt but retain it temporarily

**Pros**

- Offers recovery.
- Protects against accidental deletion.

**Cons**

- “Delete” becomes less transparent.
- Requires a purge schedule.
- Continued retention must be disclosed.

### Recommendation for MintFlow MVP

Choose Option A. Users may remove the Receipt image without deleting the Expense. Make the action and its irreversibility explicit.

Technical consequence: Expense must tolerate a missing or removed Receipt. Receipt deletion cannot cascade to Expense. Any recognition retry must be disabled after image removal.

### Long-term implications

Easy if designed from the start. Expensive if Expense validity is initially made dependent on permanent Receipt availability.

------------------------------------------------

## 9. How long should abandoned drafts live?

### Why it matters

Short retention frustrates users who return later; indefinite retention accumulates images and sensitive unfinished data.

### Option A — Short retention, such as 24 hours

**Pros**

- Strong data minimization.
- Low storage usage.
- Little abandoned state.

**Cons**

- Users lose drafts after normal interruptions.
- Poor recovery experience.
- May require receipt resubmission.

### Option B — Moderate retention, such as 7 days after last activity

**Pros**

- Enough time to resume most interrupted captures.
- Predictable cleanup.
- Reasonable privacy/storage balance.

**Cons**

- Requires expiration processing.
- Users returning after a week lose the draft.

### Option C — Indefinite retention

**Pros**

- Drafts can always be resumed.
- No expiration surprise.

**Cons**

- Accumulates sensitive images and incomplete data.
- Creates clutter.
- Complicates privacy expectations.
- Avoids rather than resolves lifecycle management.

### Recommendation for MintFlow MVP

Choose Option B: expire abandoned drafts seven days after their last meaningful user activity.

The UI should communicate expiration when relevant. Active recognition processing should not cause a draft to expire.

Technical consequence: CaptureDraft needs last-activity or expiry information, and a periodic cleanup use case must expire drafts and apply the Receipt retention policy.

### Long-term implications

The duration is easy to change. Transition semantics and cleanup behavior should be established before persistence design.

------------------------------------------------

## 10. How long should recognition results be retained?

### Why it matters

Recognition results help explain proposals and evaluate quality, but they may reproduce sensitive receipt content. Keeping them indefinitely without purpose increases privacy risk.

### Option A — Delete immediately after confirmation

**Pros**

- Strong data minimization.
- Lowest privacy exposure.
- Simple user-data story.

**Cons**

- Weak diagnostics.
- Cannot evaluate field-level recognition corrections.
- Harder to improve the pipeline using real failures.
- Provenance becomes limited.

### Option B — Retain for a limited period

For example, retain detailed results for 30 days after confirmation or draft termination, then purge them.

**Pros**

- Supports short-term diagnostics and quality measurement.
- Limits long-term sensitive-data exposure.
- Gives time to investigate user-reported problems.

**Cons**

- Requires scheduled deletion.
- Retention policy must be enforced and documented.
- Long-term model evaluation needs anonymized aggregate metrics or separately consented data.

### Option C — Retain as long as the Receipt or account exists

**Pros**

- Maximum future reprocessing and debugging capability.
- Complete provenance remains available.

**Cons**

- High privacy cost.
- Creates a large historical recognition dataset.
- Provider output formats complicate long-term maintenance.
- Users may not expect extracted text to persist indefinitely.

### Recommendation for MintFlow MVP

Choose Option B: retain detailed RecognitionResults for 30 days after confirmation, cancellation, or expiration, unless the User deletes the Receipt sooner.

Retain only non-sensitive aggregate quality metrics longer, such as whether merchant, total, or date required correction. Any corpus-building use should require a separate, explicit policy.

Technical consequence: results need retention timestamps and purge behavior. CaptureDraft must already contain or transfer the confirmed final values before recognition details disappear.

### Long-term implications

Retention duration is easy to change prospectively. It is impossible to recover purged results, which is acceptable if improvement datasets are handled through a separate consented process.

------------------------------------------------

## 11. User deletion strategy

### Why it matters

A personal-finance account contains highly sensitive data. Deletion affects every owned aggregate, receipt files, recognition output, authentication identities, backups, and audit information.

### Option A — Immediate permanent deletion

Delete all active data as soon as the User confirms account deletion.

**Pros**

- Clear and privacy-forward.
- No prolonged active retention.
- Simple user message.

**Cons**

- Accidental or compromised-account deletion cannot be reversed.
- Distributed file and backup cleanup still may not be literally immediate.
- Operational failures are harder to recover from.

### Option B — Grace period followed by permanent deletion

Deactivate the account immediately, allow recovery for a short period, then purge owned data.

**Pros**

- Protects against mistakes and account compromise.
- Provides time to complete cleanup reliably.
- Common and understandable behavior.

**Cons**

- Data remains retained during the grace period.
- Requires explicit status and scheduled purge.
- User messaging must distinguish deactivation from final deletion.

### Option C — Anonymize financial data but retain it

**Pros**

- Preserves aggregate product analytics.
- Reduces some identifying information.

**Cons**

- Receipt and spending patterns may still be identifying.
- True anonymization is difficult.
- Conflicts with straightforward user expectations.
- Unnecessary for the first 100 users.

### Recommendation for MintFlow MVP

Choose Option B:

- Immediately disable access and capture.
- Offer a short grace period, such as 14 days.
- Then purge User-owned Expenses, drafts, receipts, recognition data, and identity links.
- Handle operational backups under a documented expiry process.
- Full data export is postponed until after the MVP and is not required before deletion.

This is product architecture guidance, not legal advice; applicable legal requirements should be reviewed separately before launch.

Technical consequence: User needs a deletion/deactivation state and scheduled finalization. Ownership and deletion behavior must be explicit for every stored record and object.

### Long-term implications

This decision strongly affects foreign keys, object-storage cleanup, authentication, and operations. Changing it after persistence design is expensive.

------------------------------------------------

## 12. Initial system category set

### Why it matters

Categories determine whether early analytics are understandable. Too many create Telegram friction; too few put most spending into `Other`.

### Option A — Very small set

Examples: Food, Transport, Housing, Shopping, Other.

**Pros**

- Fast selection.
- Simple localization.
- Easy analytics.

**Cons**

- Groups unlike spending together.
- Weak explanations of spending changes.
- Users may want user-defined categories sooner.

### Option B — Moderate international set

Examples:

- Groceries
- Food & Dining
- Transport
- Shopping
- Housing
- Utilities
- Health
- Entertainment
- Travel
- Education
- Gifts
- Other
- Uncategorized

**Pros**

- Covers common personal spending.
- Still manageable in Telegram.
- Produces useful category analytics.
- Avoids country-specific tax or merchant assumptions.

**Cons**

- Some distinctions remain subjective.
- Housing and Utilities may overlap.
- Localization needs careful wording.

### Option C — Large detailed taxonomy

Include insurance, childcare, pets, gifts, electronics, personal care, subscriptions, and many more.

**Pros**

- Detailed analytics.
- Less need for user-defined categories.

**Cons**

- Slow capture.
- Difficult category choice.
- Localization and maintenance cost.
- False precision for an MVP.

### Recommendation for MintFlow MVP

Choose Option B, with a moderate flat set:

- Groceries
- Food & Dining
- Transport
- Shopping
- Housing
- Utilities
- Health
- Entertainment
- Travel
- Education
- Gifts
- Other
- Uncategorized

The MVP provides system categories only. User-defined categories are intentionally postponed. `Other` represents deliberately classified miscellaneous spending, while `Uncategorized` remains the explicit fallback for expenses not yet classified.

Technical consequence: system categories need stable semantic identifiers independent of translated labels.

### Long-term implications

Adding categories is easy. Merging or changing their semantic meaning is expensive because historical analytics may shift. Start with stable, broad meanings.

------------------------------------------------

## 13. Are categories mandatory?

### Why it matters

Category analytics are central to MintFlow, but requiring explicit categorization during capture increases friction.

### Option A — User must select a category before confirmation

**Pros**

- Every Expense is intentionally classified.
- Category analytics are immediately useful.
- No backlog of uncategorized data.

**Cons**

- Slows every capture.
- Forces guesses.
- Recognition does not initially include category.
- Conflicts with fast confirmation.

### Option B — Category is fully optional and nullable

**Pros**

- Lowest capture friction.
- Honest representation of missing classification.

**Cons**

- Every query must handle null.
- “Missing” is less visible as a user action item.
- Category invariants become weaker.

### Option C — Stored category required, user selection optional

Use `Uncategorized` when no category is chosen.

**Pros**

- Fast capture.
- Simple analytics and filtering.
- Unclassified Expenses are visible.
- Users can categorize later on the web.

**Cons**

- `Uncategorized` may become a large bucket.
- Requires careful wording so it is not mistaken for a deliberate category.

### Recommendation for MintFlow MVP

Choose Option C. Category is structurally required on Expense, but explicit category selection is not required for confirmation.

Technical consequence: a stable system `Uncategorized` Category must always exist and remain assignable.

### Long-term implications

Easy to introduce reminders or automatic suggestions later. Migrating from nullable categories to a required reference would require data cleanup, so establishing `Uncategorized` early is preferable.

------------------------------------------------

## 14. Supported currencies in the MVP

### Why it matters

Currency support affects Money validation, formatting, analytics, exports, recognition, and database constraints. Crypto assets have different precision and semantics from ordinary purchase currencies.

### Option A — Small configured currency list

Support only currencies expected among initial users.

**Pros**

- Simple testing.
- Predictable formatting.
- Fewer recognition cases.

**Cons**

- Conflicts with international positioning.
- Requires releases to add ordinary fiat currencies.
- Rejects legitimate receipts unnecessarily.

### Option B — ISO 4217 fiat currencies

Allow recognized ISO currency codes, while testing a smaller subset thoroughly.

**Pros**

- International by design.
- Stable industry standard.
- Currency metadata defines decimal behavior.
- No exchange conversion is implied.

**Cons**

- Not every currency will be equally tested.
- Currency metadata must be maintained.
- Historical or special-purpose ISO codes require policy decisions.

### Option C — Fiat and crypto assets

**Pros**

- Broadest financial coverage.
- Future-friendly for crypto users.

**Cons**

- Crypto is not consistently covered by ISO 4217.
- Precision can be much greater.
- Receipt purchases and asset transactions are different domains.
- Introduces valuation and exchange-rate expectations.

### Recommendation for MintFlow MVP

Choose Option B: support active ISO 4217 fiat currencies. Publish which currency/market combinations have tested recognition support.

Do not support crypto in the MVP.

Technical consequence: CurrencyCode validation needs a versioned currency catalogue, including minor-unit metadata. Analytics must remain partitioned by currency.

### Long-term implications

Adding more fiat currencies within the same model is easy. Adding crypto is expensive because Money precision, validation, analytics, and product semantics may all change.

------------------------------------------------

## 15. Maximum allowed expense amount

### Why it matters

A maximum prevents recognition errors, malicious input, integer overflow, and unusable analytics. A threshold that is too low rejects legitimate high-inflation currencies and large purchases.

### Option A — No product maximum beyond storage capacity

**Pros**

- Never rejects legitimate values.
- Simplest product rule.

**Cons**

- Recognition errors can create absurd amounts.
- Implementation limits become accidental business rules.
- Harder to provide useful validation.

### Option B — One hard limit in minor units

**Pros**

- Simple validation.
- Protects integer storage.

**Cons**

- Unequal meaning across currencies with different minor units.
- Poor fit for high-value or high-inflation currencies.

### Option C — Large hard safety limit plus user warning

Use a very high representational cap and warn for amounts unusual relative to currency or user history.

**Pros**

- Prevents pathological values.
- Rarely blocks legitimate spending.
- Warning behavior can improve independently.
- Does not require exchange rates.

**Cons**

- Currency-relative warnings require tuning.
- User-history warnings are not useful for brand-new users.
- A universal hard cap remains somewhat arbitrary.

### Recommendation for MintFlow MVP

Choose Option C.

Use a high technical cap, such as 999,999,999 major currency units, interpreted using that currency’s minor-unit definition. Show a confirmation warning for unusually large values, but do not introduce complex behavioral anomaly detection.

Technical consequence: Money and input parsing need overflow-safe validation. The hard cap should be configurable and tested across zero-, two-, and three-decimal currencies.

### Long-term implications

Changing the cap is easy if it is not encoded as a narrow database numeric type. Reducing it later does not invalidate historical values automatically.

------------------------------------------------

## 16. Can one receipt ever create more than one expense?

### Why it matters

Allowing one Receipt to produce several Expenses introduces allocation, split rules, shared totals, and confirmation complexity. It may eventually support split-category purchases, but receipt line items are explicitly deferred.

### Option A — One Receipt creates at most one Expense

**Pros**

- Matches the core capture workflow.
- Simple confirmation and idempotency.
- Clear ownership and analytics.
- Avoids split reconciliation.

**Cons**

- Cannot split one receipt across categories.
- Users must choose one category.
- Shared or reimbursable purchases are not represented precisely.

### Option B — One Receipt may create several Expenses

**Pros**

- Supports category splits.
- Handles shared purchases and reimbursements.
- More flexible financial representation.

**Cons**

- Must ensure component amounts reconcile with the receipt total.
- Confirmation becomes a multi-record transaction.
- Editing and deletion semantics become more complicated.
- Overlaps with future receipt-item functionality.

### Option C — One Expense with category allocations

**Pros**

- Preserves one financial record.
- Enables category splits without multiple Expenses.

**Cons**

- Introduces allocation entities and analytics complexity.
- Not justified for the MVP.
- Requires rounding and reconciliation rules.

### Recommendation for MintFlow MVP

Choose Option A: one Receipt supports one CaptureDraft, which creates at most one Expense.

Technical consequence: confirmation and idempotency remain straightforward. Persistence can enforce one resulting Expense per CaptureDraft. Be cautious about making the Receipt-to-Expense relationship impossible to generalize, but do not model generalization now.

### Long-term implications

Adding splits later is moderately to highly expensive because capture, review, analytics, editing, and persistence all change. That cost is acceptable because implementing speculative split behavior now would be worse.

------------------------------------------------

## 17. Can the currency of a confirmed expense be changed?

### Why it matters

Recognition and user input can select the wrong currency. Preventing correction preserves immutability but leaves financial history incorrect.

### Option A — Currency cannot be changed after confirmation

**Pros**

- Strong financial stability.
- Simpler audit behavior.
- Avoids dramatic analytics changes.

**Cons**

- A capture mistake requires deleting and recreating the Expense.
- Poor user experience.
- Original Receipt may no longer be available.

### Option B — Currency can be edited like any other field

**Pros**

- Users can correct mistakes.
- Consistent editing experience.
- No delete-and-recreate workflow.

**Cons**

- Amount meaning changes substantially.
- Analytics totals can move between currency groups.
- Accidental changes are consequential.

### Option C — Currency can change only through an explicit amount-and-currency correction

Treat Money as one atomic field; changing currency requires reviewing the amount at the same time.

**Pros**

- Prevents changing `100 PLN` to `100 EUR` casually.
- Preserves Money as an atomic concept.
- Still permits corrections.

**Cons**

- Slightly more UI work.
- Requires audit recording.
- Users must reconfirm an amount they may not be changing numerically.

### Recommendation for MintFlow MVP

Choose Option C. Allow correction, but edit amount and currency together as Money and show a clear warning.

Technical consequence: Expense edits must replace Money atomically. The audit record should capture both old and new Money values.

### Long-term implications

Easy if Money is already modeled atomically. Prohibiting changes initially and allowing them later is also easy, but it creates unnecessary correction friction.

------------------------------------------------

## 18. Which timezone defines analytics periods?

### Why it matters

“Today,” “this month,” and period comparisons need stable boundaries. However, Expense uses a user-confirmed local TransactionDate rather than a UTC instant.

### Option A — Current User timezone

The current month is determined from the User’s current timezone.

**Pros**

- Matches the User’s present experience.
- Simple for dashboard period selection.
- Handles travel and timezone changes naturally for “now.”

**Cons**

- Changing timezone can change which current day the dashboard considers active.
- It must not reinterpret historical TransactionDates.

### Option B — Timezone captured on every Expense

**Pros**

- Preserves original capture context.
- Supports precise travel history.

**Cons**

- Adds data and complexity.
- A receipt’s purchase timezone may differ from capture timezone.
- TransactionDate already expresses the confirmed financial date.
- Unnecessary for monthly Expense analytics.

### Option C — Fixed account timezone selected at registration

**Pros**

- Stable period boundaries.
- Predictable reporting.

**Cons**

- Poor for users who move.
- Requires explicit timezone management.
- May not match current local experience.

### Recommendation for MintFlow MVP

Use the User’s current timezone to determine what “today” and the active current period mean. Group confirmed Expenses by their stored TransactionDate.

Changing User timezone must not rewrite or reinterpret historical TransactionDates.

Technical consequence: analytics filters use date ranges, while UTC timestamps remain operational metadata. User timezone is needed to calculate the current local date and period boundary.

### Long-term implications

This is easy to preserve. More advanced travel or jurisdiction reporting could later store purchase timezone separately, but the MVP should not.

------------------------------------------------

## 19. What evidence should be stored for confirmation?

### Why it matters

MintFlow must prove that an Expense entered history through explicit confirmation, support idempotency, and diagnose duplicate or disputed operations without retaining excessive interaction data.

### Option A — Confirmation timestamp only

**Pros**

- Minimal data.
- Easy to implement.
- Low privacy impact.

**Cons**

- Does not identify actor, channel, or draft version.
- Weak duplicate-request diagnostics.
- Cannot establish what was confirmed.

### Option B — Minimal structured confirmation evidence

Store:

- CaptureDraft identity.
- Resulting Expense identity.
- Confirming User identity.
- UTC confirmation timestamp.
- Confirmation channel.
- Confirmed draft revision.
- Idempotency or interaction key.

The Expense contains the final confirmed values.

**Pros**

- Supports explicit-confirmation evidence.
- Enables idempotency.
- Provides useful diagnostics.
- Avoids retaining chat transcripts or button payloads.

**Cons**

- Adds several fields and retention considerations.
- Channel identifiers must avoid unnecessary personal data.

### Option C — Full interaction transcript

Store messages, review screens, callbacks, and all revisions.

**Pros**

- Maximum forensic detail.
- Strong support diagnostics.

**Cons**

- Serious privacy and retention burden.
- Telegram content may contain unrelated sensitive information.
- High storage and operational complexity.
- Unnecessary for the product’s core invariant.

### Recommendation for MintFlow MVP

Choose Option B. Store minimal structured evidence, not conversation transcripts.

Technical consequence: confirmation must consume a draft revision and idempotency key atomically. The resulting Expense and confirmed CaptureDraft provide the final-value evidence.

### Long-term implications

Adding transcript-level evidence later cannot reconstruct old interactions, but MintFlow is unlikely to need it. The minimal record can be extended safely if regulatory or product requirements emerge.

------------------------------------------------

## 20. Which decisions must be finalized before database design?

### Why it matters

Some choices determine entity relationships, constraints, deletion behavior, and historical data. Others are validation defaults or retention durations that can change safely after persistence exists.

### Option A — Finalize every product decision first

**Pros**

- Fewer unknowns during schema design.
- Comprehensive specification.

**Cons**

- Delays implementation.
- Creates false certainty.
- Encourages speculative modeling.

### Option B — Finalize only structural decisions

Settle choices that affect data shape, ownership, uniqueness, and irreversible history. Keep policy thresholds configurable.

**Pros**

- Enables progress without overdesign.
- Protects expensive architectural boundaries.
- Allows pilot feedback to influence flexible policies.

**Cons**

- Requires discipline to distinguish structure from policy.
- Some postponed choices may still cause modest migrations.

### Option C — Start persistence and decide incrementally

**Pros**

- Fastest initial coding.
- Decisions are informed by implementation.

**Cons**

- Risks destructive migrations and inconsistent semantics.
- Core invariants may become accidental database behavior.
- Poor fit for financial data.

### Recommendation for MintFlow MVP

Choose Option B.

### Must be finalized before database design

These materially affect relationships, constraints, retention structure, or irreversible history:

1. Refund representation and whether negative Expense amounts are valid.
2. Whether zero Expense amounts are valid.
3. Mandatory persisted Expense fields.
4. Expense deletion semantics.
5. Whether confirmed-Expense audit history is stored.
6. Whether Receipt can be deleted independently.
7. User deletion lifecycle and ownership cleanup.
8. System Category catalogue and `Uncategorized` behavior.
9. Whether Category is structurally required.
10. Currency scope and representation.
11. One CaptureDraft-to-one Expense invariant.
12. Receipt-to-Draft and Receipt-to-Expense cardinality.
13. Whether confirmed Money, including currency, is editable.
14. Minimum confirmation evidence.
15. Whether RecognitionResult is retained separately from CaptureDraft.

These do not require every UI detail, but their domain semantics should be accepted.

### Can be finalized during application design

These affect behavior but not the fundamental data model:

1. Whether today is proposed automatically.
2. Exact future-date tolerance.
3. Exact draft expiration duration.
4. Exact recognition-result retention duration.
5. Exact maximum Expense amount, provided storage is sufficiently wide.
6. Exact system category list.
7. Category presentation order, icons, and translations.
8. User-facing large-amount warning threshold.
9. Tested currency/market support messaging.
10. Confirmation UI wording.

### Can safely be postponed beyond the first MVP iteration

1. Refund linkage to an original Expense, if refunds themselves are deferred.
2. Receipt splitting and category allocations.
3. Archive behavior.
4. Advanced merchant normalization.
5. Purchase-time timezone.
6. Automatic category suggestions.
7. Recovery duration for deleted Expenses.
8. Detailed recognition-quality analytics.

### Long-term implications

The expensive changes are those involving:

- Signed versus typed financial records.
- Nullability of core Expense fields.
- Deletion and audit history.
- Ownership and cascade behavior.
- Currency representation.
- One-to-one cardinality and uniqueness.
- Recognition-data retention boundaries.

Thresholds, defaults, labels, and support-status wording are comparatively cheap to change.

Before database design, I recommend recording explicit product-owner decisions for the structural items above. The remaining policies can be documented as provisional MVP defaults and adjusted after observing the first users.
