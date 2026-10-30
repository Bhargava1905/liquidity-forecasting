# Business rules and controls

Rule IDs (R1–R9) are referenced from `ground_truth.csv` (`rule_reference`) and from the code. All constants live in
`src/liquidity_forecasting/config.py`.

## Time model

| Constant | Value | Meaning |
|---|---|---|
| `HISTORY_START` | 2026-10-01 | First ledger value date; `opening_balance` is the balance at the start of this day |
| `BACKTEST_CUTOFF` | 2026-10-15T10:00 | Cutoff of the earlier base forecast (version 1), back-tested against actuals |
| `AS_OF` | 2026-10-30T10:00 | Cutoff of the current base, delayed-receipt and stress forecasts |
| `EXTRACT_AT` | 2026-10-30T20:00 | Snapshot time; no record is later than this |
| `HORIZON_DAYS` | 30 | Each run forecasts cutoff date + 1 … cutoff date + 30 |

## Generation rules (outcomes come from rules, not random labels)

**R9 — Scheduled items and their outcomes.**
- Each receivable/payable is created at a random business-weighted time in the history window with a payment term;
  `expected_*_date = roll(created date + term)` where `roll` moves Saturday/Sunday to Monday.
- Receivable settlement date = `roll(expected + RECEIPT_DELAY_DAYS[customer_risk_grade])` with A = 0, B = 3, C = 9 days.
  Payables are paid on the expected date.
- Status at `EXTRACT_AT`: settled (`RECEIVED`/`PAID`) if the settlement date ≤ 2026-10-30; otherwise `OVERDUE` if the
  expected date has passed; otherwise `SCHEDULED`.
- Every settled item has exactly one ledger entry (`settlement_source_id`) with the same account, signed amount and
  value date as `actual_*_date`. Its state history is `CREATED → (BECAME_OVERDUE) → SETTLED` in `state_change_events`;
  the row's `status` is always the last event's `new_status`. Nothing is overwritten.
- At least `ONE_OFF_MINIMUM` payables are large one-offs (`TAX`, `CAPEX`, `DIVIDEND`, `LOAN_REPAYMENT`, median INR 35m).
- Non-forecast bank activity (card settlements, direct debits, fees, refunds, misc receipts, month-end payroll) fills the
  ledger up to the required volume; it is what the forecast cannot see and drives the `UNFORECAST` variance.
- FX SPOT rates are a seeded random walk with a documented −3% GBP move on 2026-10-22. On each run's cutoff date a
  FORWARD curve is published: `forward = spot(cutoff) × (1 + carry × days / 365)`.

## Controls applied by the pipeline (and re-derived by the validator)

**R1 — Closing reconciliation.** `opening_balance + Σ ledger cash_movement = recorded_closing_balance`. A difference of
even one cent raises `BALANCE_RECONCILIATION_MISMATCH` with decision `FLAG_AND_USE_LEDGER_BALANCE`. The recorded figure
is never corrected and never used by a forecast.

**R2 — Daily balance chain.** For every account and history date: `opening[d] = closing[d−1]` (day one = `opening_balance`),
`closing[d] = opening[d] + inflows[d] − outflows[d]`, where inflows/outflows are the ledger movements valued on `d`.
The last closing equals the ledger-derived closing.

**R3 — Currency.** Amounts in different currencies are never added before conversion.
- Forecast valuation: `closing_reporting = round(closing_native × forward(cutoff, position_date) × (1 + fx_shock_pct/100))`;
  the shock applies to non-INR accounts only. A forecast never uses a SPOT rate published after its cutoff.
- A missing rate leaves the position `UNRESOLVED_FX` with a blank INR value (`FLAG_UNRESOLVED_CONVERSION`, one exception
  per run and missing `rate_id`). Entity totals add only valued positions and are marked `UNRESOLVED_FX`.
- Entity cash reports must equal `Σ round(native × spot(ccy) / spot(reported_currency))`. A report that equals the plain
  native sum across several currencies is `MIXED_CURRENCY_WITHOUT_CONVERSION` → `REJECT_UNCONVERTED_TOTAL`.

**R4 — Cutoff.** A run uses only information known at or before `data_cutoff` (inclusive: `knowledge_time == cutoff` is
eligible). The starting balance is `opening_balance + Σ ledger entries with available_at ≤ cutoff`. A feed item whose
`source_type` is `ACTUAL_LEDGER` is an actual, not a forecast: if it became known after the cutoff it is excluded and
raises `POST_CUTOFF_ACTUAL` (`FLAG_AND_EXCLUDE_FROM_FORECAST`); if known before, it is already in the balance
(`ACTUAL_ALREADY_IN_BALANCE`) and counting it again would double count.

**R5 — Duplicates.** A feed item repeating an earlier item's `(source_type, source_id)` is excluded and raises
`DUPLICATE_CASH_FLOW` (`FLAG_AND_EXCLUDE_DUPLICATE`). The earliest `knowledge_time` (then lowest ID) is kept. Matching
amount, date, account and customer alone is **not** a duplicate (see control `NOT_DUPLICATE_SAME_AMOUNT`).

**R6 — Eligibility and scenarios.** Rules are applied in this order; the first failing rule is recorded in
`forecast_run_items.exclusion_reason`:

1. `KNOWN_AFTER_CUTOFF` (R4) 2. `DUPLICATE_SOURCE` (R5) 3. `ACTUAL_ALREADY_IN_BALANCE` (R4)
4. `DUE_ON_OR_BEFORE_CUTOFF` — overdue or already-due items are not forecast (prudent treatment)
5. scenario adjustment: receivables move to `roll(expected + receipt_delay_days_by_grade[grade])`
6. `OUTSIDE_HORIZON` — scenario date after `horizon_end`

Scenarios differ **only** by `assumption_json`:

| Scenario | Receipt delay A / B / C (days) | FX shock on non-INR | One-off outflow |
|---|---|---|---|
| BASE | 0 / 0 / 0 | 0% | none |
| DELAYED_RECEIPT | 0 / 3 / 9 (the observed behaviour) | 0% | none |
| STRESS | 3 / 10 / 21 | −5% | INR 250m from `BANKACC-ENT-000001-001` on horizon day 3 |

Runs with the same cutoff share cutoff, horizon, FX curve, calculation version and `input_snapshot_hash`; the validator
checks this and that every item-level difference from BASE is a documented receipt delay.

**R7 — Liquidity buffer.** `minimum_cash_buffer_inr = floor(0.5 × entity opening value at FX_START, INR 100,000)`.
An entity-day is `BREACH` when its valued INR total is below the buffer (`UNRESOLVED_FX` takes precedence). Each
breaching entity raises one `LIQUIDITY_BUFFER_BREACH` per run → `ESCALATE_TO_TREASURY`.

**R8 — Actual versus forecast.** For each back-tested account-day `t` (cutoff date `D`):

```
variance = actual(t) − forecast(t) = cutoff_lag + timing + unforecast
cutoff_lag = Σ ledger with value date ≤ D but available_at > cutoff          (constant over the horizon)
timing     = Σ included items: signed × ([settled ≤ t] − [scenario_date ≤ t])
unforecast = Σ ledger with D < value date ≤ t not settling an included item
```

The identity holds exactly in integer cents (the pipeline asserts it; the validator re-derives every component).
INR variance splits into `volume_effect = round(variance_native × actual_rate)` and
`fx_effect = INR variance − volume_effect ≈ forecast_native × (actual_rate − forecast_rate)`.

## Exception ownership

| Issue | Severity | Owner role | Decision |
|---|---|---|---|
| BALANCE_RECONCILIATION_MISMATCH | HIGH | OPERATIONS_ANALYST | FLAG_AND_USE_LEDGER_BALANCE |
| MISSING_FX_RATE | HIGH | OPERATIONS_ANALYST | FLAG_UNRESOLVED_CONVERSION |
| DUPLICATE_CASH_FLOW | MEDIUM | OPERATIONS_ANALYST | FLAG_AND_EXCLUDE_DUPLICATE |
| POST_CUTOFF_ACTUAL | HIGH | RISK_COMPLIANCE_REVIEWER | FLAG_AND_EXCLUDE_FROM_FORECAST |
| MIXED_CURRENCY_WITHOUT_CONVERSION | HIGH | RISK_COMPLIANCE_REVIEWER | REJECT_UNCONVERTED_TOTAL |
| LIQUIDITY_BUFFER_BREACH | HIGH | RISK_COMPLIANCE_REVIEWER | ESCALATE_TO_TREASURY |
