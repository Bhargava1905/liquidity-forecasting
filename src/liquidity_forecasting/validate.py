"""Independent validation of a generated dataset.

This module deliberately does not import the generator: it reloads the CSV
files and re-derives every balance, eligibility decision, position, variance
and anomaly from the source tables using the documented rules, then compares.
ground_truth.csv is read only at the end, to score the detections.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config as C
from .schema import SOURCE_TABLES, TABLES, columns

ALLOWED_TRANSITIONS = {("", "SCHEDULED"), ("SCHEDULED", "OVERDUE"), ("SCHEDULED", "RECEIVED"), ("OVERDUE", "RECEIVED"), ("SCHEDULED", "PAID")}
ANOMALY_TYPES = ("BALANCE_RECONCILIATION_MISMATCH", "MISSING_FX_RATE", "DUPLICATE_CASH_FLOW", "POST_CUTOFF_ACTUAL", "MIXED_CURRENCY_WITHOUT_CONVERSION")
FORMATS = {"money": re.compile(r"-?\d+\.\d{2}"), "rate": re.compile(r"\d+\.\d{6}"), "int": re.compile(r"\d+"),
           "date": re.compile(r"\d{4}-\d{2}-\d{2}"), "datetime": re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"),
           "hash": re.compile(r"[0-9a-f]{64}")}


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        return list(reader.fieldnames or []), list(reader)


def cents(text: str) -> int:
    negative = text.startswith("-")
    whole, _, fraction = text.lstrip("-").partition(".")
    value = int(whole) * 100 + int((fraction + "00")[:2])
    return -value if negative else value


def roll(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def to_inr(native: int, rate: float, shock_pct: float = 0.0) -> int:
    return round(native * rate * (1 + shock_pct / 100))


def fmt(value: int) -> str:
    return f"{'-' if value < 0 else ''}{abs(value) // 100}.{abs(value) % 100:02d}"


class Report:
    def __init__(self) -> None:
        self.checks: dict[str, dict] = {}
        self.findings: list[dict] = []

    def check(self, name: str, failures: list[str], **info) -> None:
        self.checks[name] = {"passed": not failures, "failures": len(failures), "examples": failures[:5], **info}

    def find(self, issue: str, target: str, decision: str, evidence: str) -> None:
        self.findings.append({"issue_type": issue, "target_id": target, "decision": decision, "evidence": evidence})


def validate(dataset: Path) -> dict:
    dataset = Path(dataset)
    manifest = json.loads((dataset / "source_manifest.json").read_text(encoding="utf-8"))
    scale = C.SCALES[manifest["scale"]]
    headers, t = {}, {}
    for name in TABLES:
        if name != "ground_truth":
            headers[name], t[name] = read_csv(dataset / f"{name}.csv")
    r = Report()
    as_of, extract = datetime.fromisoformat(C.AS_OF), C.EXTRACT_AT
    as_of_day = as_of.date()
    start = date.fromisoformat(C.HISTORY_START)

    # 1. Schema: headers, required fields, formats, enums, identifier patterns.
    failures = []
    for name, rows in t.items():
        if headers[name] != columns(name):
            failures.append(f"{name}: header {headers[name]} != schema")
        compiled = [(f, kind, req, re.compile(rule) if kind == "id" else rule) for f, kind, req, rule, _ in TABLES[name]["fields"]]
        for row in rows:
            for field, kind, required, rule in compiled:
                value = row[field]
                if value == "":
                    if required:
                        failures.append(f"{name}.{field} blank")
                    continue
                ok = (rule.fullmatch(value) if kind == "id" else value in rule if kind == "enum" else FORMATS[kind].fullmatch(value) if kind in FORMATS else True)
                if kind == "json":
                    try:
                        json.loads(value)
                    except ValueError:
                        ok = False
                if not ok:
                    failures.append(f"{name}.{field}={value!r} violates {kind}")
    r.check("schema_conformance", failures, tables=len(t))

    # 2. Primary keys and unique business keys.
    failures = []
    for name, rows in t.items():
        keys = [tuple(row[k] for k in TABLES[name]["pk"]) for row in rows]
        failures += [f"{name} duplicate key {k}" for k, n in Counter(keys).items() if n > 1]
    for name, field in (("ledger_entries", "transaction_reference"), ("receivables", "invoice_reference"), ("payables", "bill_reference")):
        failures += [f"{name}.{field} duplicate {k}" for k, n in Counter(row[field] for row in t[name]).items() if n > 1]
    r.check("primary_key_uniqueness", failures)

    # 3. Foreign keys, polymorphic references and parent consistency.
    failures = []
    for name, rows in t.items():
        for field, kind, _, rule, _ in TABLES[name]["fields"]:
            if kind == "ref":
                parent, key = rule.split(".")
                valid = {row[key] for row in t[parent]}
                failures += [f"{name}.{field}={row[field]} missing in {parent}" for row in rows if row[field] and row[field] not in valid]
    accounts = {a["bank_account_id"]: a for a in t["bank_accounts"]}
    ledger_by_id = {e["ledger_id"]: e for e in t["ledger_entries"]}
    receivables = {x["receivable_id"]: x for x in t["receivables"]}
    payables = {x["payable_id"]: x for x in t["payables"]}
    sources = {"RECEIVABLE": receivables, "PAYABLE": payables, "ACTUAL_LEDGER": ledger_by_id}
    for account in t["bank_accounts"]:
        if not account["bank_account_id"].startswith(f"BANKACC-{account['entity_id']}-"):
            failures.append(f"{account['bank_account_id']} prefix does not match {account['entity_id']}")
    for name, currency_field in (("ledger_entries", "currency"), ("receivables", "currency"), ("payables", "currency"), ("cash_flow_forecasts", "currency"),
                                 ("cash_positions", "native_currency")):
        for row in t[name]:
            account = accounts.get(row["bank_account_id"])
            if account and (row["entity_id"] != account["entity_id"] or row[currency_field] != account["account_currency"]):
                failures.append(f"{name} row for {row['bank_account_id']} disagrees with account entity/currency")
    for flow in t["cash_flow_forecasts"]:
        source = sources[flow["source_type"]].get(flow["source_id"])
        if source is None:
            failures.append(f"{flow['cash_flow_forecast_id']} source {flow['source_id']} missing")
            continue
        if flow["source_type"] == "ACTUAL_LEDGER":
            expected = (source["bank_account_id"], fmt(abs(cents(source["cash_movement"]))), "INFLOW" if cents(source["cash_movement"]) > 0 else "OUTFLOW")
            actual = (flow["bank_account_id"], flow["amount"], flow["direction"])
        else:
            expected = (source["bank_account_id"], source["amount"], "INFLOW" if flow["source_type"] == "RECEIVABLE" else "OUTFLOW",
                        source["expected_receipt_date" if flow["source_type"] == "RECEIVABLE" else "expected_payment_date"], source["created_at"] <= flow["knowledge_time"])
            actual = (flow["bank_account_id"], flow["amount"], flow["direction"], flow["expected_date"], True)
        if expected != actual:
            failures.append(f"{flow['cash_flow_forecast_id']} does not match its source {flow['source_id']}")
    for entry in t["ledger_entries"]:
        if entry["settlement_source_id"] and entry["settlement_source_id"] not in sources.get(entry["settlement_source_type"], {}):
            failures.append(f"{entry['ledger_id']} settles unknown {entry['settlement_source_id']}")
        if bool(entry["settlement_source_id"]) != bool(entry["settlement_source_type"]):
            failures.append(f"{entry['ledger_id']} settlement type/id incomplete")
    for event in t["state_change_events"]:
        if event["object_id"] not in (receivables if event["object_type"] == "Receivable" else payables):
            failures.append(f"{event['event_id']} unknown object {event['object_id']}")
    r.check("foreign_key_integrity", failures)

    # 4. Date ordering and effective dating.
    failures = []
    entity_from = {e["entity_id"]: e["effective_from"] for e in t["legal_entities"]}
    for name in ("legal_entities", "bank_accounts", "liquidity_scenarios"):
        failures += [f"{name} effective_to before effective_from" for row in t[name] if row["effective_to"] and row["effective_to"] < row["effective_from"]]
    failures += [f"{a['bank_account_id']} opens before its entity" for a in t["bank_accounts"] if a["effective_from"] < entity_from[a["entity_id"]]]
    for e in t["ledger_entries"]:
        if not (C.HISTORY_START <= e["transaction_date"] <= e["posting_date"] <= as_of_day.isoformat() and e["available_at"][:10] == e["posting_date"] and e["available_at"] <= extract):
            failures.append(f"{e['ledger_id']} dates out of order")
    for rows, exp, act in ((t["receivables"], "expected_receipt_date", "actual_receipt_date"), (t["payables"], "expected_payment_date", "actual_payment_date")):
        for x in rows:
            if not (x["created_at"][:10] < x[exp] and (not x[act] or x[exp] <= x[act] <= as_of_day.isoformat()) and x["created_at"] <= extract):
                failures.append(f"{x.get('receivable_id', x.get('payable_id'))} dates out of order")
    failures += [f"{e['event_id']} after extract" for e in t["state_change_events"] if e["event_time"] > extract]
    failures += [f"{f['cash_flow_forecast_id']} after extract" for f in t["cash_flow_forecasts"] if f["knowledge_time"] > extract]
    for rate in t["currency_rates"]:
        if rate["value_date"] < rate["quote_date"] or (rate["rate_type"] == "SPOT") != (rate["value_date"] == rate["quote_date"]) or float(rate["rate"]) <= 0:
            failures.append(f"{rate['rate_id']} invalid quote/value dates")
    runs = {run["forecast_run_id"]: run for run in t["forecast_runs"]}
    for run in t["forecast_runs"]:
        cutoff = datetime.fromisoformat(run["data_cutoff"])
        horizon_start = cutoff.date() + timedelta(days=1)
        if not (run["created_at"] >= run["data_cutoff"] and run["horizon_start"] == horizon_start.isoformat()
                and run["horizon_end"] == (horizon_start + timedelta(days=C.HORIZON_DAYS - 1)).isoformat() and run["fx_quote_date"] == cutoff.date().isoformat()):
            failures.append(f"{run['forecast_run_id']} cutoff/horizon inconsistent")
        if run["previous_run_id"] and not (runs[run["previous_run_id"]]["data_cutoff"] < run["data_cutoff"] and runs[run["previous_run_id"]]["scenario_id"] == run["scenario_id"]):
            failures.append(f"{run['forecast_run_id']} previous version is not an earlier run of the same scenario")
        if not run["forecast_run_id"].startswith(f"FCAST-{cutoff.date().isoformat()}-"):
            failures.append(f"{run['forecast_run_id']} ID does not carry its cutoff date")
    r.check("date_ordering", failures)

    # 5. Lifecycle transitions and settlement evidence; outcomes follow the documented delay rule.
    failures = []
    events = defaultdict(list)
    for event in sorted(t["state_change_events"], key=lambda e: (e["event_time"], e["event_id"])):
        events[event["object_id"]].append(event)
    settlement = defaultdict(list)
    for entry in t["ledger_entries"]:
        if entry["settlement_source_id"]:
            settlement[entry["settlement_source_id"]].append(entry)
    ids_in_time_order = [e["event_id"] for e in sorted(t["state_change_events"], key=lambda e: (e["event_time"], e["object_id"], e["event_type"]))]
    if ids_in_time_order != sorted(ids_in_time_order):
        failures.append("event IDs are not in event_time order")
    for kind, rows, key, exp, act, done in (("RECEIVABLE", t["receivables"], "receivable_id", "expected_receipt_date", "actual_receipt_date", "RECEIVED"),
                                            ("PAYABLE", t["payables"], "payable_id", "expected_payment_date", "actual_payment_date", "PAID")):
        for x in rows:
            history, status = events[x[key]], ""
            if not history or history[0]["event_type"] != "CREATED" or history[0]["event_time"] != x["created_at"]:
                failures.append(f"{x[key]} missing CREATED event at creation time")
            for event in history:
                if event["old_status"] != status or (event["old_status"], event["new_status"]) not in ALLOWED_TRANSITIONS:
                    failures.append(f"{x[key]} illegal transition {event['old_status']}->{event['new_status']}")
                status = event["new_status"]
            if status != x["status"]:
                failures.append(f"{x[key]} status {x['status']} != last event {status}")
            entries = settlement[x[key]]
            sign = 1 if kind == "RECEIVABLE" else -1
            if x["status"] == done:
                if len(entries) != 1 or entries[0]["transaction_date"] != x[act] or cents(entries[0]["cash_movement"]) != sign * cents(x["amount"]) \
                        or entries[0]["bank_account_id"] != x["bank_account_id"]:
                    failures.append(f"{x[key]} settled without exactly one matching ledger entry")
            elif entries or x[act]:
                failures.append(f"{x[key]} unsettled but has settlement evidence")
            delay = C.RECEIPT_DELAY_DAYS[x["customer_risk_grade"]] if kind == "RECEIVABLE" else 0
            due = date.fromisoformat(x[exp])
            rule_settle = roll(due + timedelta(days=delay)) if delay else due
            expected_status = done if rule_settle <= as_of_day else ("OVERDUE" if due < as_of_day else "SCHEDULED")
            if x["status"] != expected_status or (x[act] and x[act] != rule_settle.isoformat()):
                failures.append(f"{x[key]} outcome does not follow the delay rule")
    r.check("lifecycle_transitions", failures)

    # 6. Daily balances recomputed from the ledger; recorded closings reconciled (R1, R2).
    failures = []
    day_totals = defaultdict(lambda: [0, 0, 0])
    for e in t["ledger_entries"]:
        amount = cents(e["cash_movement"])
        if amount == 0:
            failures.append(f"{e['ledger_id']} zero movement")
        bucket = day_totals[e["bank_account_id"], e["transaction_date"]]
        bucket[0 if amount > 0 else 1] += abs(amount)
        bucket[2] += 1
    balances = defaultdict(list)
    for row in t["daily_balances"]:
        balances[row["bank_account_id"]].append(row)
    closing_on = {}
    history = [start + timedelta(days=n) for n in range((as_of_day - start).days + 1)]
    for account_id, account in accounts.items():
        rows = sorted(balances[account_id], key=lambda x: x["balance_date"])
        if [x["balance_date"] for x in rows] != [d.isoformat() for d in history]:
            failures.append(f"{account_id} daily balance dates incomplete")
            continue
        running = cents(account["opening_balance"])
        for row in rows:
            inflow, outflow, count = day_totals[account_id, row["balance_date"]]
            if (cents(row["opening_balance"]), cents(row["inflows"]), cents(row["outflows"]), int(row["entry_count"])) != (running, inflow, outflow, count) \
                    or cents(row["closing_balance"]) != running + inflow - outflow:
                failures.append(f"{account_id} {row['balance_date']} daily balance does not reconcile")
            running += inflow - outflow
            closing_on[account_id, row["balance_date"]] = running
        recorded = cents(account["recorded_closing_balance"])
        if recorded != running:
            r.find("BALANCE_RECONCILIATION_MISMATCH", account_id, "FLAG_AND_USE_LEDGER_BALANCE", f"recorded {fmt(recorded)} vs derived {fmt(running)}")
    r.check("daily_balance_reconciliation", failures, account_days=len(t["daily_balances"]),
            recorded_closing_mismatches=sum(f["issue_type"] == "BALANCE_RECONCILIATION_MISMATCH" for f in r.findings))

    # 7. Duplicate and post-cutoff feed items (R4, R5).
    first_seen, earliest_cutoff = {}, min(run["data_cutoff"] for run in t["forecast_runs"])
    for flow in sorted(t["cash_flow_forecasts"], key=lambda f: (f["knowledge_time"], f["cash_flow_forecast_id"])):
        key = (flow["source_type"], flow["source_id"])
        if key in first_seen:
            r.find("DUPLICATE_CASH_FLOW", flow["cash_flow_forecast_id"], "FLAG_AND_EXCLUDE_DUPLICATE", f"repeats {first_seen[key]}")
        else:
            first_seen[key] = flow["cash_flow_forecast_id"]
        if flow["source_type"] == "ACTUAL_LEDGER" and flow["knowledge_time"] > earliest_cutoff:
            r.find("POST_CUTOFF_ACTUAL", flow["cash_flow_forecast_id"], "FLAG_AND_EXCLUDE_FROM_FORECAST", f"actual known {flow['knowledge_time']}, after a run cutoff")

    # 8. FX coverage and converted entity reports (R3).
    rates = {(x["rate_type"], x["quote_date"], x["value_date"], x["from_currency"]): x for x in t["currency_rates"]}
    failures = [f"duplicate rate {k}" for k, n in Counter((x["rate_type"], x["quote_date"], x["value_date"], x["from_currency"]) for x in t["currency_rates"]).items() if n > 1]
    held = sorted({a["account_currency"] for a in accounts.values()})
    for run in t["forecast_runs"]:
        horizon = [date.fromisoformat(run["horizon_start"]) + timedelta(days=n) for n in range(C.HORIZON_DAYS)]
        for day in horizon:
            for currency in held:
                if ("FORWARD", run["fx_quote_date"], day.isoformat(), currency) not in rates:
                    missing = f"FX-FWD-{run['fx_quote_date'].replace('-', '')}-{day:%Y%m%d}-{currency}-INR"
                    if not any(f["target_id"] == missing for f in r.findings):
                        r.find("MISSING_FX_RATE", missing, "FLAG_UNRESOLVED_CONVERSION", f"needed by run {run['forecast_run_id']}")
    for currency in held:
        for day in history:
            if ("SPOT", day.isoformat(), day.isoformat(), currency) not in rates:
                failures.append(f"SPOT {currency} {day} missing")
    spot = lambda currency, day: float(rates["SPOT", day, day, currency]["rate"])
    for report in t["entity_balance_reports"]:
        owned = [a for a in accounts.values() if a["entity_id"] == report["entity_id"]]
        day, target = report["report_date"], report["reported_currency"]
        converted = sum(round(closing_on[a["bank_account_id"], day] * spot(a["account_currency"], day) / spot(target, day)) for a in owned)
        native = sum(closing_on[a["bank_account_id"], day] for a in owned)
        reported = cents(report["reported_total"])
        if int(report["account_count"]) != len(owned):
            failures.append(f"{report['report_id']} account_count wrong")
        if reported != converted:
            if reported == native and len({a["account_currency"] for a in owned}) > 1:
                r.find("MIXED_CURRENCY_WITHOUT_CONVERSION", report["report_id"], "REJECT_UNCONVERTED_TOTAL", f"reported {fmt(reported)} = native sum; converted {fmt(converted)}")
            else:
                failures.append(f"{report['report_id']} total wrong for another reason")
    spot_moves = {c: round(100 * (max(v) / min(v) - 1), 2) for c in held if c != C.REPORTING_CURRENCY
                  for v in [[spot(c, d.isoformat()) for d in history]]}
    r.check("fx_and_conversion", failures, spot_range_pct=spot_moves)

    # 9. Re-run every forecast from sources and compare all derived outputs (reproducibility, cutoff safety).
    scenarios = {s["scenario_id"]: json.loads(s["assumption_json"]) for s in t["liquidity_scenarios"]}
    entities = {e["entity_id"]: e for e in t["legal_entities"]}
    stored_items = defaultdict(dict)
    for item in t["forecast_run_items"]:
        stored_items[item["forecast_run_id"]][item["cash_flow_forecast_id"]] = item
    stored_positions = {(p["forecast_run_id"], p["bank_account_id"], p["position_date"]): p for p in t["cash_positions"]}
    stored_entity = {(p["forecast_run_id"], p["entity_id"], p["position_date"]): p for p in t["entity_positions"]}
    flow_by_id = {f["cash_flow_forecast_id"]: f for f in t["cash_flow_forecasts"]}
    stored_comparisons = {(x["forecast_run_id"], x["bank_account_id"], x["position_date"]): x for x in t["actual_vs_forecast"]}
    failures, safety, recomputed = [], [], {}
    for run in t["forecast_runs"]:
        run_id, cutoff = run["forecast_run_id"], run["data_cutoff"]
        cutoff_day = date.fromisoformat(cutoff[:10])
        horizon = [cutoff_day + timedelta(days=n) for n in range(1, C.HORIZON_DAYS + 1)]
        assumption = scenarios[run["scenario_id"]]
        delays, shock = assumption["receipt_delay_days_by_grade"], assumption["fx_shock_pct"]
        seen, snapshot, movement, included = set(), [], defaultdict(lambda: [0, 0]), {}
        for flow in sorted(t["cash_flow_forecasts"], key=lambda f: (f["knowledge_time"], f["cash_flow_forecast_id"])):
            signed = cents(flow["amount"]) * (1 if flow["direction"] == "INFLOW" else -1)
            scheduled = date.fromisoformat(flow["expected_date"])
            scenario_date, adjustment, reason = scheduled, "NONE", ""
            if flow["knowledge_time"] > cutoff:
                reason = "KNOWN_AFTER_CUTOFF"
            elif (flow["source_type"], flow["source_id"]) in seen:
                reason = "DUPLICATE_SOURCE"
            else:
                seen.add((flow["source_type"], flow["source_id"]))
                snapshot.append(f"FLOW|{flow['cash_flow_forecast_id']}|{flow['bank_account_id']}|{signed}|{scheduled}")
                if flow["source_type"] == "ACTUAL_LEDGER":
                    reason = "ACTUAL_ALREADY_IN_BALANCE"
                elif scheduled <= cutoff_day:
                    reason = "DUE_ON_OR_BEFORE_CUTOFF"
                else:
                    late = delays[receivables[flow["source_id"]]["customer_risk_grade"]] if flow["source_type"] == "RECEIVABLE" else 0
                    if late:
                        scenario_date, adjustment = roll(scheduled + timedelta(days=late)), "RECEIPT_DELAY"
                    if scenario_date > horizon[-1]:
                        reason = "OUTSIDE_HORIZON"
            stored = stored_items[run_id].get(flow["cash_flow_forecast_id"])
            expected = ("EXCLUDED" if reason else "INCLUDED", reason, scheduled.isoformat(), scenario_date.isoformat(), adjustment, fmt(signed))
            if stored is None or tuple(stored[k] for k in ("decision", "exclusion_reason", "scheduled_date", "scenario_date", "adjustment", "signed_amount")) != expected:
                failures.append(f"{run_id} {flow['cash_flow_forecast_id']} run item differs")
            if stored and stored["decision"] == "INCLUDED" and flow["knowledge_time"] > cutoff:
                safety.append(f"{run_id} used {flow['cash_flow_forecast_id']} known after cutoff")
            if not reason:
                movement[flow["bank_account_id"], scenario_date.isoformat()][0 if signed > 0 else 1] += abs(signed)
                included[flow["source_id"]] = (flow["bank_account_id"], signed, scenario_date.isoformat())
        if len(stored_items[run_id]) != len(t["cash_flow_forecasts"]):
            failures.append(f"{run_id} bridge rows missing")
        running = {a: cents(x["opening_balance"]) for a, x in accounts.items()}
        for e in t["ledger_entries"]:
            if e["available_at"] <= cutoff:
                running[e["bank_account_id"]] += cents(e["cash_movement"])
        snapshot += [f"BAL|{k}|{v}" for k, v in running.items()]
        snapshot += [f"FX|{x['rate_id']}|{x['rate']}" for (kind, quote, value, _), x in rates.items()
                     if kind == "FORWARD" and quote == run["fx_quote_date"] and run["horizon_start"] <= value <= run["horizon_end"]]
        if hashlib.sha256("\n".join(sorted(snapshot)).encode()).hexdigest() != run["input_snapshot_hash"]:
            failures.append(f"{run_id} input snapshot hash differs")
        one_off = defaultdict(int)
        for outflow in assumption["one_off_outflows"]:
            one_off[outflow["bank_account_id"], horizon[outflow["horizon_day"] - 1].isoformat()] += cents(outflow["amount"])
        entity_totals = defaultdict(lambda: [0, 0])
        for day in horizon:
            day_text = day.isoformat()
            for account_id, account in accounts.items():
                opening = running[account_id]
                inflow, outflow = movement[account_id, day_text]
                running[account_id] = opening + inflow - outflow - one_off[account_id, day_text]
                currency = account["account_currency"]
                rate_row = rates.get(("FORWARD", run["fx_quote_date"], day_text, currency))
                applied = shock if currency != C.REPORTING_CURRENCY else 0.0
                reporting = fmt(to_inr(running[account_id], float(rate_row["rate"]), applied)) if rate_row else ""
                expected = (fmt(opening), fmt(inflow), fmt(outflow), fmt(one_off[account_id, day_text]), fmt(running[account_id]),
                            rate_row["rate"] if rate_row else "", reporting, "VALUED" if rate_row else "UNRESOLVED_FX")
                stored = stored_positions.get((run_id, account_id, day_text))
                if stored is None or tuple(stored[k] for k in ("opening_native", "inflows_native", "outflows_native", "scenario_outflow_native", "closing_native",
                                                               "fx_rate", "closing_reporting", "valuation_status")) != expected:
                    failures.append(f"{run_id} {account_id} {day_text} position differs")
                recomputed[run_id, account_id, day_text] = running[account_id]
                bucket = entity_totals[account["entity_id"], day_text]
                if rate_row:
                    bucket[0] += to_inr(running[account_id], float(rate_row["rate"]), applied)
                else:
                    bucket[1] += 1
        for (entity_id, day_text), (total, unresolved) in entity_totals.items():
            buffer = cents(entities[entity_id]["minimum_cash_buffer_inr"])
            status = "UNRESOLVED_FX" if unresolved else ("BREACH" if total < buffer else "OK")
            stored = stored_entity.get((run_id, entity_id, day_text))
            if stored is None or (stored["valued_total_inr"], stored["unresolved_accounts"], stored["buffer_status"]) != (fmt(total), str(unresolved), status):
                failures.append(f"{run_id} {entity_id} {day_text} entity position differs")
        counts = Counter(i["decision"] for i in stored_items[run_id].values())
        if (int(run["included_items"]), int(run["excluded_items"])) != (counts["INCLUDED"], counts["EXCLUDED"]):
            failures.append(f"{run_id} item counts differ")
        # 10. Back-test variance decomposition for horizon days with complete actuals (R8).
        for day in [d for d in horizon if d <= as_of_day]:
            day_text = day.isoformat()
            lag, unforecast, timing = defaultdict(int), defaultdict(int), defaultdict(int)
            for e in t["ledger_entries"]:
                if e["transaction_date"] <= cutoff[:10] and e["available_at"] > cutoff:
                    lag[e["bank_account_id"]] += cents(e["cash_movement"])
                elif cutoff[:10] < e["transaction_date"] <= day_text and e["settlement_source_id"] not in included:
                    unforecast[e["bank_account_id"]] += cents(e["cash_movement"])
            for source_id, (account_id, signed, scenario_date) in included.items():
                settled = (receivables.get(source_id) or {}).get("actual_receipt_date") or (payables.get(source_id) or {}).get("actual_payment_date") or ""
                timing[account_id] += signed * ((bool(settled) and settled <= day_text) - (scenario_date <= day_text))
            for account_id, account in accounts.items():
                key = (run_id, account_id, day_text)
                row = stored_comparisons.get(key)
                if row is None:
                    failures.append(f"{key} comparison missing")
                    continue
                forecast, actual = recomputed[key], closing_on[account_id, day_text]
                parts = (lag[account_id], timing[account_id], unforecast[account_id])
                actual_rate = float(rates["SPOT", day_text, day_text, account["account_currency"]]["rate"])
                forecast_rate = float(row["forecast_rate"])
                variance_inr = to_inr(actual, actual_rate) - to_inr(forecast, forecast_rate)
                volume = to_inr(actual - forecast, actual_rate)
                if (cents(row["forecast_native"]), cents(row["actual_native"]), cents(row["variance_native"])) != (forecast, actual, actual - forecast) \
                        or (cents(row["cutoff_lag_component"]), cents(row["timing_component"]), cents(row["unforecast_component"])) != parts \
                        or sum(parts) != actual - forecast or cents(row["variance_reporting"]) != variance_inr \
                        or cents(row["volume_effect_reporting"]) != volume or abs(cents(row["fx_effect_reporting"]) - forecast * (actual_rate - forecast_rate)) > 2:
                    failures.append(f"{key} variance not explained by components")
    r.check("forecast_recomputation", failures, runs=len(t["forecast_runs"]), positions=len(t["cash_positions"]))
    r.check("cutoff_safety", safety)

    # 11. Scenarios differ only by documented assumptions.
    failures = []
    current = [run for run in t["forecast_runs"] if run["data_cutoff"] == C.AS_OF]
    base = next(run for run in current if run["scenario_id"] == "SCN-BASE")
    for run in current:
        same = ("data_cutoff", "horizon_start", "horizon_end", "fx_quote_date", "calculation_version", "input_snapshot_hash")
        if any(run[k] != base[k] for k in same):
            failures.append(f"{run['forecast_run_id']} differs from base in a non-assumption parameter")
        delays = scenarios[run["scenario_id"]]["receipt_delay_days_by_grade"]
        for cf_id, item in stored_items[run["forecast_run_id"]].items():
            base_item = stored_items[base["forecast_run_id"]][cf_id]
            if item != {**base_item, "forecast_run_id": item["forecast_run_id"], **{k: item[k] for k in ("scenario_date", "adjustment", "decision", "exclusion_reason")}}:
                failures.append(f"{cf_id} changed a non-scenario field")
            if (item["scenario_date"], item["decision"]) != (base_item["scenario_date"], base_item["decision"]):
                flow = flow_by_id[cf_id]
                if not (flow["source_type"] == "RECEIVABLE" and delays[receivables[flow["source_id"]]["customer_risk_grade"]] > 0 and item["adjustment"] == "RECEIPT_DELAY"):
                    failures.append(f"{cf_id} differs from base without a documented delay")
    r.check("scenario_assumption_isolation", failures, scenarios_compared=len(current))

    # 12. Exceptions raised by the pipeline agree with independent detection.
    failures = []
    exception_targets = {(x["issue_type"], x["target_id"]) for x in t["exceptions"] if x["issue_type"] in ANOMALY_TYPES}
    finding_targets = {(x["issue_type"], x["target_id"]) for x in r.findings}
    failures += [f"pipeline missed {x}" for x in sorted(finding_targets - exception_targets)]
    failures += [f"pipeline over-flagged {x}" for x in sorted(exception_targets - finding_targets)]
    breaches = {(p["forecast_run_id"], p["entity_id"]) for p in t["entity_positions"] if p["buffer_status"] == "BREACH"}
    raised = {(x["forecast_run_id"], x["target_id"]) for x in t["exceptions"] if x["issue_type"] == "LIQUIDITY_BUFFER_BREACH"}
    if breaches != raised:
        failures.append("buffer breach exceptions do not match entity positions")
    for run in t["forecast_runs"]:
        count = sum(x["forecast_run_id"] == run["forecast_run_id"] for x in t["exceptions"])
        if int(run["exception_count"]) != count or run["status"] != ("COMPLETED_WITH_EXCEPTIONS" if count else "COMPLETED"):
            failures.append(f"{run['forecast_run_id']} exception count/status inconsistent")
    r.check("exception_consistency", failures, exceptions=len(t["exceptions"]), by_type=dict(Counter(x["issue_type"] for x in t["exceptions"])))

    # 13. Ground truth stays separate from inputs.
    failures = []
    label_columns = {"case_id", "is_anomaly", "expected_decision", "rule_reference"}
    for name in t:
        failures += [f"{name} exposes label column {c}" for c in label_columns & set(headers[name])]
    for name in SOURCE_TABLES:
        failures += [f"{name} contains a label value" for row in t[name] if any(v.startswith(("ANOM-", "CTRL-")) for v in row.values())]
    r.check("ground_truth_isolation", failures)

    # 14. Volumes and coverage of the required behaviours.
    expected = {"legal_entities": scale.entities, "bank_accounts": scale.accounts, "ledger_entries": scale.ledger_entries, "cash_flow_forecasts": scale.forecast_items}
    actual = {name: len(t[name]) for name in expected}
    coverage = {
        "currencies_with_accounts": len(held),
        "delayed_receivables": sum(1 for x in t["receivables"] if x["status"] == "OVERDUE" or (x["actual_receipt_date"] and x["actual_receipt_date"] > x["expected_receipt_date"])),
        "one_off_payables": sum(x["payment_category"] != "TRADE" for x in t["payables"]),
        "max_spot_move_pct": max(spot_moves.values()),
        "scenarios": sorted(s["scenario_name"] for s in t["liquidity_scenarios"]),
        "forecast_versions": len(t["forecast_runs"]),
    }
    failures = [f"{k}: {actual[k]} != {v}" for k, v in expected.items() if actual[k] != v]
    failures += [f"coverage gap: {k}" for k, v in coverage.items() if not v]
    if len(held) != len(C.CURRENCIES):
        failures.append("not every currency has an account")
    r.check("volume_and_coverage", failures, expected=expected, actual=actual, coverage=coverage)

    # 15. Score detections against the separately stored labels.
    _, truth = read_csv(dataset / "ground_truth.csv")
    labels = {(x["issue_type"], x["target_id"]) for x in truth if x["is_anomaly"] == "true"}
    controls = [x for x in truth if x["is_anomaly"] == "false"]
    tp = len(labels & finding_targets)
    precision = tp / len(finding_targets) if finding_targets else 0.0
    recall = tp / len(labels) if labels else 0.0
    per_type = {issue: {"labelled": sum(1 for x in labels if x[0] == issue), "detected": sum(1 for x in finding_targets if x[0] == issue),
                        "true_positive": sum(1 for x in labels & finding_targets if x[0] == issue)} for issue in ANOMALY_TYPES}
    base_items = stored_items[base["forecast_run_id"]]
    control_failures = [f"{x['case_id']} wrongly handled" for x in controls
                        if any(f[1] == x["target_id"] for f in finding_targets) or base_items[x["target_id"]]["decision"] != "INCLUDED"]
    r.check("ground_truth_detection", [f"missed {x}" for x in sorted(labels - finding_targets)] + [f"false positive {x}" for x in sorted(finding_targets - labels)] + control_failures,
            precision=round(precision, 4), recall=round(recall, 4), f1=round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
            per_type=per_type, controls_checked=len(controls))

    # 16. Reproducibility evidence written by the generator.
    repro_path = dataset / "reproducibility.json"
    repro = json.loads(repro_path.read_text(encoding="utf-8")) if repro_path.exists() else None
    r.check("reproducibility", [] if repro and repro["passed"] else ["run generate.py --verify-reproducibility"] if repro is None else repro["mismatched_files"],
            files_compared=repro["files_compared"] if repro else 0)

    metrics = evaluation_metrics(t)
    passed = all(check["passed"] for check in r.checks.values())
    return {"passed": passed, "dataset": dataset.name, "scale": manifest["scale"], "seed": manifest["seed"], "checks": r.checks,
            "metrics": metrics, "detected_findings": r.findings}


def evaluation_metrics(t: dict) -> dict:
    """Back-test accuracy, scenario outcomes and distribution summaries for the README and reviewers."""
    comparisons = t["actual_vs_forecast"]
    group = defaultdict(lambda: [0, 0])
    drivers = Counter(x["primary_driver"] for x in comparisons)
    components = ("cutoff_lag_component", "timing_component", "unforecast_component")
    # Rows are in five currencies, so components are valued at each row's actual rate before summing (R3).
    inr_totals = {k.replace("_component", "_inr"): round(sum(cents(x[k]) * float(x["actual_rate"]) for x in comparisons)) for k in components}
    inr_totals["fx_effect_inr"] = sum(cents(x["fx_effect_reporting"]) for x in comparisons)
    inr_totals["total_variance_inr"] = sum(cents(x["variance_reporting"]) for x in comparisons)
    for x in comparisons:
        group[x["position_date"]][0] += cents(x["forecast_reporting"])
        group[x["position_date"]][1] += cents(x["actual_reporting"])
    errors = [actual - forecast for forecast, actual in group.values()]
    absolute_actual = sum(abs(a) for _, a in group.values())
    explained = sum(abs(cents(x["variance_native"])) for x in comparisons)
    residual = sum(abs(cents(x["variance_native"]) - sum(cents(x[k]) for k in components)) for x in comparisons)
    ledger_types = Counter(x["transaction_type"] for x in t["ledger_entries"])
    weekdays = Counter(date.fromisoformat(x["transaction_date"]).weekday() >= 5 for x in t["ledger_entries"])
    amounts = sorted(abs(cents(x["amount"])) * C.FX_START[x["currency"]] / 100 for x in t["receivables"] + t["payables"])
    pick = lambda q: round(amounts[int(q * (len(amounts) - 1))]) if amounts else 0
    return {
        "backtest": {
            "run": comparisons[0]["forecast_run_id"] if comparisons else "", "days": len(group), "account_days": len(comparisons),
            "group_mae_inr": fmt(round(sum(abs(e) for e in errors) / max(len(errors), 1))),
            "group_bias_inr": fmt(round(sum(errors) / max(len(errors), 1))),
            "group_wape_pct": round(100 * sum(abs(e) for e in errors) / absolute_actual, 4) if absolute_actual else 0.0,
            "variance_explained_pct": round(100 * (1 - residual / explained), 4) if explained else 100.0,
            "component_totals_inr": {k: fmt(v) for k, v in inr_totals.items()}, "primary_drivers": dict(drivers),
        },
        "scenarios": {x["forecast_run_id"]: {k: v for k, v in x.items() if k != "forecast_run_id"} for x in t["scenario_summary"]},
        "distributions": {
            "ledger_type_mix": dict(ledger_types), "weekend_share_pct": round(100 * weekdays[True] / max(len(t["ledger_entries"]), 1), 2),
            "receivable_status": dict(Counter(x["status"] for x in t["receivables"])), "payable_status": dict(Counter(x["status"] for x in t["payables"])),
            "risk_grades": dict(Counter(x["customer_risk_grade"] for x in t["receivables"])),
            "item_amount_inr_p10_p50_p90_p99": [pick(0.10), pick(0.50), pick(0.90), pick(0.99)],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a generated liquidity forecasting dataset.")
    parser.add_argument("--dataset", default="data/generated", type=Path)
    args = parser.parse_args()
    report = validate(args.dataset)
    (args.dataset / "validation_summary.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"passed": report["passed"], "checks": {k: v["passed"] for k, v in report["checks"].items()},
                      "detection": {k: report["checks"]["ground_truth_detection"][k] for k in ("precision", "recall", "f1")},
                      "backtest": report["metrics"]["backtest"]}, indent=2))
    if not report["passed"]:
        raise SystemExit(1)
