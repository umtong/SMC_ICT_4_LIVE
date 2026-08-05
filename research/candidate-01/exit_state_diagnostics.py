#!/usr/bin/env python3
"""Test causal scenario-invalidating exits on failed-auction positions.

A failed-auction reversal is no longer valid once price establishes value back
outside the completed range in the original breakout direction.  The current
candidate waits for the distant sweep-extreme stop.  This diagnostic measures
whether explicit re-acceptance exits reduce destructive stops without silently
using future path information.

Detection occurs only at a completed bar.  The exit is filled at the following
bar close; stop and target orders retain priority intrabar, making the test
conservative relative to a live market-order reaction.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict
from datetime import timedelta
import json
from math import sqrt
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


RULES: dict[str, tuple[float, float, int]] = {
    # boundary depth in ATR, minimum breakout-direction flow z, consecutive bars
    "baseline": (float("inf"), float("inf"), 10**9),
    "reaccept-1bar": (0.10, 0.50, 1),
    "reaccept-2bar": (0.05, 0.25, 2),
    "reaccept-price-2bar": (0.10, -float("inf"), 2),
    "reaccept-flow-strong": (0.00, 1.00, 1),
}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _zscore(value: float, history: deque[float]) -> float:
    values = np.asarray(history, dtype=float)
    if len(values) < 20:
        return 0.0
    std = float(values.std())
    if std <= 1e-12:
        return 0.0
    return (value - float(values.mean())) / std


def _causal_bar_features(bars: list[Any], candidate: CandidateConfig) -> pd.DataFrame:
    true_ranges: deque[float] = deque(maxlen=candidate.atr_lookback)
    flows: deque[float] = deque(maxlen=candidate.flow_lookback)
    previous_close: float | None = None
    rows: list[dict[str, Any]] = []
    for item in bars:
        atr = float(np.mean(true_ranges)) if len(true_ranges) >= max(20, candidate.atr_lookback // 2) else np.nan
        flow_z = _zscore(item.aggressive_imbalance, flows)
        rows.append(
            {
                "ts_ns": item.ts_event_ns,
                "atr_prior": atr,
                "flow_z_prior": flow_z,
                "open": item.open,
                "high": item.high,
                "low": item.low,
                "close": item.close,
            },
        )
        true_range = (
            item.high - item.low
            if previous_close is None
            else max(item.high - item.low, abs(item.high - previous_close), abs(item.low - previous_close))
        )
        true_ranges.append(true_range)
        flows.append(item.aggressive_imbalance)
        previous_close = item.close
    return pd.DataFrame(rows)


def _event_context(bars: list[Any], candidate: CandidateConfig) -> tuple[list[Any], dict[str, dict[str, Any]]]:
    machine = AuctionStateMachine(candidate, instrument_id="BTCUSDT-PERP.BINANCE")
    plans: list[Any] = []
    for item in bars:
        plan = machine.on_bar(item)
        if plan is not None:
            plans.append(plan)
    context: dict[str, dict[str, Any]] = {}
    for event in machine.transitions:
        if event.event_type == "LIQUIDITY_PROBE_REJECTED":
            context[event.scenario_id] = {
                "boundary": float(event.details["boundary"]),
                "probe_atr": float(event.details["atr"]),
                "internal_break": float(event.details["internal_break"]),
                "probe_time_ns": event.event_time_ns,
            }
    return plans, context


def _net_r(side: Side, entry: float, exit_price: float, stop: float, cost: float) -> float:
    planned_loss = abs(entry - stop) + entry * cost + stop * cost
    gross = (exit_price - entry) * side.sign
    return (gross - entry * cost - exit_price * cost) / planned_loss


def _simulate_rule(
    future: pd.DataFrame,
    *,
    side: Side,
    entry: float,
    stop: float,
    target: float,
    boundary: float,
    cost: float,
    depth_atr: float,
    minimum_flow_z: float,
    required_bars: int,
) -> dict[str, Any]:
    consecutive = 0
    scheduled_exit = False
    detection_ts: int | None = None
    for offset, row in enumerate(future.itertuples(index=False), start=1):
        # Existing protective orders have priority inside the fill bar.
        stop_hit = row.low <= stop if side is Side.LONG else row.high >= stop
        target_hit = row.high >= target if side is Side.LONG else row.low <= target
        if stop_hit:
            return {
                "exit_reason": "STOP",
                "bars": offset,
                "exit_price": stop,
                "realized_r": _net_r(side, entry, stop, stop, cost),
                "detection_ts_ns": detection_ts,
            }
        if target_hit:
            return {
                "exit_reason": "TARGET",
                "bars": offset,
                "exit_price": target,
                "realized_r": _net_r(side, entry, target, stop, cost),
                "detection_ts_ns": detection_ts,
            }
        if scheduled_exit:
            return {
                "exit_reason": "REACCEPTANCE",
                "bars": offset,
                "exit_price": float(row.close),
                "realized_r": _net_r(side, entry, float(row.close), stop, cost),
                "detection_ts_ns": detection_ts,
            }

        atr = float(row.atr_prior)
        if not np.isfinite(atr) or atr <= 0.0:
            continue
        breakout_flow = -float(row.flow_z_prior) * side.sign
        outside = (
            float(row.close) >= boundary + depth_atr * atr
            if side is Side.SHORT
            else float(row.close) <= boundary - depth_atr * atr
        )
        confirmed = outside and breakout_flow >= minimum_flow_z
        consecutive = consecutive + 1 if confirmed else 0
        if consecutive >= required_bars:
            scheduled_exit = True
            detection_ts = int(row.ts_ns)

    if future.empty:
        return {
            "exit_reason": "NO_FUTURE_BAR",
            "bars": 0,
            "exit_price": entry,
            "realized_r": _net_r(side, entry, entry, stop, cost),
            "detection_ts_ns": detection_ts,
        }
    last = future.iloc[-1]
    return {
        "exit_reason": "TIME",
        "bars": int(len(future)),
        "exit_price": float(last["close"]),
        "realized_r": _net_r(side, entry, float(last["close"]), stop, cost),
        "detection_ts_ns": detection_ts,
    }


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    start = parse_utc_date(args.start)
    end = parse_utc_date(args.end)
    frame, _ = load_interval(
        symbol="BTCUSDT",
        start=start,
        end=end,
        cache_dir=args.cache,
        warmup_minutes=max(int(research.get("warmup_minutes", 420)), candidate.range_minutes + 180),
    )
    bars = to_auction_bars(frame)
    features = _causal_bar_features(bars, candidate)
    index_by_ts = {int(value): index for index, value in enumerate(features["ts_ns"])}
    plans, contexts = _event_context(bars, candidate)
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0
    start_ns = int(pd.Timestamp(start).value)
    end_ns = int(pd.Timestamp(end).value)
    rows: list[dict[str, Any]] = []

    for plan in plans:
        if not start_ns <= plan.signal_time_ns < end_ns:
            continue
        signal_index = index_by_ts.get(plan.signal_time_ns)
        context = contexts.get(plan.scenario_id)
        if signal_index is None or context is None or signal_index + 1 >= len(features):
            continue
        entry_index = signal_index + 1
        entry_row = features.iloc[entry_index]
        entry = float(entry_row["close"])
        geometry_ok = (
            plan.stop_price < entry < plan.target_price
            if plan.side is Side.LONG
            else plan.target_price < entry < plan.stop_price
        )
        if not geometry_ok:
            continue
        price_risk = abs(entry - plan.stop_price)
        round_trip_cost = entry * cost + plan.stop_price * cost
        planned_loss = price_risk + round_trip_cost
        planned_gain = abs(plan.target_price - entry) - entry * cost - plan.target_price * cost
        price_risk_fraction = price_risk / planned_loss if planned_loss > 0.0 else 0.0
        net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
        if (
            price_risk_fraction < float(execution["minimum_price_risk_fraction"])
            or net_rr < float(execution["minimum_net_reward_risk"])
        ):
            continue
        future = features.iloc[
            entry_index + 1 : entry_index + 1 + plan.max_hold_bars
        ].copy()
        base = {
            **asdict(plan),
            "side": plan.side.value,
            "response": plan.response.value,
            "entry_time_ns": int(entry_row["ts_ns"]),
            "entry": entry,
            "boundary": context["boundary"],
            "price_risk_fraction": price_risk_fraction,
            "net_reward_risk_at_entry": net_rr,
        }
        for name, (depth, flow, count) in RULES.items():
            outcome = _simulate_rule(
                future,
                side=plan.side,
                entry=entry,
                stop=plan.stop_price,
                target=plan.target_price,
                boundary=float(context["boundary"]),
                cost=cost,
                depth_atr=depth,
                minimum_flow_z=flow,
                required_bars=count,
            )
            rows.append({**base, "rule": name, **outcome})

    result = pd.DataFrame(rows)
    args.output.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output / "reacceptance_exit_paths.csv", index=False)
    summaries: list[dict[str, Any]] = []
    for rule, group in result.groupby("rule", sort=True):
        values = pd.to_numeric(group["realized_r"], errors="coerce").dropna()
        gross_profit = float(values[values > 0.0].sum())
        gross_loss = abs(float(values[values < 0.0].sum()))
        summaries.append(
            {
                "rule": rule,
                "trades": int(len(values)),
                "sum_r": float(values.sum()),
                "mean_r": float(values.mean()) if len(values) else None,
                "win_rate": float((values > 0.0).mean()) if len(values) else None,
                "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
                "exit_counts": group["exit_reason"].value_counts().to_dict(),
            },
        )
    summary = {"start": start.isoformat(), "end": end.isoformat(), "rules": summaries}
    _atomic_json(args.output / "reacceptance_exit_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-exit-state")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-exit-state")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2025-01-01")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
