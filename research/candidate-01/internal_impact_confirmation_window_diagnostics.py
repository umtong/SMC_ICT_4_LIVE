#!/usr/bin/env python3
"""First-week confirmation-window control for internal impact failure.

The balanced internal-initiative prototype armed seven causal setups but none
confirmed in three event bars.  This diagnostic changes exactly one variable:
the maximum number of completed equal-notional events allowed for the opposite
flow/midpoint-failure response.  Structure, pulse classification, 0.50-z
opposite-flow requirement, strict path-extreme stop, cost, 3% NAV risk and
execution semantics remain unchanged.

Only the first frozen BTC week is available.  A longer window is acceptable
only when it creates several independent, positive-cost trades; it is not a
license to wait indefinitely for an unrelated reversal.
"""

from __future__ import annotations

import argparse
from collections import Counter
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
from data import parse_utc_date  # noqa: E402
import impact_failure_candidate as failure_module  # noqa: E402
from impact_failure_candidate import ImpactFailureStateMachine  # noqa: E402
from impact_regime_probe import CLOCK_CALIBRATION_MINUTES, ImpactRegimeDetector, ScenarioPlan, simulate  # noqa: E402
from internal_impact_failure_probe import classify_internal_pulse  # noqa: E402


WINDOWS = (3, 5, 8, 12)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    evaluation_start = parse_utc_date(str(research["discovery_week"]))
    evaluation_end = evaluation_start + timedelta(days=7)
    warmup_start = evaluation_start - timedelta(days=1)
    warmup_ns = int(pd.Timestamp(warmup_start).as_unit("ns").value)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=warmup_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    warmup_minutes = minute_quote_totals(
        iter_downloads(records),
        start_ns=warmup_ns,
        end_ns=start_ns,
    )
    target_quote = calibrate_target_from_minutes(
        warmup_minutes,
        minutes_per_event=CLOCK_CALIBRATION_MINUTES,
    )
    bars = list(
        iter_volume_bars(
            iter_downloads(records),
            target_quote_notional=target_quote,
            include_partial=False,
        ),
    )

    detector = ImpactRegimeDetector()
    initiatives_by_index: dict[int, list[ScenarioPlan]] = {}
    decisions = []
    decision_counts: Counter[str] = Counter()
    previous_pulses = 0
    for index, bar in enumerate(bars):
        detector.on_bar(bar)
        if len(detector.pulse_events) > previous_pulses:
            pulse = detector.pulse_events[-1]
            initiative, decision = classify_internal_pulse(detector=detector, pulse=pulse)
            decisions.append(decision)
            decision_counts[decision.reason_code] += 1
            if initiative is not None:
                initiatives_by_index.setdefault(index, []).append(initiative)
        previous_pulses = len(detector.pulse_events)

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(asdict(row) for row in decisions).to_csv(
        output / "internal_pulse_decisions.csv",
        index=False,
    )

    results: dict[str, Any] = {}
    original_window = failure_module.CONFIRMATION_WINDOW_BARS
    try:
        for window in WINDOWS:
            failure_module.CONFIRMATION_WINDOW_BARS = window
            machine = ImpactFailureStateMachine(
                include_intermediate_extremes=True,
                reject_consumed_target=True,
            )
            for index, feature in enumerate(detector.features):
                machine.on_feature(
                    index=index,
                    feature=feature,
                    new_initiative_plans=initiatives_by_index.get(index, ()),
                )
            trades, metrics, daily, rejections = simulate(
                features=detector.features,
                plans=machine.plans,
                evaluation_start_ns=start_ns,
                evaluation_end_ns=end_ns,
                starting_nav=float(execution["starting_nav"]),
                cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
                exit_on_boundary_reacceptance=False,
            )
            destination = output / f"window-{window}"
            destination.mkdir(parents=True, exist_ok=True)
            trades.to_csv(destination / "trades.csv", index=False)
            daily.to_csv(destination / "daily_nav.csv", index=False)
            rejections.to_csv(destination / "rejections.csv", index=False)
            pd.DataFrame(asdict(row) for row in machine.transitions).to_csv(
                destination / "scenario_transitions.csv",
                index=False,
            )
            atomic_json(destination / "metrics.json", metrics)
            results[str(window)] = {
                "state_counts": dict(machine.counts),
                "metrics": metrics,
            }
    finally:
        failure_module.CONFIRMATION_WINDOW_BARS = original_window

    payload = {
        "diagnosis": "balanced internal-initiative confirmation-window control",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "clock_calibration_minutes": CLOCK_CALIBRATION_MINUTES,
        "target_quote_notional": target_quote,
        "accepted_internal_initiatives": sum(len(rows) for rows in initiatives_by_index.values()),
        "fixed_opposite_flow_confirm_z": failure_module.OPPOSITE_FLOW_CONFIRM_Z,
        "fixed_inside_depth_atr": failure_module.INSIDE_DEPTH_ATR,
        "fixed_stop_buffer_atr": failure_module.STOP_BUFFER_ATR,
        "decision_counts": dict(decision_counts),
        "results": results,
        "long_evaluation_run": False,
    }
    atomic_json(output / "internal_impact_confirmation_window_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-aggtrades")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-internal-impact-window")
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
