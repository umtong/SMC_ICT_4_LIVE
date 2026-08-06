#!/usr/bin/env python3
"""Implementation-corrected one-second impact-resilience diagnostic.

This module changes no market hypothesis, threshold, stop, target or path rule
from ``diagnose_impact_resilience_1s``.  It only closes three implementation
ambiguities before the frozen Week-1 result is interpreted:

1. the one-second history covers the same three warm-up days as minute/OI data;
2. incomplete fifteen-second reference buckets are excluded from the rolling
   flow quantile rather than silently entering it;
3. all five-minute pools first crossed in the same second form one contact
   episode.  Opposite-side collisions are ambiguous and discarded; same-side
   collisions consume every crossed pool but retain only the nearest pool.

The script remains a structural diagnostic and creates no orders, fills, PnL,
cash ledger or NAV.  A passing route must still be implemented and evaluated in
NautilusTrader.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as base
from data_microstructure_1s import load_microstructure_1s_bundle
from diagnose_failed_flow import aggregate_flow
from diagnose_session_handoff import _align_positioning
from smc_ict_4.manifest import write_json_atomic


def attach_causal_context_gap_safe(
    seconds: pd.DataFrame,
    minute: pd.DataFrame,
    five: pd.DataFrame,
    *,
    history_windows: int,
    flow_quantile: float,
) -> pd.DataFrame:
    """Attach only completed, contiguous context to every one-second bar."""
    work = seconds.copy().reset_index(drop=True)
    work["timestamp_ns"] = work["close_time_ns"].astype("int64")
    work = work.sort_values("timestamp_ns", kind="stable").reset_index(drop=True)

    minute_context = minute[["timestamp_ns", "atr"]].dropna().copy()
    minute_context["timestamp_ns"] = minute_context["timestamp_ns"].astype("int64")
    minute_context = minute_context.rename(columns={"timestamp_ns": "atr_available_ns"})
    work = pd.merge_asof(
        work,
        minute_context.sort_values("atr_available_ns"),
        left_on="timestamp_ns",
        right_on="atr_available_ns",
        direction="backward",
        allow_exact_matches=True,
        tolerance=2 * base.NS_PER_MINUTE,
    )

    if "snapshot_ns" not in five.columns:
        raise RuntimeError("aligned five-minute positioning frame lacks snapshot_ns")
    positioning = five[
        [
            "snapshot_ns",
            "positioning_valid",
            "inventory_state",
            "sum_open_interest",
            "oi_change_fraction",
            "oi_impulse_rank",
        ]
    ].copy()
    positioning["snapshot_ns"] = positioning["snapshot_ns"].astype("int64")
    positioning = positioning.sort_values("snapshot_ns", kind="stable")
    work = pd.merge_asof(
        work,
        positioning,
        left_on="timestamp_ns",
        right_on="snapshot_ns",
        direction="backward",
        allow_exact_matches=True,
        tolerance=base.NS_PER_FIVE_MINUTES + base.NS_PER_SECOND,
    )

    work["taker_sell_quote"] = (
        work["quote_volume"] - work["taker_buy_quote"]
    ).clip(lower=0.0)
    work["signed_quote"] = work["taker_buy_quote"] - work["taker_sell_quote"]
    work["bucket_15s"] = work["timestamp_ns"] // base.NS_PER_FIFTEEN_SECONDS
    grouped = work.groupby("bucket_15s", sort=True)
    windows = grouped.agg(
        buy_quote=("taker_buy_quote", "sum"),
        sell_quote=("taker_sell_quote", "sum"),
        count=("timestamp_ns", "count"),
        first_ns=("timestamp_ns", "first"),
        last_ns=("timestamp_ns", "last"),
    ).reset_index()
    complete = (
        (windows["count"] == 15)
        & (windows["last_ns"] - windows["first_ns"] == 14 * base.NS_PER_SECOND)
    )
    windows.loc[~complete, ["buy_quote", "sell_quote"]] = np.nan
    windows["buy_q"] = windows["buy_quote"].shift(1).rolling(
        history_windows,
        min_periods=history_windows,
    ).quantile(flow_quantile)
    windows["sell_q"] = windows["sell_quote"].shift(1).rolling(
        history_windows,
        min_periods=history_windows,
    ).quantile(flow_quantile)
    work = work.merge(
        windows[["bucket_15s", "buy_q", "sell_q", "count"]],
        on="bucket_15s",
        how="left",
        sort=False,
    )
    return work


def deduplicate_contact_pools(
    bars: pd.DataFrame,
    pools: Iterable[base.Pool],
) -> tuple[list[base.Pool], dict[str, int]]:
    """Collapse coincident raw first touches into one causal contact episode."""
    pool_list = list(pools)
    timestamps = bars["timestamp_ns"].astype("int64").to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()
    closes = bars["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]

    by_touch: dict[int, list[base.Pool]] = defaultdict(list)
    no_touch = 0
    for pool in pool_list:
        touch = base._first_touch_index(
            pool,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
        )
        if touch is None:
            no_touch += 1
        else:
            by_touch[int(touch)].append(pool)

    selected: list[base.Pool] = []
    collisions = Counter()
    for touch, touched in sorted(by_touch.items()):
        sides = {pool.side for pool in touched}
        if len(sides) > 1:
            collisions["opposite_side_ambiguous_seconds"] += 1
            collisions["opposite_side_pools_consumed"] += len(touched)
            continue
        if len(touched) > 1:
            collisions["same_side_collision_seconds"] += 1
            collisions["same_side_extra_pools_consumed"] += len(touched) - 1
        anchor = float(previous_close[touch])
        selected.append(min(touched, key=lambda pool: abs(pool.level - anchor)))

    summary = {
        "source_pools": len(pool_list),
        "source_pools_never_touched": no_touch,
        "raw_touch_seconds": len(by_touch),
        "selected_contact_episodes": len(selected),
        **dict(sorted(collisions.items())),
    }
    return selected, summary


def run(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    logic = base.ImpactLogic()
    logic.validate()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    expected_warmup = int(config["warmup_days"])
    if args.micro_warmup_days != expected_warmup:
        raise ValueError(
            "micro_warmup_days must equal config warmup_days so pre-trade pool "
            f"consumption is observable: {args.micro_warmup_days} != {expected_warmup}"
        )
    bundle = load_microstructure_1s_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        positioning_warmup_days=expected_warmup,
        micro_warmup_days=args.micro_warmup_days,
        cache_root=args.data_root.resolve(),
        manifest_destination=output.with_name("impact_resilience_1s_data_manifest.json"),
    )

    minute = base._minute_features(
        bundle.minute_positioning.frame,
        atr_period=logic.minute_atr_period,
    )
    five = aggregate_flow(bundle.minute_positioning.frame, 5, 36)
    five = _align_positioning(
        five,
        bundle.minute_positioning.metrics,
        oi_period=logic.oi_period,
        oi_impulse_rank=logic.oi_impulse_rank,
    )
    five["timestamp_ns"] = five["timestamp_ns"].astype("int64")
    bars = attach_causal_context_gap_safe(
        bundle.seconds,
        minute,
        five,
        history_windows=logic.history_windows,
        flow_quantile=logic.flow_quantile,
    )

    one_minute_pools = base._pool_confirmations(
        minute,
        timeframe="1M",
        radius=logic.one_minute_pivot_radius,
    )
    five_minute_pools = base._pool_confirmations(
        five,
        timeframe="5M",
        radius=logic.five_minute_pivot_radius,
    )
    source_pools, collision_summary = deduplicate_contact_pools(
        bars,
        five_minute_pools,
    )
    result = base.diagnose(
        bars,
        source_pools=source_pools,
        target_pools={"1M": one_minute_pools, "5M": five_minute_pools},
        trade_start_ns=base._utc_ns(args.start),
        trade_end_ns=base._utc_ns(args.end),
        max_hold_seconds=int(config["max_hold_minutes"]) * 60,
        logic=logic,
    )
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "hypothesis": "one-second impact resilience after OI-release liquidity attack",
        "implementation_revision": "gap-safe history and collision-safe first touch",
        "period": {
            "start": args.start.isoformat(),
            "end_exclusive": args.end.isoformat(),
        },
        "logic": {name: getattr(logic, name) for name in logic.__dataclass_fields__},
        "data_contract": {
            "trade_mark_index": "checksum-verified Binance USD-M official one-second klines",
            "positioning": "completed public five-minute OI metrics; gaps invalidate state",
            "micro_warmup_days": args.micro_warmup_days,
            "contact_pool": "five-minute pivot confirmed after two completed right-side bars",
            "event_window": "fixed fifteen completed one-second observations from literal first touch",
            "flow_reference": "prior 120 complete non-overlapping fifteen-second windows only",
            "contact_collision": "one episode per second; opposite-side collisions discarded; nearest same-side pool retained",
            "target_hierarchy": "causally confirmed one-minute then five-minute pools",
            "pool_reuse": False,
            "single_pending_or_open_slot": True,
            "future_information": False,
            "orders_or_pnl": False,
        },
        "cadence_gaps": bundle.cadence_gaps,
        "pool_collision_summary": collision_summary,
        **result,
    }
    write_json_atomic(output, payload)
    print(json.dumps({
        "summary": payload["summary"],
        "cadence_gaps": payload["cadence_gaps"],
        "pool_collision_summary": collision_summary,
    }, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    parser.add_argument("--stage", default="week-1")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path(".research-data/candidate-07"))
    parser.add_argument("--micro-warmup-days", type=int, default=3)
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
