#!/usr/bin/env python3
"""Corrected execution wrapper for the local-plan/rich-state synthesis."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import hybrid_local_plan_rich_state as core  # noqa: E402


def enrich_period_v2(world: pd.DataFrame, rich: pd.DataFrame) -> pd.DataFrame:
    world = world.copy()
    rich = rich.copy()
    world["__side"] = core.side_series(world)
    rich["__side"] = core.side_series(rich)
    safe = core.safe_rich_columns(rich)
    rich["__quality"] = 0.0
    for column in (
        "auction_progress_r", "auction_path_efficiency",
        "confirmation_impact_per_activity", "source_confluence_count",
    ):
        if column in rich:
            rich["__quality"] += pd.to_numeric(rich[column], errors="coerce").fillna(0.0)
    rich = rich.sort_values(
        ["period", "symbol", "__side", "order_time_ns", "__quality"],
        ascending=[True, True, True, True, False],
    ).drop_duplicates(["period", "symbol", "__side", "order_time_ns"], keep="first")

    pieces: list[pd.DataFrame] = []
    for (period, symbol, side), plans in world.groupby(
        ["period", "symbol", "__side"], dropna=False, sort=False
    ):
        states = rich[
            rich.period.astype(str).eq(str(period))
            & rich.symbol.astype(str).eq(str(symbol))
            & rich.__side.astype(str).eq(str(side))
        ].copy()
        plans = plans.sort_values("order_time_ns").copy()
        if states.empty:
            plans["source_context_age_minutes"] = np.nan
            plans["rich_state_available"] = 0.0
            pieces.append(plans)
            continue

        state_columns = ["order_time_ns", *[column for column in safe if column in states]]
        states = states[state_columns].copy().sort_values("order_time_ns")
        rename = {column: f"__rich__{column}" for column in safe if column in states}
        states = states.rename(columns=rename)
        states = states.rename(columns={"order_time_ns": "__rich_time_ns"})
        merged = pd.merge_asof(
            plans,
            states,
            left_on="order_time_ns",
            right_on="__rich_time_ns",
            direction="backward",
            tolerance=12 * 60 * 1_000_000_000,
        )
        merged["source_context_age_minutes"] = (
            pd.to_numeric(merged.order_time_ns, errors="coerce")
            - pd.to_numeric(merged.__rich_time_ns, errors="coerce")
        ) / 60_000_000_000.0
        merged["rich_state_available"] = merged.__rich_time_ns.notna().astype(float)
        for original, renamed in rename.items():
            if original in core.CATEGORICAL:
                current = (
                    merged[original].fillna("UNKNOWN").astype(str)
                    if original in merged
                    else pd.Series("UNKNOWN", index=merged.index)
                )
                candidate = merged[renamed].fillna("UNKNOWN").astype(str)
                merged[original] = np.where(candidate.ne("UNKNOWN"), candidate, current)
            else:
                candidate = pd.to_numeric(merged[renamed], errors="coerce")
                if original in merged:
                    current = pd.to_numeric(merged[original], errors="coerce")
                    merged[original] = candidate.where(candidate.notna(), current)
                else:
                    merged[original] = candidate
        merged = merged.drop(
            columns=[
                *[column for column in merged if column.startswith("__rich__")],
                "__rich_time_ns",
            ],
            errors="ignore",
        )
        pieces.append(merged)
    return pd.concat(pieces, ignore_index=True, sort=False).drop(
        columns=["__side"], errors="ignore"
    )


core.enrich_period = enrich_period_v2


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world-root", type=Path, required=True)
    parser.add_argument("--rich-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    core.run(args.world_root, args.rich_root, args.output)


if __name__ == "__main__":
    main()
