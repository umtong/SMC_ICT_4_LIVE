#!/usr/bin/env python3
"""Chronologically honest final wrapper for rich causal short synthesis."""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import rich_causal_action_router_fast as fast  # noqa: E402,F401
import rich_causal_action_router as core  # noqa: E402


def run(root: Path, output: Path) -> dict:
    actions = core.load_actions(root)
    scored, model_diagnostics = core.score_periods(actions)
    period_days = core.infer_period_days(actions)
    development = scored[scored.role.astype(str).eq("dev")].copy()
    if development.empty:
        raise RuntimeError("No development actions")
    development_end = development.order_time.max()
    final_fresh = scored[
        scored.role.astype(str).eq("fresh")
        & scored.order_time.gt(development_end)
    ].copy()
    interleaved_probe = scored[
        scored.role.astype(str).eq("fresh")
        & ~scored.order_time.gt(development_end)
    ].copy()

    evaluated = []
    subset_results = []
    for families in core.family_subsets():
        _, dev_trades = core.route_account(development, families)
        rank = core.development_rank(dev_trades, period_days)
        subset_results.append(
            {
                "families": sorted(families),
                "development": core.metrics(dev_trades, period_days),
                "development_by_period": core.grouped(dev_trades, "period"),
                "rank": list(rank),
            }
        )
        evaluated.append((rank, families))
    evaluated.sort(key=lambda item: item[0], reverse=True)
    selected_families = evaluated[0][1] if evaluated else set()

    dev_orders, dev_trades = core.route_account(development, selected_families)
    probe_orders, probe_trades = core.route_account(interleaved_probe, selected_families)
    fresh_orders, fresh_trades = core.route_account(final_fresh, selected_families)
    all_orders = pd.concat([dev_orders, probe_orders, fresh_orders], ignore_index=True, sort=False)
    all_trades = pd.concat([dev_trades, probe_trades, fresh_trades], ignore_index=True, sort=False)

    summary = {
        "policy": "ML_K_RICH_CAUSAL_CHRONOLOGICAL_FINAL",
        "selection_uses_final_fresh_outcomes": False,
        "development_end_utc": str(development_end),
        "selected_scenario_families": sorted(selected_families),
        "fixed_account_rules": {
            "risk_fraction": core.RISK,
            "one_global_pending_or_position_slot": True,
            "planned_gross_rr_minimum": 1.0,
            "scale_in_or_out": False,
            "daily_loss_cap": False,
            "forced_post_fill_time_exit": False,
        },
        "implementation_clinic": core.implementation_clinic(actions),
        "model_diagnostics": model_diagnostics,
        "development": core.metrics(dev_trades, period_days),
        "interleaved_probe_not_used_as_final_evidence": core.metrics(probe_trades, period_days),
        "final_fresh": core.metrics(fresh_trades, period_days),
        "development_by_period": core.grouped(dev_trades, "period"),
        "probe_by_period": core.grouped(probe_trades, "period"),
        "final_fresh_by_period": core.grouped(fresh_trades, "period"),
        "final_fresh_by_family": core.grouped(fresh_trades, "scenario_family"),
        "final_fresh_by_symbol": core.grouped(fresh_trades, "symbol"),
        "final_fresh_by_phase": core.grouped(fresh_trades, "auction_phase"),
        "final_fresh_by_geometry": core.grouped(fresh_trades, "geometry_class"),
        "final_fresh_by_rr_band": core.grouped(fresh_trades, "rr_band"),
        "development_subset_search": subset_results,
        "period_days": period_days,
    }
    output.mkdir(parents=True, exist_ok=True)
    scored.to_csv(output / "scored_actions.csv.gz", index=False, compression="gzip")
    all_orders.to_csv(output / "selected_orders.csv", index=False)
    all_trades.to_csv(output / "closed_trades.csv", index=False)
    fresh_trades.to_csv(output / "final_fresh_closed_trades.csv", index=False)
    probe_trades.to_csv(output / "interleaved_probe_trades.csv", index=False)
    losses = fresh_trades[pd.to_numeric(fresh_trades.get("net_r_num"), errors="coerce").lt(0)]
    losses.to_csv(output / "final_fresh_loss_clinic.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.output)


if __name__ == "__main__":
    main()
