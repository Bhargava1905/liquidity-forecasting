"""Fixed parameters for the synthetic liquidity forecasting dataset.

Every assumption that changes a generated outcome lives here, so each rule in
docs/business_rules.md can be traced to one constant.
"""
from dataclasses import dataclass

SEED = 20261022
REPORTING_CURRENCY = "INR"
CURRENCIES = ("INR", "USD", "EUR", "GBP", "SGD")

HISTORY_START = "2026-10-01"             # first ledger value date; opening balances are at its start
AS_OF = "2026-10-30T10:00:00"            # data cutoff of the current forecast runs
BACKTEST_CUTOFF = "2026-10-15T10:00:00"  # cutoff of the earlier base run that is compared to actuals
EXTRACT_AT = "2026-10-30T20:00:00"       # time the source snapshot was extracted; nothing is later
HORIZON_DAYS = 30
CALCULATION_VERSION = "2.0.0"


@dataclass(frozen=True)
class Scale:
    entities: int
    accounts: int
    ledger_entries: int
    forecast_items: int


SCALES = {
    "starter": Scale(3, 15, 1_000, 300),
    "full": Scale(30, 150, 100_000, 20_000),
}

# FX: INR per unit on HISTORY_START, daily log-volatility, annual forward carry and one documented shock.
FX_START = {"INR": 1.0, "USD": 84.50, "EUR": 91.50, "GBP": 108.20, "SGD": 63.00}
FX_DAILY_VOL = {"INR": 0.0, "USD": 0.003, "EUR": 0.005, "GBP": 0.006, "SGD": 0.004}
FX_FORWARD_CARRY = {"INR": 0.0, "USD": 0.025, "EUR": 0.030, "GBP": 0.020, "SGD": 0.030}
FX_EVENTS = {("GBP", "2026-10-22"): -0.03}

# Observed customer behaviour: calendar days late by risk grade, then rolled to the next business day.
RISK_GRADE_WEIGHTS = {"A": 0.70, "B": 0.20, "C": 0.10}
RECEIPT_DELAY_DAYS = {"A": 0, "B": 3, "C": 9}

RECEIVABLE_SHARE = 0.55
ONE_OFF_SHARE = 0.006                    # share of payables that are large one-off payments
ONE_OFF_MINIMUM = 4                      # topped up from TRADE payables if the share yields fewer
ONE_OFF_CATEGORIES = ("TAX", "CAPEX", "DIVIDEND", "LOAN_REPAYMENT")
BUFFER_RATIO = 0.50                      # entity minimum cash buffer as a share of its opening INR value

# Non-forecast bank activity: (type, sign, weight, median INR, log-sigma, posts on weekends).
OPERATIONAL_TYPES = (
    ("CARD_SETTLEMENT", 1, 0.40, 25_000, 0.9, True),
    ("MISC_RECEIPT", 1, 0.10, 60_000, 1.1, False),
    ("DIRECT_DEBIT", -1, 0.36, 40_000, 1.0, False),
    ("CUSTOMER_REFUND", -1, 0.07, 15_000, 0.8, False),
    ("BANK_FEE", -1, 0.07, 400, 0.6, False),
)
PAYROLL_DAY = "2026-10-28"

SCENARIOS = (
    {"scenario_id": "SCN-BASE", "scenario_name": "BASE",
     "description": "Scheduled receivable and payable dates, forward FX curve at cutoff, no adjustments.",
     "assumptions": {"receipt_delay_days_by_grade": {"A": 0, "B": 0, "C": 0}, "fx_shock_pct": 0.0, "one_off_outflows": []}},
    {"scenario_id": "SCN-DELAY", "scenario_name": "DELAYED_RECEIPT",
     "description": "Receipts from grade B and C customers arrive late by their observed delay.",
     "assumptions": {"receipt_delay_days_by_grade": {"A": 0, "B": 3, "C": 9}, "fx_shock_pct": 0.0, "one_off_outflows": []}},
    {"scenario_id": "SCN-STRESS", "scenario_name": "STRESS",
     "description": "Longer receipt delays for all grades, 5% adverse FX on non-INR balances and an unplanned INR 250m outflow.",
     "assumptions": {"receipt_delay_days_by_grade": {"A": 3, "B": 10, "C": 21}, "fx_shock_pct": -5.0,
                     "one_off_outflows": [{"bank_account_id": "BANKACC-ENT-000001-001", "horizon_day": 3,
                                           "amount": "250000000.00", "reason": "Litigation settlement"}]}},
)

# Forecast runs are append-only versions. The 2026-10-15 base run is the prior version of the current base run.
FORECAST_RUNS = (
    {"forecast_run_id": "FCAST-2026-10-15-001", "scenario_id": "SCN-BASE", "data_cutoff": BACKTEST_CUTOFF, "run_version": 1, "previous_run_id": ""},
    {"forecast_run_id": "FCAST-2026-10-30-001", "scenario_id": "SCN-BASE", "data_cutoff": AS_OF, "run_version": 2, "previous_run_id": "FCAST-2026-10-15-001"},
    {"forecast_run_id": "FCAST-2026-10-30-002", "scenario_id": "SCN-DELAY", "data_cutoff": AS_OF, "run_version": 1, "previous_run_id": ""},
    {"forecast_run_id": "FCAST-2026-10-30-003", "scenario_id": "SCN-STRESS", "data_cutoff": AS_OF, "run_version": 1, "previous_run_id": ""},
)

# Injected anomalies. Counts are identical at every scale.
RECONCILIATION_DELTAS = (-50_000, 123_456, -1)   # cents added to recorded closing; -1 is the one-cent boundary
MISSING_FX = (("GBP", 10), ("SGD", 21))           # (currency, horizon day) removed from the AS_OF forward curve
DUPLICATE_FLOWS = 3
POST_CUTOFF_ACTUALS = 2
MIXED_CURRENCY_REPORTS = 2
INJECTED_FLOWS = DUPLICATE_FLOWS + POST_CUTOFF_ACTUALS
