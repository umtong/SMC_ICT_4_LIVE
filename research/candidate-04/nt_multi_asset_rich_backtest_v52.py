#!/usr/bin/env python3
"""V52 execution wrapper with a valid zero-trade risk-evidence contract.

This changes no signal, target, stop, cost, fill, venue, portfolio or risk-sizing
logic. The trusted V44 runner remains the execution path. The only repair is to
recognize a genuinely empty Nautilus positions report plus zero ENTRY_SUBMITTED
events as an unambiguous 0% realized-loss observation instead of a schema error.
Any position or entry ambiguity continues to fail closed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import nt_multi_asset_rich_backtest_v3 as v3
import nt_multi_asset_rich_backtest_v44 as v44
import nt_multi_asset_risk_evidence as risk


def _write_zero_trade_evidence(output: Path, metrics: dict[str, Any]) -> dict[str, Any]:
    record = {
        "candidate": "candidate-04-four-instrument-risk-evidence",
        "matched_losses": 0,
        "matched_positions": 0,
        "matched_entries": 0,
        "ordering": {symbol: "no_positions_or_entries" for symbol in risk.SYMBOLS},
        "errors": [],
        "limit": 0.0301,
        "pass": True,
        "risk_pass": True,
        "maximum_realized_loss_fraction": 0.0,
        "pnl_source": "NautilusTrader empty positions report",
        "entry_nav_source": "zero per-symbol ENTRY_SUBMITTED events",
        "performance_recalculated": False,
        "zero_trade_contract": (
            "valid only because both Nautilus positions and every strategy "
            "ENTRY_SUBMITTED stream are empty"
        ),
    }
    metrics["maximum_realized_loss_fraction"] = 0.0
    metrics["multi_asset_risk_evidence"] = record
    checks = dict(metrics.get("gate_checks") or {})
    checks["realized_loss_within_3pct_nav"] = True
    metrics["gate_checks"] = checks
    metrics["risk_pass"] = True
    metrics["candidate_pass"] = bool(
        checks
        and all(bool(value) for value in checks.values())
        and metrics.get("global_entry_pass") is True
    )
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "risk_evidence.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metrics


def reconcile_output(output: Path, limit: float = 0.0301) -> dict[str, Any]:
    positions = risk.load_positions(output / "positions.csv")
    events = risk.load_events_by_symbol(output)
    entry_count = sum(
        1
        for rows in events.values()
        for event in rows
        if event.get("event_type") == "ENTRY_SUBMITTED"
    )
    if positions.empty and entry_count == 0:
        metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
        return _write_zero_trade_evidence(output, metrics)
    return risk.reconcile_output(output, limit)


v3.reconcile_output = reconcile_output


if __name__ == "__main__":
    v44.base.main()
