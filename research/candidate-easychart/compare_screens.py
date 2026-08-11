#!/usr/bin/env python3
"""Compare a finite, explicitly declared set of EasyChart diagnostics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--variants", nargs="+", required=True)
    args = parser.parse_args()
    rows: list[dict[str, object]] = []
    for variant in args.variants:
        payload = json.loads((args.root / variant / "metrics.json").read_text(encoding="utf-8"))
        rows.append(
            {
                "variant": variant,
                "geometric_daily_growth": payload["geometric_daily_growth"],
                "total_return": payload["total_return"],
                "trades": payload["trades"],
                "trades_per_day": payload["trades"] / payload["calendar_days"],
                "win_rate": payload["win_rate"],
                "profit_factor": payload["profit_factor"],
                "max_drawdown": payload["max_drawdown"],
                "largest_winner_share": payload["largest_winner_share"],
                "open_position_at_end": payload["open_position_at_end"],
                "target_gate_passed": payload["target_gate"]["passed"],
                "plans_generated": payload["plans_generated"],
                "family_metrics": payload["family_metrics"],
                "symbol_metrics": payload["symbol_metrics"],
                "diagnostics": payload["diagnostics"],
            },
        )
    ranked = sorted(
        rows,
        key=lambda row: (
            bool(row["target_gate_passed"]),
            float(row["geometric_daily_growth"]),
            float(row["trades_per_day"]),
        ),
        reverse=True,
    )
    positive = [row for row in ranked if float(row["geometric_daily_growth"]) > 0.0]
    decision = {
        "schema": "candidate-easychart-screen-comparison-v2",
        "authoritative": False,
        "variants": rows,
        "ranking": [row["variant"] for row in ranked],
        "screen_leader": ranked[0]["variant"] if ranked else None,
        "positive_variants": [row["variant"] for row in positive],
        "classification": (
            "TARGET_GATE_PASSED_DIAGNOSTIC_ONLY_REQUIRES_NAUTILUS_PROMOTION"
            if any(bool(row["target_gate_passed"]) for row in rows)
            else "POSITIVE_DIAGNOSTIC_VARIANT_REQUIRES_CAUSAL_ABLATION_AND_NAUTILUS"
            if positive
            else "ALL_DECLARED_VARIANTS_FAILED_THIS_DEVELOPMENT_WINDOW"
        ),
    }
    destination = args.root / "comparison.json"
    destination.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(decision, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
