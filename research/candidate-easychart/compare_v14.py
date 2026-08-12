#!/usr/bin/env python3
"""Compare generic delayed reclaims with source-shaped v14 semantics."""
from __future__ import annotations

from collections import Counter
import argparse
import json
from pathlib import Path

import pandas as pd


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size <= 1:
        return pd.DataFrame()
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return pd.DataFrame()
    return pd.read_csv(path)


def read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def summarize_variant(root: Path, name: str) -> dict[str, object]:
    metrics = json.loads((root / name / "metrics.json").read_text(encoding="utf-8"))
    setups = read_csv(root / name / "setups.csv")
    trades = read_csv(root / name / "trades.csv")
    by_symbol = (
        trades.groupby("symbol")["net_pnl"].agg(["count", "sum"]).to_dict("index")
        if not trades.empty
        else {}
    )
    largest_trade = None
    if not trades.empty:
        largest = trades.loc[trades["net_pnl"].idxmax()]
        largest_trade = {
            "plan_id": str(largest["plan_id"]),
            "symbol": str(largest["symbol"]),
            "family": str(largest["family"]),
            "net_pnl": float(largest["net_pnl"]),
            "net_r": float(largest["net_r"]),
            "gross_rr": float(largest["gross_rr"]),
        }
        if not setups.empty:
            match = setups[setups["setup_id"].astype(str) == str(largest["plan_id"])]
            if not match.empty:
                largest_trade["source_pool_id"] = str(match.iloc[-1]["source_pool_id"])
                largest_trade["observed_time_ns"] = int(match.iloc[-1]["observed_time_ns"])
    return {
        "metrics": {
            key: metrics.get(key)
            for key in (
                "trades",
                "ending_nav",
                "geometric_daily_growth",
                "profit_factor",
                "win_rate",
                "max_drawdown",
                "largest_winner_share",
                "raw_setups_generated",
                "setups_generated",
            )
        },
        "setups": len(setups),
        "trades": len(trades),
        "by_symbol": by_symbol,
        "families": sorted(set(trades.get("family", pd.Series(dtype=str)).astype(str))),
        "source_pool_ids": sorted(set(setups.get("source_pool_id", pd.Series(dtype=str)).astype(str))),
        "largest_trade": largest_trade,
        "total_net_pnl": float(trades["net_pnl"].sum()) if not trades.empty else 0.0,
    }


def compare(root: Path) -> dict[str, object]:
    variants = {
        name: summarize_variant(root, name)
        for name in ("v10-generic", "v14-integrated")
    }
    audit_rows = read_jsonl(root / "v14-integrated" / "target_router_audit.jsonl")
    generic_pools = set(variants["v10-generic"]["source_pool_ids"])
    pretarget_pools = {str(row["source_pool_id"]) for row in audit_rows}
    final_pools = set(variants["v14-integrated"]["source_pool_ids"])
    largest = variants["v10-generic"]["largest_trade"]
    largest_pool = None if largest is None else largest.get("source_pool_id")
    return {
        "variants": variants,
        "v14_target_router_dispositions": dict(
            Counter(str(row.get("disposition")) for row in audit_rows),
        ),
        "v14_target_router_rows": audit_rows,
        "opportunity_survival": {
            "generic_setup_pools": len(generic_pools),
            "wm_cross_state_pretarget_pools": len(pretarget_pools),
            "final_first_objective_pools": len(final_pools),
            "generic_and_wm_pretarget_overlap": sorted(generic_pools & pretarget_pools),
            "generic_only_pools": sorted(generic_pools - pretarget_pools),
            "wm_pretarget_only_pools": sorted(pretarget_pools - generic_pools),
            "wm_and_final_overlap": sorted(pretarget_pools & final_pools),
        },
        "largest_generic_winner": {
            "trade": largest,
            "survives_wm_and_cross_state": (
                largest_pool is not None and largest_pool in pretarget_pools
            ),
            "survives_first_objective": (
                largest_pool is not None and largest_pool in final_pools
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
