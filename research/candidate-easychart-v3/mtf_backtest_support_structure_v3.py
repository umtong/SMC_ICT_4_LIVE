"""Evidence preservation for the structure-first EasyChart v3 policy."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

import mtf_backtest_support as _base
from backtest_support import RISK_TOLERANCE, _jsonable, write_json


def _find_zone(scenario: Any, zone_id: str) -> Any | None:
    for detector in scenario.detectors.values():
        for zone in detector.zones:
            if zone.zone_id == zone_id:
                return zone
    return scenario.find_zone(zone_id)


def _write_structure_trade_windows(strategy: Any, output: Path) -> int:
    scenarios_by_symbol = {
        scenario.symbol: scenario
        for scenario in strategy.scenario_engines.values()
    }
    submitted = {
        event["plan_id"]: event
        for event in strategy.event_log
        if event.get("kind") == "submitted" and event.get("plan_id")
    }
    lookbacks = {60: 24, 15: 48, 5: 72, 1: 120}
    lookaheads = {60: 12, 15: 24, 5: 36, 1: 60}
    count = 0
    with (output / "mtf_trade_windows.jsonl").open("w", encoding="utf-8") as stream:
        for plan_id, submitted_event in submitted.items():
            plan = strategy.plan_log.get(plan_id)
            if plan is None:
                continue
            scenario = scenarios_by_symbol.get(plan.symbol)
            if scenario is None:
                continue
            windows: dict[str, list[dict[str, Any]]] = {}
            for timeframe, detector in sorted(scenario.detectors.items(), reverse=True):
                trigger_index = next(
                    (
                        index
                        for index, bar in enumerate(detector.bars)
                        if bar.ts_close_ns >= plan.trigger_time_ns
                    ),
                    len(detector.bars) - 1,
                )
                start_index = max(0, trigger_index - lookbacks[timeframe])
                end_index = min(len(detector.bars), trigger_index + lookaheads[timeframe] + 1)
                windows[str(timeframe)] = [
                    {
                        "index": index,
                        "relative_to_trigger": index - trigger_index,
                        **asdict(detector.bars[index]),
                    }
                    for index in range(start_index, end_index)
                ]
            zone_ids = {
                "higher": plan.higher_zone_id,
                "decision": plan.lower_zone_id,
                "trigger": plan.trigger_zone_id,
                "target": plan.target_zone_id,
            }
            zones = {
                role: None if (zone := _find_zone(scenario, zone_id)) is None else asdict(zone)
                for role, zone_id in zone_ids.items()
            }
            related_events = [
                event
                for event in strategy.event_log
                if event.get("plan_id") == plan_id
            ]
            record = {
                "plan": asdict(plan),
                "submitted": submitted_event,
                "zones": zones,
                "events": related_events,
                "bars": windows,
            }
            stream.write(json.dumps(_jsonable(record), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def _write_funding_evidence(
    funding_module: Any,
    output: Path,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    columns = [
        "symbol",
        "instrument_id",
        "position_id",
        "account_id",
        "strategy_id",
        "funding_time_ns",
        "processed_time_ns",
        "interval_minutes",
        "rate",
        "mark_price",
        "signed_qty",
        "notional",
        "currency",
        "amount",
    ]
    ledger = pd.DataFrame(funding_module.ledger)
    if ledger.empty:
        ledger = pd.DataFrame(columns=columns)
    else:
        missing = sorted(set(columns) - set(ledger.columns))
        if missing:
            raise RuntimeError(f"funding ledger lost required columns: {missing}")
        ledger = ledger[columns]
    ledger.to_csv(output / "funding_ledger.csv", index=False)
    with (output / "funding_ledger.jsonl").open("w", encoding="utf-8") as stream:
        for record in funding_module.ledger:
            stream.write(json.dumps(_jsonable(record), ensure_ascii=False, sort_keys=True) + "\n")

    audit_path = output / "trade_audit.csv"
    if not audit_path.exists():
        raise RuntimeError("trade_audit.csv unavailable for funding join")
    audit = pd.read_csv(audit_path)
    if "position_id" not in audit.columns or "realized_pnl" not in audit.columns:
        raise RuntimeError("trade audit lost position or realized-PnL fields")
    audit["position_id"] = audit["position_id"].astype(str)
    ledger_position_ids: set[str] = set()
    if ledger.empty:
        funding_by_position = pd.Series(dtype=float)
        funding_total = 0.0
    else:
        ledger["position_id"] = ledger["position_id"].astype(str)
        ledger["amount_numeric"] = pd.to_numeric(ledger["amount"], errors="raise")
        ledger_position_ids = set(ledger["position_id"])
        funding_by_position = ledger.groupby("position_id")["amount_numeric"].sum()
        funding_total = float(ledger["amount_numeric"].sum())

    audited_position_ids = set(audit["position_id"])
    unmatched_funding_positions = sorted(ledger_position_ids - audited_position_ids)
    audit["funding_pnl"] = audit["position_id"].map(funding_by_position).fillna(0.0)
    execution_pnl = pd.to_numeric(audit["realized_pnl"], errors="coerce")
    audit["realized_pnl_after_funding"] = execution_pnl + audit["funding_pnl"]
    risk_budget = pd.to_numeric(audit.get("risk_budget"), errors="coerce")
    audit["actual_net_r_after_funding"] = audit["realized_pnl_after_funding"] / risk_budget
    audit["actual_loss_budget_breach_after_funding"] = (
        (audit["realized_pnl_after_funding"] < 0.0)
        & (-audit["realized_pnl_after_funding"] > risk_budget * RISK_TOLERANCE)
    ).fillna(False)
    audit.to_csv(audit_path, index=False)

    execution_total = float(execution_pnl.fillna(0.0).sum())
    expected_final_nav = float(metrics["starting_nav"]) + execution_total + funding_total
    nav_reconciliation_error = float(metrics["final_nav"]) - expected_final_nav
    loaded_boundaries = len(funding_module.boundaries)
    processed_boundaries = int(funding_module.processed_boundaries)
    funding_risk_breaches = int(
        audit["actual_loss_budget_breach_after_funding"].astype(bool).sum()
    )
    return {
        "funding_boundaries_loaded": loaded_boundaries,
        "funding_boundaries_processed": processed_boundaries,
        "funding_position_settlements": int(funding_module.settled_positions),
        "funding_positions_charged_or_credited": len(funding_by_position.index),
        "funding_total": funding_total,
        "execution_realized_pnl_total": execution_total,
        "realized_pnl_after_funding_total": execution_total + funding_total,
        "expected_final_nav_from_trade_and_funding_ledgers": expected_final_nav,
        "nav_reconciliation_error": nav_reconciliation_error,
        "unmatched_funding_position_ids": unmatched_funding_positions,
        "actual_loss_budget_breaches_after_funding": funding_risk_breaches,
    }


def _concentration_metrics(output: Path, calendar_days: int) -> dict[str, Any]:
    """Measure whether one lucky trade or one long hold dominates the account path."""
    path = output / "trade_audit.csv"
    if not path.exists():
        raise RuntimeError("trade_audit.csv unavailable for concentration evidence")
    audit = pd.read_csv(path)
    if audit.empty:
        return {
            "pnl_basis": "realized_pnl_after_funding",
            "positive_pnl_total": 0.0,
            "largest_winner_pnl": None,
            "largest_winner_share_of_positive_pnl": None,
            "top3_winner_share_of_positive_pnl": None,
            "largest_abs_pnl_share_of_gross_abs_pnl": None,
            "median_duration_hours": None,
            "maximum_duration_hours": None,
            "trades_longer_than_24h": 0,
            "single_slot_occupancy_fraction": 0.0,
        }
    pnl_column = (
        "realized_pnl_after_funding"
        if "realized_pnl_after_funding" in audit.columns
        else "realized_pnl"
    )
    pnl = pd.to_numeric(audit[pnl_column], errors="coerce").dropna()
    positive = pnl[pnl > 0.0].sort_values(ascending=False)
    gross_abs = float(pnl.abs().sum())
    duration_ns = pd.to_numeric(audit["duration_ns"], errors="coerce").dropna()
    duration_hours = duration_ns / 3_600_000_000_000.0
    positive_total = float(positive.sum())
    return {
        "pnl_basis": pnl_column,
        "positive_pnl_total": positive_total,
        "largest_winner_pnl": None if positive.empty else float(positive.iloc[0]),
        "largest_winner_share_of_positive_pnl": (
            None if positive_total <= 0.0 else float(positive.iloc[0] / positive_total)
        ),
        "top3_winner_share_of_positive_pnl": (
            None if positive_total <= 0.0 else float(positive.iloc[:3].sum() / positive_total)
        ),
        "largest_abs_pnl_share_of_gross_abs_pnl": (
            None if gross_abs <= 0.0 else float(pnl.abs().max() / gross_abs)
        ),
        "median_duration_hours": (
            None if duration_hours.empty else float(duration_hours.median())
        ),
        "maximum_duration_hours": (
            None if duration_hours.empty else float(duration_hours.max())
        ),
        "trades_longer_than_24h": int((duration_hours > 24.0).sum()),
        # The project has one global position slot, so summed position time is
        # true slot occupation rather than a multi-position approximation.
        "single_slot_occupancy_fraction": (
            0.0
            if calendar_days <= 0
            else float(duration_hours.sum() / (calendar_days * 24.0))
        ),
    }


def preserve_structure_results(*args: Any, **kwargs: Any) -> dict[str, Any]:
    # The base writer owns validated order/position joins and Nautilus account
    # reports. This wrapper adds historical financing provenance and joins its
    # cash-flow ledger back to the positions which incurred it.
    funding_summaries = kwargs.pop("funding_summaries", None)
    funding_module = kwargs.pop("funding_module", None)
    if not isinstance(funding_summaries, dict) or not funding_summaries:
        raise RuntimeError("historical funding summaries are required")
    if funding_module is None:
        raise RuntimeError("historical funding module is required")

    original = _base._write_mtf_trade_windows
    _base._write_mtf_trade_windows = _write_structure_trade_windows
    try:
        metrics = _base.preserve_mtf_results(*args, **kwargs)
    finally:
        _base._write_mtf_trade_windows = original

    output: Path = kwargs["output"] if "output" in kwargs else args[2]
    funding_evidence = _write_funding_evidence(funding_module, output, metrics)
    metrics["candidate"] = "candidate-easychart-v3-structure-first"
    metrics["decision_policy"] = (
        "structure -> first causal objective -> interaction -> auction state -> "
        "event-local footprint -> immutable plan"
    )
    metrics["entry_policy"] = "first confirmed retest close -> one market parent"
    metrics["historical_funding_enabled"] = True
    metrics["funding_accounting"] = funding_evidence
    metrics["funding_data_by_symbol"] = funding_summaries
    metrics["funding_records_loaded"] = sum(
        int(summary["records"]) for summary in funding_summaries.values()
    )
    metrics["concentration"] = _concentration_metrics(
        output,
        int(metrics["calendar_days"]),
    )

    validation_path = output / "validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    failures = list(validation.get("failures", []))
    if funding_evidence["funding_boundaries_processed"] != funding_evidence["funding_boundaries_loaded"]:
        failures.append(
            "historical funding boundaries were not all processed by the continuous account",
        )
    if funding_evidence["unmatched_funding_position_ids"]:
        failures.append(
            "funding ledger contains positions absent from the Nautilus position audit",
        )
    if abs(float(funding_evidence["nav_reconciliation_error"])) > 1e-4:
        failures.append(
            "final NAV does not reconcile to execution PnL plus historical funding cash flow",
        )
    if funding_evidence["actual_loss_budget_breaches_after_funding"]:
        failures.append(
            f"{funding_evidence['actual_loss_budget_breaches_after_funding']} positions exceed "
            "the three-percent loss budget after funding",
        )
    validation["status"] = "FAIL" if failures else "PASS"
    validation["failures"] = failures
    validation["funding"] = funding_evidence
    write_json(validation_path, validation)

    write_json(output / "metrics.json", metrics)
    write_json(
        output / "run.json",
        {
            "run_id": f"ecv3-structure-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "candidate": "candidate-easychart-v3-structure-first",
            "engine": "NautilusTrader BacktestEngine",
            "data": (
                "checksum-verified Binance Vision USD-M 1m trade-price klines, "
                "fundingRate archives and 1m markPriceKlines; Nautilus internal "
                "5m/15m/60m composites"
            ),
            "funding": {
                "nautilus_simulation_module_account_settlement": True,
                "account_engine": "NautilusTrader",
                "settlement_extension": (
                    "same exchange.adjust_account path used by NautilusTrader "
                    "FXRolloverInterestModule"
                ),
                "position_report_policy": (
                    "execution PnL remains native; funding cash flows are joined "
                    "from funding_ledger.csv"
                ),
                "source": (
                    "Binance Vision monthly fundingRate plus one-minute "
                    "markPriceKlines open at each realized boundary"
                ),
                "checksum_verified": True,
                "causal_mark_policy": "mark-price bar open at funding boundary",
                "module_runs_after_strategy_callbacks_at_each_timestamp": True,
                "evidence": funding_evidence,
                "by_symbol": funding_summaries,
            },
            "scenario": (
                "causal wick structure -> first unspent objective -> "
                "rejection/acceptance/rotation/bounce -> event-local OB/FVG "
                "where required -> first retest -> fixed entry/stop/target"
            ),
            "contract": {
                "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
                "single_global_pending_or_position": True,
                "single_entry_decision": True,
                "single_full_stop_market": True,
                "single_full_target": True,
                "risk_fraction_current_nav": 0.03,
                "min_pre_entry_gross_rr": 1.0,
                "partial_management": False,
                "daily_loss_limit": False,
                "daily_trade_limit": False,
                "historical_funding_applied_to_account": True,
                "outlier_concentration_measured_after_funding": True,
            },
            "provenance_classes": [
                "SOURCE_EXPLICIT",
                "SOURCE_AMBIGUITY_TRANSLATION",
                "RESEARCH_HYPOTHESIS",
                "EXTERNAL_METHOD",
            ],
        },
    )
    if failures:
        raise RuntimeError("; ".join(failures))
    return metrics
