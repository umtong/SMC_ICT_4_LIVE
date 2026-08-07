#!/usr/bin/env python3
"""One-shot causal funnel diagnosis for the frozen v40 first week.

This script does not create orders or change candidate conditions. It replays
the identical completed-minute state machine and counts where a two-peer active
delivery state fails to become an executable laggard plan.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

import cross_asset_laggard_v39 as base  # noqa: E402
import cross_asset_laggard_v39_quantity_fix as quantity_fix  # noqa: E402
import persistent_cross_asset_delivery_v40 as v40  # noqa: E402
from aggtrade_data import download_aggtrade_days  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from resolved_impact_v17_nautilus_week import atomic_json, load_execution  # noqa: E402


def diagnose(args: argparse.Namespace) -> dict[str, Any]:
    quantity_fix.install_period_quantity_specs()
    execution = load_execution(args.execution_config)
    start = parse_utc_date(args.week)
    end = start + timedelta(days=7)
    context = start - timedelta(minutes=base.CONTEXT_MINUTES)
    download_end = end + timedelta(minutes=1)
    context_ns = int(pd.Timestamp(context).as_unit("ns").value)
    start_ns = int(pd.Timestamp(start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(end).as_unit("ns").value)
    download_end_ns = int(pd.Timestamp(download_end).as_unit("ns").value)

    frames: dict[str, pd.DataFrame] = {}
    for symbol in base.SYMBOLS:
        records = download_aggtrade_days(
            symbol=symbol,
            start=context,
            end=download_end,
            cache_dir=args.cache,
            workers=args.workers,
        )
        raw = base.aggregate_trade_minutes(
            records,
            start_ns=context_ns,
            end_ns=download_end_ns,
        )
        frames[symbol] = base.add_causal_features(raw)

    common = set.intersection(*({int(value) for value in frame.index} for frame in frames.values()))
    active: dict[str, v40.LeaderDeliveryState] = {}
    counts: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    cost = execution.all_in_cost_bps_per_side / 10_000.0

    required = (
        "external_high", "external_low", "internal_high", "internal_low",
        "range_median_bps", "return_bps", "range_bps", "flow_imbalance",
    )
    for minute_start_ns in sorted(common):
        signal_time_ns = base._minute_end_ns(minute_start_ns)
        rows = {symbol: frames[symbol].loc[minute_start_ns] for symbol in base.SYMBOLS}
        for symbol in tuple(active):
            if v40._state_invalidated(active[symbol], rows[symbol]):
                del active[symbol]
                counts["leader_state_invalidations"] += 1
        for symbol, row in rows.items():
            side = base._leader_side(row)
            if side is not None:
                active[symbol] = v40._arm_state(
                    symbol=symbol,
                    side=side,
                    row=row,
                    signal_time_ns=signal_time_ns,
                )
                counts["leader_state_arms_or_refreshes"] += 1
        if not start_ns <= signal_time_ns < end_ns:
            continue
        counts["joint_evaluation_minutes"] += 1

        for side in (Side.LONG, Side.SHORT):
            available = tuple(
                symbol for symbol in base.SYMBOLS
                if symbol in active and active[symbol].side is side
            )
            if len(available) >= 2 and base.CORE_LEADERS.intersection(available):
                counts[f"{side.value.lower()}_two_peer_consensus_minutes"] += 1
            else:
                continue
            for symbol in base.SYMBOLS:
                peers = tuple(item for item in available if item != symbol)
                if len(peers) < 2 or not base.CORE_LEADERS.intersection(peers):
                    continue
                counts["peer_consensus_symbol_checks"] += 1
                row = rows[symbol]
                if any(pd.isna(row[name]) for name in required):
                    counts["reject_missing_feature"] += 1
                    continue
                counts["feature_ready"] += 1

                external_high = float(row["external_high"])
                external_low = float(row["external_low"])
                internal_high = float(row["internal_high"])
                internal_low = float(row["internal_low"])
                close = float(row["close"])
                high = float(row["high"])
                low = float(row["low"])
                flow = float(row["flow_imbalance"])
                return_bps = float(row["return_bps"])
                range_bps = float(row["range_bps"])
                median_range = float(row["range_median_bps"])
                location = float(row["close_location"])

                target_unconsumed = high < external_high if side is Side.LONG else low > external_low
                if not target_unconsumed:
                    counts["reject_hourly_target_consumed"] += 1
                    continue
                counts["hourly_target_unconsumed"] += 1

                internal_break = close > internal_high if side is Side.LONG else close < internal_low
                if not internal_break:
                    counts["reject_no_aligned_internal_break"] += 1
                    continue
                counts["aligned_internal_break"] += 1

                signed_return = return_bps > 0.0 if side is Side.LONG else return_bps < 0.0
                if not signed_return:
                    counts["reject_return_not_aligned"] += 1
                    continue
                counts["return_aligned"] += 1

                aligned_flow = flow > 0.0 if side is Side.LONG else flow < 0.0
                if not aligned_flow:
                    counts["reject_flow_not_aligned"] += 1
                    continue
                counts["flow_aligned"] += 1

                close_ok = location >= 0.5 if side is Side.LONG else location <= 0.5
                if not close_ok:
                    counts["reject_close_location"] += 1
                    continue
                counts["close_location_aligned"] += 1

                if range_bps < median_range:
                    counts["reject_no_range_expansion"] += 1
                    continue
                counts["range_expanded"] += 1

                if side is Side.LONG:
                    stop = min(low, internal_low) * (1.0 - base.STOP_BUFFER_FRACTION)
                    target = external_high
                else:
                    stop = max(high, internal_high) * (1.0 + base.STOP_BUFFER_FRACTION)
                    target = external_low
                geometry = base._execution_geometry(
                    side=side,
                    entry=close,
                    stop=stop,
                    target=target,
                    cost_fraction_per_side=cost,
                )
                if geometry is None:
                    counts["reject_invalid_or_nonpositive_geometry"] += 1
                    continue
                counts["positive_geometry"] += 1
                price_fraction, net_rr = geometry
                if price_fraction < execution.minimum_price_risk_fraction:
                    counts["reject_cost_dominated"] += 1
                    continue
                counts["price_risk_share_passed"] += 1
                if net_rr < execution.minimum_net_reward_risk:
                    counts["reject_insufficient_net_reward_risk"] += 1
                    if len(examples) < 20:
                        examples.append({
                            "symbol": symbol,
                            "side": side.value,
                            "signal_time_ns": signal_time_ns,
                            "peers": list(peers),
                            "entry": close,
                            "stop": stop,
                            "target": target,
                            "price_risk_fraction": price_fraction,
                            "net_reward_risk": net_rr,
                            "reason": "INSUFFICIENT_NET_REWARD_RISK",
                        })
                    continue
                counts["fully_executable_signal_geometry"] += 1

    return {
        "candidate": "v40 persistent cross-asset delivery state",
        "week": args.week,
        "diagnostic_only": True,
        "strategy_changed": False,
        "counts": dict(counts),
        "near_geometry_examples": examples,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument(
        "--execution-config",
        type=Path,
        default=HERE / "nautilus_execution.json",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-v40",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v40-funnel.json",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    payload = diagnose(args)
    atomic_json(args.output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
