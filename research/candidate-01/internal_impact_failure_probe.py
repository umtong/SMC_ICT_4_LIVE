#!/usr/bin/env python3
"""First-week causal internal-initiative failure scenario.

The successful external failed-impact scenario waits for aggressive initiative
to leave a completed structure and then fail.  This independent scenario covers
a different auction state: a completed rotational balance where a strong
initiative traverses the interior but cannot reach external liquidity.

Causal sequence
---------------
1. The prior 20 equal-notional event bars form a rotational balance: low close
   path efficiency and repeated midpoint crossings.
2. A three-event aggressive-flow pulse starts from the opposite half, crosses
   the balance midpoint efficiently, but remains inside the external edge.
3. No trade is taken from the pulse itself.
4. Within three completed events, opposite flow appears and price closes through
   the initiative pulse midpoint.
5. Enter on the next event open only while that midpoint failure still holds.
6. Invalidate beyond every observed pulse/confirmation-path extreme plus
   0.15 event ATR; target the opposite external edge of the completed balance.

The 20-minute prior-day equal-notional clock, impact detector, execution costs,
3% current-NAV risk, stop-first ambiguity and one-position gate are frozen.
Only the first random BTC week is evaluated.  This program does not combine the
scenario with external failed impact and cannot run a long period.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
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

from aggtrade_clock import (  # noqa: E402
    calibrate_target_from_minutes,
    iter_volume_bars,
    minute_quote_totals,
)
from aggtrade_data import download_aggtrade_days, iter_downloads  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_failure_candidate import ImpactFailureStateMachine  # noqa: E402
from impact_regime_probe import (  # noqa: E402
    CLOCK_CALIBRATION_MINUTES,
    PULSE_BARS,
    STRUCTURE_BARS,
    ImpactRegimeDetector,
    PulseEvent,
    ScenarioPlan,
    path_efficiency,
    simulate,
)


BALANCE_MAX_EFFICIENCY = 0.35
MIN_MIDPOINT_CROSSINGS = 4
MIN_PULSE_MOVE_ATR = 0.65
MIN_PULSE_EFFICIENCY = 0.55
MIN_ALIGNED_CLOSE_LOCATION = 0.70
MAX_EXTERNAL_EXCURSION_ATR = 0.08
MIN_END_HALF_PROGRESS = 0.10
MIN_STRUCTURE_WIDTH_ATR = 2.0


@dataclass(frozen=True, slots=True)
class InternalPulseDecision:
    scenario_id: str
    event_index: int
    event_time_ns: int
    direction: str
    accepted: bool
    reason_code: str
    structure_efficiency: float
    midpoint_crossings: int
    start_location: float
    end_location: float
    structure_width_atr: float
    pulse_move_atr: float
    pulse_efficiency: float
    aligned_close_location: float
    outward_excursion_atr: float
    pulse_midpoint: float


def midpoint_crossings(closes: list[float], midpoint: float) -> int:
    if len(closes) < 2:
        return 0
    result = 0
    previous = closes[0] - midpoint
    for close in closes[1:]:
        current = close - midpoint
        if previous == 0.0:
            previous = current
            continue
        if current == 0.0 or previous * current < 0.0:
            result += 1
        previous = current
    return result


def classify_internal_pulse(
    *,
    detector: ImpactRegimeDetector,
    pulse: PulseEvent,
) -> tuple[ScenarioPlan | None, InternalPulseDecision]:
    index = pulse.bar_index
    pulse_start = index - PULSE_BARS + 1
    structure_start = pulse_start - STRUCTURE_BARS
    structure_features = detector.features[structure_start:pulse_start]
    pulse_features = detector.features[pulse_start : index + 1]
    if len(structure_features) != STRUCTURE_BARS or len(pulse_features) != PULSE_BARS:
        raise ValueError("pulse does not have complete causal structure history")

    structure_bars = [feature.bar for feature in structure_features]
    pulse_bars = [feature.bar for feature in pulse_features]
    structure_high = max(bar.high for bar in structure_bars)
    structure_low = min(bar.low for bar in structure_bars)
    width = structure_high - structure_low
    midpoint = 0.5 * (structure_high + structure_low)
    closes = [bar.close for bar in structure_bars]
    efficiency = path_efficiency(closes)
    crossings = midpoint_crossings(closes, midpoint)
    pre_pulse_close = structure_bars[-1].close
    pulse_close = pulse_bars[-1].close
    start_location = (
        (pre_pulse_close - structure_low) / width if width > 0.0 else 0.5
    )
    end_location = (
        (pulse_close - structure_low) / width if width > 0.0 else 0.5
    )
    pulse_midpoint = 0.5 * (pulse.pulse_high + pulse.pulse_low)
    direction = Side(pulse.direction)

    reason = "ACCEPTED"
    accepted = True
    if pulse.classification != "NO_TRADE":
        accepted = False
        reason = "NOT_INTERNAL_UNCLASSIFIED_PULSE"
    elif pulse.structure_width_atr < MIN_STRUCTURE_WIDTH_ATR:
        accepted = False
        reason = "STRUCTURE_TOO_NARROW"
    elif efficiency > BALANCE_MAX_EFFICIENCY:
        accepted = False
        reason = "STRUCTURE_NOT_ROTATIONAL"
    elif crossings < MIN_MIDPOINT_CROSSINGS:
        accepted = False
        reason = "INSUFFICIENT_MIDPOINT_ROTATION"
    elif pulse.move_atr < MIN_PULSE_MOVE_ATR:
        accepted = False
        reason = "PULSE_DISPLACEMENT_TOO_SMALL"
    elif pulse.path_efficiency < MIN_PULSE_EFFICIENCY:
        accepted = False
        reason = "PULSE_NOT_EFFICIENT"
    elif pulse.aligned_close_location < MIN_ALIGNED_CLOSE_LOCATION:
        accepted = False
        reason = "PULSE_CLOSE_NOT_DIRECTIONAL"
    elif pulse.outward_excursion_atr >= MAX_EXTERNAL_EXCURSION_ATR:
        accepted = False
        reason = "EXTERNAL_LIQUIDITY_ALREADY_TOUCHED"
    elif direction is Side.LONG and not (
        start_location <= 0.50
        and end_location >= 0.50 + MIN_END_HALF_PROGRESS
        and pulse_close < structure_high
    ):
        accepted = False
        reason = "BULLISH_PULSE_NOT_INTERIOR_TRAVERSAL"
    elif direction is Side.SHORT and not (
        start_location >= 0.50
        and end_location <= 0.50 - MIN_END_HALF_PROGRESS
        and pulse_close > structure_low
    ):
        accepted = False
        reason = "BEARISH_PULSE_NOT_INTERIOR_TRAVERSAL"

    decision = InternalPulseDecision(
        scenario_id=pulse.scenario_id,
        event_index=index,
        event_time_ns=pulse.event_time_ns,
        direction=direction.value,
        accepted=accepted,
        reason_code=reason,
        structure_efficiency=efficiency,
        midpoint_crossings=crossings,
        start_location=start_location,
        end_location=end_location,
        structure_width_atr=pulse.structure_width_atr,
        pulse_move_atr=pulse.move_atr,
        pulse_efficiency=pulse.path_efficiency,
        aligned_close_location=pulse.aligned_close_location,
        outward_excursion_atr=pulse.outward_excursion_atr,
        pulse_midpoint=pulse_midpoint,
    )
    if not accepted:
        return None, decision

    # This pseudo-initiative plan is never executed. It arms the shared strict
    # failure state machine with the pulse midpoint as the value-hold boundary.
    initiative = ScenarioPlan(
        scenario_id=pulse.scenario_id + ":internal-initiative",
        response="CONTINUATION",
        side=direction,
        signal_bar_index=index,
        signal_time_ns=pulse.event_time_ns,
        stop_price=(
            pulse.pulse_low if direction is Side.LONG else pulse.pulse_high
        ),
        target_price=(
            structure_high if direction is Side.LONG else structure_low
        ),
        confirmation_hold_price=pulse_midpoint,
        structure_high=structure_high,
        structure_low=structure_low,
        structure_midpoint=midpoint,
        pulse_high=pulse.pulse_high,
        pulse_low=pulse.pulse_low,
        pulse_flow_score=pulse.flow_score,
        pulse_move_atr=pulse.move_atr,
        pulse_path_efficiency=pulse.path_efficiency,
        pulse_close_location=pulse.aligned_close_location,
        reason_code="BALANCED_INTERNAL_INITIATIVE_OBSERVED",
    )
    return initiative, decision


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
    failure = ImpactFailureStateMachine(
        include_intermediate_extremes=True,
        reject_consumed_target=True,
    )
    decisions: list[InternalPulseDecision] = []
    classification_counts: Counter[str] = Counter()
    previous_pulses = 0
    for index, bar in enumerate(bars):
        detector.on_bar(bar)
        feature = detector.features[-1]
        initiatives: list[ScenarioPlan] = []
        if len(detector.pulse_events) > previous_pulses:
            pulse = detector.pulse_events[-1]
            initiative, decision = classify_internal_pulse(
                detector=detector,
                pulse=pulse,
            )
            decisions.append(decision)
            classification_counts[decision.reason_code] += 1
            if initiative is not None:
                initiatives.append(initiative)
        previous_pulses = len(detector.pulse_events)
        failure.on_feature(
            index=index,
            feature=feature,
            new_initiative_plans=initiatives,
        )

    trades, metrics, daily, rejections = simulate(
        features=detector.features,
        plans=failure.plans,
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
    pd.DataFrame(asdict(row) for row in decisions).to_csv(
        output / "internal_pulse_decisions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in failure.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    payload = {
        "candidate": "balanced internal-initiative failure reversal",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "clock_calibration_minutes": CLOCK_CALIBRATION_MINUTES,
        "target_quote_notional": target_quote,
        "evaluation_event_bars": len(evaluation_bars),
        "event_bars_per_day": len(evaluation_bars) / 7.0,
        "scenario_parameters": {
            "balance_max_efficiency": BALANCE_MAX_EFFICIENCY,
            "minimum_midpoint_crossings": MIN_MIDPOINT_CROSSINGS,
            "minimum_pulse_move_atr": MIN_PULSE_MOVE_ATR,
            "minimum_pulse_efficiency": MIN_PULSE_EFFICIENCY,
            "minimum_aligned_close_location": MIN_ALIGNED_CLOSE_LOCATION,
            "maximum_external_excursion_atr": MAX_EXTERNAL_EXCURSION_ATR,
            "minimum_end_half_progress": MIN_END_HALF_PROGRESS,
            "minimum_structure_width_atr": MIN_STRUCTURE_WIDTH_ATR,
        },
        "pulse_classification_counts": dict(classification_counts),
        "failure_state_counts": dict(failure.counts),
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "combined_with_external_failure": False,
        "long_evaluation_run": False,
    }
    atomic_json(output / "internal_impact_failure_summary.json", payload)
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
        default=ROOT / "artifacts" / "candidate-01-internal-impact-failure",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
