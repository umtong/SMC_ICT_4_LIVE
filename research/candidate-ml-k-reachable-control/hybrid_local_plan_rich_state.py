#!/usr/bin/env python3
"""Bind local-frontier plans to the latest public rich auction state.

The liquidity-episode generator has cleaner entry/stop/local-target geometry,
while the candidate-1k generator contains the richer price-volume, structure and
liquidity-state description.  This join keeps the former plan and outcome, then
adds only decision-time fields from a candidate-1k state observed no later than
the plan decision.  It is therefore a true research synthesis rather than a
post-outcome merge.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd

PERIOD_RE = re.compile(r"(?:dev|fresh)-\d{4}-[a-z0-9-]+", re.I)
REQUIRED = {"action_id", "state_id", "episode_id", "order_time_ns"}
CATEGORICAL = (
    "auction_phase", "setup_kind", "location_kind", "source_pool_kind",
    "narrative_branch",
)
ALLOWED_PREFIXES = (
    "approach_", "arm_", "auction_", "confirmation_", "departure_",
    "directional_gap_", "event_", "liquidity_", "relative_", "route_",
    "semantic_", "sequence_block_", "source_", "structure_", "volume_route_",
    "vwap_", "dealing_range_",
)
ALLOWED_EXACT = {
    "order_block_present", "zone_width_bps", "source_confluence_count",
}
FORBIDDEN = (
    "outcome", "fill", "resolved", "target_first", "win", "net_r", "mfe",
    "mae", "holding", "entry_wait", "actual_", "future_", "realized",
    "diagnostic", "oracle", "label", "post_decision", "terminal",
    "resolution", "order_time", "fill_time", "event_time", "departure_time",
    "interaction_time", "emission_time",
)
ABSOLUTE = {
    "entry", "stop", "target", "price", "open", "high", "low", "close",
    "route_price", "zone_lower", "zone_upper", "event_extreme",
    "pullback_extreme", "symbol", "action_id", "state_id", "episode_id",
}


def period_from(path: Path) -> str:
    for part in reversed(path.parts):
        match = PERIOD_RE.search(part)
        if match:
            return match.group(0)
    return path.parent.name


def bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.fillna(False).astype(str).str.lower().isin({"true", "1", "yes"})


def read_csv(path: Path) -> pd.DataFrame | None:
    try:
        frame = pd.read_csv(path, low_memory=False)
    except Exception:
        return None
    return frame if REQUIRED.issubset(frame.columns) else None


def safe_rich_columns(frame: pd.DataFrame) -> list[str]:
    columns: list[str] = []
    for column in frame.columns:
        low = column.lower()
        if column in ABSOLUTE or any(token in low for token in FORBIDDEN):
            continue
        if low.endswith("_index") or low.endswith("_time_ns"):
            continue
        if column in CATEGORICAL:
            columns.append(column)
            continue
        if column not in ALLOWED_EXACT and not column.startswith(ALLOWED_PREFIXES):
            continue
        if pd.api.types.is_numeric_dtype(frame[column]):
            columns.append(column)
    return sorted(set(columns))


def load_world(root: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(root.rglob("departure_actions.csv.gz")):
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty or not REQUIRED.issubset(frame.columns):
            continue
        frame = frame.copy()
        frame["period"] = period_from(path)
        if "order_exists" in frame:
            frame = frame[bool_series(frame.order_exists)]
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"No local-frontier action file below {root}")
    out = pd.concat(frames, ignore_index=True, sort=False)
    out["order_time_ns"] = pd.to_numeric(out.order_time_ns, errors="coerce")
    out = out[out.order_time_ns.notna()].copy()
    out["order_time_ns"] = out.order_time_ns.astype(np.int64)
    return out.drop_duplicates("action_id", keep="last").reset_index(drop=True)


def load_rich(root: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(root.rglob("*.csv")):
        frame = read_csv(path)
        if frame is None or frame.empty:
            continue
        frame = frame.copy()
        frame["period"] = period_from(path)
        if "order_exists" in frame:
            frame = frame[bool_series(frame.order_exists)]
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"No rich action file below {root}")
    out = pd.concat(frames, ignore_index=True, sort=False)
    out["order_time_ns"] = pd.to_numeric(out.order_time_ns, errors="coerce")
    out = out[out.order_time_ns.notna()].copy()
    out["order_time_ns"] = out.order_time_ns.astype(np.int64)
    out = out.drop_duplicates("action_id", keep="last").reset_index(drop=True)
    return out


def side_series(frame: pd.DataFrame) -> pd.Series:
    if "side" in frame:
        return frame.side.fillna("UNKNOWN").astype(str).str.upper()
    return pd.Series("UNKNOWN", index=frame.index)


def enrich_period(world: pd.DataFrame, rich: pd.DataFrame) -> pd.DataFrame:
    world = world.copy()
    rich = rich.copy()
    world["__side"] = side_series(world)
    rich["__side"] = side_series(rich)
    safe = safe_rich_columns(rich)
    keep = ["period", "symbol", "__side", "order_time_ns", *safe]
    keep = [column for column in keep if column in rich]
    rich = rich[keep].copy()

    # Prefer the most structurally mature simultaneous state without using any
    # outcome field.  Duplicates are then removed before backward as-of joining.
    quality_columns = [
        column for column in (
            "auction_progress_r", "auction_path_efficiency",
            "confirmation_impact_per_activity", "source_confluence_count",
        ) if column in rich
    ]
    if quality_columns:
        quality = sum(
            pd.to_numeric(rich[column], errors="coerce").fillna(0.0)
            for column in quality_columns
        )
        rich["__quality"] = quality
    else:
        rich["__quality"] = 0.0
    rich = rich.sort_values(
        ["period", "symbol", "__side", "order_time_ns", "__quality"],
        ascending=[True, True, True, True, False],
    ).drop_duplicates(["period", "symbol", "__side", "order_time_ns"], keep="first")

    pieces: list[pd.DataFrame] = []
    for key, plans in world.groupby(["period", "symbol", "__side"], dropna=False, sort=False):
        period, symbol, side = key
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
        states = states.sort_values("order_time_ns")
        rename = {
            column: f"__rich__{column}"
            for column in safe
            if column in states and column not in {"period", "symbol", "__side"}
        }
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
        age = (
            pd.to_numeric(merged.order_time_ns, errors="coerce")
            - pd.to_numeric(merged.__rich_time_ns, errors="coerce")
        ) / 60_000_000_000.0
        merged["source_context_age_minutes"] = age
        merged["rich_state_available"] = merged.__rich_time_ns.notna().astype(float)
        for original, renamed in rename.items():
            if original in CATEGORICAL:
                current = (
                    merged[original].fillna("UNKNOWN").astype(str)
                    if original in merged else pd.Series("UNKNOWN", index=merged.index)
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
        drop = [column for column in merged if column.startswith("__rich__")]
        drop.extend([column for column in ("__rich_time_ns", "__quality") if column in merged])
        merged = merged.drop(columns=drop, errors="ignore")
        pieces.append(merged)
    out = pd.concat(pieces, ignore_index=True, sort=False)
    return out.drop(columns=["__side"], errors="ignore")


def run(world_root: Path, rich_root: Path, output: Path) -> dict[str, Any]:
    world = load_world(world_root)
    rich = load_rich(rich_root)
    pieces: list[pd.DataFrame] = []
    periods = sorted(set(world.period.astype(str)) & set(rich.period.astype(str)))
    for period in periods:
        pieces.append(
            enrich_period(
                world[world.period.astype(str).eq(period)],
                rich[rich.period.astype(str).eq(period)],
            )
        )
    if not pieces:
        raise RuntimeError("No common causal period between local plans and rich states")
    hybrid = pd.concat(pieces, ignore_index=True, sort=False)
    output.mkdir(parents=True, exist_ok=True)
    for period, frame in hybrid.groupby("period", sort=True):
        target = output / str(period)
        target.mkdir(parents=True, exist_ok=True)
        frame.to_csv(target / "hybrid_actions.csv", index=False)
    matched = pd.to_numeric(hybrid.rich_state_available, errors="coerce").fillna(0.0)
    summary = {
        "periods": periods,
        "world_plans": int(len(world)),
        "hybrid_plans": int(len(hybrid)),
        "rich_state_match_rate": float(matched.mean()) if len(hybrid) else 0.0,
        "uses_future_rich_state": False,
        "maximum_context_age_minutes": float(
            pd.to_numeric(hybrid.source_context_age_minutes, errors="coerce").max()
        ) if len(hybrid) else None,
        "plan_geometry_owner": "causal-liquidity-episode local completion frontier",
        "market_state_owner": "candidate-1k rich causal action state",
    }
    (output / "join_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world-root", type=Path, required=True)
    parser.add_argument("--rich-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.world_root, args.rich_root, args.output)


if __name__ == "__main__":
    main()
