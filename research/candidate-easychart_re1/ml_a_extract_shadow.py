"""Extract one-pass shadow labels into a plan-level ML research table."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fee_profiles_v5 import FEE_PROFILES
from instruments import CONTRACTS
from ml_a_enrich_counterfactual import enrich


OUTCOME = {
    "TARGET": "TARGET_FIRST",
    "STOP": "STOP_FIRST",
    "STOP_TIE": "AMBIGUOUS_SAME_MINUTE",
}


def _net_r(row: pd.Series, fee_profile: str, entry_ticks: int, stop_ticks: int) -> float:
    side = str(row["side"])
    sign = 1.0 if side == "LONG" else -1.0
    entry, stop, target = float(row["entry"]), float(row["stop"]), float(row["target"])
    risk = abs(entry - stop)
    if risk <= 0.0:
        return np.nan
    tick = float(CONTRACTS[str(row["symbol"])].price_increment)
    fee = float(FEE_PROFILES[fee_profile].taker_rate)
    actual_entry = entry + sign * entry_ticks * tick
    if str(row["counterfactual_outcome"]) == "TARGET_FIRST":
        actual_exit = target - sign * tick
    else:
        actual_exit = stop - sign * stop_ticks * tick
    return float(
        sign * (actual_exit - actual_entry) / risk
        - fee * (abs(actual_entry) + abs(actual_exit)) / risk
    )


def extract(
    events: Path,
    output: Path,
    *,
    fee_profile: str,
    entry_ticks: int,
    stop_ticks: int,
) -> dict[str, Any]:
    data = pd.read_csv(events, low_memory=False)
    registered = data[data["kind"].eq("shadow_plan_registered")].copy()
    resolved = data[data["kind"].eq("shadow_plan_resolved")].copy()
    if registered.empty:
        raise RuntimeError("no shadow_plan_registered rows")
    keep = [
        "plan_id", "outcome", "resolution_time_ns", "max_favorable_r",
        "max_adverse_r", "bars_observed", "resolution_high", "resolution_low",
    ]
    keep = [column for column in keep if column in resolved]
    resolved = resolved[keep].drop_duplicates("plan_id", keep="last")
    plans = registered.drop_duplicates("plan_id", keep="last").merge(
        resolved, on="plan_id", how="left", suffixes=("", "_resolution")
    )
    plans["counterfactual_outcome"] = plans["outcome"].map(OUTCOME)
    resolution_ns = pd.to_numeric(plans["resolution_time_ns"], errors="coerce")
    plan_ns = pd.to_numeric(plans["observed_time_ns"], errors="coerce")
    plans["counterfactual_resolution_time"] = pd.to_datetime(
        resolution_ns, unit="ns", utc=True, errors="coerce"
    ).astype("string")
    plans["counterfactual_minutes_to_resolution"] = (resolution_ns - plan_ns) / 60_000_000_000.0
    plans["counterfactual_mfe_r"] = pd.to_numeric(plans["max_favorable_r"], errors="coerce")
    plans["counterfactual_mae_r"] = pd.to_numeric(plans["max_adverse_r"], errors="coerce")
    plans["ts_ns"] = plan_ns
    resolved_mask = plans["counterfactual_outcome"].notna()
    plans.loc[resolved_mask, "counterfactual_net_r_conservative"] = plans.loc[
        resolved_mask
    ].apply(
        _net_r,
        axis=1,
        fee_profile=fee_profile,
        entry_ticks=entry_ticks,
        stop_ticks=stop_ticks,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    plans.to_csv(output, index=False)
    trace = enrich(events, output, output)
    summary = {
        "plans": int(len(plans)),
        "resolved": int(resolved_mask.sum()),
        "target_first": int(plans["counterfactual_outcome"].eq("TARGET_FIRST").sum()),
        "stop_first": int(plans["counterfactual_outcome"].eq("STOP_FIRST").sum()),
        "ambiguous": int(plans["counterfactual_outcome"].eq("AMBIGUOUS_SAME_MINUTE").sum()),
        "trace": trace,
        "policy": "STRICTLY_LATER_COMPLETED_ONE_MINUTE_STOP_FIRST_TIES",
    }
    (output.parent / "counterfactual_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fee-profile", choices=tuple(FEE_PROFILES), default="usd_m_vip0")
    parser.add_argument("--entry-slippage-ticks", type=int, default=2)
    parser.add_argument("--stop-slippage-ticks", type=int, default=2)
    args = parser.parse_args()
    print(json.dumps(extract(
        args.events,
        args.output,
        fee_profile=args.fee_profile,
        entry_ticks=args.entry_slippage_ticks,
        stop_ticks=args.stop_slippage_ticks,
    ), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
