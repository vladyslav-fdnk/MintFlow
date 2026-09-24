# Exchange Rates Design (sprint `exchange-rates`)

Status: approved on 2026-09-24.

## 1. Purpose

Let a user who spends in several currencies see one total in their main currency. The product
owner amended decision 14 on 2026-09-24: dashboard totals and charts are converted into the
user's default currency; expenses themselves never change.

## 2. Settled inputs

- Every Expense keeps its own amount and currency; conversion happens only when showing totals.
- The main currency is the user's default currency from Settings. Without one, nothing is
  converted and the dashboard works per currency as before.
- Converted values are always marked as approximate ("≈") and say which rates were used.
- The JSON API is unchanged.

## 3. Decisions

### X1. Today's rate, chosen by the product owner

All amounts convert at the latest known rate, not the rate on the expense's date. Trade-off:
simpler and needs no rate history, but a past month's converted total moves slightly with the
rates. The page says so.

### X2. Rates from the ECB, completed by the National Bank of Ukraine

- The ECB daily reference rates (EUR base, working days) give USD, GBP, PLN, JPY and others.
  The NBU daily rates (UAH per unit) give UAH, converted to the EUR base through its EUR rate.
- Both are free, official, and need no key. BHD is published by neither: BHD amounts stay
  unconverted and are listed separately with "no rate".
- A new command, `python -m mintflow.commands.refresh_exchange_rates`, runs daily from the
  scheduler (like the retention commands). It keeps the latest rate per currency with its date
  and source; a failed source keeps the previous rates and makes the command exit non-zero.

### X3. Conversion arithmetic

- Rates are stored as units per EUR (`Decimal`). Converting minor units of X into Y:
  `major_X / rate_X * rate_Y`, rounded half-even to Y's minor units. Sums convert per currency
  group first, then add, so each group rounds once.
- A currency without a rate is never guessed: its amounts stay out of the converted total and
  appear separately.

### X4. The dashboard in the main currency

- The analytics queries also group by currency; the use case converts each group and builds the
  same summary, charts, and insights in the main currency, with the comparison and the largest
  expense converted the same way.
- The per-currency view stays available.

### X5. History shows both

A history row in another currency shows its original amount and "≈ amount" in the main
currency.

## 4. Out of scope

- Rates on the expense's date and rate history.
- Converting in the JSON API or the Telegram bot.
- Currencies outside the MintFlow catalogue.

## 5. Task breakdown

| Task | Summary | Depends on |
| --- | --- | --- |
| FX-01 | Exchange rates: table, ECB and NBU adapters, refresh command | — |
| FX-02 | Dashboard use case in the main currency | FX-01 |
| FX-03 | Web: converted dashboard by default, per-currency view, "≈" in history | FX-02 |
