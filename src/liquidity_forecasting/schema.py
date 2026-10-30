"""Single source of truth for every exported table.

The generator writes columns in this order, the SQL export builds its DDL from
it, the validator enforces it and docs/data_dictionary.md is rendered from it.

Field tuple: (name, type, required, rule, description)
  id       rule is the regex the value must match (the table's own identifier)
  ref      rule is "table.field" for a foreign key
  enum     rule is the tuple of allowed values
  money    signed decimal with exactly two places, in the unit named by the description
  rate     positive decimal with six places
  int, date (YYYY-MM-DD), datetime (YYYY-MM-DDTHH:MM:SS), text, json, hash (64 hex chars)
"""
from __future__ import annotations

from .config import CURRENCIES, ONE_OFF_CATEGORIES, OPERATIONAL_TYPES

CCY = tuple(CURRENCIES)
ISSUES = ("BALANCE_RECONCILIATION_MISMATCH", "MISSING_FX_RATE", "DUPLICATE_CASH_FLOW", "POST_CUTOFF_ACTUAL",
          "MIXED_CURRENCY_WITHOUT_CONVERSION", "LIQUIDITY_BUFFER_BREACH")
DECISIONS = ("FLAG_AND_USE_LEDGER_BALANCE", "FLAG_UNRESOLVED_CONVERSION", "FLAG_AND_EXCLUDE_DUPLICATE",
             "FLAG_AND_EXCLUDE_FROM_FORECAST", "REJECT_UNCONVERTED_TOTAL", "ESCALATE_TO_TREASURY", "INCLUDE")

TABLES: dict[str, dict] = {
    "legal_entities": {
        "entity": "LegalEntity", "kind": "source", "pk": ["entity_id"],
        "fields": [
            ("entity_id", "id", True, r"ENT-\d{6}", "Immutable entity identifier"),
            ("legal_name", "text", True, None, "Synthetic registered name"),
            ("country", "enum", True, ("IN", "US", "GB", "DE", "SG"), "Country of incorporation"),
            ("functional_currency", "enum", True, CCY, "Currency of the entity's books"),
            ("minimum_cash_buffer_inr", "money", True, None, "INR; BUFFER_RATIO x opening INR value, floored to 100,000"),
            ("status", "enum", True, ("ACTIVE", "INACTIVE"), "Lifecycle state"),
            ("effective_from", "date", True, None, "Start of validity"),
            ("effective_to", "date", False, None, "End of validity; blank while active"),
        ],
    },
    "bank_accounts": {
        "entity": "BankAccount", "kind": "source", "pk": ["bank_account_id"],
        "fields": [
            ("bank_account_id", "id", True, r"BANKACC-ENT-\d{6}-\d{3}", "BANKACC-<entity-id>-<ordinal>"),
            ("entity_id", "ref", True, "legal_entities.entity_id", "Owning entity; must match the ID prefix"),
            ("bank_name", "text", True, None, "Synthetic bank name"),
            ("account_name", "text", True, None, "Display name"),
            ("account_currency", "enum", True, CCY, "Currency of every movement on the account"),
            ("account_purpose", "enum", True, ("OPERATING", "COLLECTION", "PAYROLL", "TREASURY"), "Usage"),
            ("opening_balance", "money", True, None, "Account currency; balance at start of opening_balance_date"),
            ("opening_balance_date", "date", True, None, "Equals HISTORY_START"),
            ("recorded_closing_balance", "money", True, None, "Account currency; bank-reported balance at end of closing_balance_date"),
            ("closing_balance_date", "date", True, None, "Last history date"),
            ("status", "enum", True, ("ACTIVE", "RESTRICTED", "CLOSED"), "Lifecycle state"),
            ("effective_from", "date", True, None, "On/after the entity's effective_from"),
            ("effective_to", "date", False, None, "Blank while open"),
        ],
    },
    "currency_rates": {
        "entity": "CurrencyRate", "kind": "source", "pk": ["rate_id"],
        "fields": [
            ("rate_id", "id", True, r"FX-(SPOT|FWD)-\d{8}-\d{8}-[A-Z]{3}-INR", "FX-<type>-<quote yyyymmdd>-<value yyyymmdd>-<from>-INR"),
            ("rate_type", "enum", True, ("SPOT", "FORWARD"), "SPOT for history, FORWARD curve published at a cutoff"),
            ("from_currency", "enum", True, CCY, "Base currency"),
            ("to_currency", "enum", True, ("INR",), "Always the reporting currency"),
            ("quote_date", "date", True, None, "Date the rate was published (known)"),
            ("value_date", "date", True, None, "Date the rate applies to; equals quote_date for SPOT"),
            ("rate", "rate", True, None, "INR per 1 unit of from_currency"),
            ("source", "enum", True, ("SYNTHETIC",), "Provenance"),
            ("status", "enum", True, ("ACTIVE", "SUPERSEDED"), "Lifecycle state"),
        ],
    },
    "liquidity_scenarios": {
        "entity": "LiquidityScenario", "kind": "source", "pk": ["scenario_id"],
        "fields": [
            ("scenario_id", "id", True, r"SCN-[A-Z]+", "Immutable scenario identifier"),
            ("scenario_name", "enum", True, ("BASE", "DELAYED_RECEIPT", "STRESS"), "Scenario family"),
            ("description", "text", True, None, "Plain-language assumption summary"),
            ("assumption_json", "json", True, None, "The only parameters allowed to differ between scenarios"),
            ("effective_from", "date", True, None, "Start of validity"),
            ("effective_to", "date", False, None, "Blank while active"),
            ("status", "enum", True, ("ACTIVE", "RETIRED"), "Lifecycle state"),
        ],
    },
    "receivables": {
        "entity": "Receivable", "kind": "source", "pk": ["receivable_id"],
        "fields": [
            ("receivable_id", "id", True, r"REC-2026-\d{6}", "Numbered in creation order"),
            ("entity_id", "ref", True, "legal_entities.entity_id", "Owning entity"),
            ("bank_account_id", "ref", True, "bank_accounts.bank_account_id", "Collection account; belongs to entity_id"),
            ("customer_reference", "text", True, None, "Synthetic customer code"),
            ("customer_risk_grade", "enum", True, ("A", "B", "C"), "Payment-behaviour grade of the customer"),
            ("invoice_reference", "text", True, None, "Unique invoice number"),
            ("amount", "money", True, None, "Account currency; positive"),
            ("currency", "enum", True, CCY, "Equals account currency"),
            ("created_at", "datetime", True, None, "Time the receivable became known"),
            ("expected_receipt_date", "date", True, None, "Contractual date rolled to a business day; after created_at"),
            ("actual_receipt_date", "date", False, None, "Blank until received; on/after expected date"),
            ("status", "enum", True, ("SCHEDULED", "OVERDUE", "RECEIVED"), "State at EXTRACT_AT; history in state_change_events"),
        ],
    },
    "payables": {
        "entity": "Payable", "kind": "source", "pk": ["payable_id"],
        "fields": [
            ("payable_id", "id", True, r"PAY-2026-\d{6}", "Numbered in creation order"),
            ("entity_id", "ref", True, "legal_entities.entity_id", "Owning entity"),
            ("bank_account_id", "ref", True, "bank_accounts.bank_account_id", "Paying account; belongs to entity_id"),
            ("supplier_reference", "text", True, None, "Synthetic supplier or authority code"),
            ("payment_category", "enum", True, ("TRADE",) + ONE_OFF_CATEGORIES, "Non-TRADE categories are large one-off payments"),
            ("bill_reference", "text", True, None, "Unique bill number"),
            ("amount", "money", True, None, "Account currency; positive"),
            ("currency", "enum", True, CCY, "Equals account currency"),
            ("created_at", "datetime", True, None, "Time the payable became known"),
            ("expected_payment_date", "date", True, None, "Scheduled date rolled to a business day"),
            ("actual_payment_date", "date", False, None, "Blank until paid"),
            ("status", "enum", True, ("SCHEDULED", "PAID"), "State at EXTRACT_AT"),
        ],
    },
    "ledger_entries": {
        "entity": "LedgerEntry", "kind": "source", "pk": ["ledger_id"],
        "fields": [
            ("ledger_id", "id", True, r"LED-2026-\d{8}", "Numbered in value-date order"),
            ("bank_account_id", "ref", True, "bank_accounts.bank_account_id", "Account moved"),
            ("entity_id", "ref", True, "legal_entities.entity_id", "Owner of the account"),
            ("transaction_date", "date", True, None, "Value date; within the history window"),
            ("posting_date", "date", True, None, "On/after transaction_date, never after the last history date"),
            ("available_at", "datetime", True, None, "Time the entry became visible to treasury"),
            ("transaction_type", "enum", True, ("CUSTOMER_RECEIPT", "SUPPLIER_PAYMENT", "ONE_OFF_PAYMENT", "PAYROLL") + tuple(t[0] for t in OPERATIONAL_TYPES), "Cash movement category"),
            ("cash_movement", "money", True, None, "Account currency; positive inflow, negative outflow, never zero"),
            ("currency", "enum", True, CCY, "Equals account currency"),
            ("transaction_reference", "id", True, r"TXN-2026-\d{8}", "Unique bank reference"),
            ("settlement_source_type", "enum", False, ("RECEIVABLE", "PAYABLE"), "Set when the entry settles a scheduled item"),
            ("settlement_source_id", "text", False, None, "receivable_id or payable_id settled by this entry"),
            ("record_status", "enum", True, ("POSTED",), "Ledger entries are immutable once posted"),
        ],
    },
    "cash_flow_forecasts": {
        "entity": "CashFlowForecast", "kind": "source", "pk": ["cash_flow_forecast_id"],
        "fields": [
            ("cash_flow_forecast_id", "id", True, r"CF-2026-\d{6}", "Numbered in knowledge_time order"),
            ("source_type", "enum", True, ("RECEIVABLE", "PAYABLE", "ACTUAL_LEDGER"), "What the feed says the item came from"),
            ("source_id", "text", True, None, "receivable_id, payable_id or ledger_id"),
            ("entity_id", "ref", True, "legal_entities.entity_id", "Matches the source"),
            ("bank_account_id", "ref", True, "bank_accounts.bank_account_id", "Matches the source"),
            ("direction", "enum", True, ("INFLOW", "OUTFLOW"), "Receivable inflow, payable outflow"),
            ("amount", "money", True, None, "Account currency; positive"),
            ("currency", "enum", True, CCY, "Equals account currency"),
            ("expected_date", "date", True, None, "Scheduled date from the source"),
            ("knowledge_time", "datetime", True, None, "When the item entered the forecast feed"),
            ("source_reference", "text", True, None, "Invoice, bill or bank reference"),
        ],
    },
    "state_change_events": {
        "entity": "StateChangeEvent", "kind": "source", "pk": ["event_id"],
        "fields": [
            ("event_id", "id", True, r"EVT-2026-\d{8}", "Numbered in event_time order"),
            ("object_type", "enum", True, ("Receivable", "Payable"), "Type of object whose state changed"),
            ("object_id", "text", True, None, "receivable_id or payable_id"),
            ("event_type", "enum", True, ("CREATED", "BECAME_OVERDUE", "SETTLED"), "Lifecycle event"),
            ("old_status", "enum", False, ("SCHEDULED", "OVERDUE"), "Blank for CREATED"),
            ("new_status", "enum", True, ("SCHEDULED", "OVERDUE", "RECEIVED", "PAID"), "State after the event"),
            ("event_time", "datetime", True, None, "Not after EXTRACT_AT"),
            ("details", "text", True, None, "Human-readable reason"),
        ],
    },
    "entity_balance_reports": {
        "entity": "EntityBalanceReport", "kind": "source", "pk": ["report_id"],
        "fields": [
            ("report_id", "id", True, r"EBR-2026-\d{6}", "Entity cash report submitted to group treasury"),
            ("entity_id", "ref", True, "legal_entities.entity_id", "Reporting entity"),
            ("report_date", "date", True, None, "Balance date"),
            ("reported_currency", "enum", True, CCY, "Entity functional currency"),
            ("reported_total", "money", True, None, "reported_currency; sum of account closings converted at SPOT"),
            ("account_count", "int", True, None, "Accounts included"),
            ("submitted_at", "datetime", True, None, "Submission time"),
        ],
    },
    "daily_balances": {
        "entity": "DailyBalance", "kind": "derived", "pk": ["bank_account_id", "balance_date"],
        "fields": [
            ("bank_account_id", "ref", True, "bank_accounts.bank_account_id", "Account"),
            ("balance_date", "date", True, None, "History date"),
            ("currency", "enum", True, CCY, "Account currency"),
            ("opening_balance", "money", True, None, "Previous day's closing (opening_balance on day one)"),
            ("inflows", "money", True, None, "Sum of positive ledger movements on the value date"),
            ("outflows", "money", True, None, "Sum of negative ledger movements (as a positive number)"),
            ("closing_balance", "money", True, None, "opening + inflows - outflows"),
            ("entry_count", "int", True, None, "Ledger entries on the value date"),
        ],
    },
    "forecast_runs": {
        "entity": "ForecastRun", "kind": "derived", "pk": ["forecast_run_id"],
        "fields": [
            ("forecast_run_id", "id", True, r"FCAST-\d{4}-\d{2}-\d{2}-\d{3}", "FCAST-<cutoff date>-<counter>"),
            ("scenario_id", "ref", True, "liquidity_scenarios.scenario_id", "Scenario applied"),
            ("run_version", "int", True, None, "Version number within the scenario"),
            ("previous_run_id", "ref", False, "forecast_runs.forecast_run_id", "Prior version; never overwritten"),
            ("created_at", "datetime", True, None, "On/after data_cutoff"),
            ("data_cutoff", "datetime", True, None, "Only information known at or before this time is used"),
            ("horizon_start", "date", True, None, "Day after the cutoff date"),
            ("horizon_end", "date", True, None, "horizon_start + HORIZON_DAYS - 1"),
            ("fx_quote_date", "date", True, None, "Forward curve used for valuation"),
            ("calculation_version", "text", True, None, "Code version of the rules"),
            ("input_snapshot_hash", "hash", True, None, "SHA-256 of eligible flows, starting balances and FX used"),
            ("included_items", "int", True, None, "Run items with decision INCLUDED"),
            ("excluded_items", "int", True, None, "Run items with decision EXCLUDED"),
            ("exception_count", "int", True, None, "Exceptions raised by the run"),
            ("status", "enum", True, ("COMPLETED", "COMPLETED_WITH_EXCEPTIONS", "FAILED"), "Final state"),
        ],
    },
    "forecast_run_items": {
        "entity": "ForecastRunItem (bridge ForecastRun N:M CashFlowForecast)", "kind": "derived",
        "pk": ["forecast_run_id", "cash_flow_forecast_id"],
        "fields": [
            ("forecast_run_id", "ref", True, "forecast_runs.forecast_run_id", "Run"),
            ("cash_flow_forecast_id", "ref", True, "cash_flow_forecasts.cash_flow_forecast_id", "Candidate item"),
            ("decision", "enum", True, ("INCLUDED", "EXCLUDED"), "Outcome of the eligibility rules"),
            ("exclusion_reason", "enum", False, ("KNOWN_AFTER_CUTOFF", "DUPLICATE_SOURCE", "ACTUAL_ALREADY_IN_BALANCE",
                                                  "DUE_ON_OR_BEFORE_CUTOFF", "OUTSIDE_HORIZON"), "First failing rule; blank when included"),
            ("scheduled_date", "date", True, None, "expected_date of the item"),
            ("scenario_date", "date", True, None, "Date after scenario adjustment"),
            ("adjustment", "enum", True, ("NONE", "RECEIPT_DELAY"), "Scenario adjustment applied"),
            ("signed_amount", "money", True, None, "Account currency; positive inflow, negative outflow"),
        ],
    },
    "cash_positions": {
        "entity": "CashPosition", "kind": "derived", "pk": ["forecast_run_id", "bank_account_id", "position_date"],
        "fields": [
            ("forecast_run_id", "ref", True, "forecast_runs.forecast_run_id", "Run"),
            ("bank_account_id", "ref", True, "bank_accounts.bank_account_id", "Account"),
            ("entity_id", "ref", True, "legal_entities.entity_id", "Owner"),
            ("position_date", "date", True, None, "Horizon date"),
            ("native_currency", "enum", True, CCY, "Account currency"),
            ("opening_native", "money", True, None, "Ledger balance known at cutoff on day one, else previous closing"),
            ("inflows_native", "money", True, None, "Included inflows on scenario_date"),
            ("outflows_native", "money", True, None, "Included outflows on scenario_date (positive)"),
            ("scenario_outflow_native", "money", True, None, "Unplanned one-off outflow from scenario assumptions"),
            ("closing_native", "money", True, None, "opening + inflows - outflows - scenario_outflow"),
            ("fx_rate", "rate", False, None, "Forward rate quote_date=fx_quote_date, value_date=position_date; blank if missing"),
            ("fx_shock_pct", "text", True, None, "Scenario FX shock applied to non-INR accounts"),
            ("closing_reporting", "money", False, None, "INR; round(closing_native x rate x (1 + shock/100)); blank if unresolved"),
            ("valuation_status", "enum", True, ("VALUED", "UNRESOLVED_FX"), "Never valued without a rate"),
        ],
    },
    "entity_positions": {
        "entity": "EntityPosition", "kind": "derived", "pk": ["forecast_run_id", "entity_id", "position_date"],
        "fields": [
            ("forecast_run_id", "ref", True, "forecast_runs.forecast_run_id", "Run"),
            ("entity_id", "ref", True, "legal_entities.entity_id", "Entity"),
            ("position_date", "date", True, None, "Horizon date"),
            ("valued_total_inr", "money", True, None, "INR; sum of VALUED account positions only"),
            ("unresolved_accounts", "int", True, None, "Accounts left out because FX is missing"),
            ("minimum_cash_buffer_inr", "money", True, None, "INR; from legal_entities"),
            ("buffer_status", "enum", True, ("OK", "BREACH", "UNRESOLVED_FX"), "UNRESOLVED_FX wins; else BREACH when total < buffer"),
        ],
    },
    "exceptions": {
        "entity": "ExceptionRecord", "kind": "derived", "pk": ["exception_id"],
        "fields": [
            ("exception_id", "id", True, r"EXC-2026-\d{6}", "Sequential"),
            ("forecast_run_id", "ref", False, "forecast_runs.forecast_run_id", "Blank for data-level controls"),
            ("issue_type", "enum", True, ISSUES, "Control that fired"),
            ("severity", "enum", True, ("HIGH", "MEDIUM"), "Triage priority"),
            ("target_type", "text", True, None, "Entity type of target_id"),
            ("target_id", "text", True, None, "Record (or missing rate_id) the exception concerns"),
            ("decision", "enum", True, DECISIONS, "What the pipeline did"),
            ("owner_role", "enum", True, ("OPERATIONS_ANALYST", "RISK_COMPLIANCE_REVIEWER"), "Who must act"),
            ("detected_at", "datetime", True, None, "Run creation time, or AS_OF for data-level controls"),
            ("details", "text", True, None, "Evidence"),
        ],
    },
    "actual_vs_forecast": {
        "entity": "ForecastActualComparison", "kind": "derived", "pk": ["forecast_run_id", "bank_account_id", "position_date"],
        "fields": [
            ("forecast_run_id", "ref", True, "forecast_runs.forecast_run_id", "Run being back-tested"),
            ("bank_account_id", "ref", True, "bank_accounts.bank_account_id", "Account"),
            ("position_date", "date", True, None, "Horizon date with complete actuals"),
            ("currency", "enum", True, CCY, "Account currency"),
            ("forecast_native", "money", True, None, "cash_positions.closing_native"),
            ("actual_native", "money", True, None, "daily_balances.closing_balance"),
            ("variance_native", "money", True, None, "actual - forecast = sum of the three components"),
            ("cutoff_lag_component", "money", True, None, "Entries valued on/before cutoff date but visible only after the cutoff"),
            ("timing_component", "money", True, None, "Included items settled on a different date than forecast"),
            ("unforecast_component", "money", True, None, "Horizon ledger activity not linked to an included item"),
            ("primary_driver", "enum", True, ("NONE", "CUTOFF_LAG", "TIMING", "UNFORECAST"), "Largest absolute component"),
            ("forecast_rate", "rate", True, None, "Forward rate used by the run"),
            ("actual_rate", "rate", True, None, "SPOT rate on position_date"),
            ("forecast_reporting", "money", True, None, "INR"),
            ("actual_reporting", "money", True, None, "INR"),
            ("variance_reporting", "money", True, None, "INR; volume_effect + fx_effect"),
            ("volume_effect_reporting", "money", True, None, "INR; variance_native valued at actual_rate"),
            ("fx_effect_reporting", "money", True, None, "INR; forecast_native x (actual_rate - forecast_rate), incl. rounding"),
        ],
    },
    "scenario_summary": {
        "entity": "ScenarioSummary", "kind": "derived", "pk": ["forecast_run_id"],
        "fields": [
            ("forecast_run_id", "ref", True, "forecast_runs.forecast_run_id", "Run"),
            ("scenario_id", "ref", True, "liquidity_scenarios.scenario_id", "Scenario"),
            ("opening_group_inr", "money", True, None, "INR; starting balances valued at the first horizon rate"),
            ("min_group_inr", "money", True, None, "INR; lowest group total over fully valued days"),
            ("min_group_date", "date", True, None, "Date of min_group_inr"),
            ("closing_group_inr", "money", True, None, "INR; group total on the last fully valued day"),
            ("breach_entity_days", "int", True, None, "Entity-days below minimum buffer"),
            ("entities_breaching", "int", True, None, "Distinct entities with at least one breach"),
            ("unresolved_positions", "int", True, None, "Account-days left unvalued for missing FX"),
            ("delayed_items", "int", True, None, "Included items moved by RECEIPT_DELAY"),
        ],
    },
    "ground_truth": {
        "entity": "GroundTruth (labels only; never an input)", "kind": "label", "pk": ["case_id"],
        "fields": [
            ("case_id", "id", True, r"(ANOM|CTRL)-\d{3}", "ANOM = injected anomaly, CTRL = boundary control that must not be flagged"),
            ("issue_type", "enum", True, ISSUES + ("NOT_DUPLICATE_SAME_AMOUNT", "KNOWLEDGE_AT_CUTOFF"), "Label"),
            ("is_anomaly", "enum", True, ("true", "false"), "Whether a control should fire"),
            ("target_type", "text", True, None, "Entity type"),
            ("target_id", "text", True, None, "Labelled record"),
            ("expected_decision", "enum", True, DECISIONS, "Decision a correct pipeline makes"),
            ("rule_reference", "text", True, None, "Rule in docs/business_rules.md"),
            ("notes", "text", True, None, "How the case was constructed"),
        ],
    },
}

SOURCE_TABLES = [name for name, spec in TABLES.items() if spec["kind"] == "source"]
DERIVED_TABLES = [name for name, spec in TABLES.items() if spec["kind"] == "derived"]


def columns(table: str) -> list[str]:
    return [field[0] for field in TABLES[table]["fields"]]


SQL_TYPES = {"money": "NUMERIC(18,2)", "rate": "NUMERIC(18,6)", "int": "INTEGER", "date": "DATE", "datetime": "TIMESTAMP"}


def sql_ddl(table: str) -> str:
    spec = TABLES[table]
    lines = []
    for name, kind, required, rule, _ in spec["fields"]:
        lines.append(f'  "{name}" {SQL_TYPES.get(kind, "TEXT")}{" NOT NULL" if required else ""}')
    lines.append("  PRIMARY KEY (" + ", ".join(f'"{key}"' for key in spec["pk"]) + ")")
    for name, kind, _, rule, _ in spec["fields"]:
        if kind == "ref":
            parent, field = rule.split(".")
            lines.append(f'  FOREIGN KEY ("{name}") REFERENCES "{parent}" ("{field}")')
    return f'CREATE TABLE "{table}" (\n' + ",\n".join(lines) + "\n);"


def render_data_dictionary() -> str:
    out = ["# Data dictionary", "",
           "Generated from `src/liquidity_forecasting/schema.py` by `python scripts/build_docs.py`; do not edit by hand.", "",
           "Money is a signed decimal string with two places in the unit stated; calculations use integer cents. "
           "Dates are ISO `YYYY-MM-DD`, timestamps ISO `YYYY-MM-DDTHH:MM:SS` (synthetic local treasury time). "
           "Every ID is immutable and unique within its table. `kind` is `source` (generator input / seed data), "
           "`derived` (calculated only from source rows) or `label` (ground truth, never read by the pipeline).", ""]
    for table, spec in TABLES.items():
        out += [f"## {spec['entity']} — `{table}.csv`", "",
                f"kind: **{spec['kind']}** · primary key: `{', '.join(spec['pk'])}`", "",
                "| Field | Type | Required | Allowed values / format | Description |", "|---|---|---|---|---|"]
        for name, kind, required, rule, description in spec["fields"]:
            if kind == "enum":
                allowed = ", ".join(rule)
            elif kind in ("id", "ref"):
                allowed = f"`{rule}`" if kind == "id" else f"FK → `{rule}`"
            else:
                allowed = {"money": "decimal(18,2)", "rate": "decimal(18,6) > 0", "int": "integer ≥ 0", "date": "ISO date",
                           "datetime": "ISO timestamp", "json": "JSON object", "hash": "64 hex chars", "text": "non-empty text"}[kind]
            unique = " (unique)" if [name] == spec["pk"] or name in ("transaction_reference", "invoice_reference", "bill_reference") else ""
            out.append(f"| `{name}` | {kind} | {'yes' if required else 'no'} | {allowed}{unique} | {description} |")
        out.append("")
    return "\n".join(out)
