# Architecture

```text
config.py ─┐                    ┌─ data/generated/*.csv, seed_data.{json,sql}.gz, schema.sql
schema.py ─┼─► pipeline.py ─────┤   ground_truth.{csv,json}, source_manifest.json, reproducibility.json
           │   1 build_sources  │
           │   2 run_forecasts  └─► validate.py (independent) ─► validation_summary.json
           └──────────────────────────┘
```

| Module | Responsibility |
|---|---|
| `config.py` | Seed, scales, time model, FX parameters, behaviour rules, scenario assumptions, run versions, anomaly counts |
| `schema.py` | One definition per table: columns, types, required flags, enums, ID patterns, PK/FK. Drives CSV column order, SQL DDL, validation and `docs/data_dictionary.md` |
| `pipeline.py` | **Stage 1** `build_sources`: FX → entities → accounts → receivables/payables → ledger (settlements first) → events → forecast feed → entity reports → labelled anomalies. **Stage 2** `run_forecasts`: reads only source rows and derives daily balances, run items, positions, entity positions, exceptions, back-test comparisons and summaries |
| `validate.py` | Does not import the pipeline. Reloads CSVs, re-derives every derived table from the source tables and documented rules, detects anomalies on its own, then scores against `ground_truth.csv` |

## Generation order

1. Parents first: `currency_rates`, `legal_entities`, `bank_accounts`, `liquidity_scenarios`.
2. Dependent records with valid parent IDs: `receivables`, `payables`, then `ledger_entries` (settlement entries carry
   the item ID), `state_change_events`, `cash_flow_forecasts`, `entity_balance_reports`.
3. A complete, valid baseline is built before anything is broken. Anomalies are then injected into specific rows and
   each one is written to the separate ground-truth list. Unrelated rows are never touched.
4. IDs are assigned after sorting by business time (creation, value date, knowledge time), so a label cannot be guessed
   from an ID's position.

## Forecast runs

Four append-only runs: base version 1 at the 2026-10-15 cutoff (back-tested), then base version 2, delayed-receipt and
stress at the 2026-10-30 cutoff. Every candidate feed item gets a `forecast_run_items` row per run, so any position can be
traced back to the items and decisions behind it. `input_snapshot_hash` fingerprints the eligible inputs, starting
balances and FX curve; runs with the same cutoff have the same hash, which shows they consumed identical inputs.

## Roles

| Actor | What they use |
|---|---|
| Customer / account holder | Origin of receivables (`customer_reference`, `customer_risk_grade`) and account activity |
| Operations analyst | `cash_positions`, `entity_positions`, `exceptions` where `owner_role = OPERATIONS_ANALYST` |
| Risk / compliance reviewer | `exceptions` where `owner_role = RISK_COMPLIANCE_REVIEWER`, `scenario_summary`, `validation_summary.json`, `ground_truth.csv` |
| System administrator | `config.py` (single place for parameters), generator/validator commands, `source_manifest.json`, `reproducibility.json` |

## Determinism

One `random.Random(seed)` drives everything. Money is integer cents. Rates are rounded to six decimals before use. The
gzip archives are written with `mtime=0`. Two runs with the same seed and code produce byte-identical files, and
`--verify-reproducibility` checks this for every output. The full dataset takes about 12 seconds to generate and about
15 seconds to validate.
