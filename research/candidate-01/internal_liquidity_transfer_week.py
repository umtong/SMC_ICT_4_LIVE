#!/usr/bin/env python3
"""Five-minute internal-liquidity transfer to external range liquidity.

This scenario is separate from the aggressive-flow pulse detector.  It treats a
completed 20-bar range as a dealing range and asks whether a new initiative
transfers inventory from the opposite half through equilibrium toward the still
untouched external liquidity:

1. A three-bar standardized aggressive-flow pulse begins in discount for a long
   or premium for a short.
2. The completed pulse crosses the range midpoint, closes at least 0.05 ATR on
   the destination side, moves at least 0.65 ATR with path efficiency >= 0.55,
   and closes in the directional 70% of its bar.
3. The destination external range edge has not yet traded.
4. Enter at the next five-minute open only while equilibrium still holds.
5. Invalidate beyond the complete pulse extreme plus 0.15 ATR and target the
   untouched external range edge.

The market-data clock is UTC-aligned five-minute aggregate-trade auctions, so
signed order flow and opportunity resolution remain comparable across years.
The 20-bar structure is 100 minutes and the shared 45-bar maximum hold is 225
minutes.  Execution uses 7 bps per side, current-NAV 3% planned risk,
stop-first ambiguity and one global position.  One invocation evaluates exactly
one BTC week.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
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
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_regime_probe import (  # noqa: E402
    MAX_HOLD_BARS,
    PULSE_BARS,
    STRUCTURE_BARS,
    EventFeature,
    ImpactRegimeDetector,
    PulseEvent,
    ScenarioPlan,
    simulate,
)


TIMEFRAME_MINUTES = 5
MIN_STRUCTURE_WIDTH_ATR = 1.25
MIN_MOVE_ATR = 0.65
MIN_PATH_EFFICIENCY = 0.55
MIN_ALIGNED_CLOSE_LOCATION = 0.70
MIDPOINT_HOLD_ATR = 0.05
STOP_BUFFER_ATR = 0.15


@dataclass(frozen=True, slots=True)
class TransferDecision:
    scenario_id: str
    event_index: int
    event_time_ns: int
    direction: str
    accepted: bool
    reason_code: str
    start_price: float
    start_fraction: float
    end_fraction: float
    structure_midpoint: float
    structure_width_atr: float
    move_atr: float
    path_efficiency: float
    aligned_close_location: float
    target_price: float
    stop_price: float


def classify_transfer(
    *,
    pulse: PulseEvent,
    start_price: float,
) -> tuple[TransferDecision, ScenarioPlan | None]:
    side = Side(pulse.direction)
    width = pulse.structure_high - pulse.structure_low
    midpoint = 0.5 * (pulse.structure_high + pulse.structure_low)
    start_fraction = (
        (start_price - pulse.structure_low) / width
        if width > 0.0
        else 0.5
    )
    end_fraction = (
        (pulse.pulse_close - pulse.structure_low) / width
        if width > 0.0
        else 0.5
    )
    target = pulse.structure_high if side is Side.LONG else pulse.structure_low
    stop = (
        pulse.pulse_low - STOP_BUFFER_ATR * pulse.atr
        if side is Side.LONG
        else pulse.pulse_high + STOP_BUFFER_ATR * pulse.atr
    )

    if pulse.structure_width_atr < MIN_STRUCTURE_WIDTH_ATR:
        reason = "DEALING_RANGE_TOO_NARROW"
    elif pulse.move_atr < MIN_MOVE_ATR:
        reason = "INSUFFICIENT_DISPLACEMENT"
    elif pulse.path_efficiency < MIN_PATH_EFFICIENCY:
        reason = "INEFFICIENT_PATH"
    elif pulse.aligned_close_location < MIN_ALIGNED_CLOSE_LOCATION:
        reason = "WEAK_DIRECTIONAL_CLOSE"
    elif side is Side.LONG and start_price > midpoint:
        reason = "LONG_DID_NOT_BEGIN_IN_DISCOUNT"
    elif side is Side.SHORT and start_price < midpoint:
        reason = "SHORT_DID_NOT_BEGIN_IN_PREMIUM"
    elif side is Side.LONG and pulse.pulse_close < midpoint + MIDPOINT_HOLD_ATR * pulse.atr:
        reason = "LONG_DID_NOT_HOLD_ABOVE_EQUILIBRIUM"
    elif side is Side.SHORT and pulse.pulse_close > midpoint - MIDPOINT_HOLD_ATR * pulse.atr:
        reason = "SHORT_DID_NOT_HOLD_BELOW_EQUILIBRIUM"
    elif side is Side.LONG and pulse.pulse_high >= target:
        reason = "LONG_EXTERNAL_TARGET_ALREADY_TOUCHED"
    elif side is Side.SHORT and pulse.pulse_low <= target:
        reason = "SHORT_EXTERNAL_TARGET_ALREADY_TOUCHED"
    else:
        reason = "INTERNAL_LIQUIDITY_TRANSFER_CONFIRMED"

    accepted = reason == "INTERNAL_LIQUIDITY_TRANSFER_CONFIRMED"
    decision = TransferDecision(
        scenario_id=pulse.scenario_id,
        event_index=pulse.bar_index,
        event_time_ns=pulse.event_time_ns,
        direction=pulse.direction,
        accepted=accepted,
        reason_code=reason,
        start_price=start_price,
        start_fraction=start_fraction,
        end_fraction=end_fraction,
        structure_midpoint=midpoint,
        structure_width_atr=pulse.structure_width_atr,
        move_atr=pulse.move_atr,
        path_efficiency=pulse.path_efficiency,
        aligned_close_location=pulse.aligned_close_location,
        target_price=target,
        stop_price=stop,
    )
    if not accepted:
        return decision, None

    plan = ScenarioPlan(
        scenario_id=pulse.scenario_id + ":internal-transfer",
        response="CONTINUATION",
        side=side,
        signal_bar_index=pulse.bar_index,
        signal_time_ns=pulse.event_time_ns,
        stop_price=stop,
        target_price=target,
        confirmation_hold_price=midpoint,
        structure_high=pulse.structure_high,
        structure_low=pulse.structure_low,
        structure_midpoint=midpoint,
        pulse_high=pulse.pulse_high,
        pulse_low=pulse.pulse_low,
        pulse_flow_score=pulse.flow_score,
        pulse_move_atr=pulse.move_atr,
        pulse_path_efficiency=pulse.path_efficiency,
        pulse_close_location=pulse.aligned_close_location,
        reason_code=reason,
    )
    return decision, plan


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
    plans: list[ScenarioPlan] = []
    decisions: list[TransferDecision] = []
    counts: Counter[str] = Counter()
    previous_pulses = 0
    for bar in bars:
        detector.on_bar(bar)
        for pulse in detector.pulse_events[previous_pulses:]:
            pulse_start = pulse.bar_index - PULSE_BARS + 1
            if pulse_start <= 0:
                counts["MISSING_PRE_PULSE_CLOSE"] += 1
                continue
            start_price = detector.features[pulse_start - 1].bar.close
            decision, plan = classify_transfer(
                pulse=pulse,
                start_price=start_price,
            )
            decisions.append(decision)
            counts[decision.reason_code] += 1
            if plan is not None:
                plans.append(plan)
        previous_pulses = len(detector.pulse_events)

    trades, metrics, daily, rejections = simulate(
        features=detector.features,
        plans=plans,
        evaluation_start_ns=start_ns,
        evaluation_end_ns=end_ns,
        starting_nav=float(execution["starting_nav"]),
        cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
        exit_on_boundary_reacceptance=False,
    )
    evaluation_bars = [bar for bar in bars if start_ns <= bar.end_time_ns < end_ns]
    range_bps = [bar.range_fraction * 10_000.0 for bar in evaluation_bars]

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    daily.to_csv(output / "daily_nav.csv", index=False)
    rejections.to_csv(output / "rejections.csv", index=False)
    pd.DataFrame(asdict(row) for row in decisions).to_csv(
        output / "transfer_decisions.csv",
        index=False,
    )
    payload = {
        "candidate": "internal dealing-range liquidity transfer",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "timeframe_minutes": TIMEFRAME_MINUTES,
        "structure_minutes": STRUCTURE_BARS * TIMEFRAME_MINUTES,
        "initiative_minutes": PULSE_BARS * TIMEFRAME_MINUTES,
        "maximum_hold_minutes": MAX_HOLD_BARS * TIMEFRAME_MINUTES,
        "evaluation_bars": len(evaluation_bars),
        "bars_per_day": len(evaluation_bars) / 7.0,
        "median_bar_range_bps": float(median(range_bps)) if range_bps else None,
        "scenario_parameters": {
            "minimum_structure_width_atr": MIN_STRUCTURE_WIDTH_ATR,
            "minimum_move_atr": MIN_MOVE_ATR,
            "minimum_path_efficiency": MIN_PATH_EFFICIENCY,
            "minimum_aligned_close_location": MIN_ALIGNED_CLOSE_LOCATION,
            "midpoint_hold_atr": MIDPOINT_HOLD_ATR,
            "stop_buffer_atr": STOP_BUFFER_ATR,
        },
        "detector_counts": dict(detector.counts),
        "decision_counts": dict(counts),
        "plans": len(plans),
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "internal_liquidity_transfer_week_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-timebar-aggtrades")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-internal-liquidity-transfer")
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
