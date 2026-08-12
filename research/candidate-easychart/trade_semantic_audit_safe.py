#!/usr/bin/env python3
"""Empty-run-safe front end for :mod:`trade_semantic_audit`.

A zero-trade result is information, not an I/O error.  Diagnostic screens write
an empty CSV when no setup survives semantic routing.  This wrapper supplies the
known dataclass headers, runs the same audit, and preserves an explicit empty
casebook and schema-bearing outputs so the reason for zero opportunity can be
reviewed from upstream router evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import fields
from pathlib import Path
import shutil
import tempfile

import pandas as pd

from domain_v3 import ArmedSetup
from simulator_v3_types import TradeRecord
from trade_semantic_audit import run_audit


SETUP_AUDIT_COLUMNS = [
    "plan_id",
    "symbol",
    "family",
    "observed_time_ns",
    "audit_classification",
    "audit_event",
    "audit_event_open_time_ns",
    "audit_event_close_time_ns",
    "recorded_trade",
    "recorded_entry_time_ns",
    "busy_plan_id",
    "gross_rr_geometry",
    "semantic_roles",
    "path_results",
    "simultaneous_winner_plan_id",
    "disposition",
]
TRADE_AUDIT_COLUMNS = [
    "plan_id",
    "symbol",
    "family",
    "recorded_outcome",
    "recorded_entry_time_ns",
    "recorded_exit_time_ns",
    "entry_audit_classification",
    "entry_audit_event",
    "entry_time_match",
    "exit_audit_classification",
    "exit_audit_event",
    "exit_time_match",
    "outcome_match",
    "classification",
    "gross_rr",
    "net_r",
    "entry_notional_to_nav",
    "semantic_roles",
    "entry_path_results",
    "exit_path_results",
]


def _normalize_csv(source: Path, destination: Path, columns: list[str]) -> None:
    if source.exists() and source.stat().st_size > 1 and source.read_text(encoding="utf-8").strip():
        shutil.copy2(source, destination)
        return
    pd.DataFrame(columns=columns).to_csv(destination, index=False)


def run_safe(args: argparse.Namespace):
    original = args.run_dir.resolve()
    with tempfile.TemporaryDirectory(prefix="easychart-audit-") as temporary:
        normalized = Path(temporary)
        _normalize_csv(
            original / "setups.csv",
            normalized / "setups.csv",
            [field.name for field in fields(ArmedSetup)],
        )
        _normalize_csv(
            original / "trades.csv",
            normalized / "trades.csv",
            [field.name for field in fields(TradeRecord)],
        )
        shutil.copy2(original / "run.json", normalized / "run.json")
        values = vars(args).copy()
        values["run_dir"] = normalized
        summary = run_audit(argparse.Namespace(**values))

    output = args.output.resolve()
    setup_path = output / "setup_semantic_audit.csv"
    trade_path = output / "trade_path_audit.csv"
    if not setup_path.exists() or setup_path.stat().st_size <= 1:
        pd.DataFrame(columns=SETUP_AUDIT_COLUMNS).to_csv(setup_path, index=False)
    if not trade_path.exists() or trade_path.stat().st_size <= 1:
        pd.DataFrame(columns=TRADE_AUDIT_COLUMNS).to_csv(trade_path, index=False)
    casebook = output / "trade_casebook.jsonl"
    casebook.touch(exist_ok=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--coverage", type=Path)
    args = parser.parse_args()
    run_safe(args)


if __name__ == "__main__":
    main()
