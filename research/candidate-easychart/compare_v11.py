#!/usr/bin/env python3
"""Compare declared three-role confluence variants."""
from __future__ import annotations
import argparse, json
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--variants", nargs="+", required=True)
    a = p.parse_args()
    rows = []
    for variant in a.variants:
        m = json.loads((a.root / variant / "metrics.json").read_text())
        rows.append({
            "variant": variant,
            "geometric_daily_growth": m["geometric_daily_growth"],
            "total_return": m["total_return"], "trades": m["trades"],
            "trades_per_day": m["trades"] / m["calendar_days"],
            "win_rate": m["win_rate"], "profit_factor": m["profit_factor"],
            "mean_gross_r": m["mean_gross_r"], "mean_cost_r": m["mean_cost_r"],
            "mean_net_r": m["mean_net_r"], "max_drawdown": m["max_drawdown"],
            "largest_winner_share": m["largest_winner_share"],
            "target_gate_passed": m["target_gate"]["passed"],
            "raw_setups_generated": m["raw_setups_generated"],
            "setups_generated": m["setups_generated"],
            "family_metrics": m["family_metrics"], "symbol_metrics": m["symbol_metrics"],
            "diagnostics": m["diagnostics"],
            "confluence_diagnostics": m["confluence_diagnostics"],
        })
    ranked = sorted(rows, key=lambda r:(r["target_gate_passed"],r["geometric_daily_growth"],r["mean_net_r"],r["trades_per_day"]), reverse=True)
    positive = [r for r in ranked if r["geometric_daily_growth"] > 0]
    out = {
        "schema":"candidate-easychart-v11-comparison", "authoritative":False,
        "ranking":[r["variant"] for r in ranked],
        "screen_leader": ranked[0]["variant"] if ranked else None,
        "positive_variants":[r["variant"] for r in positive],
        "classification": "TARGET_GATE_PASSED_DIAGNOSTIC_REQUIRES_NAUTILUS" if any(r["target_gate_passed"] for r in rows) else "POSITIVE_VARIANT_REQUIRES_CROSS_WINDOW_AND_NAUTILUS" if positive else "ALL_DECLARED_V11_VARIANTS_FAILED_THIS_DEVELOPMENT_WINDOW",
        "variants": rows,
    }
    (a.root / "comparison.json").write_text(json.dumps(out, indent=2, sort_keys=True)+"\n")
    print(json.dumps(out, indent=2, sort_keys=True))

if __name__ == "__main__": main()
