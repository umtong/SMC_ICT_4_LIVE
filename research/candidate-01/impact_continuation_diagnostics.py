#!/usr/bin/env python3
"""Controlled first-week diagnosis of efficient-impact continuation failure.

The aggregate-trade clock, pulse detector, prior structure, structural stop,
measured target, fees, risk sizing and single-position accounting are frozen.
Only the post-pulse response is changed:

* baseline: enter on the next event open (already failed);
* persistent continuation: require the next three events to produce a same-side
  flow z >= 0.5 while price still holds beyond the broken boundary;
* failed-impact reversal: require price to close back inside the boundary with
  opposite flow z >= 0.5 and through the pulse midpoint.

This is a causal variable-control test on the first BTC week only.
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
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from aggtrade_clock import calibrate_target_from_minutes, iter_volume_bars, minute_quote_totals  # noqa: E402
from aggtrade_data import download_aggtrade_days, iter_downloads  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_regime_probe import (  # noqa: E402
    CLOCK_CALIBRATION_MINUTES,
    COST_PER_SIDE,
    ImpactRegimeDetector,
    ScenarioPlan,
    simulate,
)


CONFIRM_WINDOW = 3
FLOW_CONFIRM_Z = 0.50
HORIZONS = (1, 3, 6, 12, 24, 45)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def derived_plans(
    detector: ImpactRegimeDetector,
) -> tuple[list[ScenarioPlan], list[ScenarioPlan], list[dict[str, Any]]]:
    persistent: list[ScenarioPlan] = []
    reversals: list[ScenarioPlan] = []
    paths: list[dict[str, Any]] = []
    features = detector.features
    for original in detector.continuation_plans:
        index = original.signal_bar_index
        direction = original.side
        atr = features[index].atr
        if atr is None or atr <= 0.0:
            continue
        pulse_midpoint = 0.5 * (original.pulse_high + original.pulse_low)
        persistent_plan: ScenarioPlan | None = None
        reversal_plan: ScenarioPlan | None = None
        for offset in range(1, CONFIRM_WINDOW + 1):
            future_index = index + offset
            if future_index >= len(features):
                break
            feature = features[future_index]
            bar = feature.bar
            z = feature.imbalance_z
            aligned_z = direction.sign * z if z is not None else None
            holds = (
                bar.close >= original.confirmation_hold_price
                if direction is Side.LONG
                else bar.close <= original.confirmation_hold_price
            )
            directional_close = (
                bar.close >= features[future_index - 1].bar.close
                if direction is Side.LONG
                else bar.close <= features[future_index - 1].bar.close
            )
            if (
                persistent_plan is None
                and aligned_z is not None
                and aligned_z >= FLOW_CONFIRM_Z
                and holds
                and directional_close
            ):
                persistent_plan = ScenarioPlan(
                    scenario_id=original.scenario_id + f":persistent:{future_index}",
                    response="CONTINUATION",
                    side=direction,
                    signal_bar_index=future_index,
                    signal_time_ns=bar.end_time_ns,
                    stop_price=original.stop_price,
                    target_price=original.target_price,
                    confirmation_hold_price=original.confirmation_hold_price,
                    structure_high=original.structure_high,
                    structure_low=original.structure_low,
                    structure_midpoint=original.structure_midpoint,
                    pulse_high=max(original.pulse_high, bar.high),
                    pulse_low=min(original.pulse_low, bar.low),
                    pulse_flow_score=original.pulse_flow_score,
                    pulse_move_atr=original.pulse_move_atr,
                    pulse_path_efficiency=original.pulse_path_efficiency,
                    pulse_close_location=original.pulse_close_location,
                    reason_code="IMPACT_PERSISTENCE_CONFIRMED",
                )
            opposite = Side.SHORT if direction is Side.LONG else Side.LONG
            opposite_z = opposite.sign * z if z is not None else None
            inside = (
                bar.close <= original.confirmation_hold_price - 0.05 * atr
                if direction is Side.LONG
                else bar.close >= original.confirmation_hold_price + 0.05 * atr
            )
            midpoint_break = (
                bar.close < pulse_midpoint
                if opposite is Side.SHORT
                else bar.close > pulse_midpoint
            )
            if (
                reversal_plan is None
                and opposite_z is not None
                and opposite_z >= FLOW_CONFIRM_Z
                and inside
                and midpoint_break
            ):
                stop = (
                    max(original.pulse_high, bar.high) + 0.15 * atr
                    if opposite is Side.SHORT
                    else min(original.pulse_low, bar.low) - 0.15 * atr
                )
                target = (
                    original.structure_low
                    if opposite is Side.SHORT
                    else original.structure_high
                )
                reversal_plan = ScenarioPlan(
                    scenario_id=original.scenario_id + f":decay-reversal:{future_index}",
                    response="EXHAUSTION_REVERSAL",
                    side=opposite,
                    signal_bar_index=future_index,
                    signal_time_ns=bar.end_time_ns,
                    stop_price=stop,
                    target_price=target,
                    confirmation_hold_price=original.confirmation_hold_price,
                    structure_high=original.structure_high,
                    structure_low=original.structure_low,
                    structure_midpoint=original.structure_midpoint,
                    pulse_high=max(original.pulse_high, bar.high),
                    pulse_low=min(original.pulse_low, bar.low),
                    pulse_flow_score=original.pulse_flow_score,
                    pulse_move_atr=original.pulse_move_atr,
                    pulse_path_efficiency=original.pulse_path_efficiency,
                    pulse_close_location=original.pulse_close_location,
                    reason_code="EFFICIENT_IMPACT_DECAY_REVERSED",
                )
        if persistent_plan is not None:
            persistent.append(persistent_plan)
        if reversal_plan is not None:
            reversals.append(reversal_plan)

        path: dict[str, Any] = {
            "scenario_id": original.scenario_id,
            "signal_bar_index": index,
            "side": direction.value,
            "persistent_confirmation": persistent_plan is not None,
            "decay_reversal_confirmation": reversal_plan is not None,
        }
        signal_close = features[index].bar.close
        for horizon in HORIZONS:
            end_index = min(index + horizon, len(features) - 1)
            future = features[index + 1 : end_index + 1]
            if not future:
                path[f"close_r_{horizon}"] = None
                path[f"mfe_r_{horizon}"] = None
                path[f"mae_r_{horizon}"] = None
                continue
            price_risk = abs(signal_close - original.stop_price)
            if price_risk <= 0.0:
                continue
            future_close = future[-1].bar.close
            path[f"close_r_{horizon}"] = direction.sign * (future_close - signal_close) / price_risk
            favorable = (
                max(item.bar.high for item in future) - signal_close
                if direction is Side.LONG
                else signal_close - min(item.bar.low for item in future)
            )
            adverse = (
                signal_close - min(item.bar.low for item in future)
                if direction is Side.LONG
                else max(item.bar.high for item in future) - signal_close
            )
            path[f"mfe_r_{horizon}"] = favorable / price_risk
            path[f"mae_r_{horizon}"] = adverse / price_risk
        paths.append(path)
    return persistent, reversals, paths


def summarize_paths(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "plans": int(len(frame.index)),
        "persistent_confirmations": int(frame.get("persistent_confirmation", pd.Series(dtype=bool)).sum()),
        "decay_reversal_confirmations": int(frame.get("decay_reversal_confirmation", pd.Series(dtype=bool)).sum()),
    }
    for horizon in HORIZONS:
        for prefix in ("close_r", "mfe_r", "mae_r"):
            column = f"{prefix}_{horizon}"
            values = pd.to_numeric(frame.get(column, pd.Series(dtype=float)), errors="coerce").dropna()
            result[column] = {
                "count": int(len(values)),
                "mean": float(values.mean()) if len(values) else None,
                "median": float(values.median()) if len(values) else None,
                "positive_fraction": float((values > 0.0).mean()) if len(values) else None,
            }
    return result


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    start = parse_utc_date(str(research["discovery_week"]))
    end = start + timedelta(days=7)
    warmup = start - timedelta(days=1)
    warmup_ns = int(pd.Timestamp(warmup).as_unit("ns").value)
    start_ns = int(pd.Timestamp(start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=warmup,
        end=end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    minute_totals = minute_quote_totals(
        iter_downloads(records),
        start_ns=warmup_ns,
        end_ns=start_ns,
    )
    target = calibrate_target_from_minutes(
        minute_totals,
        minutes_per_event=CLOCK_CALIBRATION_MINUTES,
    )
    bars = list(
        iter_volume_bars(
            iter_downloads(records),
            target_quote_notional=target,
            include_partial=False,
        ),
    )
    detector = ImpactRegimeDetector()
    for bar in bars:
        detector.on_bar(bar)
    persistent, reversals, paths = derived_plans(detector)
    path_frame = pd.DataFrame(paths)

    variants = {
        "baseline": detector.continuation_plans,
        "persistent-continuation": persistent,
        "failed-impact-reversal": reversals,
        "confirmed-combined": sorted(
            [*persistent, *reversals],
            key=lambda plan: (plan.signal_bar_index, plan.scenario_id),
        ),
    }
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    path_frame.to_csv(output / "continuation_paths.csv", index=False)
    results: dict[str, Any] = {}
    for label, plans in variants.items():
        trades, metrics, daily, rejections = simulate(
            features=detector.features,
            plans=plans,
            evaluation_start_ns=start_ns,
            evaluation_end_ns=end_ns,
            starting_nav=float(execution["starting_nav"]),
            cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
        )
        destination = output / label
        destination.mkdir(parents=True, exist_ok=True)
        trades.to_csv(destination / "trades.csv", index=False)
        daily.to_csv(destination / "daily_nav.csv", index=False)
        rejections.to_csv(destination / "rejections.csv", index=False)
        atomic_json(destination / "metrics.json", metrics)
        results[label] = metrics

    payload = {
        "diagnosis": "efficient impact persistence versus decay",
        "evaluation_start_utc": start.isoformat(),
        "evaluation_end_utc": end.isoformat(),
        "clock_calibration_minutes": CLOCK_CALIBRATION_MINUTES,
        "target_quote_notional": target,
        "confirm_window_bars": CONFIRM_WINDOW,
        "flow_confirm_z": FLOW_CONFIRM_Z,
        "path_summary": summarize_paths(path_frame),
        "results": results,
        "long_evaluation_run": False,
    }
    atomic_json(output / "impact_continuation_diagnostics.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-aggtrades",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-impact-continuation-diagnostics",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
