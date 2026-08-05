#!/usr/bin/env python3
"""Diagnose continuation after a failed failed-auction reversal.

A liquidity probe that re-enters a completed range can justify a responsive
reversal.  If price subsequently closes through the probe extreme with renewed
breakout-direction flow, that reversal thesis is causally invalidated and the
auction has supplied stronger evidence of outside-value acceptance.

This module tests a stop-and-reverse *scenario transition*:

    external probe -> re-entry -> reversal displacement -> reversal stop
    -> close beyond probe extreme with flow -> continuation next bar

The continuation stop is back inside the old range and its target is a measured
fraction of the completed range beyond the boundary.  Every signal uses one
completed-bar delay and 7 bps-per-side stress costs.  Same-bar ambiguity is
resolved against the strategy.
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


PROFILES: dict[str, dict[str, float | int]] = {
    "extreme-flow-0.50-half-range": {
        "extreme_depth_atr": 0.05,
        "minimum_flow_z": 0.50,
        "confirmation_window": 3,
        "target_range_fraction": 0.50,
    },
    "extreme-flow-1.00-half-range": {
        "extreme_depth_atr": 0.05,
        "minimum_flow_z": 1.00,
        "confirmation_window": 3,
        "target_range_fraction": 0.50,
    },
    "extreme-flow-0.50-full-range": {
        "extreme_depth_atr": 0.05,
        "minimum_flow_z": 0.50,
        "confirmation_window": 3,
        "target_range_fraction": 1.00,
    },
    "extreme-price-half-range": {
        "extreme_depth_atr": 0.10,
        "minimum_flow_z": -10.0,
        "confirmation_window": 2,
        "target_range_fraction": 0.50,
    },
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


def _zscore(value: float, history: deque[float]) -> float:
    values = np.asarray(history, dtype=float)
    if len(values) < 20:
        return 0.0
    std = float(values.std())
    if std <= 1e-12:
        return 0.0
    return (value - float(values.mean())) / std


def _features(bars: list[Any], candidate: CandidateConfig) -> pd.DataFrame:
    true_ranges: deque[float] = deque(maxlen=candidate.atr_lookback)
    flow_history: deque[float] = deque(maxlen=candidate.flow_lookback)
    previous_close: float | None = None
    rows: list[dict[str, Any]] = []
    for item in bars:
        atr = float(np.mean(true_ranges)) if len(true_ranges) >= max(20, candidate.atr_lookback // 2) else np.nan
        rows.append(
            {
                "ts_ns": item.ts_event_ns,
                "open": item.open,
                "high": item.high,
                "low": item.low,
                "close": item.close,
                "atr_prior": atr,
                "flow_z_prior": _zscore(item.aggressive_imbalance, flow_history),
            },
        )
        true_range = (
            item.high - item.low
            if previous_close is None
            else max(item.high - item.low, abs(item.high - previous_close), abs(item.low - previous_close))
        )
        true_ranges.append(true_range)
        flow_history.append(item.aggressive_imbalance)
        previous_close = item.close
    return pd.DataFrame(rows)


def _plans(bars: list[Any], candidate: CandidateConfig) -> list[Any]:
    machine = AuctionStateMachine(candidate, instrument_id="BTCUSDT-PERP.BINANCE")
    result: list[Any] = []
    for item in bars:
        plan = machine.on_bar(item)
        if plan is not None:
            result.append(plan)
    return result


def _loss(entry: float, stop: float, cost: float) -> float:
    return abs(entry - stop) + entry * cost + stop * cost


def _net_r(side: Side, entry: float, exit_price: float, stop: float, cost: float) -> float:
    gross = (exit_price - entry) * side.sign
    return (gross - entry * cost - exit_price * cost) / _loss(entry, stop, cost)


def _baseline_stop_index(
    future: pd.DataFrame,
    *,
    side: Side,
    stop: float,
    target: float,
) -> tuple[int | None, str, float]:
    for offset, row in enumerate(future.itertuples(index=False), start=1):
        stop_hit = row.low <= stop if side is Side.LONG else row.high >= stop
        target_hit = row.high >= target if side is Side.LONG else row.low <= target
        if stop_hit:
            return offset, "STOP", stop
        if target_hit:
            return None, "TARGET", target
    if future.empty:
        return None, "TIME", float("nan")
    return None, "TIME", float(future.iloc[-1]["close"])


def _continuation(
    features: pd.DataFrame,
    *,
    stop_offset: int,
    fade_side: Side,
    fade_plan: Any,
    cost: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
    minimum_stop_atr: float,
    profile: dict[str, float | int],
) -> dict[str, Any] | None:
    breakout_side = Side.SHORT if fade_side is Side.LONG else Side.LONG
    breakout_sign = breakout_side.sign
    confirmation_window = int(profile["confirmation_window"])
    confirmation_slice = features.iloc[
        stop_offset - 1 : stop_offset - 1 + confirmation_window
    ]
    confirmation_index: int | None = None
    confirmation_atr: float | None = None
    for local_offset, row in enumerate(confirmation_slice.itertuples(index=True), start=0):
        atr = float(row.atr_prior)
        if not np.isfinite(atr) or atr <= 0.0:
            continue
        extreme_progress = (float(row.close) - fade_plan.sweep_extreme) * breakout_sign
        breakout_flow = float(row.flow_z_prior) * breakout_sign
        if (
            extreme_progress >= float(profile["extreme_depth_atr"]) * atr
            and breakout_flow >= float(profile["minimum_flow_z"])
        ):
            confirmation_index = int(row.Index)
            confirmation_atr = atr
            break
    if confirmation_index is None or confirmation_atr is None:
        return None
    entry_index = confirmation_index + 1
    if entry_index >= len(features):
        return None
    entry_row = features.iloc[entry_index]
    entry = float(entry_row["close"])
    width = fade_plan.anchor_high - fade_plan.anchor_low
    if breakout_side is Side.LONG:
        boundary = fade_plan.anchor_high
        stop = boundary - minimum_stop_atr * confirmation_atr
        target = boundary + float(profile["target_range_fraction"]) * width
        geometry_ok = stop < entry < target
    else:
        boundary = fade_plan.anchor_low
        stop = boundary + minimum_stop_atr * confirmation_atr
        target = boundary - float(profile["target_range_fraction"]) * width
        geometry_ok = target < entry < stop
    if not geometry_ok:
        return None
    planned_loss = _loss(entry, stop, cost)
    price_risk = abs(entry - stop)
    planned_gain = abs(target - entry) - entry * cost - target * cost
    price_fraction = price_risk / planned_loss if planned_loss > 0.0 else 0.0
    net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
    if (
        price_fraction < minimum_price_risk_fraction
        or net_rr < minimum_net_reward_risk
        or planned_gain <= 0.0
    ):
        return None

    future = features.iloc[entry_index + 1 : entry_index + 1 + fade_plan.max_hold_bars]
    for offset, row in enumerate(future.itertuples(index=False), start=1):
        stop_hit = row.low <= stop if breakout_side is Side.LONG else row.high >= stop
        target_hit = row.high >= target if breakout_side is Side.LONG else row.low <= target
        if stop_hit:
            return {
                "breakout_side": breakout_side.value,
                "confirmation_time_ns": int(features.iloc[confirmation_index]["ts_ns"]),
                "entry_time_ns": int(entry_row["ts_ns"]),
                "entry": entry,
                "stop": stop,
                "target": target,
                "exit_reason": "STOP",
                "bars": offset,
                "exit_price": stop,
                "realized_r": _net_r(breakout_side, entry, stop, stop, cost),
                "price_risk_fraction": price_fraction,
                "net_reward_risk_at_entry": net_rr,
            }
        if target_hit:
            return {
                "breakout_side": breakout_side.value,
                "confirmation_time_ns": int(features.iloc[confirmation_index]["ts_ns"]),
                "entry_time_ns": int(entry_row["ts_ns"]),
                "entry": entry,
                "stop": stop,
                "target": target,
                "exit_reason": "TARGET",
                "bars": offset,
                "exit_price": target,
                "realized_r": _net_r(breakout_side, entry, target, stop, cost),
                "price_risk_fraction": price_fraction,
                "net_reward_risk_at_entry": net_rr,
            }
    exit_price = float(future.iloc[-1]["close"]) if not future.empty else entry
    return {
        "breakout_side": breakout_side.value,
        "confirmation_time_ns": int(features.iloc[confirmation_index]["ts_ns"]),
        "entry_time_ns": int(entry_row["ts_ns"]),
        "entry": entry,
        "stop": stop,
        "target": target,
        "exit_reason": "TIME",
        "bars": int(len(future)),
        "exit_price": exit_price,
        "realized_r": _net_r(breakout_side, entry, exit_price, stop, cost),
        "price_risk_fraction": price_fraction,
        "net_reward_risk_at_entry": net_rr,
    }


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []

    for label, start, end, role in _segments(research):
        frame, _ = load_interval(
            symbol="BTCUSDT",
            start=start,
            end=end,
            cache_dir=args.cache,
            warmup_minutes=max(int(research.get("warmup_minutes", 420)), candidate.range_minutes + 180),
        )
        bars = to_auction_bars(frame)
        features = _features(bars, candidate)
        index_by_ts = {int(value): index for index, value in enumerate(features["ts_ns"])}
        start_ns = int(pd.Timestamp(start).value)
        end_ns = int(pd.Timestamp(end).value)
        for plan in _plans(bars, candidate):
            if not start_ns <= plan.signal_time_ns < end_ns:
                continue
            signal_index = index_by_ts.get(plan.signal_time_ns)
            if signal_index is None or signal_index + 1 >= len(features):
                continue
            entry_index = signal_index + 1
            entry = float(features.iloc[entry_index]["close"])
            geometry_ok = (
                plan.stop_price < entry < plan.target_price
                if plan.side is Side.LONG
                else plan.target_price < entry < plan.stop_price
            )
            if not geometry_ok:
                continue
            planned_loss = _loss(entry, plan.stop_price, cost)
            price_fraction = abs(entry - plan.stop_price) / planned_loss if planned_loss > 0.0 else 0.0
            planned_gain = abs(plan.target_price - entry) - entry * cost - plan.target_price * cost
            net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
            if (
                price_fraction < float(execution["minimum_price_risk_fraction"])
                or net_rr < float(execution["minimum_net_reward_risk"])
            ):
                continue
            baseline_future = features.iloc[
                entry_index + 1 : entry_index + 1 + plan.max_hold_bars
            ].reset_index(drop=True)
            stop_offset, baseline_reason, baseline_exit = _baseline_stop_index(
                baseline_future,
                side=plan.side,
                stop=plan.stop_price,
                target=plan.target_price,
            )
            if stop_offset is None or baseline_reason != "STOP":
                continue
            original_r = _net_r(plan.side, entry, baseline_exit, plan.stop_price, cost)
            # ``features`` passed to the continuation starts at the first bar
            # after the fade entry, so ``stop_offset`` remains causally aligned.
            for profile_name, profile in PROFILES.items():
                continuation = _continuation(
                    baseline_future,
                    stop_offset=stop_offset,
                    fade_side=plan.side,
                    fade_plan=plan,
                    cost=cost,
                    minimum_price_risk_fraction=float(execution["minimum_price_risk_fraction"]),
                    minimum_net_reward_risk=float(execution["minimum_net_reward_risk"]),
                    minimum_stop_atr=float(candidate.minimum_stop_atr),
                    profile=profile,
                )
                row = {
                    **asdict(plan),
                    "side": plan.side.value,
                    "response": plan.response.value,
                    "segment": label,
                    "role": role,
                    "fade_entry": entry,
                    "fade_stop_offset": stop_offset,
                    "fade_realized_r": original_r,
                    "profile": profile_name,
                    "continuation_emitted": continuation is not None,
                }
                if continuation is not None:
                    row.update(continuation)
                    row["combined_r"] = original_r + float(continuation["realized_r"])
                else:
                    row["combined_r"] = original_r
                rows.append(row)

    result = pd.DataFrame(rows)
    result.to_csv(output / "failed_reversal_continuations.csv", index=False)
    summaries: list[dict[str, Any]] = []
    for (role, profile), group in result.groupby(["role", "profile"], sort=True):
        emitted = group[group["continuation_emitted"]]
        continuation_r = pd.to_numeric(emitted.get("realized_r", pd.Series(dtype=float)), errors="coerce").dropna()
        combined_r = pd.to_numeric(group["combined_r"], errors="coerce").dropna()
        gross_profit = float(continuation_r[continuation_r > 0.0].sum())
        gross_loss = abs(float(continuation_r[continuation_r < 0.0].sum()))
        summaries.append(
            {
                "role": role,
                "profile": profile,
                "stopped_fades": int(len(group)),
                "continuations": int(len(emitted)),
                "continuation_sum_r": float(continuation_r.sum()),
                "continuation_mean_r": float(continuation_r.mean()) if len(continuation_r) else None,
                "continuation_win_rate": float((continuation_r > 0.0).mean()) if len(continuation_r) else None,
                "continuation_profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
                "combined_sum_r_for_stopped_fades": float(combined_r.sum()),
                "continuation_exit_counts": emitted.get("exit_reason", pd.Series(dtype=str)).value_counts().to_dict(),
            },
        )
    summary = {"rows": int(len(result)), "profiles": summaries}
    _atomic_json(output / "failed_reversal_continuation_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-failure-flip")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-failure-flip")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
