#!/usr/bin/env python3
"""First-week midpoint-confirmation control for absorbed impact release.

The baseline absorbed-impact scenario found five correctly ordered releases but
all entries arrived only after a full pulse-midpoint cross and failed the fixed
cost-adjusted reward/risk gate.  This control changes exactly one condition:
confirmation requires opposite flow and a close back inside the swept external
boundary, but not a second pulse-midpoint cross.

Pulse absorption, clock, three-event response window, strict path-extreme stop,
opposite structure target, delayed entry, confirmation hold, 7 bps per side,
current-NAV 3% risk and one global position remain unchanged.
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

from absorbed_impact_release_week import (  # noqa: E402
    CLOCK_MINUTES,
    CLOCK_RANGE_FLOOR_BPS,
    INSIDE_DEPTH_ATR,
    OPPOSITE_FLOW_CONFIRM_Z,
    AbsorbedImpactSetup,
    AbsorbedImpactStateMachine,
    atomic_json,
)
from adaptive_aggtrade_clock import build_daily_cost_resolved_bars  # noqa: E402
from aggtrade_data import download_aggtrade_days  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_regime_probe import EventFeature, ImpactRegimeDetector, simulate  # noqa: E402


class BoundaryReentryAbsorptionStateMachine(AbsorbedImpactStateMachine):
    """Confirm release at boundary re-entry without waiting for midpoint."""

    @staticmethod
    def _confirmed(
        setup: AbsorbedImpactSetup,
        feature: EventFeature,
    ) -> tuple[bool, float | None]:
        z = feature.imbalance_z
        opposite_z = setup.reversal_side.sign * z if z is not None else None
        if setup.outward_side is Side.LONG:
            inside = feature.bar.close <= setup.boundary - INSIDE_DEPTH_ATR * setup.atr
        else:
            inside = feature.bar.close >= setup.boundary + INSIDE_DEPTH_ATR * setup.atr
        return (
            opposite_z is not None
            and opposite_z >= OPPOSITE_FLOW_CONFIRM_Z
            and inside
        ), opposite_z


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    execution = dict(raw["execution"])
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    feature_warmup_start = evaluation_start - timedelta(days=1)
    clock_source_start = evaluation_start - timedelta(days=2)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=clock_source_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    bars, calibrations = build_daily_cost_resolved_bars(
        records,
        bar_start=feature_warmup_start,
        bar_end=evaluation_end,
        minimum_range_bps=CLOCK_RANGE_FLOOR_BPS,
        candidate_minutes=CLOCK_MINUTES,
    )

    detector = ImpactRegimeDetector()
    scenario = BoundaryReentryAbsorptionStateMachine()
    previous_pulses = 0
    for index, bar in enumerate(bars):
        detector.on_bar(bar)
        feature = detector.features[-1]
        scenario.on_feature(index=index, feature=feature)
        for pulse in detector.pulse_events[previous_pulses:]:
            scenario.observe_pulse(pulse=pulse, feature=feature)
        previous_pulses = len(detector.pulse_events)

    trades, metrics, daily, rejections = simulate(
        features=detector.features,
        plans=scenario.plans,
        evaluation_start_ns=start_ns,
        evaluation_end_ns=end_ns,
        starting_nav=float(execution["starting_nav"]),
        cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
        exit_on_boundary_reacceptance=False,
    )
    evaluation_bars = [bar for bar in bars if start_ns <= bar.end_time_ns < end_ns]
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    daily.to_csv(output / "daily_nav.csv", index=False)
    rejections.to_csv(output / "rejections.csv", index=False)
    pd.DataFrame(asdict(row) for row in scenario.decisions).to_csv(
        output / "absorption_decisions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    payload = {
        "diagnosis": "absorbed-impact boundary-reentry confirmation control",
        "changed_variable": "pulse midpoint cross removed",
        "fixed_confirmation": "opposite flow plus external boundary re-entry",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "clock_minutes": list(CLOCK_MINUTES),
        "evaluation_event_bars": len(evaluation_bars),
        "event_bars_per_day": len(evaluation_bars) / 7.0,
        "scenario_counts": dict(scenario.counts),
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "absorbed_impact_confirmation_control_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-impact-resolution-adaptive-first")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-absorbed-impact-confirmation")
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
