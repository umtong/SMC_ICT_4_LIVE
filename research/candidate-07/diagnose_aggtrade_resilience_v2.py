#!/usr/bin/env python3
"""Efficient implementation-correct wrapper for aggregate-trade resilience.

Minute/OI history still spans the configured three warm-up days.  Official raw
aggregate trades are needed only for the prior day plus Week-1: older source and
target pools are conservatively marked consumed whenever a completed minute bar
after confirmation reached their level before the aggregate-trade window.  This
prevents stale-pool resurrection while avoiding unnecessary multi-million-row
transfers.  Market logic and thresholds are identical to the baseline module.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

import diagnose_aggtrade_resilience as candidate
import diagnose_impact_resilience_1s as impact
from data_aggtrades_1s import load_aggtrade_1s_bundle
from diagnose_failed_flow import aggregate_flow
from diagnose_impact_resilience_1s_v2 import (
    attach_causal_context_gap_safe,
    deduplicate_contact_pools,
)
from diagnose_session_handoff import _align_positioning
from smc_ict_4.manifest import write_json_atomic


def preconsume_before_event_window(
    pools: Iterable[impact.Pool],
    minute: pd.DataFrame,
    *,
    event_start_ns: int,
) -> tuple[list[impact.Pool], dict[str, int]]:
    """Conservatively remove pools touched before exact event data begins."""
    pool_list = list(pools)
    timestamps = minute["timestamp_ns"].astype("int64")
    highs = minute["high"].astype(float)
    lows = minute["low"].astype(float)
    keep: list[impact.Pool] = []
    consumed = 0
    activated_after_window_start = 0
    for pool in pool_list:
        if pool.confirmed_ts_ns >= event_start_ns:
            keep.append(pool)
            activated_after_window_start += 1
            continue
        eligible = (
            (timestamps > pool.confirmed_ts_ns)
            & (timestamps < event_start_ns)
        )
        touched = bool(
            (highs.loc[eligible] >= pool.level).any()
            if pool.side == "UPPER"
            else (lows.loc[eligible] <= pool.level).any()
        )
        if touched:
            consumed += 1
        else:
            keep.append(pool)
    return keep, {
        "input_pools": len(pool_list),
        "pre_event_consumed": consumed,
        "activated_at_or_after_event_start": activated_after_window_start,
        "retained": len(keep),
    }


def run(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    logic = impact.ImpactLogic()
    logic.validate()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    positioning_warmup = int(config["warmup_days"])
    if not 1 <= args.event_warmup_days <= positioning_warmup:
        raise ValueError("event_warmup_days must be in [1, config warmup_days]")

    bundle = load_aggtrade_1s_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        positioning_warmup_days=positioning_warmup,
        event_warmup_days=args.event_warmup_days,
        cache_root=args.data_root.resolve(),
        manifest_destination=output.with_name("aggtrade_resilience_data_manifest.json"),
    )
    minute = impact._minute_features(
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
    event_start_ns = int(bars.iloc[0]["timestamp_ns"])

    one_minute_all = impact._pool_confirmations(
        minute,
        timeframe="1M",
        radius=logic.one_minute_pivot_radius,
    )
    five_minute_all = impact._pool_confirmations(
        five,
        timeframe="5M",
        radius=logic.five_minute_pivot_radius,
    )
    one_minute_pools, one_preconsume = preconsume_before_event_window(
        one_minute_all,
        minute,
        event_start_ns=event_start_ns,
    )
    five_minute_pools, five_preconsume = preconsume_before_event_window(
        five_minute_all,
        minute,
        event_start_ns=event_start_ns,
    )
    source_pools, collision_summary = deduplicate_contact_pools(
        bars,
        five_minute_pools,
    )
    result = candidate.diagnose(
        bars,
        source_pools=source_pools,
        target_pools={"1M": one_minute_pools, "5M": five_minute_pools},
        trade_start_ns=impact._utc_ns(args.start),
        trade_end_ns=impact._utc_ns(args.end),
        max_hold_seconds=int(config["max_hold_minutes"]) * 60,
        logic=logic,
        require_oi_release=not args.ablate_oi_release,
    )
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "variant": "ablation_no_oi_release" if args.ablate_oi_release else "baseline",
        "hypothesis": "aggregate-trade impact resilience after liquidity attack",
        "implementation_revision": "minute-preconsumed pools plus one-day raw event warmup",
        "period": {
            "start": args.start.isoformat(),
            "end_exclusive": args.end.isoformat(),
        },
        "logic": {name: getattr(logic, name) for name in logic.__dataclass_fields__},
        "data_contract": {
            "flow_and_price": "checksum-verified Binance USD-M aggTrades reduced to completed one-second bars",
            "aggressor_side": "buyer-maker false is aggressive buy; true is aggressive sell",
            "missing_second_policy": "not synthesized; candidate event requires exact one-second continuity",
            "positioning": "completed five-minute OI joined backward-as-of; gaps invalidate",
            "pre_event_consumption": "completed minute high/low conservatively removes old pools before raw event window",
            "raw_event_warmup_days": args.event_warmup_days,
            "contact_pool": "five-minute pivot confirmed after two completed right-side bars",
            "target_hierarchy": "unconsumed, confirmed one-minute then five-minute pools",
            "pool_reuse": False,
            "single_pending_or_open_slot": True,
            "future_information": False,
            "orders_or_pnl": False,
        },
        "loader_diagnostics": bundle.diagnostics,
        "preconsumption": {
            "one_minute": one_preconsume,
            "five_minute": five_preconsume,
        },
        "pool_collision_summary": collision_summary,
        **result,
    }
    write_json_atomic(output, payload)
    print(json.dumps({
        "variant": payload["variant"],
        "summary": payload["summary"],
        "loader_diagnostics": payload["loader_diagnostics"],
        "preconsumption": payload["preconsumption"],
        "pool_collision_summary": payload["pool_collision_summary"],
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
    parser.add_argument("--event-warmup-days", type=int, default=1)
    parser.add_argument("--ablate-oi-release", action="store_true")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
