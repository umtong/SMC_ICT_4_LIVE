"""Cost-corrected wrapper for the V15 pullback/re-break geometry audit.

The first geometry pass expressed outcomes in structural R but omitted the fact
that a 20-bps round trip can exceed one full R when the pullback candle is very
narrow.  That omission materially overstates this setup.  This wrapper reruns
the unchanged causal episode detector, converts fees/slippage into R for every
individual setup, and replaces the implementation warrant with cost-after
geometry.

This is an implementation correction, not a new strategy variant.  Signal,
pullback, trigger, stop and arbitration are unchanged.
"""
from __future__ import annotations

from datetime import date
import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd


_BASE_PATH = Path(__file__).resolve().with_name(
    "v15_bb_pullback_rebreak_forensics.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_v15_rebreak_gross_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load gross geometry audit: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

COST_FLOOR_FRACTION = 0.002


def number_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def cost_summary(group: pd.DataFrame) -> dict[str, Any]:
    if group.empty:
        return {"episodes": 0}
    frame = group.copy()
    frame["cost_r"] = COST_FLOOR_FRACTION / frame["risk_fraction"]
    frame["net_max_favorable_r_120m"] = (
        frame["max_favorable_r_120m"] - frame["cost_r"]
    )
    frame["net_close_r_120m"] = frame["close_r_120m"] - frame["cost_r"]
    frame["hit_net_1r_before_stop"] = (
        frame["max_favorable_r_120m"] >= 1.0 + frame["cost_r"]
    )
    frame["hit_net_2r_before_stop"] = (
        frame["max_favorable_r_120m"] >= 2.0 + frame["cost_r"]
    )

    stop_present = frame["stop_minute"].notna()
    payoff_1r = np.where(
        frame["hit_net_1r_before_stop"],
        1.0,
        np.where(
            stop_present,
            -1.0 - frame["cost_r"],
            frame["net_close_r_120m"],
        ),
    )
    payoff_2r = np.where(
        frame["hit_net_2r_before_stop"],
        2.0,
        np.where(
            stop_present,
            -1.0 - frame["cost_r"],
            frame["net_close_r_120m"],
        ),
    )
    return {
        "episodes": int(len(frame)),
        "symbols": frame["symbol"].value_counts().to_dict(),
        "median_risk_fraction": number_or_none(frame["risk_fraction"].median()),
        "median_risk_bps": number_or_none(frame["risk_fraction"].median() * 10_000.0),
        "median_cost_r": number_or_none(frame["cost_r"].median()),
        "mean_cost_r": number_or_none(frame["cost_r"].mean()),
        "median_net_max_favorable_r_120m": number_or_none(
            frame["net_max_favorable_r_120m"].median()
        ),
        "mean_net_max_favorable_r_120m": number_or_none(
            frame["net_max_favorable_r_120m"].mean()
        ),
        "median_net_close_r_120m": number_or_none(
            frame["net_close_r_120m"].median()
        ),
        "mean_net_close_r_120m": number_or_none(
            frame["net_close_r_120m"].mean()
        ),
        "hit_net_1r_before_stop_fraction": float(
            frame["hit_net_1r_before_stop"].mean()
        ),
        "hit_net_2r_before_stop_fraction": float(
            frame["hit_net_2r_before_stop"].mean()
        ),
        "cost_after_1r_bracket_mean_r": number_or_none(float(np.mean(payoff_1r))),
        "cost_after_1r_bracket_median_r": number_or_none(float(np.median(payoff_1r))),
        "cost_after_2r_bracket_mean_r": number_or_none(float(np.mean(payoff_2r))),
        "cost_after_2r_bracket_median_r": number_or_none(float(np.median(payoff_2r))),
        "cost_after_1r_positive_fraction": float((payoff_1r > 0.0).mean()),
        "cost_after_2r_positive_fraction": float((payoff_2r > 0.0).mean()),
    }


def run(*, start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    result = _BASE.run(start=start, end=end, cache=cache, output=output)
    events = pd.read_csv(output / "episodes.csv")
    actionable = events[events["actionable"] == 1]
    selected = events[events["selected_actionable"] == 1]
    result["implementation_correction"] = {
        "gross_r_warrant_invalidated": True,
        "cost_floor_fraction": COST_FLOOR_FRACTION,
        "reason": (
            "Structural pullback risk is frequently narrower than the round-trip cost floor; "
            "gross R hit rates cannot warrant an account."
        ),
    }
    result["cost_after_actionable"] = cost_summary(actionable)
    result["cost_after_selected_actionable"] = cost_summary(selected)
    selected_cost = result["cost_after_selected_actionable"]
    days = int(result["calendar_days"])
    checks = {
        "at_least_five_selected_episodes": int(selected_cost.get("episodes", 0)) >= 5,
        "selected_density_at_least_half_per_day": int(selected_cost.get("episodes", 0)) / days >= 0.5,
        "median_net_max_favorable_r_positive": float(
            selected_cost.get("median_net_max_favorable_r_120m") or -math.inf
        ) > 0.0,
        "cost_after_1r_bracket_positive": float(
            selected_cost.get("cost_after_1r_bracket_mean_r") or -math.inf
        ) > 0.0,
        "cost_after_2r_bracket_positive": float(
            selected_cost.get("cost_after_2r_bracket_mean_r") or -math.inf
        ) > 0.0,
    }
    result["prediction_checks"] = checks
    result["prediction_supported_in_this_window"] = all(checks.values())
    result["execution_backtest"] = False
    result["account_implementation_warranted"] = False
    result["account_not_run_reason"] = (
        "Only cost-after geometry can justify Nautilus. The corrected selected setup must be positive "
        "under both fixed 1R and 2R diagnostics in at least two windows first."
    )
    (output / "SUMMARY.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        cache=args.cache,
        output=args.output,
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
