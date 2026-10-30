import csv
import gzip
import json
import re
import shutil
import sqlite3
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

from liquidity_forecasting import config as C
from liquidity_forecasting.pipeline import generate
from liquidity_forecasting.schema import SOURCE_TABLES, render_data_dictionary
from liquidity_forecasting.validate import validate

ROOT = Path(__file__).resolve().parents[1]


def rows(directory: Path, table: str) -> list[dict]:
    with (directory / f"{table}.csv").open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def rewrite(directory: Path, table: str, change) -> None:
    data = rows(directory, table)
    change(data)
    with (directory / f"{table}.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(data[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(data)


class StarterDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.temp.name) / "starter"
        cls.manifest = generate(cls.out, "starter")
        cls.report = validate(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def corrupted_copy(self, table: str, change) -> dict:
        copy = Path(self.temp.name) / f"corrupt-{table}"
        shutil.copytree(self.out, copy, dirs_exist_ok=True)
        rewrite(copy, table, change)
        return validate(copy)

    # --- acceptance criteria
    def test_every_integrity_check_passes(self):
        failed = {k: v["examples"] for k, v in self.report["checks"].items() if not v["passed"] and k != "reproducibility"}
        self.assertEqual(failed, {})

    def test_all_labelled_anomalies_detected_without_false_positives(self):
        detection = self.report["checks"]["ground_truth_detection"]
        self.assertEqual((detection["precision"], detection["recall"]), (1.0, 1.0))
        self.assertTrue(all(v["labelled"] >= 1 for v in detection["per_type"].values()))
        self.assertEqual(detection["controls_checked"], 2)

    def test_same_seed_is_byte_identical_and_other_seed_differs(self):
        with tempfile.TemporaryDirectory() as other:
            again = generate(Path(other) / "a", "starter")
            different = generate(Path(other) / "b", "starter", seed=C.SEED + 1)
        self.assertEqual(self.manifest["files"], again["files"])
        self.assertNotEqual(self.manifest["files"]["ledger_entries.csv"], different["files"]["ledger_entries.csv"])

    def test_identifier_conventions(self):
        patterns = {("legal_entities", "entity_id"): r"ENT-\d{6}", ("bank_accounts", "bank_account_id"): r"BANKACC-ENT-\d{6}-\d{3}",
                    ("ledger_entries", "ledger_id"): r"LED-2026-\d{8}", ("cash_flow_forecasts", "cash_flow_forecast_id"): r"CF-2026-\d{6}",
                    ("forecast_runs", "forecast_run_id"): r"FCAST-2026-\d{2}-\d{2}-\d{3}"}
        for (table, field), pattern in patterns.items():
            values = [row[field] for row in rows(self.out, table)]
            self.assertTrue(all(re.fullmatch(pattern, v) for v in values), table)
        self.assertEqual(rows(self.out, "legal_entities")[0]["entity_id"], "ENT-000001")
        self.assertEqual(rows(self.out, "ledger_entries")[0]["ledger_id"], "LED-2026-00000001")
        self.assertEqual(rows(self.out, "cash_flow_forecasts")[0]["cash_flow_forecast_id"], "CF-2026-000001")

    def test_daily_balances_chain_to_ledger_closing(self):
        ledger = defaultdict(int)
        for entry in rows(self.out, "ledger_entries"):
            ledger[entry["bank_account_id"]] += round(float(entry["cash_movement"]) * 100)
        last = {r["bank_account_id"]: r for r in rows(self.out, "daily_balances") if r["balance_date"] == C.AS_OF[:10]}
        for account in rows(self.out, "bank_accounts"):
            derived = round(float(account["opening_balance"]) * 100) + ledger[account["bank_account_id"]]
            self.assertEqual(round(float(last[account["bank_account_id"]]["closing_balance"]) * 100), derived)

    def test_scenarios_differ_only_where_assumptions_apply(self):
        items = defaultdict(dict)
        for item in rows(self.out, "forecast_run_items"):
            items[item["forecast_run_id"]][item["cash_flow_forecast_id"]] = item
        delayed_accounts = {f["bank_account_id"] for f in rows(self.out, "cash_flow_forecasts")
                            if items["FCAST-2026-10-30-002"][f["cash_flow_forecast_id"]]["adjustment"] == "RECEIPT_DELAY"}
        positions = {(p["forecast_run_id"], p["bank_account_id"], p["position_date"]): p["closing_native"] for p in rows(self.out, "cash_positions")}
        for (run, account, day), value in positions.items():
            if run == "FCAST-2026-10-30-002" and account not in delayed_accounts:
                self.assertEqual(value, positions["FCAST-2026-10-30-001", account, day])
        runs = {r["forecast_run_id"]: r for r in rows(self.out, "forecast_runs")}
        self.assertEqual(len({runs[f"FCAST-2026-10-30-00{n}"]["input_snapshot_hash"] for n in (1, 2, 3)}), 1)
        summary = {s["scenario_id"]: float(s["min_group_inr"]) for s in rows(self.out, "scenario_summary") if s["forecast_run_id"].startswith("FCAST-2026-10-30")}
        self.assertGreaterEqual(summary["SCN-BASE"], summary["SCN-DELAY"])
        self.assertGreater(summary["SCN-DELAY"], summary["SCN-STRESS"])

    def test_labelled_feed_items_are_excluded_and_controls_included(self):
        truth = {t["target_id"]: t for t in rows(self.out, "ground_truth")}
        current = {i["cash_flow_forecast_id"]: i for i in rows(self.out, "forecast_run_items") if i["forecast_run_id"] == "FCAST-2026-10-30-001"}
        for target, label in truth.items():
            if label["issue_type"] == "DUPLICATE_CASH_FLOW":
                self.assertEqual(current[target]["exclusion_reason"], "DUPLICATE_SOURCE")
            if label["issue_type"] == "POST_CUTOFF_ACTUAL":
                self.assertEqual(current[target]["exclusion_reason"], "KNOWN_AFTER_CUTOFF")
            if label["is_anomaly"] == "false":
                self.assertEqual(current[target]["decision"], "INCLUDED")

    def test_missing_fx_leaves_positions_unvalued_not_guessed(self):
        unresolved = [p for p in rows(self.out, "cash_positions") if p["valuation_status"] == "UNRESOLVED_FX"]
        self.assertTrue(unresolved)
        self.assertTrue(all(p["closing_reporting"] == "" and p["fx_rate"] == "" for p in unresolved))

    def test_variance_components_sum_to_variance(self):
        comparisons = rows(self.out, "actual_vs_forecast")
        self.assertTrue(comparisons)
        for row in comparisons:
            parts = sum(round(float(row[k]) * 100) for k in ("cutoff_lag_component", "timing_component", "unforecast_component"))
            self.assertEqual(parts, round(float(row["variance_native"]) * 100))

    def test_ground_truth_never_appears_in_inputs(self):
        for table in SOURCE_TABLES:
            header = (self.out / f"{table}.csv").read_text(encoding="utf-8").splitlines()[0]
            self.assertNotIn("issue_type", header)
            self.assertNotIn("is_anomaly", header)
        with gzip.open(self.out / "seed_data.json.gz", "rt", encoding="utf-8") as stream:
            self.assertNotIn("ground_truth", json.load(stream)["tables"])

    def test_sql_seed_loads_with_foreign_keys_enforced(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        with gzip.open(self.out / "seed_data.sql.gz", "rt", encoding="utf-8") as stream:
            connection.executescript(stream.read())
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        for table in SOURCE_TABLES:
            count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            self.assertEqual(count, len(rows(self.out, table)), table)

    # --- the validator must fail on broken data (exception paths)
    def test_validator_detects_tampered_ledger(self):
        def change(data):
            data[10]["cash_movement"] = f"{float(data[10]['cash_movement']) + 1:.2f}"
        report = self.corrupted_copy("ledger_entries", change)
        self.assertFalse(report["checks"]["daily_balance_reconciliation"]["passed"])
        self.assertFalse(report["passed"])

    def test_validator_detects_tampered_position(self):
        def change(data):
            data[0]["closing_native"] = "0.00"
        self.assertFalse(self.corrupted_copy("cash_positions", change)["checks"]["forecast_recomputation"]["passed"])

    def test_validator_detects_undocumented_scenario_change(self):
        def change(data):
            assumptions = json.loads(data[2]["assumption_json"])
            assumptions["fx_shock_pct"] = -1.0
            data[2]["assumption_json"] = json.dumps(assumptions, sort_keys=True)
        self.assertFalse(self.corrupted_copy("liquidity_scenarios", change)["checks"]["forecast_recomputation"]["passed"])

    def test_validator_detects_unlabelled_duplicate(self):
        def change(data):
            data.append({**data[5], "cash_flow_forecast_id": "CF-2026-999999", "knowledge_time": data[5]["knowledge_time"]})
        report = self.corrupted_copy("cash_flow_forecasts", change)
        self.assertLess(report["checks"]["ground_truth_detection"]["precision"], 1.0)


class ProjectConfiguration(unittest.TestCase):
    def test_full_scale_matches_brief(self):
        self.assertEqual(C.SCALES["full"], C.Scale(30, 150, 100_000, 20_000))
        self.assertEqual(len(C.CURRENCIES), 5)
        self.assertEqual(C.SEED, 20261022)

    def test_data_dictionary_is_generated_from_schema(self):
        current = (ROOT / "docs" / "data_dictionary.md").read_text(encoding="utf-8")
        self.assertEqual(current, render_data_dictionary() + "\n", "run python scripts/build_docs.py")


if __name__ == "__main__":
    unittest.main()
