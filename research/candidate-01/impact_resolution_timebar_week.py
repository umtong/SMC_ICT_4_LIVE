#!/usr/bin/env python3
"""Three-event resolved impact on fixed five-minute aggregate-trade auctions.

This is an independent clock representation, not another threshold search.  The
verified aggregate-trade stream is grouped into UTC-aligned five-minute bars,
so order-flow state is measured at the same market-time resolution in every
year while retaining aggressor-signed quote notional.

The trading scenario is unchanged from the successful first-week resolved
impact state machine:

* a three-bar aggressive-flow initiative probes completed 20-bar external
  liquidity;
* no trade is taken from the initiative itself;
* exactly three completed bars resolve failed impact (reversal precedence) or
  durable outside acceptance (continuation);
* stops include the full observed initiative/response path;
* targets are the opposite structure edge or measured external liquidity;
* the next completed bar open must retain the confirmation boundary.

Five minutes is fixed before observing PnL.  It gives a 100-minute dealing
structure, 15-minute initiative and 225-minute maximum hold, all intraday.  One
invocation evaluates exactly one BTC week at 7 bps per side, current-NAV 3%
risk, stop-first ambiguity and one global position.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
from statistics import median
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from aggtrade_data import download_aggtrade_days, iter_downloads  # noqa: E402
from aggtrade_time_clock import iter_time_bars  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_regime_probe import (  # noqa: E402
    MAX_HOLD_BARS,
    PULSE_BARS,
    STRUCTURE_BARS,
    ImpactRegimeDetector,
    simulate,
)
from impact_resolution_candidate import (  # noqa: E402
    CONTINUATION_FLOW_SUM_Z,
    INSIDE_DEPTH_ATR,
    MIN_OUTSIDE_HOLDS,
    OPPOSITE_FLOW_CONFIRM_Z,
    OUTSIDE_DEPTH_ATR,
    RESOLUTION_WINDOW_BARS,
    STOP_BUFFER_ATR,
    ImpactResolutionStateMachine,
)


TIMEFRAME_MINUTES = 5


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    execution = dict(raw["execution"])
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    warmup_start = evaluation_start - timedelta(days=1)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=warmup_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    bars = list(
        iter_time_bars(
            iter_downloads(records),
            interval_minutes=TIMEFRAME_MINUTES,
            include_partial=False,
        ),
    )

    detector = ImpactRegimeDetector()
    resolver = ImpactResolutionStateMachine()
    previous_initiatives = 0
    for index, bar in enumerate(bars):
        detector.on_bar(bar)
        initiatives = detector.continuation_plans[previous_initiatives:]
        previous_initiatives = len(detector.continuation_plans)
        resolver.on_feature(
            index=index,
            feature=detector.features[-1],
            new_initiative_plans=initiatives,
        )

    trades, metrics, daily, rejections = simulate(
        features=detector.features,
        plans=resolver.plans,
        evaluation_start_ns=start_ns,
        evaluation_end_ns=end_ns,
        starting_nav=float(execution["starting_nav"]),
        cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
        exit_on_boundary_reacceptance=False,
    )
    evaluation_bars = [bar for bar in bars if start_ns <= bar.end_time_ns < end_ns]
    range_bps = [bar.range_fraction * 10_000.0 for bar in evaluation_bars]
    quote_notionals = [bar.quote_notional for bar in evaluation_bars]

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    daily.to_csv(output / "daily_nav.csv", index=False)
    rejections.to_csv(output / "rejections.csv", index=False)
    pd.DataFrame(asdict(row) for row in detector.pulse_events).to_csv(
        output / "pulse_events.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in resolver.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    payload = {
        "candidate": "fixed-five-minute resolved impact",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "warmup_start_utc": warmup_start.isoformat(),
        "timeframe_minutes": TIMEFRAME_MINUTES,
        "structure_minutes": STRUCTURE_BARS * TIMEFRAME_MINUTES,
        "initiative_minutes": PULSE_BARS * TIMEFRAME_MINUTES,
        "maximum_hold_minutes": MAX_HOLD_BARS * TIMEFRAME_MINUTES,
        "resolution_window_minutes": RESOLUTION_WINDOW_BARS * TIMEFRAME_MINUTES,
        "evaluation_bars": len(evaluation_bars),
        "bars_per_day": len(evaluation_bars) / 7.0,
        "median_bar_range_bps": float(median(range_bps)) if range_bps else None,
        "median_quote_notional": float(median(quote_notionals)) if quote_notionals else None,
        "scenario_parameters": {
            "resolution_window_bars": RESOLUTION_WINDOW_BARS,
            "opposite_flow_confirm_z": OPPOSITE_FLOW_CONFIRM_Z,
            "continuation_flow_sum_z": CONTINUATION_FLOW_SUM_Z,
            "inside_depth_atr": INSIDE_DEPTH_ATR,
            "outside_depth_atr": OUTSIDE_DEPTH_ATR,
            "minimum_outside_holds": MIN_OUTSIDE_HOLDS,
            "stop_buffer_atr": STOP_BUFFER_ATR,
        },
        "detector_counts": dict(detector.counts),
        "initiative_plans": len(detector.continuation_plans),
        "resolution_counts": dict(resolver.counts),
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "impact_resolution_timebar_week_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-timebar-aggtrades")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-impact-resolution-timebar")
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
