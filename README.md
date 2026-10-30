# Liquidity Forecasting

A reproducible, dependency-free pipeline that consolidates bank balances and expected cash flows for a 30-entity,
5-currency group and forecasts short-term liquidity under base, delayed-receipt and stress scenarios. It generates
synthetic source data from a fixed seed, injects labelled anomalies, derives every balance, position and variance from
source records, raises traceable exceptions, back-tests an earlier forecast version against actuals, and validates all
of it with an independent checker scored against separate ground truth.

## Quick start

Requires Python 3.11+ and only the standard library.

```bash
git clone https://github.com/Bhargava1905/liquidity-forecasting.git
cd liquidity-forecasting
export PYTHONPATH=src                       # PowerShell: $env:PYTHONPATH = "src"
python scripts/generate.py --scale full --output data/generated --verify-reproducibility
python scripts/validate.py --dataset data/generated
python -m unittest discover -s tests -v
```

- `--scale starter` (3 entities, 15 accounts, 1,000 ledger entries, 300 forecast items) uses the same generator and
  rules and runs in under a second. `--scale full` is the brief's volume (about 12 s to generate, 15 s to validate).
- `--seed` defaults to `20261022`. `--verify-reproducibility` generates a second time into a temporary folder and
  compares the SHA-256 of every file (`reproducibility.json`).
- `validate.py` exits non-zero if any check fails. `python scripts/build_docs.py` regenerates the data dictionary.
- `pytest` works too (`pyproject.toml` sets the path), but nothing needs installing.

The committed `data/generated/` folder is the full dataset produced by the commands above.

## How it works

1. **Master data first:** FX rates → 30 `LegalEntity` → 150 `BankAccount` (`BANKACC-<entity-id>-NNN`) → scenarios.
2. **Dependent records:** 10,911 receivables and 9,084 payables, whose outcomes follow documented rules. Grade B/C
   customers pay 3 or 9 days late, so 807 receivables are delayed, and 50 are large one-off payments (tax, capex,
   dividend, loan). Then 100,000 ledger entries, which include every settlement, payroll and non-forecast activity.
   Lifecycle events are append-only, and the 20,000-item forecast feed is built last.
3. **Labelled anomalies are injected** into a fully valid baseline and recorded only in `ground_truth.csv`.
4. **Forecasting** reads only source rows. Each of 4 append-only `ForecastRun` versions evaluates every feed item through
   ordered eligibility rules (cutoff, duplicate, actual, due, horizon) and writes the decision to the
   `forecast_run_items` bridge table (N:M). It then builds daily account positions from the ledger balance known at the
   cutoff, values them with the forward FX curve published at the cutoff, and totals entities against their liquidity
   buffer.
5. **Back-test:** the 2026-10-15 base forecast is compared with actual daily balances for 15 days. Each variance is
   decomposed exactly into cutoff lag, receipt timing and unforecast activity, plus an INR FX effect.
6. **Independent validation** re-derives every output from the source CSVs and scores its own detections against ground
   truth.

Full rules: [business_rules.md](docs/business_rules.md). Entities, cardinality and lifecycles: [schema.md](docs/schema.md).
Every field: [data_dictionary.md](docs/data_dictionary.md). Design: [architecture.md](docs/architecture.md).

## Results (full dataset, seed 20261022)

**Scenarios** (current runs, cutoff 2026-10-30 10:00, horizon 2026-10-31 → 2026-11-29):

| Run | Scenario | Opening group INR | Lowest group INR | Items delayed | Entities below buffer | Entity-days below buffer |
|---|---|---|---|---|---|---|
| FCAST-2026-10-30-001 | BASE | 10.75 bn | 10.14 bn (11-23) | 0 | 0 | 0 |
| FCAST-2026-10-30-002 | DELAYED_RECEIPT | 10.75 bn | 9.96 bn (11-23) | 1,374 | 0 | 0 |
| FCAST-2026-10-30-003 | STRESS | 10.31 bn | 8.75 bn (11-25) | 4,298 | 2 | 34 |

All three share one `input_snapshot_hash`, so they used identical inputs and differ only by `assumption_json`. The
stress opening is lower because of the −5% FX shock on non-INR balances.

**Back-test** of `FCAST-2026-10-15-001` (version 1 of base) against actuals, 2026-10-16 → 2026-10-30, 150 accounts:
group WAPE 1.69%, mean absolute daily error INR 188.6m, bias −181.5m. **100% of variance is explained**: the residual
is zero in integer cents. Components: cutoff lag −316.0m, receipt timing −308.7m, unforecast activity −13.5m.

**Anomaly detection** (validator vs `ground_truth.csv`): precision 1.0, recall 1.0, F1 1.0. Both boundary controls are
correctly left unflagged.

| Required edge case | Labelled cases | How it is caught | Pipeline decision |
|---|---|---|---|
| Opening + ledger ≠ closing | 3 (including a 0.01 boundary) | R1 reconciliation | Flag; forecast uses ledger-derived balance |
| FX rate missing | 2 forward rates (GBP, SGD) | Required-rate coverage per run | Position `UNRESOLVED_FX`, never guessed |
| Cash flow counted twice | 3 re-sent feed items | `(source_type, source_id)` repeat | Excluded and flagged |
| Forecast uses actual after cutoff | 2 actuals pushed into the feed | `knowledge_time` > cutoff | Excluded and flagged |
| Currency mixed without conversion | 2 entity reports | Recomputed converted total | Report rejected |
| Controls (must not flag) | same amount/date, different invoice; knowledge exactly at cutoff | — | Included |

Distributions: item amounts are log-normal (p10/p50/p90/p99 ≈ INR 0.13m / 0.43m / 1.39m / 3.95m equivalent),
amounts are scaled into each account's currency, and weekend activity is about 4%. FX spot ranges over the month are
USD 1.7%, EUR 2.5%, SGD 2.6% and GBP 7.2% (including the documented −3% shock). The full metric set is in
`validation_summary.json → metrics`.

## Validation checks (`validation_summary.json`)

| Check | What is verified |
|---|---|
| `schema_conformance` | Headers, required fields, money/date/rate formats, enums and ID patterns for all 19 tables |
| `primary_key_uniqueness` | Every PK (including composite ones) plus invoice, bill and bank references |
| `foreign_key_integrity` | All FKs, polymorphic source references, account↔entity↔currency consistency, feed rows match their source |
| `date_ordering` | Effective ranges, transaction ≤ posting ≤ available, created < expected ≤ actual, run cutoff/horizon/version order |
| `lifecycle_transitions` | Allowed state machine, status = last event, settlement evidence, outcomes follow the delay rule |
| `daily_balance_reconciliation` | Daily chain recomputed from the ledger; recorded closings reconciled |
| `fx_and_conversion` | One rate per key, SPOT coverage, entity report totals recomputed |
| `forecast_recomputation` | Every run item, position, entity total, snapshot hash and variance component re-derived exactly |
| `cutoff_safety` | No included item was known after its run's cutoff |
| `scenario_assumption_isolation` | Same inputs and parameters; every item difference is a documented delay |
| `exception_consistency` | Pipeline exceptions equal independent detections; run exception counts and statuses |
| `ground_truth_isolation` | No label columns or label values in any input |
| `volume_and_coverage` | Brief volumes, 5 currencies, delayed receipts, one-offs, FX movement, 3 scenarios, versions |
| `ground_truth_detection` | Precision, recall and F1 per issue type, and the controls |
| `reproducibility` | Two same-seed generations are byte-identical |

The 17 unit tests also confirm that the validator **fails** on tampered data: an edited ledger amount, an edited
position, an undocumented scenario change, or an unlabelled duplicate. They also load the SQL seed into SQLite with
foreign keys enforced.

## Outputs (`data/generated/`)

| Kind | Files |
|---|---|
| Source / seed | `legal_entities`, `bank_accounts`, `currency_rates`, `liquidity_scenarios`, `receivables`, `payables`, `ledger_entries`, `cash_flow_forecasts`, `state_change_events`, `entity_balance_reports` (CSV); `seed_data.json.gz`, `seed_data.sql.gz`; `schema.sql` |
| Derived | `daily_balances`, `forecast_runs`, `forecast_run_items`, `cash_positions`, `entity_positions`, `exceptions`, `actual_vs_forecast`, `scenario_summary` |
| Labels | `ground_truth.csv`, `ground_truth.json` (never read by the pipeline) |
| Evidence | `source_manifest.json` (command, seed, provenance, row counts, SHA-256), `reproducibility.json`, `validation_summary.json` |

The JSON and SQL seeds are gzipped (about 3 MB each instead of about 60 MB). Load the SQL with
`gunzip -c data/generated/seed_data.sql.gz | sqlite3 liquidity.db`.

## Repository layout

```text
src/liquidity_forecasting/  config.py  schema.py  pipeline.py  validate.py
scripts/                    generate.py  validate.py  build_docs.py
tests/test_pipeline.py
docs/                       architecture.md  business_rules.md  schema.md  data_dictionary.md  source_manifest.md
data/generated/             full dataset and evidence files
```

## Scope and provenance

This is a rule-based treasury workflow, not an ML model or a live banking integration. All data is synthetic, generated
locally, and contains no personal or external data ([source_manifest.md](docs/source_manifest.md)). The four actors map to
outputs (see [architecture.md](docs/architecture.md#roles)): exceptions carry an `owner_role` of operations analyst or
risk/compliance reviewer, and the administrator controls everything through `config.py`.
