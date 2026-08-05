#!/usr/bin/env python3
"""Post-signal diagnostics for target and invalidation design.

This module is not imported by the strategy.  It replays the causal state
machine, preserves the one-completed-bar execution delay, then measures only
subsequent bars.  The resulting MFE/MAE and level-hit table is evidence for
scenario design; it never leaks future information into signal generation.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for path in (HERE, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core import AuctionStateMachine, CandidateConfig, Side  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402


COST_FRACTION_PER_SIDE = 0.0007
RISK_FRACTION = 0.01
TARGET_FRACTIONS = (0.25, 0.50, 0.75, 1.00)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _first_hit(
    bars: list[Any],
    *,
    side: Side,
    stop: float,
    target: float,
) -> tuple[str, int, float]:
    """Return conservative first exit after entry; stop wins same-bar ambiguity."""

    for offset, item in enumerate(bars, start=1):
        if side is Side.LONG:
            stop_hit = item.low <= stop
            target_hit = item.high >= target
        else:
            stop_hit = item.high >= stop
            target_hit = item.low <= target
        if stop_hit:
            return "STOP", offset, stop
        if target_hit:
            return "TARGET", offset, target
    if not bars:
        return "NO_FUTURE_BAR", 0, float("nan")
    return "TIME", len(bars), bars[-1].close


def _net_r_multiple(
    *,
    side: Side,
    entry: float,
    exit_price: float,
    stop: float,
    cost: float,
) -> float:
    planned_loss = abs(entry - stop) + entry * cost + stop * cost
    if planned_loss <= 0.0:
        return float("nan")
    gross = (exit_price - entry) * side.sign
    net = gross - entry * cost - exit_price * cost
    return net / planned_loss


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    cost = float(execution.get("all_in_cost_bps_per_side", 7.0)) / 10_000.0

    start = parse_utc_date(args.start or str(research["discovery_week"]))
    end = parse_utc_date(args.end) if args.end else start + timedelta(days=7)
    frame, _ = load_interval(
        symbol=str(research.get("symbol", "BTCUSDT")),
        start=start,
        end=end,
        cache_dir=args.cache,
        warmup_minutes=int(research.get("warmup_minutes", 420)),
    )
    bars = to_auction_bars(frame)
    machine = AuctionStateMachine(candidate, instrument_id="BTCUSDT-PERP.BINANCE")
    evaluation_start_ns = int(pd.Timestamp(start).value)
    evaluation_end_ns = int(pd.Timestamp(end).value)

    rows: list[dict[str, Any]] = []
    for signal_index, item in enumerate(bars):
        plan = machine.on_bar(item)
        if plan is None or plan.signal_time_ns < evaluation_start_ns:
            continue
        entry_index = signal_index + 1
        if entry_index >= len(bars):
            continue
        entry_bar = bars[entry_index]
        if entry_bar.ts_event_ns >= evaluation_end_ns:
            continue
        entry = entry_bar.close
        if plan.side is Side.LONG and not plan.stop_price < entry < plan.target_price:
            continue
        if plan.side is Side.SHORT and not plan.target_price < entry < plan.stop_price:
            continue

        future = bars[entry_index + 1 : entry_index + 1 + plan.max_hold_bars]
        if plan.side is Side.LONG:
            mfe = max((bar.high - entry for bar in future), default=0.0)
            mae = max((entry - bar.low for bar in future), default=0.0)
            opposite = plan.anchor_high
        else:
            mfe = max((entry - bar.low for bar in future), default=0.0)
            mae = max((bar.high - entry for bar in future), default=0.0)
            opposite = plan.anchor_low

        price_risk = abs(entry - plan.stop_price)
        round_trip_cost = entry * cost + plan.stop_price * cost
        total_planned_loss = price_risk + round_trip_cost
        row: dict[str, Any] = {
            **asdict(plan),
            "side": plan.side.value,
            "response": plan.response.value,
            "entry_time_ns": entry_bar.ts_event_ns,
            "entry": entry,
            "one_bar_delay": True,
            "price_risk": price_risk,
            "round_trip_cost_at_stop": round_trip_cost,
            "price_risk_fraction": price_risk / total_planned_loss if total_planned_loss > 0 else None,
            "effective_leverage_at_one_percent_risk": (
                entry * RISK_FRACTION / total_planned_loss if total_planned_loss > 0 else None
            ),
            "mfe": mfe,
            "mae": mae,
            "mfe_atr": mfe / plan.atr,
            "mae_atr": mae / plan.atr,
            "mfe_r": mfe / total_planned_loss if total_planned_loss > 0 else None,
            "mae_r": mae / total_planned_loss if total_planned_loss > 0 else None,
        }
        for fraction in TARGET_FRACTIONS:
            level = entry + (opposite - entry) * fraction
            outcome, bars_to_exit, exit_price = _first_hit(
                future,
                side=plan.side,
                stop=plan.stop_price,
                target=level,
            )
            key = f"target_{int(fraction * 100):03d}"
            row[f"{key}_price"] = level
            row[f"{key}_outcome"] = outcome
            row[f"{key}_bars"] = bars_to_exit
            row[f"{key}_net_r"] = _net_r_multiple(
                side=plan.side,
                entry=entry,
                exit_price=exit_price,
                stop=plan.stop_price,
                cost=cost,
            )
        rows.append(row)

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    table = pd.DataFrame(rows)
    table.to_csv(output / "forward_paths.csv", index=False)

    summaries: dict[str, Any] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "plans": len(table.index),
        "cost_fraction_per_side": cost,
        "targets": {},
    }
    if not table.empty:
        for fraction in TARGET_FRACTIONS:
            key = f"target_{int(fraction * 100):03d}"
            values = pd.to_numeric(table[f"{key}_net_r"], errors="coerce").dropna()
            summaries["targets"][key] = {
                "trades": int(len(values.index)),
                "target_hits": int((table[f"{key}_outcome"] == "TARGET").sum()),
                "stop_hits": int((table[f"{key}_outcome"] == "STOP").sum()),
                "time_exits": int((table[f"{key}_outcome"] == "TIME").sum()),
                "net_r_sum": float(values.sum()),
                "mean_net_r": float(values.mean()),
                "win_rate": float((values > 0.0).mean()),
                "growth_factor_at_one_percent_risk": float((1.0 + RISK_FRACTION * values).prod()),
            }
    _write_json(output / "forward_path_summary.json", summaries)
    print(json.dumps(summaries, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-diagnostics")
    parser.add_argument("--start")
    parser.add_argument("--end")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
