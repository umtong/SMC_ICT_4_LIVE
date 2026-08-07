#!/usr/bin/env python3
"""Fail a candidate replay when accounting and position lifecycles diverge."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)
    return pd.read_csv(path)


def validate(root: Path) -> dict:
    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    fills = _read_csv(root / "fills.csv")
    positions = _read_csv(root / "positions.csv")
    trades = _read_csv(root / "trades.csv")

    duplicate_opening_counts = (
        positions.groupby("opening_order_id", dropna=False).size()
        if not positions.empty
        else pd.Series(dtype="int64")
    )
    duplicate_openings = {
        str(order_id): int(count)
        for order_id, count in duplicate_opening_counts.items()
        if int(count) != 1
    }
    parent_fills = fills[
        fills["parent_order_id"].isna()
        & fills["contingency_type"].astype(str).eq("OTO")
    ]
    nav_change = float(metrics["final_nav"]) - float(metrics["initial_nav"])
    attributed_trade_pnl = (
        float(trades["net_pnl"].sum()) if not trades.empty else 0.0
    )
    pnl_gap = nav_change - attributed_trade_pnl
    tolerance = max(0.01, abs(nav_change) * 1e-9)

    checks = {
        "metrics_trade_count_matches_trade_rows": (
            int(metrics["trades"]) == int(len(trades.index))
        ),
        "one_parent_fill_per_recorded_trade": (
            int(len(parent_fills.index)) == int(len(trades.index))
        ),
        "one_position_lifecycle_per_recorded_trade": (
            int(len(positions.index)) == int(len(trades.index))
        ),
        "one_position_lifecycle_per_opening_order": not duplicate_openings,
        "all_positions_flat": (
            positions.empty
            or (
                positions["side"].astype(str).eq("FLAT").all()
                and pd.to_numeric(
                    positions["quantity"], errors="coerce"
                ).fillna(0.0).abs().le(1e-12).all()
            )
        ),
        "all_positions_closed": (
            positions.empty or positions["ts_closed"].notna().all()
        ),
        "nav_change_fully_attributed_to_recorded_trades": (
            abs(pnl_gap) <= tolerance
        ),
        "single_slot_metric_true": bool(
            metrics.get("risk_contract", {}).get("single_slot_enforced", False)
        ),
    }
    checks = {name: bool(value) for name, value in checks.items()}
    payload = {
        "checks": checks,
        "passed": all(checks.values()),
        "metrics_trades": int(metrics["trades"]),
        "parent_fill_rows": int(len(parent_fills.index)),
        "position_rows": int(len(positions.index)),
        "trade_rows": int(len(trades.index)),
        "duplicate_opening_order_counts": duplicate_openings,
        "nav_change": nav_change,
        "attributed_trade_pnl": attributed_trade_pnl,
        "pnl_attribution_gap": pnl_gap,
        "pnl_tolerance": tolerance,
    }
    (root / "execution_invariants.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not payload["passed"]:
        raise RuntimeError(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.output), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
