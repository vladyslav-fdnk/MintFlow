# Dashboard Design (sprint `dashboard`)

Status: approved on 2026-09-23.

## 1. Purpose

Deliver the backend for the Web Client dashboard (docs/mvp_definition.md, section 8): a summary,
exactly three charts, and exactly two generated insights for one period and one currency, built
from confirmed, non-deleted Expenses.

The repository has no Web Client frontend yet, so this sprint delivers a JSON API only.
Rendering, chart accessibility (text and table equivalents), and localized insight sentences are
frontend concerns. The API returns the data they need.

## 2. Settled inputs

These are already decided and are not reopened here:

- Only confirmed, non-deleted Expenses count. Every query starts from `select_active_expenses`
  (expense_management_design.md, D6).
- The user's current timezone defines "today" and the default period. Expenses are grouped by
  their stored `transaction_date`, which is never reinterpreted (product decision 18).
- Currencies are never added together or converted (decision 14, MVP section 8).
- One date-range filter and one optional currency filter. The default range is the current
  calendar month in the user's timezone.
- The contents are fixed: summary (total, count, change from the previous comparable period),
  spending over time, spending by category, top five merchants plus "Other", largest category,
  and largest expense. Nothing else.

## 3. Decisions

### D1. One endpoint, one snapshot

`GET /analytics/dashboard?date_from=&date_to=&currency=` returns the whole dashboard in one
response, under a new `analytics` router and application module.

- Trade-off: per-widget endpoints would allow independent loading, but they cost more round trips
  on 4G (the target is a usable page within three seconds) and can show widgets computed from
  different data if an edit lands in between. One response is one consistent read.
- It is a new `/analytics` prefix, not `/capture`: the dashboard reads history and captures
  nothing. Expense routes stay where they are (D7 of the previous sprint).

### D2. Period rules

- `date_from` and `date_to` are inclusive ISO dates and must be given together. When both are
  absent, the period is the first through the last day of the current month in the user's
  timezone.
- The maximum range is 366 days. Longer ranges return the generic 422. This bounds the query cost
  and the monthly chart to at most 13 columns.
- Year bounds match `TransactionDate` (2000–2100).

### D3. Currency resolution

The response always includes `currencies`: the total and count per currency in the period,
ordered by total descending. This satisfies "presents separate totals" without combining them.
The detailed sections (summary comparison, charts, insights) use one selected currency:

1. the `currency` parameter, when given (even if it has no data in the period);
2. otherwise the only currency present in the period;
3. otherwise the user's default currency, if it is among the present currencies;
4. otherwise nothing is selected. The detailed sections are `null` and
   `currency_selection_required` is `true`.

With no Expenses at all, the selected currency is the user's default currency (or `null` when
unset) and every section is empty rather than missing.

Trade-off: step 3 picks a currency without an explicit choice on this request. It uses the
user's own stated preference, and the separate totals remain visible, so nothing is hidden or
combined.

### D4. Previous comparable period

- The compared window is the part of the selected period up to the user's local today. A period
  entirely in the future has no comparison.
- If the compared window starts on the first day of a month and stays inside that month, the
  previous period covers the same day offsets one month earlier, clamped to that month's length.
  For example, 1–23 August compares with 1–23 July, and 1–31 March compares with 1–29 February
  in a leap year. Otherwise it is the window of equal length immediately before. (Clarified
  during DASH-01: shifting a multi-month window by one month would overlap the window itself.)
- There is no comparison when the previous window would start before 2000, where no Expense can
  exist.
- The comparison is `null` when the previous period has no Expenses in the selected currency.
  The percentage is `null` when the previous total is zero, which cannot happen for positive
  amounts but is guarded anyway.
- Trade-off: comparing month-to-date with a full previous month would always look like a drop.
  Month-aligned offsets match how people read "this month vs last month". Equal-length windows
  keep arbitrary ranges honest.

### D5. Spending over time

- Ranges of up to 31 days use daily buckets, others use calendar-month buckets. A monthly bucket
  at either edge covers only the part of the month inside the range, and its `date_from` and
  `date_to` say so.
- Every bucket in the range is present, including zero buckets, so sparse data renders as gaps
  rather than missing columns.

### D6. Spending by category

- Every category with spending in the period, ranked by total descending, ties by key.
  Uncategorized is included like any other category.
- Each entry has the category key, the display name from the categories table, the total, the
  count, and its share in basis points (1/100 of a percent), rounded half-even. Shares may not
  sum to exactly 10 000.

### D7. Top merchants

- Groups use the stored, already whitespace-normalized merchant name exactly. There is no case
  folding or other normalization ("without advanced normalization").
- The top five named merchants by total, ties by name. Everything else, including Expenses
  without a merchant, is one `other` entry with its total, count, and share, present only when
  non-empty.

### D8. Insights are structured facts

- **Largest category:** the top-ranked category excluding Uncategorized, with its total and
  share. It is `null` when the period has no categorized spending.
- **Largest expense:** the Expense with the greatest amount. Ties go to the later transaction
  date, then the later creation time, then id. It includes the id, amount, merchant (possibly
  `null`), and date.
- The API returns data, not sentences. The Web Client renders and localizes the wording.

### D9. Computation stays in PostgreSQL

- A read-only analytics repository runs `GROUP BY` aggregates over `select_active_expenses`:
  per-currency totals, per-day totals, per-category totals, per-merchant totals, and the largest
  expense. No Expense rows are loaded into Python.
- Period arithmetic, bucketing, currency resolution, ranking, shares, and the "Other" grouping
  are pure application functions, unit-tested without a database.
- The existing `ix_expenses_active_history` index covers the owner and date-range scan. No new
  index is added unless the integration test plan shows it is needed.

### D10. Drill-through uses the existing history endpoint

The time and category charts link through to `GET /capture/expenses` with its existing date,
category, and currency filters. Merchant drill-through is not possible because merchant search is
postponed after the MVP. This is documented, not built around.

## 4. Out of scope

- Any frontend, chart rendering, or sentence generation.
- Additional charts, insights, forecasts, anomaly detection, or budgets.
- Currency conversion or combined totals.
- Merchant search or merchant drill-through.
- The internal product metrics in MVP section 9, including dashboard revisit rate.
- Caching or precomputed aggregates.

## 5. Task breakdown

| Task | Summary | Depends on |
| --- | --- | --- |
| DASH-01 | Dashboard period rules: default period, validation, buckets, previous period | — |
| DASH-02 | Analytics read repository: `GROUP BY` aggregates over active Expenses | — |
| DASH-03 | Build Dashboard use case: currency resolution, summary, charts, insights | DASH-01, DASH-02 |
| DASH-04 | `GET /analytics/dashboard` endpoint | DASH-03 |
