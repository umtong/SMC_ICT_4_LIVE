#!/usr/bin/env python3
"""Diagnose causal profit-lock transitions after failed-auction progress.

The 2024 run contains many positions that first rotate in the expected direction
and later return all the way to the sweep-extreme stop.  A completed auction
rotation should become progressively less tolerant of that return once price
has traversed meaningful risk or range distance.

Each rule below is a state transition, not future-aware optimization:

1. The protective stop active at the start of a bar always has priority.
2. Progress is observed only when the bar completes.
3. A stop modification becomes active on the next bar.
4. Same-bar stop/target ambiguity is resolved against the strategy.
5. Fees are included when translating a desired locked R into a stop price.

The module compares a small set of economically distinct policies on the 2024
development interval and the three frozen quick weeks.  Production remains
unchanged until a rule improves both roles.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from core import AuctionStateMachine, CandidateConfig, Side  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402


POLICIES: dict[str, dict[str, float | bool]] = {
    "baseline": {"activate_r": float("inf"), "lock_r": -1.0, "partial": False},
    "progress-0.50-lock-cost-be": {"activate_r": 0.50, "lock_r": 0.0, "partial": False},
    "progress-0.75-lock-cost-be": {"activate_r": 0.75, "lock_r": 0.0, "partial": False},
    "progress-1.00-lock-0.25r": {"activate_r": 1.00, "lock_r": 0.25, "partial": False},
    "progress-1.50-lock-0.50r": {"activate_r": 1.50, "lock_r": 0.50, "partial": False},
    "partial-half-at-0.75r-lock-be": {"activate_r": 0.75, "lock_r": 0.0, "partial": True},
    "partial-half-at-1.00r-lock-0.25r": {"activate_r": 1.00, "lock_r": 0.25, "partial": True},
}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _segments(research: dict[str, Any]) -> list[tuple[str, datetime, datetime, str]]:
    def week(label: str, value: str) -> tuple[str, datetime, datetime, str]:
        start = parse_utc_date(value)
        return label, start, start + timedelta(days=7), "quick"

    return [
        week("discovery", str(research["discovery_week"])),
        *[
            week(f"confirmation-{index + 1}", value)
            for index, value in enumerate(research["confirmation_weeks"])
        ],
        (
            "long-evaluation",
            parse_utc_date(str(research["long_start"])),
            parse_utc_date(str(research["long_end"])),
            "development",
        ),
    ]


def _plans(bars: list[Any], candidate: CandidateConfig) -> list[Any]:
    machine = AuctionStateMachine(candidate, instrument_id="BTCUSDT-PERP.BINANCE")
    result: list[Any] = []
    for item in bars:
        plan = machine.on_bar(item)
        if plan is not None:
            result.append(plan)
    return result


def _planned_loss(side: Side, entry: float, stop: float, cost: float) -> float:
    del side
    return abs(entry - stop) + entry * cost + stop * cost


def _exit_r(side: Side, entry: float, exit_price: float, stop: float, cost: float) -> float:
    loss = _planned_loss(side, entry, stop, cost)
    gross = (exit_price - entry) * side.sign
    return (gross - entry * cost - exit_price * cost) / loss


def _price_for_net_r(
    side: Side,
    *,
    entry: float,
    original_stop: float,
    cost: float,
    desired_r: float,
) -> float:
    loss = _planned_loss(side, entry, original_stop, cost)
    if side is Side.LONG:
        return (entry * (1.0 + cost) + desired_r * loss) / (1.0 - cost)
    return (entry * (1.0 - cost) - desired_r * loss) / (1.0 + cost)


def _simulate_policy(
    future: list[Any],
    *,
    side: Side,
    entry: float,
    original_stop: float,
    target: float,
    cost: float,
    activate_r: float,
    lock_r: float,
    partial: bool,
) -> dict[str, Any]:
    active_stop = original_stop
    pending_stop: float | None = None
    activated = False
    remaining_fraction = 1.0
    realized_weighted_r = 0.0
    activation_price = _price_for_net_r(
        side,
        entry=entry,
        original_stop=original_stop,
        cost=cost,
        desired_r=activate_r,
    )
    lock_price = _price_for_net_r(
        side,
        entry=entry,
        original_stop=original_stop,
        cost=cost,
        desired_r=lock_r,
    )

    for offset, item in enumerate(future, start=1):
        if pending_stop is not None:
            active_stop = pending_stop
            pending_stop = None

        stop_hit = item.low <= active_stop if side is Side.LONG else item.high >= active_stop
        target_hit = item.high >= target if side is Side.LONG else item.low <= target
        if stop_hit:
            exit_component = _exit_r(side, entry, active_stop, original_stop, cost)
            return {
                "exit_reason": "LOCKED_STOP" if activated else "STOP",
                "bars": offset,
                "exit_price": active_stop,
                "realized_r": realized_weighted_r + remaining_fraction * exit_component,
                "activated": activated,
            }
        if target_hit:
            exit_component = _exit_r(side, entry, target, original_stop, cost)
            return {
                "exit_reason": "TARGET",
                "bars": offset,
                "exit_price": target,
                "realized_r": realized_weighted_r + remaining_fraction * exit_component,
                "activated": activated,
            }

        threshold_hit = (
            item.high >= activation_price if side is Side.LONG else item.low <= activation_price
        )
        if not activated and threshold_hit:
            activated = True
            if partial:
                # Half exits at the observed progress level.  The remaining half
                # receives the new structural lock from the next bar.
                realized_weighted_r += 0.5 * activate_r
                remaining_fraction = 0.5
            pending_stop = lock_price

    if not future:
        exit_price = entry
        bars = 0
    else:
        exit_price = future[-1].close
        bars = len(future)
    exit_component = _exit_r(side, entry, exit_price, original_stop, cost)
    return {
        "exit_reason": "TIME",
        "bars": bars,
        "exit_price": exit_price,
        "realized_r": realized_weighted_r + remaining_fraction * exit_component,
        "activated": activated,
    }


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, Any]] = []

    for label, start, end, role in _segments(research):
        frame, _ = load_interval(
            symbol="BTCUSDT",
            start=start,
            end=end,
            cache_dir=args.cache,
            warmup_minutes=max(int(research.get("warmup_minutes", 420)), candidate.range_minutes + 180),
        )
        bars = to_auction_bars(frame)
        index_by_ts = {item.ts_event_ns: index for index, item in enumerate(bars)}
        start_ns = int(pd.Timestamp(start).value)
        end_ns = int(pd.Timestamp(end).value)
        segment_rows: list[dict[str, Any]] = []
        for plan in _plans(bars, candidate):
            if not start_ns <= plan.signal_time_ns < end_ns:
                continue
            signal_index = index_by_ts.get(plan.signal_time_ns)
            if signal_index is None or signal_index + 1 >= len(bars):
                continue
            entry_index = signal_index + 1
            entry_bar = bars[entry_index]
            if entry_bar.ts_event_ns >= end_ns:
                continue
            entry = entry_bar.close
            geometry_ok = (
                plan.stop_price < entry < plan.target_price
                if plan.side is Side.LONG
                else plan.target_price < entry < plan.stop_price
            )
            if not geometry_ok:
                continue
            price_risk = abs(entry - plan.stop_price)
            total_loss = _planned_loss(plan.side, entry, plan.stop_price, cost)
            gain = abs(plan.target_price - entry) - entry * cost - plan.target_price * cost
            price_fraction = price_risk / total_loss if total_loss > 0.0 else 0.0
            net_rr = gain / total_loss if total_loss > 0.0 else -1.0
            if (
                price_fraction < float(execution["minimum_price_risk_fraction"])
                or net_rr < float(execution["minimum_net_reward_risk"])
            ):
                continue
            future = bars[entry_index + 1 : entry_index + 1 + plan.max_hold_bars]
            base = {
                **asdict(plan),
                "side": plan.side.value,
                "response": plan.response.value,
                "segment": label,
                "role": role,
                "entry_time_ns": entry_bar.ts_event_ns,
                "entry": entry,
                "price_risk_fraction": price_fraction,
                "net_reward_risk_at_entry": net_rr,
            }
            for policy, values in POLICIES.items():
                outcome = _simulate_policy(
                    future,
                    side=plan.side,
                    entry=entry,
                    original_stop=plan.stop_price,
                    target=plan.target_price,
                    cost=cost,
                    activate_r=float(values["activate_r"]),
                    lock_r=float(values["lock_r"]),
                    partial=bool(values["partial"]),
                )
                segment_rows.append({**base, "policy": policy, **outcome})
        segment = pd.DataFrame(segment_rows)
        segment.to_csv(output / f"{label}_trade_management.csv", index=False)
        all_rows.extend(segment_rows)

    result = pd.DataFrame(all_rows)
    result.to_csv(output / "combined_trade_management.csv", index=False)
    summaries: list[dict[str, Any]] = []
    for (role, policy), group in result.groupby(["role", "policy"], sort=True):
        values = pd.to_numeric(group["realized_r"], errors="coerce").dropna()
        gross_profit = float(values[values > 0.0].sum())
        gross_loss = abs(float(values[values < 0.0].sum()))
        summaries.append(
            {
                "role": role,
                "policy": policy,
                "trades": int(len(values)),
                "sum_r": float(values.sum()),
                "mean_r": float(values.mean()) if len(values) else None,
                "win_rate": float((values > 0.0).mean()) if len(values) else None,
                "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
                "activation_rate": float(group["activated"].mean()) if len(group) else None,
                "exit_counts": group["exit_reason"].value_counts().to_dict(),
            },
        )
    summary = {"rows": int(len(result)), "policies": summaries}
    _atomic_json(output / "trade_management_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-trade-management")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-trade-management")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
