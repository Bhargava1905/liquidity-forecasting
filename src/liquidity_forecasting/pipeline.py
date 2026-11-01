"""Generate deterministic synthetic source data and liquidity forecast outputs.

Stage 1, `build_sources`, creates master data first, then dependent records,
then injects the documented anomalies and records them in a separate ground
truth list. Stage 2, `run_forecasts`, reads only those source rows: every
balance, position, variance and exception is derived from them. Money is held
as integer cents and exported as fixed two-decimal strings.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import random
import tempfile
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import config as C
from .schema import DERIVED_TABLES, SOURCE_TABLES, TABLES, columns, sql_ddl


# ---------------------------------------------------------------- helpers

def money(value: int) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}{value // 100}.{value % 100:02d}"


def cents(text: str) -> int:
    negative = text.startswith("-")
    whole, _, fraction = text.lstrip("-").partition(".")
    value = int(whole) * 100 + int((fraction + "00")[:2])
    return -value if negative else value


def stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def roll(day: date) -> date:
    """Following business-day convention: Saturday and Sunday move to Monday."""
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def rate_text(value: float) -> str:
    return f"{value:.6f}"


def to_inr(native: int, rate: float, shock_pct: float = 0.0) -> int:
    return round(native * rate * (1 + shock_pct / 100))


def rate_id(rate_type: str, quote: date, value: date, currency: str) -> str:
    return f"FX-{'SPOT' if rate_type == 'SPOT' else 'FWD'}-{quote:%Y%m%d}-{value:%Y%m%d}-{currency}-{C.REPORTING_CURRENCY}"


def snapshot_hash(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()


# ---------------------------------------------------------------- stage 1: sources

def build_sources(scale_name: str = "starter", seed: int = C.SEED) -> tuple[dict[str, list[dict]], list[dict]]:
    scale = C.SCALES[scale_name]
    rng = random.Random(seed)
    start = date.fromisoformat(C.HISTORY_START)
    as_of = datetime.fromisoformat(C.AS_OF)
    as_of_day = as_of.date()
    days = [start + timedelta(days=n) for n in range((as_of_day - start).days + 1)]
    business_weight = [1.0 if day.weekday() < 5 else 0.15 for day in days]
    t: dict[str, list[dict]] = {name: [] for name in SOURCE_TABLES}
    truth: list[dict] = []

    # FX: SPOT random walk over history, then a FORWARD curve published on each run's cutoff date.
    spot: dict[tuple[str, date], float] = {}
    level = dict(C.FX_START)
    for day in days:
        for currency in C.CURRENCIES:
            if day != start and currency != C.REPORTING_CURRENCY:
                level[currency] *= math.exp(rng.gauss(0, C.FX_DAILY_VOL[currency])) * (1 + C.FX_EVENTS.get((currency, day.isoformat()), 0))
            spot[currency, day] = float(rate_text(level[currency]))
            t["currency_rates"].append(fx_row("SPOT", day, day, currency, spot[currency, day]))
    quote_days = sorted({datetime.fromisoformat(run["data_cutoff"]).date() for run in C.FORECAST_RUNS})
    for quote in quote_days:
        for offset in range(1, C.HORIZON_DAYS + 1):
            for currency in C.CURRENCIES:
                forward = float(rate_text(spot[currency, quote] * (1 + C.FX_FORWARD_CARRY[currency] * offset / 365)))
                t["currency_rates"].append(fx_row("FORWARD", quote, quote + timedelta(days=offset), currency, forward))

    # Master data: entities, then their accounts.
    countries = (("IN", "INR"), ("US", "USD"), ("GB", "GBP"), ("DE", "EUR"), ("SG", "SGD"))
    for index in range(1, scale.entities + 1):
        country, currency = countries[(index - 1) % len(countries)]
        t["legal_entities"].append({
            "entity_id": f"ENT-{index:06d}", "legal_name": f"Aster {country} Operations {index:02d} Ltd", "country": country,
            "functional_currency": currency, "minimum_cash_buffer_inr": "0.00", "status": "ACTIVE",
            "effective_from": date(rng.randrange(2012, 2026), rng.randint(1, 12), 1).isoformat(), "effective_to": "",
        })
    banks = ("Meridian Commercial Bank", "Harbourline Bank", "Northgate Trust", "Crescent Federal Bank")
    purposes = ("OPERATING", "COLLECTION", "PAYROLL", "TREASURY", "COLLECTION")
    per_entity, remainder = divmod(scale.accounts, scale.entities)
    activity: list[float] = []
    for number, entity in enumerate(t["legal_entities"], start=1):
        entity_from = date.fromisoformat(entity["effective_from"])
        for ordinal in range(1, per_entity + (1 if number <= remainder else 0) + 1):
            others = [c for c in C.CURRENCIES if c != entity["functional_currency"]]
            currency = entity["functional_currency"] if ordinal % 2 == 1 else others[(number + ordinal // 2 - 1) % len(others)]
            purpose = purposes[(ordinal - 1) % len(purposes)]
            opening = round(rng.lognormvariate(math.log(60_000_000), 0.6) / C.FX_START[currency] * 100)
            activity.append(rng.lognormvariate(0, 0.5))
            t["bank_accounts"].append({
                "bank_account_id": f"BANKACC-{entity['entity_id']}-{ordinal:03d}", "entity_id": entity["entity_id"],
                "bank_name": banks[rng.randrange(len(banks))], "account_name": f"{entity['country']}{number:02d} {currency} {purpose.title()} {ordinal}",
                "account_currency": currency, "account_purpose": purpose, "opening_balance": money(opening),
                "opening_balance_date": start.isoformat(), "recorded_closing_balance": "0.00", "closing_balance_date": as_of_day.isoformat(),
                "status": "ACTIVE", "effective_from": (entity_from + timedelta(days=rng.randrange((date(2026, 9, 1) - entity_from).days))).isoformat(),
                "effective_to": "",
            })
    accounts = t["bank_accounts"]
    for entity in t["legal_entities"]:
        opening_inr = sum(cents(a["opening_balance"]) * C.FX_START[a["account_currency"]] for a in accounts if a["entity_id"] == entity["entity_id"])
        entity["minimum_cash_buffer_inr"] = money(int(opening_inr * C.BUFFER_RATIO // 10_000_000) * 10_000_000)

    t["liquidity_scenarios"] = [{
        "scenario_id": s["scenario_id"], "scenario_name": s["scenario_name"], "description": s["description"],
        "assumption_json": json.dumps(s["assumptions"], sort_keys=True), "effective_from": "2026-10-01", "effective_to": "", "status": "ACTIVE",
    } for s in C.SCENARIOS]

    # Scheduled receivables and payables with outcomes derived from documented rules.
    customers = max(20, scale.forecast_items // 25)
    grades = rng.choices(list(C.RISK_GRADE_WEIGHTS), weights=list(C.RISK_GRADE_WEIGHTS.values()), k=customers)
    suppliers = max(20, scale.forecast_items // 30)
    items = []
    for sequence in range(scale.forecast_items - C.INJECTED_FLOWS):
        receivable = rng.random() < C.RECEIVABLE_SHARE
        account = rng.choices(accounts, weights=activity)[0]
        created_day = rng.choices(days, weights=business_weight)[0]
        created = datetime.combine(created_day, time(rng.randint(8, 18), rng.randrange(60)))
        if receivable:
            customer = rng.randrange(customers)
            term, category = rng.choice((7, 14, 14, 21, 30, 30, 45, 60)), "TRADE"
            inr = rng.lognormvariate(math.log(450_000), 0.9)
            counterparty, grade = f"CUST-{customer + 1:05d}", grades[customer]
        else:
            term = rng.choice((7, 14, 21, 30, 30, 45))
            one_off = rng.random() < C.ONE_OFF_SHARE
            category = rng.choice(C.ONE_OFF_CATEGORIES) if one_off else "TRADE"
            inr = rng.lognormvariate(math.log(35_000_000), 0.35) if one_off else rng.lognormvariate(math.log(400_000), 0.9)
            counterparty, grade = f"SUP-{rng.randrange(suppliers) + 1:05d}", ""
        items.append({"kind": "RECEIVABLE" if receivable else "PAYABLE", "account": account, "created": created,
                      "expected": roll(created_day + timedelta(days=term)), "amount": max(100, round(inr / C.FX_START[account["account_currency"]] * 100)),
                      "counterparty": counterparty, "grade": grade, "category": category, "sequence": sequence})

    # Guarantee the large one-off payments exist at every scale.
    trade = [i for i in items if i["kind"] == "PAYABLE" and i["category"] == "TRADE"]
    for item in rng.sample(trade, max(0, C.ONE_OFF_MINIMUM - sum(i["category"] != "TRADE" for i in items if i["kind"] == "PAYABLE"))):
        item["category"] = rng.choice(C.ONE_OFF_CATEGORIES)
        item["amount"] = round(rng.lognormvariate(math.log(35_000_000), 0.35) / C.FX_START[item["account"]["account_currency"]] * 100)

    # Boundary controls (labelled, must NOT be flagged): same amount/date/customer but a different invoice, and
    # knowledge exactly at the cutoff.
    open_receivables = [i for i in items if i["kind"] == "RECEIVABLE" and as_of_day < i["expected"] <= as_of_day + timedelta(days=C.HORIZON_DAYS - 7)
                        and i["created"] <= as_of - timedelta(days=1)]
    twin_a, twin_b, at_cutoff = rng.sample(open_receivables, 3)
    for key in ("account", "expected", "amount", "counterparty", "grade"):
        twin_b[key] = twin_a[key]
    at_cutoff["created"] = as_of

    items.sort(key=lambda i: (i["created"], i["kind"], i["sequence"]))
    counters = {"RECEIVABLE": 0, "PAYABLE": 0}
    for item in items:
        counters[item["kind"]] += 1
        number = counters[item["kind"]]
        item["id"] = f"{'REC' if item['kind'] == 'RECEIVABLE' else 'PAY'}-2026-{number:06d}"
        item["reference"] = f"{'INV' if item['kind'] == 'RECEIVABLE' else 'BILL'}-2026-{number:06d}"
        delay = C.RECEIPT_DELAY_DAYS[item["grade"]] if item["kind"] == "RECEIVABLE" else 0
        item["settle"] = roll(item["expected"] + timedelta(days=delay))
        item["settled"] = item["settle"] <= as_of_day

    # Ledger: settlements of scheduled items, monthly payroll, then non-forecast operational activity.
    raw = []
    for item in items:
        if item["settled"]:
            ledger_type = "CUSTOMER_RECEIPT" if item["kind"] == "RECEIVABLE" else ("SUPPLIER_PAYMENT" if item["category"] == "TRADE" else "ONE_OFF_PAYMENT")
            raw.append({"account": item["account"], "day": item["settle"], "amount": item["amount"] * (1 if item["kind"] == "RECEIVABLE" else -1),
                        "type": ledger_type, "source_type": item["kind"], "item": item})
    for entity in t["legal_entities"]:
        owned = [a for a in accounts if a["entity_id"] == entity["entity_id"]]
        payroll = next((a for a in owned if a["account_purpose"] == "PAYROLL"), owned[0])
        raw.append({"account": payroll, "day": date.fromisoformat(C.PAYROLL_DAY), "type": "PAYROLL", "source_type": "", "item": None,
                    "amount": -round(rng.lognormvariate(math.log(12_000_000), 0.3) / C.FX_START[payroll["account_currency"]] * 100)})
    operational = scale.ledger_entries - len(raw)
    if operational < 0:
        raise ValueError("Scale has fewer ledger entries than settlements")
    weekday_days = [d for d in days if d.weekday() < 5]
    for _ in range(operational):
        name, sign, _, median, sigma, weekends = rng.choices(C.OPERATIONAL_TYPES, weights=[x[2] for x in C.OPERATIONAL_TYPES])[0]
        account = rng.choices(accounts, weights=activity)[0]
        day = rng.choices(days, weights=[1.0 if d.weekday() < 5 else 0.3 for d in days])[0] if weekends else rng.choice(weekday_days)
        raw.append({"account": account, "day": day, "type": name, "source_type": "", "item": None,
                    "amount": sign * max(1, round(rng.lognormvariate(math.log(median), sigma) / C.FX_START[account["account_currency"]] * 100))})
    for entry in raw:
        lag = 1 if rng.random() < 0.25 else 0
        entry["posting"] = min(roll(entry["day"] + timedelta(days=lag)), as_of_day) if lag else entry["day"]
        entry["available"] = datetime.combine(entry["posting"], time(rng.randint(6, 18), rng.randrange(60)))
    raw.sort(key=lambda e: (e["day"], e["available"], e["account"]["bank_account_id"], e["amount"]))
    closing = {a["bank_account_id"]: cents(a["opening_balance"]) for a in accounts}
    for number, entry in enumerate(raw, start=1):
        account, item = entry["account"], entry["item"]
        entry["id"] = f"LED-2026-{number:08d}"
        closing[account["bank_account_id"]] += entry["amount"]
        if item:
            item["settlement_entry"] = entry
        t["ledger_entries"].append({
            "ledger_id": entry["id"], "bank_account_id": account["bank_account_id"], "entity_id": account["entity_id"],
            "transaction_date": entry["day"].isoformat(), "posting_date": entry["posting"].isoformat(), "available_at": stamp(entry["available"]),
            "transaction_type": entry["type"], "cash_movement": money(entry["amount"]), "currency": account["account_currency"],
            "transaction_reference": f"TXN-2026-{number:08d}", "settlement_source_type": entry["source_type"],
            "settlement_source_id": item["id"] if item else "", "record_status": "POSTED",
        })
    for account in accounts:
        account["recorded_closing_balance"] = money(closing[account["bank_account_id"]])

    # Source rows, lifecycle events and forecast-feed items.
    events = []
    for item in items:
        receivable = item["kind"] == "RECEIVABLE"
        status = ("RECEIVED" if receivable else "PAID") if item["settled"] else ("OVERDUE" if item["expected"] < as_of_day else "SCHEDULED")
        common = {"entity_id": item["account"]["entity_id"], "bank_account_id": item["account"]["bank_account_id"]}
        if receivable:
            t["receivables"].append({"receivable_id": item["id"], **common, "customer_reference": item["counterparty"],
                                     "customer_risk_grade": item["grade"], "invoice_reference": item["reference"], "amount": money(item["amount"]),
                                     "currency": item["account"]["account_currency"], "created_at": stamp(item["created"]),
                                     "expected_receipt_date": item["expected"].isoformat(),
                                     "actual_receipt_date": item["settle"].isoformat() if item["settled"] else "", "status": status})
        else:
            t["payables"].append({"payable_id": item["id"], **common, "supplier_reference": item["counterparty"],
                                  "payment_category": item["category"], "bill_reference": item["reference"], "amount": money(item["amount"]),
                                  "currency": item["account"]["account_currency"], "created_at": stamp(item["created"]),
                                  "expected_payment_date": item["expected"].isoformat(),
                                  "actual_payment_date": item["settle"].isoformat() if item["settled"] else "", "status": status})
        object_type = "Receivable" if receivable else "Payable"
        events.append((item["created"], object_type, item["id"], "CREATED", "", "SCHEDULED", f"{item['reference']} scheduled for {item['expected']}"))
        current = "SCHEDULED"
        if item["settle"] > item["expected"] and item["expected"] < as_of_day:
            events.append((datetime.combine(item["expected"] + timedelta(days=1), time()), object_type, item["id"], "BECAME_OVERDUE",
                           current, "OVERDUE", f"Not received by {item['expected']} (grade {item['grade']})"))
            current = "OVERDUE"
        if item["settled"]:
            entry = item["settlement_entry"]
            events.append((entry["available"], object_type, item["id"], "SETTLED", current, status, f"Settled by {entry['id']}"))
    events.sort(key=lambda e: (e[0], e[2], e[3]))
    for number, (moment, object_type, object_id, event_type, old, new, details) in enumerate(events, start=1):
        t["state_change_events"].append({"event_id": f"EVT-2026-{number:08d}", "object_type": object_type, "object_id": object_id,
                                         "event_type": event_type, "old_status": old, "new_status": new, "event_time": stamp(moment), "details": details})

    feed = []
    for item in items:
        feed.append({"source_type": item["kind"], "source_id": item["id"], "entity_id": item["account"]["entity_id"],
                     "bank_account_id": item["account"]["bank_account_id"], "direction": "INFLOW" if item["kind"] == "RECEIVABLE" else "OUTFLOW",
                     "amount": money(item["amount"]), "currency": item["account"]["account_currency"], "expected_date": item["expected"].isoformat(),
                     "knowledge_time": stamp(item["created"]), "source_reference": item["reference"], "_item": item})

    # Anomaly 3: the feed re-sends an eligible item two hours later under a new forecast ID.
    duplicate_pool = [f for f in feed if as_of_day < f["_item"]["expected"] <= as_of_day + timedelta(days=C.HORIZON_DAYS)
                      and f["_item"]["created"] <= as_of - timedelta(days=1) and f["_item"] not in (twin_a, twin_b, at_cutoff)]
    for original in rng.sample(duplicate_pool, C.DUPLICATE_FLOWS):
        copy = {**original, "knowledge_time": stamp(original["_item"]["created"] + timedelta(hours=2)), "_duplicate_of": original}
        feed.append(copy)
    # Anomaly 4: actual bank entries visible only after the cutoff are pushed into the forecast feed.
    late_entries = [e for e in raw if e["day"] == as_of_day and e["available"] > as_of and not e["item"]]
    for entry in rng.sample(late_entries, C.POST_CUTOFF_ACTUALS):
        account = entry["account"]
        feed.append({"source_type": "ACTUAL_LEDGER", "source_id": entry["id"], "entity_id": account["entity_id"],
                     "bank_account_id": account["bank_account_id"], "direction": "INFLOW" if entry["amount"] > 0 else "OUTFLOW",
                     "amount": money(abs(entry["amount"])), "currency": account["account_currency"],
                     "expected_date": roll(as_of_day + timedelta(days=1)).isoformat(),
                     "knowledge_time": stamp(entry["available"] + timedelta(minutes=20)),
                     "source_reference": f"TXN-2026-{int(entry['id'][-8:]):08d}", "_late": entry})
    feed.sort(key=lambda f: (f["knowledge_time"], f["source_type"], f["source_id"], "_duplicate_of" in f))
    for number, flow in enumerate(feed, start=1):
        flow["cash_flow_forecast_id"] = f"CF-2026-{number:06d}"
    t["cash_flow_forecasts"] = [{name: flow[name] for name in columns("cash_flow_forecasts")} for flow in feed]

    # Entity cash reports for the previous business day, converted at SPOT through INR.
    report_day = as_of_day - timedelta(days=1)
    while report_day.weekday() >= 5:
        report_day -= timedelta(days=1)
    report_balance = {a["bank_account_id"]: cents(a["opening_balance"]) for a in accounts}
    for entry in raw:
        if entry["day"] <= report_day:
            report_balance[entry["account"]["bank_account_id"]] += entry["amount"]
    raw_native_total = {}
    for number, entity in enumerate(t["legal_entities"], start=1):
        owned = [a for a in accounts if a["entity_id"] == entity["entity_id"]]
        functional = entity["functional_currency"]
        total = sum(round(report_balance[a["bank_account_id"]] * spot[a["account_currency"], report_day] / spot[functional, report_day]) for a in owned)
        raw_native_total[entity["entity_id"]] = sum(report_balance[a["bank_account_id"]] for a in owned)
        t["entity_balance_reports"].append({"report_id": f"EBR-2026-{number:06d}", "entity_id": entity["entity_id"], "report_date": report_day.isoformat(),
                                            "reported_currency": functional, "reported_total": money(total), "account_count": str(len(owned)),
                                            "submitted_at": f"{as_of_day.isoformat()}T09:30:00"})

    # ---- labelled anomaly injection (baseline above is fully valid)
    case = 0

    def label(issue: str, target_type: str, target_id: str, decision: str, rule: str, notes: str, anomaly: bool = True) -> None:
        nonlocal case
        case += 1
        truth.append({"case_id": f"{'ANOM' if anomaly else 'CTRL'}-{case:03d}", "issue_type": issue, "is_anomaly": "true" if anomaly else "false",
                      "target_type": target_type, "target_id": target_id, "expected_decision": decision, "rule_reference": rule, "notes": notes})

    # Anomaly 1: recorded bank closing differs from opening + ledger.
    for account_index, delta in zip(sorted(rng.sample(range(len(accounts)), len(C.RECONCILIATION_DELTAS))), C.RECONCILIATION_DELTAS):
        account = accounts[account_index]
        account["recorded_closing_balance"] = money(cents(account["recorded_closing_balance"]) + delta)
        label("BALANCE_RECONCILIATION_MISMATCH", "BankAccount", account["bank_account_id"], "FLAG_AND_USE_LEDGER_BALANCE", "R1",
              f"Recorded closing changed by {money(delta)} {account['account_currency']} after the ledger was generated.")
    # Anomaly 2: forward rates missing on the current curve for currencies that hold accounts.
    held = {a["account_currency"] for a in accounts}
    for currency, horizon_day in C.MISSING_FX:
        if currency not in held:
            continue
        missing = rate_id("FORWARD", as_of_day, as_of_day + timedelta(days=horizon_day), currency)
        t["currency_rates"] = [r for r in t["currency_rates"] if r["rate_id"] != missing]
        label("MISSING_FX_RATE", "CurrencyRate", missing, "FLAG_UNRESOLVED_CONVERSION", "R3",
              f"{currency} forward for horizon day {horizon_day} removed from the {as_of_day} curve.")
    for flow in feed:
        if "_duplicate_of" in flow:
            label("DUPLICATE_CASH_FLOW", "CashFlowForecast", flow["cash_flow_forecast_id"], "FLAG_AND_EXCLUDE_DUPLICATE", "R5",
                  f"Re-sent copy of {flow['_duplicate_of']['cash_flow_forecast_id']} ({flow['source_id']}) two hours later.")
        if "_late" in flow:
            label("POST_CUTOFF_ACTUAL", "CashFlowForecast", flow["cash_flow_forecast_id"], "FLAG_AND_EXCLUDE_FROM_FORECAST", "R4",
                  f"Copies actual {flow['source_id']} that became available at {stamp(flow['_late']['available'])}, after the {C.AS_OF} cutoff.")
    # Anomaly 5: an entity reports native balances added across currencies without conversion.
    multi = [e for e in t["legal_entities"] if len({a["account_currency"] for a in accounts if a["entity_id"] == e["entity_id"]}) > 1]
    for entity in sorted(rng.sample(multi, C.MIXED_CURRENCY_REPORTS), key=lambda e: e["entity_id"]):
        report = next(r for r in t["entity_balance_reports"] if r["entity_id"] == entity["entity_id"])
        report["reported_total"] = money(raw_native_total[entity["entity_id"]])
        label("MIXED_CURRENCY_WITHOUT_CONVERSION", "EntityBalanceReport", report["report_id"], "REJECT_UNCONVERTED_TOTAL", "R3",
              "Reported total is the plain sum of account balances in different currencies.")
    by_item = {id(f["_item"]): f for f in feed if "_item" in f and "_duplicate_of" not in f}
    label("NOT_DUPLICATE_SAME_AMOUNT", "CashFlowForecast", by_item[id(twin_b)]["cash_flow_forecast_id"], "INCLUDE", "R5",
          f"Same customer, account, amount and date as {by_item[id(twin_a)]['cash_flow_forecast_id']} but a different invoice.", anomaly=False)
    label("KNOWLEDGE_AT_CUTOFF", "CashFlowForecast", by_item[id(at_cutoff)]["cash_flow_forecast_id"], "INCLUDE", "R4",
          "knowledge_time equals the cutoff exactly; the rule is inclusive.", anomaly=False)
    return t, truth


def fx_row(rate_type: str, quote: date, value: date, currency: str, rate: float) -> dict:
    return {"rate_id": rate_id(rate_type, quote, value, currency), "rate_type": rate_type, "from_currency": currency,
            "to_currency": C.REPORTING_CURRENCY, "quote_date": quote.isoformat(), "value_date": value.isoformat(),
            "rate": rate_text(rate), "source": "SYNTHETIC", "status": "ACTIVE"}


# ---------------------------------------------------------------- stage 2: forecasts

def run_forecasts(src: dict[str, list[dict]]) -> dict[str, list[dict]]:
    accounts = src["bank_accounts"]
    account_by_id = {a["bank_account_id"]: a for a in accounts}
    entities = {e["entity_id"]: e for e in src["legal_entities"]}
    as_of = datetime.fromisoformat(C.AS_OF)
    as_of_day = as_of.date()
    start = date.fromisoformat(C.HISTORY_START)
    rates = {(r["rate_type"], r["quote_date"], r["value_date"], r["from_currency"]): float(r["rate"]) for r in src["currency_rates"]}
    ledger = [(e["bank_account_id"], date.fromisoformat(e["transaction_date"]), datetime.fromisoformat(e["available_at"]),
               cents(e["cash_movement"]), e["settlement_source_id"]) for e in src["ledger_entries"]]
    grade = {r["receivable_id"]: r["customer_risk_grade"] for r in src["receivables"]}
    actual_date = {r["receivable_id"]: r["actual_receipt_date"] for r in src["receivables"]}
    actual_date.update({p["payable_id"]: p["actual_payment_date"] for p in src["payables"]})
    scenarios = {s["scenario_id"]: s for s in src["liquidity_scenarios"]}
    out: dict[str, list[dict]] = {name: [] for name in DERIVED_TABLES}
    exceptions: list[dict] = []

    def raise_exception(run_id, issue, severity, target_type, target_id, decision, owner, detected, details):
        exceptions.append({"exception_id": "", "forecast_run_id": run_id, "issue_type": issue, "severity": severity, "target_type": target_type,
                           "target_id": target_id, "decision": decision, "owner_role": owner, "detected_at": detected, "details": details})

    # Historical daily balances and the ledger reconciliation control (R1, R2).
    by_day = defaultdict(lambda: [0, 0, 0])
    for account_id, day, _, amount, _ in ledger:
        bucket = by_day[account_id, day]
        bucket[0 if amount > 0 else 1] += abs(amount)
        bucket[2] += 1
    history_days = [start + timedelta(days=n) for n in range((as_of_day - start).days + 1)]
    actual_closing = {}
    for account in accounts:
        balance = cents(account["opening_balance"])
        for day in history_days:
            inflow, outflow, count = by_day[account["bank_account_id"], day]
            out["daily_balances"].append({"bank_account_id": account["bank_account_id"], "balance_date": day.isoformat(),
                                          "currency": account["account_currency"], "opening_balance": money(balance), "inflows": money(inflow),
                                          "outflows": money(outflow), "closing_balance": money(balance + inflow - outflow), "entry_count": str(count)})
            balance += inflow - outflow
            actual_closing[account["bank_account_id"], day] = balance
        recorded = cents(account["recorded_closing_balance"])
        if recorded != balance:
            raise_exception("", "BALANCE_RECONCILIATION_MISMATCH", "HIGH", "BankAccount", account["bank_account_id"], "FLAG_AND_USE_LEDGER_BALANCE",
                            "OPERATIONS_ANALYST", C.AS_OF, f"Recorded {money(recorded)} vs opening + ledger {money(balance)}; difference {money(recorded - balance)}.")

    # Entity reports must be converted totals (R3).
    for report in src["entity_balance_reports"]:
        owned = [a for a in accounts if a["entity_id"] == report["entity_id"]]
        day = date.fromisoformat(report["report_date"])
        target = report["reported_currency"]
        spot_of = lambda ccy: rates["SPOT", report["report_date"], report["report_date"], ccy]
        converted = sum(round(actual_closing[a["bank_account_id"], day] * spot_of(a["account_currency"]) / spot_of(target)) for a in owned)
        native = sum(actual_closing[a["bank_account_id"], day] for a in owned)
        reported = cents(report["reported_total"])
        if reported != converted and len({a["account_currency"] for a in owned}) > 1 and reported == native:
            raise_exception("", "MIXED_CURRENCY_WITHOUT_CONVERSION", "HIGH", "EntityBalanceReport", report["report_id"], "REJECT_UNCONVERTED_TOTAL",
                            "RISK_COMPLIANCE_REVIEWER", C.AS_OF, f"Reported {money(reported)} {target} equals the unconverted native sum; converted total is {money(converted)}.")

    flows = src["cash_flow_forecasts"]  # already in knowledge_time order
    for run in C.FORECAST_RUNS:
        run_id, cutoff = run["forecast_run_id"], datetime.fromisoformat(run["data_cutoff"])
        cutoff_day = cutoff.date()
        horizon = [cutoff_day + timedelta(days=n) for n in range(1, C.HORIZON_DAYS + 1)]
        created = stamp(cutoff + timedelta(minutes=30))
        assumptions = json.loads(scenarios[run["scenario_id"]]["assumption_json"])
        delays, shock = assumptions["receipt_delay_days_by_grade"], assumptions["fx_shock_pct"]
        first_exception = len(exceptions)

        # Eligibility rules (R4, R5, R6) per candidate item -> bridge rows.
        seen, items, snapshot = set(), [], []
        movements = defaultdict(lambda: [0, 0])
        delayed = 0
        for flow in flows:
            signed = cents(flow["amount"]) * (1 if flow["direction"] == "INFLOW" else -1)
            scheduled = date.fromisoformat(flow["expected_date"])
            scenario_date, adjustment, reason = scheduled, "NONE", ""
            if datetime.fromisoformat(flow["knowledge_time"]) > cutoff:
                reason = "KNOWN_AFTER_CUTOFF"
                if flow["source_type"] == "ACTUAL_LEDGER":
                    raise_exception(run_id, "POST_CUTOFF_ACTUAL", "HIGH", "CashFlowForecast", flow["cash_flow_forecast_id"], "FLAG_AND_EXCLUDE_FROM_FORECAST",
                                    "RISK_COMPLIANCE_REVIEWER", created, f"Actual {flow['source_id']} entered the feed at {flow['knowledge_time']}, after cutoff {run['data_cutoff']}.")
            elif (flow["source_type"], flow["source_id"]) in seen:
                reason = "DUPLICATE_SOURCE"
                raise_exception(run_id, "DUPLICATE_CASH_FLOW", "MEDIUM", "CashFlowForecast", flow["cash_flow_forecast_id"], "FLAG_AND_EXCLUDE_DUPLICATE",
                                "OPERATIONS_ANALYST", created, f"{flow['source_type']} {flow['source_id']} already supplied by an earlier feed item.")
            else:
                seen.add((flow["source_type"], flow["source_id"]))
                snapshot.append(f"FLOW|{flow['cash_flow_forecast_id']}|{flow['bank_account_id']}|{signed}|{scheduled}")
                if flow["source_type"] == "ACTUAL_LEDGER":
                    reason = "ACTUAL_ALREADY_IN_BALANCE"
                elif scheduled <= cutoff_day:
                    reason = "DUE_ON_OR_BEFORE_CUTOFF"
                else:
                    days_late = delays[grade[flow["source_id"]]] if flow["source_type"] == "RECEIVABLE" else 0
                    if days_late:
                        scenario_date, adjustment = roll(scheduled + timedelta(days=days_late)), "RECEIPT_DELAY"
                    if scenario_date > horizon[-1]:
                        reason = "OUTSIDE_HORIZON"
            if not reason:
                movements[flow["bank_account_id"], scenario_date][0 if signed > 0 else 1] += abs(signed)
                delayed += adjustment != "NONE"
            items.append({"forecast_run_id": run_id, "cash_flow_forecast_id": flow["cash_flow_forecast_id"], "decision": "EXCLUDED" if reason else "INCLUDED",
                          "exclusion_reason": reason, "scheduled_date": scheduled.isoformat(), "scenario_date": scenario_date.isoformat(),
                          "adjustment": adjustment, "signed_amount": money(signed), "_source_id": flow["source_id"], "_account": flow["bank_account_id"]})

        # Starting balance = opening + ledger entries visible at the cutoff; never the recorded closing.
        balances = {a["bank_account_id"]: cents(a["opening_balance"]) for a in accounts}
        for account_id, _, available, amount, _ in ledger:
            if available <= cutoff:
                balances[account_id] += amount
        snapshot += [f"BAL|{k}|{v}" for k, v in balances.items()]
        snapshot += [f"FX|{rate_id('FORWARD', cutoff_day, day, ccy)}|{rate_text(rates[key])}" for day in horizon for ccy in C.CURRENCIES
                     if (key := ("FORWARD", cutoff_day.isoformat(), day.isoformat(), ccy)) in rates]
        one_offs = defaultdict(int)
        for outflow in assumptions["one_off_outflows"]:
            one_offs[outflow["bank_account_id"], horizon[outflow["horizon_day"] - 1]] += cents(outflow["amount"])

        positions, missing_rates = [], defaultdict(int)
        running = dict(balances)
        for day in horizon:
            for account in accounts:
                account_id, currency = account["bank_account_id"], account["account_currency"]
                opening = running[account_id]
                inflow, outflow = movements[account_id, day]
                running[account_id] = opening + inflow - outflow - one_offs[account_id, day]
                rate = rates.get(("FORWARD", cutoff_day.isoformat(), day.isoformat(), currency))
                applied_shock = shock if currency != C.REPORTING_CURRENCY else 0.0
                if rate is None:
                    missing_rates[rate_id("FORWARD", cutoff_day, day, currency)] += 1
                positions.append({"forecast_run_id": run_id, "bank_account_id": account_id, "entity_id": account["entity_id"], "position_date": day.isoformat(),
                                  "native_currency": currency, "opening_native": money(opening), "inflows_native": money(inflow), "outflows_native": money(outflow),
                                  "scenario_outflow_native": money(one_offs[account_id, day]), "closing_native": money(running[account_id]),
                                  "fx_rate": rate_text(rate) if rate is not None else "", "fx_shock_pct": f"{applied_shock:.1f}",
                                  "closing_reporting": money(to_inr(running[account_id], rate, applied_shock)) if rate is not None else "",
                                  "valuation_status": "VALUED" if rate is not None else "UNRESOLVED_FX"})
        for missing, affected in sorted(missing_rates.items()):
            raise_exception(run_id, "MISSING_FX_RATE", "HIGH", "CurrencyRate", missing, "FLAG_UNRESOLVED_CONVERSION", "OPERATIONS_ANALYST", created,
                            f"No forward rate on the {cutoff_day} curve; {affected} account position(s) left unvalued.")

        # Entity totals and buffer control (R7).
        group = defaultdict(int)
        complete_days = set(d.isoformat() for d in horizon)
        breach_days, breaching = 0, set()
        per_entity = defaultdict(lambda: [0, 0])
        for position in positions:
            bucket = per_entity[position["entity_id"], position["position_date"]]
            if position["valuation_status"] == "VALUED":
                bucket[0] += cents(position["closing_reporting"])
                group[position["position_date"]] += cents(position["closing_reporting"])
            else:
                bucket[1] += 1
                complete_days.discard(position["position_date"])
        first_breach = {}
        for entity_id, entity in entities.items():
            buffer = cents(entity["minimum_cash_buffer_inr"])
            for day in horizon:
                total, unresolved = per_entity[entity_id, day.isoformat()]
                status = "UNRESOLVED_FX" if unresolved else ("BREACH" if total < buffer else "OK")
                if status == "BREACH":
                    breach_days += 1
                    breaching.add(entity_id)
                    first_breach.setdefault(entity_id, (day, total))
                out["entity_positions"].append({"forecast_run_id": run_id, "entity_id": entity_id, "position_date": day.isoformat(), "valued_total_inr": money(total),
                                                "unresolved_accounts": str(unresolved), "minimum_cash_buffer_inr": entity["minimum_cash_buffer_inr"], "buffer_status": status})
        for entity_id, (day, total) in sorted(first_breach.items()):
            days_below = sum(1 for p in out["entity_positions"] if p["forecast_run_id"] == run_id and p["entity_id"] == entity_id and p["buffer_status"] == "BREACH")
            raise_exception(run_id, "LIQUIDITY_BUFFER_BREACH", "HIGH", "LegalEntity", entity_id, "ESCALATE_TO_TREASURY", "RISK_COMPLIANCE_REVIEWER", created,
                            f"INR {money(total)} on {day} is below buffer {entities[entity_id]['minimum_cash_buffer_inr']}; {days_below} breach day(s).")

        valued_days = [d.isoformat() for d in horizon if d.isoformat() in complete_days]
        minimum_day = min(valued_days, key=lambda d: (group[d], d))
        first_rates = {ccy: rates.get(("FORWARD", cutoff_day.isoformat(), horizon[0].isoformat(), ccy)) for ccy in C.CURRENCIES}
        opening_group = sum(to_inr(balances[a["bank_account_id"]], first_rates[a["account_currency"]], shock if a["account_currency"] != C.REPORTING_CURRENCY else 0.0) for a in accounts)
        included = sum(i["decision"] == "INCLUDED" for i in items)
        run_exceptions = len(exceptions) - first_exception
        out["forecast_runs"].append({"forecast_run_id": run_id, "scenario_id": run["scenario_id"], "run_version": str(run["run_version"]),
                                     "previous_run_id": run["previous_run_id"], "created_at": created, "data_cutoff": run["data_cutoff"],
                                     "horizon_start": horizon[0].isoformat(), "horizon_end": horizon[-1].isoformat(), "fx_quote_date": cutoff_day.isoformat(),
                                     "calculation_version": C.CALCULATION_VERSION, "input_snapshot_hash": snapshot_hash(snapshot),
                                     "included_items": str(included), "excluded_items": str(len(items) - included), "exception_count": str(run_exceptions),
                                     "status": "COMPLETED_WITH_EXCEPTIONS" if run_exceptions else "COMPLETED"})
        out["scenario_summary"].append({"forecast_run_id": run_id, "scenario_id": run["scenario_id"], "opening_group_inr": money(opening_group),
                                        "min_group_inr": money(group[minimum_day]), "min_group_date": minimum_day, "closing_group_inr": money(group[valued_days[-1]]),
                                        "breach_entity_days": str(breach_days), "entities_breaching": str(len(breaching)),
                                        "unresolved_positions": str(sum(missing_rates.values())), "delayed_items": str(delayed)})
        out["cash_positions"] += positions

        # Back-test against actuals for horizon days that are already complete (R8).
        settled_included = {i["_source_id"]: i for i in items if i["decision"] == "INCLUDED"}
        for day in [d for d in horizon if d <= as_of_day]:
            lag, timing, unforecast = defaultdict(int), defaultdict(int), defaultdict(int)
            for account_id, value_day, available, amount, source_id in ledger:
                if value_day <= cutoff_day and available > cutoff:
                    lag[account_id] += amount
                elif cutoff_day < value_day <= day and source_id not in settled_included:
                    unforecast[account_id] += amount
            for item in settled_included.values():
                account_id = item["_account"]
                settled = actual_date.get(item["_source_id"], "")
                amount = cents(item["signed_amount"])
                timing[account_id] += amount * ((bool(settled) and settled <= day.isoformat()) - (item["scenario_date"] <= day.isoformat()))
            for position in (p for p in positions if p["position_date"] == day.isoformat()):
                account_id, currency = position["bank_account_id"], position["native_currency"]
                forecast_native, actual_native = cents(position["closing_native"]), actual_closing[account_id, day]
                components = {"CUTOFF_LAG": lag[account_id], "TIMING": timing[account_id], "UNFORECAST": unforecast[account_id]}
                if sum(components.values()) != actual_native - forecast_native:
                    raise AssertionError(f"Unexplained variance for {account_id} {day}")
                driver = max(components, key=lambda k: abs(components[k])) if actual_native != forecast_native else "NONE"
                forecast_rate, actual_rate = float(position["fx_rate"]), rates["SPOT", day.isoformat(), day.isoformat(), currency]
                forecast_inr, actual_inr = to_inr(forecast_native, forecast_rate), to_inr(actual_native, actual_rate)
                volume = to_inr(actual_native - forecast_native, actual_rate)
                out["actual_vs_forecast"].append({"forecast_run_id": run_id, "bank_account_id": account_id, "position_date": day.isoformat(), "currency": currency,
                                                  "forecast_native": money(forecast_native), "actual_native": money(actual_native),
                                                  "variance_native": money(actual_native - forecast_native), "cutoff_lag_component": money(components["CUTOFF_LAG"]),
                                                  "timing_component": money(components["TIMING"]), "unforecast_component": money(components["UNFORECAST"]),
                                                  "primary_driver": driver, "forecast_rate": rate_text(forecast_rate), "actual_rate": rate_text(actual_rate),
                                                  "forecast_reporting": money(forecast_inr), "actual_reporting": money(actual_inr),
                                                  "variance_reporting": money(actual_inr - forecast_inr), "volume_effect_reporting": money(volume),
                                                  "fx_effect_reporting": money(actual_inr - forecast_inr - volume)})
        out["forecast_run_items"] += [{k: v for k, v in item.items() if not k.startswith("_")} for item in items]

    for number, record in enumerate(exceptions, start=1):
        record["exception_id"] = f"EXC-2026-{number:06d}"
    out["exceptions"] = exceptions
    return out


# ---------------------------------------------------------------- export

def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_gzip(path: Path, text: str) -> None:
    # mtime=0 and an empty embedded filename keep the archive byte-identical across runs.
    with path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
        stream.write(text.encode("utf-8"))


def sql_value(value: str, kind: str) -> str:
    if value == "":
        return "NULL"
    return value if kind in ("money", "rate", "int") else "'" + value.replace("'", "''") + "'"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def export(output: Path, sources: dict, derived: dict, truth: list[dict], scale_name: str, seed: int) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    tables = {**sources, **derived, "ground_truth": truth}
    for name in TABLES:
        write_csv(output / f"{name}.csv", columns(name), tables[name])
    (output / "ground_truth.json").write_text(json.dumps(truth, indent=2) + "\n", encoding="utf-8", newline="\n")
    (output / "schema.sql").write_text("-- DDL for every exported table, generated from schema.py.\n\n"
                                       + "\n\n".join(sql_ddl(name) for name in TABLES) + "\n", encoding="utf-8", newline="\n")
    seed_json = {"seed": seed, "scale": scale_name, "tables": {name: sources[name] for name in SOURCE_TABLES}}
    write_gzip(output / "seed_data.json.gz", json.dumps(seed_json, sort_keys=True, separators=(",", ":")) + "\n")
    sql = ["-- Source/seed tables in parent-first order. Load: gunzip -c seed_data.sql.gz | sqlite3 liquidity.db", "BEGIN;"]
    for name in SOURCE_TABLES:
        kinds = [(field, kind) for field, kind, *_ in TABLES[name]["fields"]]
        sql.append(sql_ddl(name))
        head = f'INSERT INTO "{name}" (' + ", ".join(f'"{field}"' for field, _ in kinds) + ") VALUES "
        sql += [head + "(" + ", ".join(sql_value(row[field], kind) for field, kind in kinds) + ");" for row in sources[name]]
    sql.append("COMMIT;")
    write_gzip(output / "seed_data.sql.gz", "\n".join(sql) + "\n")

    manifest = {
        "generator": "python scripts/generate.py --scale " + scale_name + (f" --seed {seed}" if seed != C.SEED else ""),
        "seed": seed, "scale": scale_name, "calculation_version": C.CALCULATION_VERSION,
        "history_start": C.HISTORY_START, "as_of_cutoff": C.AS_OF, "backtest_cutoff": C.BACKTEST_CUTOFF, "extract_at": C.EXTRACT_AT,
        "reporting_currency": C.REPORTING_CURRENCY, "currencies": list(C.CURRENCIES),
        "provenance": {"external_data": "none", "personal_data": "none",
                       "description": "All records are synthetic and produced by this repository's generator from the fixed seed.",
                       "license": "Generated data may be redistributed with the repository."},
        "files": {},
    }
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name not in ("source_manifest.json", "reproducibility.json", "validation_summary.json"):
            rows = None
            if path.suffix == ".csv":
                with path.open(encoding="utf-8") as stream:
                    rows = sum(1 for _ in stream) - 1
            manifest["files"][path.name] = {"rows": rows, "sha256": sha256(path)}
    (output / "source_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return manifest


def generate(output: Path, scale_name: str = "starter", seed: int = C.SEED) -> dict:
    sources, truth = build_sources(scale_name, seed)
    derived = run_forecasts(sources)
    return export(Path(output), sources, derived, truth, scale_name, seed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic liquidity forecasting data.")
    parser.add_argument("--output", default="data/generated", type=Path)
    parser.add_argument("--scale", choices=sorted(C.SCALES), default="starter")
    parser.add_argument("--seed", type=int, default=C.SEED)
    parser.add_argument("--verify-reproducibility", action="store_true", help="Generate a second time and compare every file checksum.")
    args = parser.parse_args()
    manifest = generate(args.output, args.scale, args.seed)
    if args.verify_reproducibility:
        with tempfile.TemporaryDirectory() as temporary:
            second = generate(Path(temporary), args.scale, args.seed)
        mismatched = sorted(name for name in manifest["files"] if manifest["files"][name] != second["files"].get(name))
        result = {"seed": args.seed, "scale": args.scale, "passed": not mismatched and manifest["files"].keys() == second["files"].keys(),
                  "mismatched_files": mismatched, "files_compared": len(manifest["files"]),
                  "first_run": manifest["files"], "second_run": second["files"]}
        (args.output / "reproducibility.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        if not result["passed"]:
            raise SystemExit(f"Reproducibility check failed: {mismatched}")
    print(json.dumps({"output": str(args.output), "scale": args.scale, "seed": args.seed, "files": len(manifest["files"]),
                      "reproducible": args.verify_reproducibility or None}, indent=2))
