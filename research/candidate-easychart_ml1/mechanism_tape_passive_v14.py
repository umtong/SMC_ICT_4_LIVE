#!/usr/bin/env python3
"""Exact pre-decision tape plus causal passive/market action alternatives."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

import mechanism_passive_v13 as passive
import mechanism_tape_v10 as tape
import mechanism_tape_v12 as cached_tape

base = passive.base
SYMBOLS = passive.SYMBOLS
FEATURE_COLUMNS = tuple(passive.FEATURE_COLUMNS) + tuple(tape.TAPE_FEATURE_COLUMNS)
TAPE_FEATURE_COLUMNS = tape.TAPE_FEATURE_COLUMNS


def harvest(
    period: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
) -> None:
    passive._install()
    cached_tape._install()
    base.FEATURE_COLUMNS = FEATURE_COLUMNS
    output.mkdir(parents=True, exist_ok=True)
    base.harvest(period, start, end, cache / "bars", output)
    action_path = output / "actions.csv"
    actions = pd.read_csv(action_path, low_memory=False)
    augmented = tape.augment_actions(actions, cache / "aggtrades")
    augmented.to_csv(action_path, index=False)

    diagnostics_path = output / "diagnostics.json"
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    diagnostics["features"] = list(FEATURE_COLUMNS)
    diagnostics["entry_actions"] = {
        "market": int(augmented["entry_style"].eq("MARKET_CONFIRMED").sum()),
        "passive": int(augmented["entry_style"].eq("PASSIVE_FIRST_RETEST").sum()),
        "passive_filled": int(
            (
                augmented["entry_style"].eq("PASSIVE_FIRST_RETEST")
                & pd.to_numeric(augmented["filled"], errors="coerce").fillna(0).eq(1)
            ).sum()
        ),
        "passive_fill_model": "ONE_TICK_TRADE_THROUGH_THEN_MAKER_ENTRY",
        "pending_account_rule": "GLOBAL_SLOT_RESERVED_FROM_PLACEMENT_TO_EXIT_OR_EXPIRY",
    }
    diagnostics["exact_tape"] = {
        "source": "CHECKSUM_VERIFIED_BINANCE_USDM_AGGTRADES",
        "availability": "STRICTLY_BEFORE_ORDER_DECISION",
        "windows_seconds": list(tape.WINDOWS),
        "actions_with_60s_tape": int(
            pd.to_numeric(augmented["tape_available"], errors="coerce")
            .fillna(0.0)
            .sum()
        ),
    }
    diagnostics_path.write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--cache", type=Path, default=Path(".cache/mechanism-v14"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    harvest(args.period, args.start, args.end, args.cache, args.output)


if __name__ == "__main__":
    main()
