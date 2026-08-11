#!/usr/bin/env python3
"""Explain why the frozen liquidity-vacuum parent produced zero events.

The source parent is an AND of price/balance, urgency, spot participation and
book-withdrawal conditions.  This diagnostic uses the already-persisted exact
feature rows and evaluates every non-price stage unchanged.  If no row survives
all non-price conditions, price geometry cannot make the source state exist.
No threshold is searched or changed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
SCHEMA = "candidate-60-liquidity-vacuum-nonprice-funnel-v1"


def _as_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes"})


def _side_counts(frame: pd.DataFrame, side: int) -> dict[str, Any]:
    ready = _as_bool(frame["feature_ready"])
    common = (
        ready
        & frame["notional_burst"].ge(1.50)
        & frame["efficiency_60s"].ge(0.45)
        & frame["spot_efficiency_60s"].ge(0.30)
    )
    if side > 0:
        flow = frame["flow_60s"].ge(0.15) & frame["flow_3m"].ge(0.08)
        spot = (
            frame["spot_flow_60s"].ge(0.08)
            & frame["spot_flow_3m"].ge(0.04)
            & frame["spot_ret_1m_bps"].ge(1.0)
        )
        depth_parts = {
            "depth_imbalance_1": frame["depth_imbalance_1"].ge(0.15),
            "depth_imbalance_2": frame["depth_imbalance_2"].ge(0.05),
            "opposing_cancel_1m": frame["ask_depth_change_1_1m"].le(-0.10),
            "opposing_cancel_5m": frame["ask_depth_change_1_5m"].le(-0.15),
            "same_side_not_cancelled_1m": frame["bid_depth_change_1_1m"].ge(-0.10),
        }
    else:
        flow = frame["flow_60s"].le(-0.15) & frame["flow_3m"].le(-0.08)
        spot = (
            frame["spot_flow_60s"].le(-0.08)
            & frame["spot_flow_3m"].le(-0.04)
            & frame["spot_ret_1m_bps"].le(-1.0)
        )
        depth_parts = {
            "depth_imbalance_1": frame["depth_imbalance_1"].le(-0.15),
            "depth_imbalance_2": frame["depth_imbalance_2"].le(-0.05),
            "opposing_cancel_1m": frame["bid_depth_change_1_1m"].le(-0.10),
            "opposing_cancel_5m": frame["bid_depth_change_1_5m"].le(-0.15),
            "same_side_not_cancelled_1m": frame["ask_depth_change_1_1m"].ge(-0.10),
        }
    common_flow = common & flow
    common_flow_spot = common_flow & spot
    all_depth = pd.Series(True, index=frame.index)
    for condition in depth_parts.values():
        all_depth &= condition
    result: dict[str, Any] = {
        "common_urgency_efficiency": int(common.sum()),
        "flow": int(flow.sum()),
        "spot": int(spot.sum()),
        "common_and_flow": int(common_flow.sum()),
        "common_flow_and_spot": int(common_flow_spot.sum()),
        "all_depth": int(all_depth.sum()),
        "flow_and_all_depth_without_common_or_spot": int((flow & all_depth).sum()),
        "complete_nonprice_parent": int((common_flow_spot & all_depth).sum()),
        "depth_parts_within_common_flow_spot": {
            name: int((common_flow_spot & condition).sum())
            for name, condition in depth_parts.items()
        },
    }
    base = frame.loc[common_flow_spot]
    if not base.empty:
        columns = [
            "depth_imbalance_1",
            "depth_imbalance_2",
            "bid_depth_change_1_1m",
            "ask_depth_change_1_1m",
            "bid_depth_change_1_5m",
            "ask_depth_change_1_5m",
        ]
        result["depth_medians_within_common_flow_spot"] = {
            column: float(base[column].median()) for column in columns
        }
    return result


def run(root: Path, output: Path) -> dict[str, Any]:
    per_symbol: dict[str, Any] = {}
    aggregate_frames: list[pd.DataFrame] = []
    for symbol in SYMBOLS:
        path = root / symbol / "feature_contract" / "features.csv.gz"
        if not path.is_file():
            raise RuntimeError(f"missing exact feature evidence: {path}")
        frame = pd.read_csv(path, compression="gzip")
        aggregate_frames.append(frame.assign(symbol=symbol))
        per_symbol[symbol] = {
            "rows": int(len(frame)),
            "ready_rows": int(_as_bool(frame["feature_ready"]).sum()),
            "long": _side_counts(frame, 1),
            "short": _side_counts(frame, -1),
        }
    aggregate = pd.concat(aggregate_frames, ignore_index=True)
    result = {
        "schema": SCHEMA,
        "role": (
            "outcome-blind causal funnel for an exact zero-parent source policy; "
            "no threshold search, PnL, or direction flip"
        ),
        "source_policy_unchanged": True,
        "price_and_balance_conditions_intentionally_not_used": True,
        "logical_implication": (
            "when complete_nonprice_parent is zero, no price/balance condition "
            "can produce an exact source parent"
        ),
        "per_symbol": per_symbol,
        "aggregate": {
            "rows": int(len(aggregate)),
            "ready_rows": int(_as_bool(aggregate["feature_ready"]).sum()),
            "long": _side_counts(aggregate, 1),
            "short": _side_counts(aggregate, -1),
        },
        "interpretation_contract": {
            "zero_events_do_not_justify_threshold_relaxation": True,
            "preserve_individual_observations_only_if_they_solve_a_distinct_problem": True,
            "exact_and_chain_is_retired_if_nonprice_parent_is_structurally_empty": True,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.root.resolve(), args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
