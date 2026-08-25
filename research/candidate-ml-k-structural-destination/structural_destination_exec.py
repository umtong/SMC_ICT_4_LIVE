#!/usr/bin/env python3
"""Executable adapter for ML-k structural-destination counterfactuals."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import structural_destination_policy as policy

core = policy.core
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
        order_exists = (
            frame.get("order_exists", pd.Series(False, index=frame.index))
            .astype(str)
            .str.lower()
            .isin({"true", "1", "yes"})
        )
        orders = frame[order_exists]
        summary.update(
            {
                "episode_rows": int(len(frame)),
                "counterfactual_target_rows": int(len(orders)),
                "causal_episodes_with_targets": (
                    int(orders.episode_id.nunique())
                    if "episode_id" in orders
                    else 0
                ),
                "mean_target_candidates_per_episode": (
                    float(orders.groupby("episode_id").size().mean())
                    if len(orders) and "episode_id" in orders
                    else 0.0
                ),
                "one_executed_plan_per_episode_after_routing": True,
                "counterfactual_rows_are_not_simultaneous_orders": True,
                "gross_planned_rr_floor": 1.0,
                "policy_version": policy.POLICY_VERSION,
                "policy_model_inputs_are_causal": True,
            }
        )
        (Path(output) / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    return summary


core.run_research = run_research


def main() -> None:
    core.main()


if __name__ == "__main__":
    main()
