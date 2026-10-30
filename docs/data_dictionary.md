# Data dictionary

Generated from `src/liquidity_forecasting/schema.py` by `python scripts/build_docs.py`; do not edit by hand.

Money is a signed decimal string with two places in the unit stated; calculations use integer cents. Dates are ISO `YYYY-MM-DD`, timestamps ISO `YYYY-MM-DDTHH:MM:SS` (synthetic local treasury time). Every ID is immutable and unique within its table. `kind` is `source` (generator input / seed data), `derived` (calculated only from source rows) or `label` (ground truth, never read by the pipeline).

## LegalEntity — `legal_entities.csv`

kind: **source** · primary key: `entity_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `entity_id` | id | yes | `ENT-\d{6}` (unique) | Immutable entity identifier |
| `legal_name` | text | yes | non-empty text | Synthetic registered name |
| `country` | enum | yes | IN, US, GB, DE, SG | Country of incorporation |
| `functional_currency` | enum | yes | INR, USD, EUR, GBP, SGD | Currency of the entity's books |
| `minimum_cash_buffer_inr` | money | yes | decimal(18,2) | INR; BUFFER_RATIO x opening INR value, floored to 100,000 |
| `status` | enum | yes | ACTIVE, INACTIVE | Lifecycle state |
| `effective_from` | date | yes | ISO date | Start of validity |
| `effective_to` | date | no | ISO date | End of validity; blank while active |

## BankAccount — `bank_accounts.csv`

kind: **source** · primary key: `bank_account_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `bank_account_id` | id | yes | `BANKACC-ENT-\d{6}-\d{3}` (unique) | BANKACC-<entity-id>-<ordinal> |
| `entity_id` | ref | yes | FK → `legal_entities.entity_id` | Owning entity; must match the ID prefix |
| `bank_name` | text | yes | non-empty text | Synthetic bank name |
| `account_name` | text | yes | non-empty text | Display name |
| `account_currency` | enum | yes | INR, USD, EUR, GBP, SGD | Currency of every movement on the account |
| `account_purpose` | enum | yes | OPERATING, COLLECTION, PAYROLL, TREASURY | Usage |
| `opening_balance` | money | yes | decimal(18,2) | Account currency; balance at start of opening_balance_date |
| `opening_balance_date` | date | yes | ISO date | Equals HISTORY_START |
| `recorded_closing_balance` | money | yes | decimal(18,2) | Account currency; bank-reported balance at end of closing_balance_date |
| `closing_balance_date` | date | yes | ISO date | Last history date |
| `status` | enum | yes | ACTIVE, RESTRICTED, CLOSED | Lifecycle state |
| `effective_from` | date | yes | ISO date | On/after the entity's effective_from |
| `effective_to` | date | no | ISO date | Blank while open |

## CurrencyRate — `currency_rates.csv`

kind: **source** · primary key: `rate_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `rate_id` | id | yes | `FX-(SPOT|FWD)-\d{8}-\d{8}-[A-Z]{3}-INR` (unique) | FX-<type>-<quote yyyymmdd>-<value yyyymmdd>-<from>-INR |
| `rate_type` | enum | yes | SPOT, FORWARD | SPOT for history, FORWARD curve published at a cutoff |
| `from_currency` | enum | yes | INR, USD, EUR, GBP, SGD | Base currency |
| `to_currency` | enum | yes | INR | Always the reporting currency |
| `quote_date` | date | yes | ISO date | Date the rate was published (known) |
| `value_date` | date | yes | ISO date | Date the rate applies to; equals quote_date for SPOT |
| `rate` | rate | yes | decimal(18,6) > 0 | INR per 1 unit of from_currency |
| `source` | enum | yes | SYNTHETIC | Provenance |
| `status` | enum | yes | ACTIVE, SUPERSEDED | Lifecycle state |

## LiquidityScenario — `liquidity_scenarios.csv`

kind: **source** · primary key: `scenario_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `scenario_id` | id | yes | `SCN-[A-Z]+` (unique) | Immutable scenario identifier |
| `scenario_name` | enum | yes | BASE, DELAYED_RECEIPT, STRESS | Scenario family |
| `description` | text | yes | non-empty text | Plain-language assumption summary |
| `assumption_json` | json | yes | JSON object | The only parameters allowed to differ between scenarios |
| `effective_from` | date | yes | ISO date | Start of validity |
| `effective_to` | date | no | ISO date | Blank while active |
| `status` | enum | yes | ACTIVE, RETIRED | Lifecycle state |

## Receivable — `receivables.csv`

kind: **source** · primary key: `receivable_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `receivable_id` | id | yes | `REC-2026-\d{6}` (unique) | Numbered in creation order |
| `entity_id` | ref | yes | FK → `legal_entities.entity_id` | Owning entity |
| `bank_account_id` | ref | yes | FK → `bank_accounts.bank_account_id` | Collection account; belongs to entity_id |
| `customer_reference` | text | yes | non-empty text | Synthetic customer code |
| `customer_risk_grade` | enum | yes | A, B, C | Payment-behaviour grade of the customer |
| `invoice_reference` | text | yes | non-empty text (unique) | Unique invoice number |
| `amount` | money | yes | decimal(18,2) | Account currency; positive |
| `currency` | enum | yes | INR, USD, EUR, GBP, SGD | Equals account currency |
| `created_at` | datetime | yes | ISO timestamp | Time the receivable became known |
| `expected_receipt_date` | date | yes | ISO date | Contractual date rolled to a business day; after created_at |
| `actual_receipt_date` | date | no | ISO date | Blank until received; on/after expected date |
| `status` | enum | yes | SCHEDULED, OVERDUE, RECEIVED | State at EXTRACT_AT; history in state_change_events |

## Payable — `payables.csv`

kind: **source** · primary key: `payable_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `payable_id` | id | yes | `PAY-2026-\d{6}` (unique) | Numbered in creation order |
| `entity_id` | ref | yes | FK → `legal_entities.entity_id` | Owning entity |
| `bank_account_id` | ref | yes | FK → `bank_accounts.bank_account_id` | Paying account; belongs to entity_id |
| `supplier_reference` | text | yes | non-empty text | Synthetic supplier or authority code |
| `payment_category` | enum | yes | TRADE, TAX, CAPEX, DIVIDEND, LOAN_REPAYMENT | Non-TRADE categories are large one-off payments |
| `bill_reference` | text | yes | non-empty text (unique) | Unique bill number |
| `amount` | money | yes | decimal(18,2) | Account currency; positive |
| `currency` | enum | yes | INR, USD, EUR, GBP, SGD | Equals account currency |
| `created_at` | datetime | yes | ISO timestamp | Time the payable became known |
| `expected_payment_date` | date | yes | ISO date | Scheduled date rolled to a business day |
| `actual_payment_date` | date | no | ISO date | Blank until paid |
| `status` | enum | yes | SCHEDULED, PAID | State at EXTRACT_AT |

## LedgerEntry — `ledger_entries.csv`

kind: **source** · primary key: `ledger_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `ledger_id` | id | yes | `LED-2026-\d{8}` (unique) | Numbered in value-date order |
| `bank_account_id` | ref | yes | FK → `bank_accounts.bank_account_id` | Account moved |
| `entity_id` | ref | yes | FK → `legal_entities.entity_id` | Owner of the account |
| `transaction_date` | date | yes | ISO date | Value date; within the history window |
| `posting_date` | date | yes | ISO date | On/after transaction_date, never after the last history date |
| `available_at` | datetime | yes | ISO timestamp | Time the entry became visible to treasury |
| `transaction_type` | enum | yes | CUSTOMER_RECEIPT, SUPPLIER_PAYMENT, ONE_OFF_PAYMENT, PAYROLL, CARD_SETTLEMENT, MISC_RECEIPT, DIRECT_DEBIT, CUSTOMER_REFUND, BANK_FEE | Cash movement category |
| `cash_movement` | money | yes | decimal(18,2) | Account currency; positive inflow, negative outflow, never zero |
| `currency` | enum | yes | INR, USD, EUR, GBP, SGD | Equals account currency |
| `transaction_reference` | id | yes | `TXN-2026-\d{8}` (unique) | Unique bank reference |
| `settlement_source_type` | enum | no | RECEIVABLE, PAYABLE | Set when the entry settles a scheduled item |
| `settlement_source_id` | text | no | non-empty text | receivable_id or payable_id settled by this entry |
| `record_status` | enum | yes | POSTED | Ledger entries are immutable once posted |

## CashFlowForecast — `cash_flow_forecasts.csv`

kind: **source** · primary key: `cash_flow_forecast_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `cash_flow_forecast_id` | id | yes | `CF-2026-\d{6}` (unique) | Numbered in knowledge_time order |
| `source_type` | enum | yes | RECEIVABLE, PAYABLE, ACTUAL_LEDGER | What the feed says the item came from |
| `source_id` | text | yes | non-empty text | receivable_id, payable_id or ledger_id |
| `entity_id` | ref | yes | FK → `legal_entities.entity_id` | Matches the source |
| `bank_account_id` | ref | yes | FK → `bank_accounts.bank_account_id` | Matches the source |
| `direction` | enum | yes | INFLOW, OUTFLOW | Receivable inflow, payable outflow |
| `amount` | money | yes | decimal(18,2) | Account currency; positive |
| `currency` | enum | yes | INR, USD, EUR, GBP, SGD | Equals account currency |
| `expected_date` | date | yes | ISO date | Scheduled date from the source |
| `knowledge_time` | datetime | yes | ISO timestamp | When the item entered the forecast feed |
| `source_reference` | text | yes | non-empty text | Invoice, bill or bank reference |

## StateChangeEvent — `state_change_events.csv`

kind: **source** · primary key: `event_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `event_id` | id | yes | `EVT-2026-\d{8}` (unique) | Numbered in event_time order |
| `object_type` | enum | yes | Receivable, Payable | Type of object whose state changed |
| `object_id` | text | yes | non-empty text | receivable_id or payable_id |
| `event_type` | enum | yes | CREATED, BECAME_OVERDUE, SETTLED | Lifecycle event |
| `old_status` | enum | no | SCHEDULED, OVERDUE | Blank for CREATED |
| `new_status` | enum | yes | SCHEDULED, OVERDUE, RECEIVED, PAID | State after the event |
| `event_time` | datetime | yes | ISO timestamp | Not after EXTRACT_AT |
| `details` | text | yes | non-empty text | Human-readable reason |

## EntityBalanceReport — `entity_balance_reports.csv`

kind: **source** · primary key: `report_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `report_id` | id | yes | `EBR-2026-\d{6}` (unique) | Entity cash report submitted to group treasury |
| `entity_id` | ref | yes | FK → `legal_entities.entity_id` | Reporting entity |
| `report_date` | date | yes | ISO date | Balance date |
| `reported_currency` | enum | yes | INR, USD, EUR, GBP, SGD | Entity functional currency |
| `reported_total` | money | yes | decimal(18,2) | reported_currency; sum of account closings converted at SPOT |
| `account_count` | int | yes | integer ≥ 0 | Accounts included |
| `submitted_at` | datetime | yes | ISO timestamp | Submission time |

## DailyBalance — `daily_balances.csv`

kind: **derived** · primary key: `bank_account_id, balance_date`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `bank_account_id` | ref | yes | FK → `bank_accounts.bank_account_id` | Account |
| `balance_date` | date | yes | ISO date | History date |
| `currency` | enum | yes | INR, USD, EUR, GBP, SGD | Account currency |
| `opening_balance` | money | yes | decimal(18,2) | Previous day's closing (opening_balance on day one) |
| `inflows` | money | yes | decimal(18,2) | Sum of positive ledger movements on the value date |
| `outflows` | money | yes | decimal(18,2) | Sum of negative ledger movements (as a positive number) |
| `closing_balance` | money | yes | decimal(18,2) | opening + inflows - outflows |
| `entry_count` | int | yes | integer ≥ 0 | Ledger entries on the value date |

## ForecastRun — `forecast_runs.csv`

kind: **derived** · primary key: `forecast_run_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `forecast_run_id` | id | yes | `FCAST-\d{4}-\d{2}-\d{2}-\d{3}` (unique) | FCAST-<cutoff date>-<counter> |
| `scenario_id` | ref | yes | FK → `liquidity_scenarios.scenario_id` | Scenario applied |
| `run_version` | int | yes | integer ≥ 0 | Version number within the scenario |
| `previous_run_id` | ref | no | FK → `forecast_runs.forecast_run_id` | Prior version; never overwritten |
| `created_at` | datetime | yes | ISO timestamp | On/after data_cutoff |
| `data_cutoff` | datetime | yes | ISO timestamp | Only information known at or before this time is used |
| `horizon_start` | date | yes | ISO date | Day after the cutoff date |
| `horizon_end` | date | yes | ISO date | horizon_start + HORIZON_DAYS - 1 |
| `fx_quote_date` | date | yes | ISO date | Forward curve used for valuation |
| `calculation_version` | text | yes | non-empty text | Code version of the rules |
| `input_snapshot_hash` | hash | yes | 64 hex chars | SHA-256 of eligible flows, starting balances and FX used |
| `included_items` | int | yes | integer ≥ 0 | Run items with decision INCLUDED |
| `excluded_items` | int | yes | integer ≥ 0 | Run items with decision EXCLUDED |
| `exception_count` | int | yes | integer ≥ 0 | Exceptions raised by the run |
| `status` | enum | yes | COMPLETED, COMPLETED_WITH_EXCEPTIONS, FAILED | Final state |

## ForecastRunItem (bridge ForecastRun N:M CashFlowForecast) — `forecast_run_items.csv`

kind: **derived** · primary key: `forecast_run_id, cash_flow_forecast_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `forecast_run_id` | ref | yes | FK → `forecast_runs.forecast_run_id` | Run |
| `cash_flow_forecast_id` | ref | yes | FK → `cash_flow_forecasts.cash_flow_forecast_id` | Candidate item |
| `decision` | enum | yes | INCLUDED, EXCLUDED | Outcome of the eligibility rules |
| `exclusion_reason` | enum | no | KNOWN_AFTER_CUTOFF, DUPLICATE_SOURCE, ACTUAL_ALREADY_IN_BALANCE, DUE_ON_OR_BEFORE_CUTOFF, OUTSIDE_HORIZON | First failing rule; blank when included |
| `scheduled_date` | date | yes | ISO date | expected_date of the item |
| `scenario_date` | date | yes | ISO date | Date after scenario adjustment |
| `adjustment` | enum | yes | NONE, RECEIPT_DELAY | Scenario adjustment applied |
| `signed_amount` | money | yes | decimal(18,2) | Account currency; positive inflow, negative outflow |

## CashPosition — `cash_positions.csv`

kind: **derived** · primary key: `forecast_run_id, bank_account_id, position_date`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `forecast_run_id` | ref | yes | FK → `forecast_runs.forecast_run_id` | Run |
| `bank_account_id` | ref | yes | FK → `bank_accounts.bank_account_id` | Account |
| `entity_id` | ref | yes | FK → `legal_entities.entity_id` | Owner |
| `position_date` | date | yes | ISO date | Horizon date |
| `native_currency` | enum | yes | INR, USD, EUR, GBP, SGD | Account currency |
| `opening_native` | money | yes | decimal(18,2) | Ledger balance known at cutoff on day one, else previous closing |
| `inflows_native` | money | yes | decimal(18,2) | Included inflows on scenario_date |
| `outflows_native` | money | yes | decimal(18,2) | Included outflows on scenario_date (positive) |
| `scenario_outflow_native` | money | yes | decimal(18,2) | Unplanned one-off outflow from scenario assumptions |
| `closing_native` | money | yes | decimal(18,2) | opening + inflows - outflows - scenario_outflow |
| `fx_rate` | rate | no | decimal(18,6) > 0 | Forward rate quote_date=fx_quote_date, value_date=position_date; blank if missing |
| `fx_shock_pct` | text | yes | non-empty text | Scenario FX shock applied to non-INR accounts |
| `closing_reporting` | money | no | decimal(18,2) | INR; round(closing_native x rate x (1 + shock/100)); blank if unresolved |
| `valuation_status` | enum | yes | VALUED, UNRESOLVED_FX | Never valued without a rate |

## EntityPosition — `entity_positions.csv`

kind: **derived** · primary key: `forecast_run_id, entity_id, position_date`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `forecast_run_id` | ref | yes | FK → `forecast_runs.forecast_run_id` | Run |
| `entity_id` | ref | yes | FK → `legal_entities.entity_id` | Entity |
| `position_date` | date | yes | ISO date | Horizon date |
| `valued_total_inr` | money | yes | decimal(18,2) | INR; sum of VALUED account positions only |
| `unresolved_accounts` | int | yes | integer ≥ 0 | Accounts left out because FX is missing |
| `minimum_cash_buffer_inr` | money | yes | decimal(18,2) | INR; from legal_entities |
| `buffer_status` | enum | yes | OK, BREACH, UNRESOLVED_FX | UNRESOLVED_FX wins; else BREACH when total < buffer |

## ExceptionRecord — `exceptions.csv`

kind: **derived** · primary key: `exception_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `exception_id` | id | yes | `EXC-2026-\d{6}` (unique) | Sequential |
| `forecast_run_id` | ref | no | FK → `forecast_runs.forecast_run_id` | Blank for data-level controls |
| `issue_type` | enum | yes | BALANCE_RECONCILIATION_MISMATCH, MISSING_FX_RATE, DUPLICATE_CASH_FLOW, POST_CUTOFF_ACTUAL, MIXED_CURRENCY_WITHOUT_CONVERSION, LIQUIDITY_BUFFER_BREACH | Control that fired |
| `severity` | enum | yes | HIGH, MEDIUM | Triage priority |
| `target_type` | text | yes | non-empty text | Entity type of target_id |
| `target_id` | text | yes | non-empty text | Record (or missing rate_id) the exception concerns |
| `decision` | enum | yes | FLAG_AND_USE_LEDGER_BALANCE, FLAG_UNRESOLVED_CONVERSION, FLAG_AND_EXCLUDE_DUPLICATE, FLAG_AND_EXCLUDE_FROM_FORECAST, REJECT_UNCONVERTED_TOTAL, ESCALATE_TO_TREASURY, INCLUDE | What the pipeline did |
| `owner_role` | enum | yes | OPERATIONS_ANALYST, RISK_COMPLIANCE_REVIEWER | Who must act |
| `detected_at` | datetime | yes | ISO timestamp | Run creation time, or AS_OF for data-level controls |
| `details` | text | yes | non-empty text | Evidence |

## ForecastActualComparison — `actual_vs_forecast.csv`

kind: **derived** · primary key: `forecast_run_id, bank_account_id, position_date`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `forecast_run_id` | ref | yes | FK → `forecast_runs.forecast_run_id` | Run being back-tested |
| `bank_account_id` | ref | yes | FK → `bank_accounts.bank_account_id` | Account |
| `position_date` | date | yes | ISO date | Horizon date with complete actuals |
| `currency` | enum | yes | INR, USD, EUR, GBP, SGD | Account currency |
| `forecast_native` | money | yes | decimal(18,2) | cash_positions.closing_native |
| `actual_native` | money | yes | decimal(18,2) | daily_balances.closing_balance |
| `variance_native` | money | yes | decimal(18,2) | actual - forecast = sum of the three components |
| `cutoff_lag_component` | money | yes | decimal(18,2) | Entries valued on/before cutoff date but visible only after the cutoff |
| `timing_component` | money | yes | decimal(18,2) | Included items settled on a different date than forecast |
| `unforecast_component` | money | yes | decimal(18,2) | Horizon ledger activity not linked to an included item |
| `primary_driver` | enum | yes | NONE, CUTOFF_LAG, TIMING, UNFORECAST | Largest absolute component |
| `forecast_rate` | rate | yes | decimal(18,6) > 0 | Forward rate used by the run |
| `actual_rate` | rate | yes | decimal(18,6) > 0 | SPOT rate on position_date |
| `forecast_reporting` | money | yes | decimal(18,2) | INR |
| `actual_reporting` | money | yes | decimal(18,2) | INR |
| `variance_reporting` | money | yes | decimal(18,2) | INR; volume_effect + fx_effect |
| `volume_effect_reporting` | money | yes | decimal(18,2) | INR; variance_native valued at actual_rate |
| `fx_effect_reporting` | money | yes | decimal(18,2) | INR; forecast_native x (actual_rate - forecast_rate), incl. rounding |

## ScenarioSummary — `scenario_summary.csv`

kind: **derived** · primary key: `forecast_run_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `forecast_run_id` | ref | yes | FK → `forecast_runs.forecast_run_id` (unique) | Run |
| `scenario_id` | ref | yes | FK → `liquidity_scenarios.scenario_id` | Scenario |
| `opening_group_inr` | money | yes | decimal(18,2) | INR; starting balances valued at the first horizon rate |
| `min_group_inr` | money | yes | decimal(18,2) | INR; lowest group total over fully valued days |
| `min_group_date` | date | yes | ISO date | Date of min_group_inr |
| `closing_group_inr` | money | yes | decimal(18,2) | INR; group total on the last fully valued day |
| `breach_entity_days` | int | yes | integer ≥ 0 | Entity-days below minimum buffer |
| `entities_breaching` | int | yes | integer ≥ 0 | Distinct entities with at least one breach |
| `unresolved_positions` | int | yes | integer ≥ 0 | Account-days left unvalued for missing FX |
| `delayed_items` | int | yes | integer ≥ 0 | Included items moved by RECEIPT_DELAY |

## GroundTruth (labels only; never an input) — `ground_truth.csv`

kind: **label** · primary key: `case_id`

| Field | Type | Required | Allowed values / format | Description |
|---|---|---|---|---|
| `case_id` | id | yes | `(ANOM|CTRL)-\d{3}` (unique) | ANOM = injected anomaly, CTRL = boundary control that must not be flagged |
| `issue_type` | enum | yes | BALANCE_RECONCILIATION_MISMATCH, MISSING_FX_RATE, DUPLICATE_CASH_FLOW, POST_CUTOFF_ACTUAL, MIXED_CURRENCY_WITHOUT_CONVERSION, LIQUIDITY_BUFFER_BREACH, NOT_DUPLICATE_SAME_AMOUNT, KNOWLEDGE_AT_CUTOFF | Label |
| `is_anomaly` | enum | yes | true, false | Whether a control should fire |
| `target_type` | text | yes | non-empty text | Entity type |
| `target_id` | text | yes | non-empty text | Labelled record |
| `expected_decision` | enum | yes | FLAG_AND_USE_LEDGER_BALANCE, FLAG_UNRESOLVED_CONVERSION, FLAG_AND_EXCLUDE_DUPLICATE, FLAG_AND_EXCLUDE_FROM_FORECAST, REJECT_UNCONVERTED_TOTAL, ESCALATE_TO_TREASURY, INCLUDE | Decision a correct pipeline makes |
| `rule_reference` | text | yes | non-empty text | Rule in docs/business_rules.md |
| `notes` | text | yes | non-empty text | How the case was constructed |

