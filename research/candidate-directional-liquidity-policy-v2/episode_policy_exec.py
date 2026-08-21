#!/usr/bin/env python3
"""Harvest adapter for the directional-liquidity policy."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import departure_first_return_harvest_fixed as fixed
import directional_liquidity_policy as policy
from metrics_compat import load_range_metrics_sparse

core = fixed.core
# Replace only two inherited extension points.  Source/event generation remains the
# mature point-in-time implementation; plan construction is the new coherent policy.
core.generate_symbol = policy.generate_symbol
core.load_range_metrics = load_range_metrics_sparse
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
        exists = (
            frame.get("order_exists", pd.Series(False, index=frame.index))
            .astype(str).str.lower().isin({"true", "1", "yes"})
        )
        summary.update(
            {
                "episode_rows": int(len(frame)),
                "plans": int(exists.sum()),
                "no_trade_episodes": int((~exists).sum()),
                "states": int(frame.state_id.nunique()) if "state_id" in frame else 0,
                "episodes": int(frame.episode_id.nunique()) if "episode_id" in frame else 0,
                "policy_version": "directional-liquidity-policy-v2",
                "direction_is_first_class": True,
                "one_plan_per_episode": True,
                "market_wide_episode_clustering_is_router_responsibility": True,
                "fixed_rr_target_lattice": False,
                "target_selected_before_rr": True,
                "fitted_admission_model": False,
                "symbol_identity_feature": False,
                "policy_model_inputs_are_causal": True,
                "optional_metrics_are_sparse_safe": True,
            }
        )
        (Path(output) / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    return summary


core.run_research = run_research

if __name__ == "__main__":
    core.main()
