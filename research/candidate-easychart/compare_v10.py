#!/usr/bin/env python3
"""Compare cross-sectional v10 session states."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--variants", nargs="+", required=True)
    args = parser.parse_args()
    rows = []
    for variant in args.variants:
        payload = json.loads((args.root / variant / "metrics.json").read_text(encoding="utf-8"))
        rows.append(
            {
                "variant": variant,
                "session_families": payload["session_families"],
                "cross_state": payload["cross_state"],
                "geometric_daily_growth": payload["geometric_daily_growth"],
                "total_return": payload["total_return"],
                "trades": payload["trades"],
                "trades_per_day": payload["trades"] / payload["calendar_days"],
                "win_rate": payload["win_rate"],
                "profit_factor": payload["profit_factor"],
                "mean_gross_r": payload["mean_gross_r"],
                "mean_cost_r": payload["mean_cost_r"],
                "mean_net_r": payload["mean_net_r"],
                "max_drawdown": payload["max_drawdown"],
                "largest_winner_share": payload["largest_winner_share"],
                "open_position_at_end": payload["open_position_at_end"],
                "target_gate_passed": payload["target_gate"]["passed"],
                "raw_setups_generated": payload["raw_setups_generated"],
                "setups_generated": payload["setups_generated"],
                "family_metrics": payload["family_metrics"],
                "symbol_metrics": payload["symbol_metrics"],
                "diagnostics": payload["diagnostics"],
                "source_diagnostics": payload["source_diagnostics"],
                "routing_diagnostics": payload["routing_diagnostics"],
            },
        )
    ranked = sorted(
        rows,
        key=lambda row: (
            bool(row["target_gate_passed"]),
            float(row["geometric_daily_growth"]),
            float(row["mean_net_r"]),
            float(row["trades_per_day"]),
        ),
        reverse=True,
    )
    positive = [row for row in ranked if float(row["geometric_daily_growth"]) > 0.0]
    decision = {
        "schema": "candidate-easychart-v10-comparison",
        "authoritative": False,
        "ranking": [row["variant"] for row in ranked],
        "screen_leader": ranked[0]["variant"] if ranked else None,
        "positive_variants": [row["variant"] for row in positive],
        "classification": (
            "TARGET_GATE_PASSED_DIAGNOSTIC_REQUIRES_NAUTILUS"
            if any(row["target_gate_passed"] for row in rows)
            else "POSITIVE_VARIANT_REQUIRES_CROSS_WINDOW_AND_NAUTILUS"
            if positive
            else "ALL_DECLARED_V10_VARIANTS_FAILED_THIS_DEVELOPMENT_WINDOW"
        ),
        "variants": rows,
    }
    (args.root / "comparison.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(decision, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
