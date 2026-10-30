-- DDL for every exported table, generated from schema.py.

CREATE TABLE "legal_entities" (
  "entity_id" TEXT NOT NULL,
  "legal_name" TEXT NOT NULL,
  "country" TEXT NOT NULL,
  "functional_currency" TEXT NOT NULL,
  "minimum_cash_buffer_inr" NUMERIC(18,2) NOT NULL,
  "status" TEXT NOT NULL,
  "effective_from" DATE NOT NULL,
  "effective_to" DATE,
  PRIMARY KEY ("entity_id")
);

CREATE TABLE "bank_accounts" (
  "bank_account_id" TEXT NOT NULL,
  "entity_id" TEXT NOT NULL,
  "bank_name" TEXT NOT NULL,
  "account_name" TEXT NOT NULL,
  "account_currency" TEXT NOT NULL,
  "account_purpose" TEXT NOT NULL,
  "opening_balance" NUMERIC(18,2) NOT NULL,
  "opening_balance_date" DATE NOT NULL,
  "recorded_closing_balance" NUMERIC(18,2) NOT NULL,
  "closing_balance_date" DATE NOT NULL,
  "status" TEXT NOT NULL,
  "effective_from" DATE NOT NULL,
  "effective_to" DATE,
  PRIMARY KEY ("bank_account_id"),
  FOREIGN KEY ("entity_id") REFERENCES "legal_entities" ("entity_id")
);

CREATE TABLE "currency_rates" (
  "rate_id" TEXT NOT NULL,
  "rate_type" TEXT NOT NULL,
  "from_currency" TEXT NOT NULL,
  "to_currency" TEXT NOT NULL,
  "quote_date" DATE NOT NULL,
  "value_date" DATE NOT NULL,
  "rate" NUMERIC(18,6) NOT NULL,
  "source" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  PRIMARY KEY ("rate_id")
);

CREATE TABLE "liquidity_scenarios" (
  "scenario_id" TEXT NOT NULL,
  "scenario_name" TEXT NOT NULL,
  "description" TEXT NOT NULL,
  "assumption_json" TEXT NOT NULL,
  "effective_from" DATE NOT NULL,
  "effective_to" DATE,
  "status" TEXT NOT NULL,
  PRIMARY KEY ("scenario_id")
);

CREATE TABLE "receivables" (
  "receivable_id" TEXT NOT NULL,
  "entity_id" TEXT NOT NULL,
  "bank_account_id" TEXT NOT NULL,
  "customer_reference" TEXT NOT NULL,
  "customer_risk_grade" TEXT NOT NULL,
  "invoice_reference" TEXT NOT NULL,
  "amount" NUMERIC(18,2) NOT NULL,
  "currency" TEXT NOT NULL,
  "created_at" TIMESTAMP NOT NULL,
  "expected_receipt_date" DATE NOT NULL,
  "actual_receipt_date" DATE,
  "status" TEXT NOT NULL,
  PRIMARY KEY ("receivable_id"),
  FOREIGN KEY ("entity_id") REFERENCES "legal_entities" ("entity_id"),
  FOREIGN KEY ("bank_account_id") REFERENCES "bank_accounts" ("bank_account_id")
);

CREATE TABLE "payables" (
  "payable_id" TEXT NOT NULL,
  "entity_id" TEXT NOT NULL,
  "bank_account_id" TEXT NOT NULL,
  "supplier_reference" TEXT NOT NULL,
  "payment_category" TEXT NOT NULL,
  "bill_reference" TEXT NOT NULL,
  "amount" NUMERIC(18,2) NOT NULL,
  "currency" TEXT NOT NULL,
  "created_at" TIMESTAMP NOT NULL,
  "expected_payment_date" DATE NOT NULL,
  "actual_payment_date" DATE,
  "status" TEXT NOT NULL,
  PRIMARY KEY ("payable_id"),
  FOREIGN KEY ("entity_id") REFERENCES "legal_entities" ("entity_id"),
  FOREIGN KEY ("bank_account_id") REFERENCES "bank_accounts" ("bank_account_id")
);

CREATE TABLE "ledger_entries" (
  "ledger_id" TEXT NOT NULL,
  "bank_account_id" TEXT NOT NULL,
  "entity_id" TEXT NOT NULL,
  "transaction_date" DATE NOT NULL,
  "posting_date" DATE NOT NULL,
  "available_at" TIMESTAMP NOT NULL,
  "transaction_type" TEXT NOT NULL,
  "cash_movement" NUMERIC(18,2) NOT NULL,
  "currency" TEXT NOT NULL,
  "transaction_reference" TEXT NOT NULL,
  "settlement_source_type" TEXT,
  "settlement_source_id" TEXT,
  "record_status" TEXT NOT NULL,
  PRIMARY KEY ("ledger_id"),
  FOREIGN KEY ("bank_account_id") REFERENCES "bank_accounts" ("bank_account_id"),
  FOREIGN KEY ("entity_id") REFERENCES "legal_entities" ("entity_id")
);

CREATE TABLE "cash_flow_forecasts" (
  "cash_flow_forecast_id" TEXT NOT NULL,
  "source_type" TEXT NOT NULL,
  "source_id" TEXT NOT NULL,
  "entity_id" TEXT NOT NULL,
  "bank_account_id" TEXT NOT NULL,
  "direction" TEXT NOT NULL,
  "amount" NUMERIC(18,2) NOT NULL,
  "currency" TEXT NOT NULL,
  "expected_date" DATE NOT NULL,
  "knowledge_time" TIMESTAMP NOT NULL,
  "source_reference" TEXT NOT NULL,
  PRIMARY KEY ("cash_flow_forecast_id"),
  FOREIGN KEY ("entity_id") REFERENCES "legal_entities" ("entity_id"),
  FOREIGN KEY ("bank_account_id") REFERENCES "bank_accounts" ("bank_account_id")
);

CREATE TABLE "state_change_events" (
  "event_id" TEXT NOT NULL,
  "object_type" TEXT NOT NULL,
  "object_id" TEXT NOT NULL,
  "event_type" TEXT NOT NULL,
  "old_status" TEXT,
  "new_status" TEXT NOT NULL,
  "event_time" TIMESTAMP NOT NULL,
  "details" TEXT NOT NULL,
  PRIMARY KEY ("event_id")
);

CREATE TABLE "entity_balance_reports" (
  "report_id" TEXT NOT NULL,
  "entity_id" TEXT NOT NULL,
  "report_date" DATE NOT NULL,
  "reported_currency" TEXT NOT NULL,
  "reported_total" NUMERIC(18,2) NOT NULL,
  "account_count" INTEGER NOT NULL,
  "submitted_at" TIMESTAMP NOT NULL,
  PRIMARY KEY ("report_id"),
  FOREIGN KEY ("entity_id") REFERENCES "legal_entities" ("entity_id")
);

CREATE TABLE "daily_balances" (
  "bank_account_id" TEXT NOT NULL,
  "balance_date" DATE NOT NULL,
  "currency" TEXT NOT NULL,
  "opening_balance" NUMERIC(18,2) NOT NULL,
  "inflows" NUMERIC(18,2) NOT NULL,
  "outflows" NUMERIC(18,2) NOT NULL,
  "closing_balance" NUMERIC(18,2) NOT NULL,
  "entry_count" INTEGER NOT NULL,
  PRIMARY KEY ("bank_account_id", "balance_date"),
  FOREIGN KEY ("bank_account_id") REFERENCES "bank_accounts" ("bank_account_id")
);

CREATE TABLE "forecast_runs" (
  "forecast_run_id" TEXT NOT NULL,
  "scenario_id" TEXT NOT NULL,
  "run_version" INTEGER NOT NULL,
  "previous_run_id" TEXT,
  "created_at" TIMESTAMP NOT NULL,
  "data_cutoff" TIMESTAMP NOT NULL,
  "horizon_start" DATE NOT NULL,
  "horizon_end" DATE NOT NULL,
  "fx_quote_date" DATE NOT NULL,
  "calculation_version" TEXT NOT NULL,
  "input_snapshot_hash" TEXT NOT NULL,
  "included_items" INTEGER NOT NULL,
  "excluded_items" INTEGER NOT NULL,
  "exception_count" INTEGER NOT NULL,
  "status" TEXT NOT NULL,
  PRIMARY KEY ("forecast_run_id"),
  FOREIGN KEY ("scenario_id") REFERENCES "liquidity_scenarios" ("scenario_id"),
  FOREIGN KEY ("previous_run_id") REFERENCES "forecast_runs" ("forecast_run_id")
);

CREATE TABLE "forecast_run_items" (
  "forecast_run_id" TEXT NOT NULL,
  "cash_flow_forecast_id" TEXT NOT NULL,
  "decision" TEXT NOT NULL,
  "exclusion_reason" TEXT,
  "scheduled_date" DATE NOT NULL,
  "scenario_date" DATE NOT NULL,
  "adjustment" TEXT NOT NULL,
  "signed_amount" NUMERIC(18,2) NOT NULL,
  PRIMARY KEY ("forecast_run_id", "cash_flow_forecast_id"),
  FOREIGN KEY ("forecast_run_id") REFERENCES "forecast_runs" ("forecast_run_id"),
  FOREIGN KEY ("cash_flow_forecast_id") REFERENCES "cash_flow_forecasts" ("cash_flow_forecast_id")
);

CREATE TABLE "cash_positions" (
  "forecast_run_id" TEXT NOT NULL,
  "bank_account_id" TEXT NOT NULL,
  "entity_id" TEXT NOT NULL,
  "position_date" DATE NOT NULL,
  "native_currency" TEXT NOT NULL,
  "opening_native" NUMERIC(18,2) NOT NULL,
  "inflows_native" NUMERIC(18,2) NOT NULL,
  "outflows_native" NUMERIC(18,2) NOT NULL,
  "scenario_outflow_native" NUMERIC(18,2) NOT NULL,
  "closing_native" NUMERIC(18,2) NOT NULL,
  "fx_rate" NUMERIC(18,6),
  "fx_shock_pct" TEXT NOT NULL,
  "closing_reporting" NUMERIC(18,2),
  "valuation_status" TEXT NOT NULL,
  PRIMARY KEY ("forecast_run_id", "bank_account_id", "position_date"),
  FOREIGN KEY ("forecast_run_id") REFERENCES "forecast_runs" ("forecast_run_id"),
  FOREIGN KEY ("bank_account_id") REFERENCES "bank_accounts" ("bank_account_id"),
  FOREIGN KEY ("entity_id") REFERENCES "legal_entities" ("entity_id")
);

CREATE TABLE "entity_positions" (
  "forecast_run_id" TEXT NOT NULL,
  "entity_id" TEXT NOT NULL,
  "position_date" DATE NOT NULL,
  "valued_total_inr" NUMERIC(18,2) NOT NULL,
  "unresolved_accounts" INTEGER NOT NULL,
  "minimum_cash_buffer_inr" NUMERIC(18,2) NOT NULL,
  "buffer_status" TEXT NOT NULL,
  PRIMARY KEY ("forecast_run_id", "entity_id", "position_date"),
  FOREIGN KEY ("forecast_run_id") REFERENCES "forecast_runs" ("forecast_run_id"),
  FOREIGN KEY ("entity_id") REFERENCES "legal_entities" ("entity_id")
);

CREATE TABLE "exceptions" (
  "exception_id" TEXT NOT NULL,
  "forecast_run_id" TEXT,
  "issue_type" TEXT NOT NULL,
  "severity" TEXT NOT NULL,
  "target_type" TEXT NOT NULL,
  "target_id" TEXT NOT NULL,
  "decision" TEXT NOT NULL,
  "owner_role" TEXT NOT NULL,
  "detected_at" TIMESTAMP NOT NULL,
  "details" TEXT NOT NULL,
  PRIMARY KEY ("exception_id"),
  FOREIGN KEY ("forecast_run_id") REFERENCES "forecast_runs" ("forecast_run_id")
);

CREATE TABLE "actual_vs_forecast" (
  "forecast_run_id" TEXT NOT NULL,
  "bank_account_id" TEXT NOT NULL,
  "position_date" DATE NOT NULL,
  "currency" TEXT NOT NULL,
  "forecast_native" NUMERIC(18,2) NOT NULL,
  "actual_native" NUMERIC(18,2) NOT NULL,
  "variance_native" NUMERIC(18,2) NOT NULL,
  "cutoff_lag_component" NUMERIC(18,2) NOT NULL,
  "timing_component" NUMERIC(18,2) NOT NULL,
  "unforecast_component" NUMERIC(18,2) NOT NULL,
  "primary_driver" TEXT NOT NULL,
  "forecast_rate" NUMERIC(18,6) NOT NULL,
  "actual_rate" NUMERIC(18,6) NOT NULL,
  "forecast_reporting" NUMERIC(18,2) NOT NULL,
  "actual_reporting" NUMERIC(18,2) NOT NULL,
  "variance_reporting" NUMERIC(18,2) NOT NULL,
  "volume_effect_reporting" NUMERIC(18,2) NOT NULL,
  "fx_effect_reporting" NUMERIC(18,2) NOT NULL,
  PRIMARY KEY ("forecast_run_id", "bank_account_id", "position_date"),
  FOREIGN KEY ("forecast_run_id") REFERENCES "forecast_runs" ("forecast_run_id"),
  FOREIGN KEY ("bank_account_id") REFERENCES "bank_accounts" ("bank_account_id")
);

CREATE TABLE "scenario_summary" (
  "forecast_run_id" TEXT NOT NULL,
  "scenario_id" TEXT NOT NULL,
  "opening_group_inr" NUMERIC(18,2) NOT NULL,
  "min_group_inr" NUMERIC(18,2) NOT NULL,
  "min_group_date" DATE NOT NULL,
  "closing_group_inr" NUMERIC(18,2) NOT NULL,
  "breach_entity_days" INTEGER NOT NULL,
  "entities_breaching" INTEGER NOT NULL,
  "unresolved_positions" INTEGER NOT NULL,
  "delayed_items" INTEGER NOT NULL,
  PRIMARY KEY ("forecast_run_id"),
  FOREIGN KEY ("forecast_run_id") REFERENCES "forecast_runs" ("forecast_run_id"),
  FOREIGN KEY ("scenario_id") REFERENCES "liquidity_scenarios" ("scenario_id")
);

CREATE TABLE "ground_truth" (
  "case_id" TEXT NOT NULL,
  "issue_type" TEXT NOT NULL,
  "is_anomaly" TEXT NOT NULL,
  "target_type" TEXT NOT NULL,
  "target_id" TEXT NOT NULL,
  "expected_decision" TEXT NOT NULL,
  "rule_reference" TEXT NOT NULL,
  "notes" TEXT NOT NULL,
  PRIMARY KEY ("case_id")
);
