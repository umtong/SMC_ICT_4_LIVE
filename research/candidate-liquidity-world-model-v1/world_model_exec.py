#!/usr/bin/env python3
"""Executable adapter for the liquidity world-model candidate.

The mature V5 loader remains responsible for point-in-time price, volume,
derivatives, common-market state and semantic liquidity preparation. This module
replaces only candidate generation with one causal episode and one actual plan.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import departure_first_return_harvest_fixed as fixed
import world_model_policy as policy

core = fixed.core
core.generate_symbol = policy.generate_symbol
_BASE_RUN = core.run_research


def run_research(*, start, end, warmup_days, symbols, cache, output):
    summary = _BASE_RUN(
        start=start,
        end=end,
        warmup_days=warmup_days,
        symbols=symbols,
        cache=cache,
        output=output,
    )
    action_path = Path(output) / "departure_actions.csv.gz"
    if action_path.exists():
        frame = pd.read_csv(action_path, low_memory=False)
        if "order_exists" in frame:
            exists = frame.order_exists.astype(str).str.lower().isin({"true", "1", "yes"})
            summary["episode_rows"] = int(len(frame))
            summary["plans"] = int(exists.sum())
            summary["no_trade_episodes"] = int((~exists).sum())
            summary["states"] = int(frame.state_id.nunique()) if not frame.empty else 0
            summary["episodes"] = int(frame.episode_id.nunique()) if not frame.empty else 0
            summary["one_plan_per_episode"] = True
            summary["fixed_rr_target_lattice"] = False
            summary["target_selected_before_rr"] = True
            (Path(output) / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
    return summary


core.run_research = run_research

if __name__ == "__main__":
    core.main()
