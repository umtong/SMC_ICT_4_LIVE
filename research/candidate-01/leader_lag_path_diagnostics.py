#!/usr/bin/env python3
"""Measure all BTC leader-to-alt lag-release plans before execution gates."""

from __future__ import annotations

import argparse
from dataclasses import asdict
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

from core import CandidateConfig, Side  # noqa: E402
from leader_lag_release_probe import (  # noqa: E402
    RULES,
    SYMBOLS,
    LeaderLagDetector,
    load_segment,
    week_segments,
)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def path_row(
    *,
    pending: Any,
    bars_by_symbol: dict[str, list[Any]],
    index_by_symbol: dict[str, dict[int, int]],
    cost: float,
) -> dict[str, Any]:
    plan = pending.plan
    symbol = pending.symbol
    bars = bars_by_symbol[symbol]
    signal_index = index_by_symbol[symbol].get(plan.signal_time_ns)
    if signal_index is None or signal_index + 1 >= len(bars):
        return {
            "scenario_id": plan.scenario_id,
            "symbol": symbol,
            "valid": False,
            "reason": "missing_next_bar",
        }
    entry_index = signal_index + 1
    entry_bar = bars[entry_index]
    entry = entry_bar.close
    stop = plan.stop_price
    target = plan.target_price
    geometry = stop < entry < target if plan.side is Side.LONG else target < entry < stop
    if not geometry:
        return {
            **asdict(plan),
            "side": plan.side.value,
            "response": plan.response.value,
            "symbol": symbol,
            "valid": False,
            "reason": "invalid_delayed_geometry",
            "entry_time_ns": entry_bar.ts_event_ns,
            "entry": entry,
            "stop": stop,
            "target": target,
        }

    price_risk = abs(entry - stop)
    planned_loss = price_risk + entry * cost + stop * cost
    planned_gain = abs(target - entry) - entry * cost - target * cost
    price_risk_fraction = price_risk / planned_loss if planned_loss > 0.0 else 0.0
    net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
    final_index = min(entry_index + plan.max_hold_bars, len(bars) - 1)
    exit_price = bars[final_index].close
    exit_reason = "TIME"
    exit_index = final_index
    for index in range(entry_index + 1, final_index + 1):
        bar = bars[index]
        if plan.side is Side.LONG:
            stop_hit = bar.low <= stop
            target_hit = bar.high >= target
        else:
            stop_hit = bar.high >= stop
            target_hit = bar.low <= target
        if stop_hit:
            exit_price = stop
            exit_reason = "STOP"
            exit_index = index
            break
        if target_hit:
            exit_price = target
            exit_reason = "TARGET"
            exit_index = index
            break

    gross = (exit_price - entry) * plan.side.sign
    net = gross - entry * cost - exit_price * cost
    realized_r = net / planned_loss if planned_loss > 0.0 else -1.0
    return {
        **asdict(plan),
        "side": plan.side.value,
        "response": plan.response.value,
        "symbol": symbol,
        "valid": True,
        "entry_time_ns": entry_bar.ts_event_ns,
        "entry": entry,
        "stop": stop,
        "target": target,
        "planned_loss_per_unit": planned_loss,
        "planned_gain_per_unit": planned_gain,
        "price_risk_fraction": price_risk_fraction,
        "net_reward_risk": net_rr,
        "exit_time_ns": bars[exit_index].ts_event_ns,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "bars_held": exit_index - entry_index,
        "realized_r": realized_r,
    }


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    validity = frame.get("valid", pd.Series(False, index=frame.index, dtype=bool))
    valid = frame.loc[validity == True].copy()  # noqa: E712
    invalid = (
        frame.loc[validity != True, "reason"].value_counts().to_dict()  # noqa: E712
        if "reason" in frame.columns
        else {}
    )
    values = pd.to_numeric(
        valid.get("realized_r", pd.Series(dtype=float)),
        errors="coerce",
    ).dropna()
    profits = float(values[values > 0.0].sum())
    losses = abs(float(values[values < 0.0].sum()))
    return {
        "plans": int(len(frame)),
        "valid_paths": int(len(values)),
        "invalid_reasons": invalid,
        "sum_r": float(values.sum()),
        "mean_r": float(values.mean()) if len(values) else None,
        "win_rate": float((values > 0.0).mean()) if len(values) else None,
        "profit_factor": profits / losses if losses > 0.0 else None,
        "exit_counts": valid.get(
            "exit_reason",
            pd.Series(dtype=str),
        ).value_counts().to_dict(),
        "symbol_counts": valid.get(
            "symbol",
            pd.Series(dtype=str),
        ).value_counts().to_dict(),
        "cost_dominated_paths": (
            int((valid["price_risk_fraction"] < 0.65).sum())
            if "price_risk_fraction" in valid.columns
            else 0
        ),
        "insufficient_net_rr_paths": (
            int((valid["net_reward_risk"] < 1.20).sum())
            if "net_reward_risk" in valid.columns
            else 0
        ),
    }


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    combined_rows: dict[str, list[pd.DataFrame]] = {rule: [] for rule in RULES}
    result: dict[str, Any] = {rule: {"segments": {}} for rule in RULES}

    for label, start, end in week_segments(research):
        bars_by_symbol, _ = load_segment(start=start, end=end, cache=args.cache)
        bar_maps = {
            symbol: {bar.ts_event_ns: bar for bar in bars}
            for symbol, bars in bars_by_symbol.items()
        }
        timestamps = sorted(
            set.intersection(*(set(mapping) for mapping in bar_maps.values())),
        )
        detector = LeaderLagDetector(candidate)
        for ts_ns in timestamps:
            detector.on_bars(
                {symbol: bar_maps[symbol][ts_ns] for symbol in SYMBOLS},
            )
        indices = {
            symbol: {bar.ts_event_ns: index for index, bar in enumerate(bars)}
            for symbol, bars in bars_by_symbol.items()
        }

        for rule in RULES:
            rows = [
                path_row(
                    pending=pending,
                    bars_by_symbol=bars_by_symbol,
                    index_by_symbol=indices,
                    cost=cost,
                )
                for pending_rows in detector.schedules[rule].values()
                for pending in pending_rows
                if start
                <= pd.Timestamp(
                    pending.plan.signal_time_ns,
                    unit="ns",
                    tz="UTC",
                ).to_pydatetime()
                < end
            ]
            frame = pd.DataFrame(rows)
            frame.to_csv(output / f"{rule}-{label}.csv", index=False)
            if not frame.empty:
                frame["segment"] = label
                combined_rows[rule].append(frame)
            result[rule]["segments"][label] = (
                summarize(frame)
                if rows
                else {
                    "plans": 0,
                    "valid_paths": 0,
                    "invalid_reasons": {},
                    "sum_r": 0.0,
                    "mean_r": None,
                    "win_rate": None,
                    "profit_factor": None,
                    "exit_counts": {},
                    "symbol_counts": {},
                    "cost_dominated_paths": 0,
                    "insufficient_net_rr_paths": 0,
                }
            )

    for rule in RULES:
        combined = (
            pd.concat(combined_rows[rule], ignore_index=True)
            if combined_rows[rule]
            else pd.DataFrame()
        )
        combined.to_csv(output / f"{rule}-combined.csv", index=False)
        result[rule]["combined"] = (
            summarize(combined)
            if not combined.empty
            else {
                "plans": 0,
                "valid_paths": 0,
                "invalid_reasons": {},
                "sum_r": 0.0,
                "mean_r": None,
                "win_rate": None,
                "profit_factor": None,
                "exit_counts": {},
                "symbol_counts": {},
                "cost_dominated_paths": 0,
                "insufficient_net_rr_paths": 0,
            }
        )

    payload = {
        "scenario": "BTC leader-to-alt lag-release paths before execution gates",
        "entry_delay_bars": 1,
        "same_bar_ambiguity": "stop_first",
        "all_in_cost_bps_per_side": float(
            execution["all_in_cost_bps_per_side"],
        ),
        "rules": result,
    }
    atomic_json(output / "leader_lag_path_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-leader-lag-release",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-leader-lag-paths",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
