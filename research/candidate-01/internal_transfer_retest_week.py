#!/usr/bin/env python3
"""Five-minute internal transfer with one-minute equilibrium-retest entry.

The first-week direct internal-transfer scenario produced 17 structurally valid
plans but every next-five-minute entry failed the fixed cost/reward gate.  This
candidate changes only execution timing while preserving the direction,
structure, invalidation source and target:

* detect the completed 5m discount-to-premium or premium-to-discount transfer;
* do not chase the displacement close;
* for at most 15 minutes, watch completed 1m aggregate-trade bars;
* the range equilibrium must be traded, the 1m bar must close on the destination
  side, and raw signed aggressive flow must align with the transfer;
* enter at the next 1m open while equilibrium still holds;
* stop beyond every observed 5m pulse and 1m retest-path extreme plus 0.15 5m
  ATR; target the original untouched external range edge.

If equilibrium fails or the target trades before entry, the setup is canceled.
The 45-bar holding contract is converted from 5m to 225 completed 1m bars, so
maximum elapsed holding time remains 225 minutes.  Costs, current-NAV 3% risk,
stop-first ambiguity and one global position are unchanged.  One invocation
evaluates exactly one BTC week.
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
from aggtrade_time_clock import NS_PER_MINUTE, iter_time_bars  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
import impact_regime_probe as execution_module  # noqa: E402
from impact_regime_probe import (  # noqa: E402
    MAX_HOLD_BARS,
    PULSE_BARS,
    EventFeature,
    ImpactRegimeDetector,
    PulseEvent,
    ScenarioPlan,
    simulate,
)
from internal_liquidity_transfer_week import (  # noqa: E402
    STOP_BUFFER_ATR,
    TransferDecision,
    classify_transfer,
)


STRUCTURE_TIMEFRAME_MINUTES = 5
EXECUTION_TIMEFRAME_MINUTES = 1
RETEST_WINDOW_MINUTES = 15
RETEST_ZONE_ATR = 0.10
EQUILIBRIUM_FAILURE_ATR = 0.05
MIN_ALIGNED_RAW_IMBALANCE = 0.0
ONE_MINUTE_HOLD_BARS = MAX_HOLD_BARS * STRUCTURE_TIMEFRAME_MINUTES


@dataclass(slots=True)
class TransferRetestSetup:
    scenario_id: str
    side: Side
    source_signal_time_ns: int
    expiry_time_ns: int
    atr: float
    equilibrium: float
    target_price: float
    path_high: float
    path_low: float
    structure_high: float
    structure_low: float
    pulse_flow_score: float
    pulse_move_atr: float
    pulse_path_efficiency: float
    pulse_close_location: float


@dataclass(frozen=True, slots=True)
class TransferRetestTransition:
    scenario_id: str
    event_type: str
    event_index: int
    event_time_ns: int
    reason_code: str
    side: str
    equilibrium: float
    target_price: float
    path_high: float
    path_low: float
    raw_imbalance: float
    close: float


class TransferRetestStateMachine:
    def __init__(self) -> None:
        self.active: list[TransferRetestSetup] = []
        self.plans: list[ScenarioPlan] = []
        self.transitions: list[TransferRetestTransition] = []
        self.counts: Counter[str] = Counter()

    def _transition(
        self,
        *,
        setup: TransferRetestSetup,
        feature: EventFeature,
        index: int,
        event_type: str,
        reason_code: str,
    ) -> None:
        self.transitions.append(
            TransferRetestTransition(
                scenario_id=setup.scenario_id,
                event_type=event_type,
                event_index=index,
                event_time_ns=feature.bar.end_time_ns,
                reason_code=reason_code,
                side=setup.side.value,
                equilibrium=setup.equilibrium,
                target_price=setup.target_price,
                path_high=setup.path_high,
                path_low=setup.path_low,
                raw_imbalance=feature.bar.imbalance,
                close=feature.bar.close,
            ),
        )

    def arm(self, *, source: ScenarioPlan, pulse: PulseEvent) -> None:
        setup = TransferRetestSetup(
            scenario_id=source.scenario_id + ":equilibrium-retest",
            side=source.side,
            source_signal_time_ns=source.signal_time_ns,
            expiry_time_ns=(
                source.signal_time_ns + RETEST_WINDOW_MINUTES * NS_PER_MINUTE
            ),
            atr=pulse.atr,
            equilibrium=source.confirmation_hold_price,
            target_price=source.target_price,
            path_high=pulse.pulse_high,
            path_low=pulse.pulse_low,
            structure_high=source.structure_high,
            structure_low=source.structure_low,
            pulse_flow_score=source.pulse_flow_score,
            pulse_move_atr=source.pulse_move_atr,
            pulse_path_efficiency=source.pulse_path_efficiency,
            pulse_close_location=source.pulse_close_location,
        )
        self.active.append(setup)
        self.counts["armed"] += 1

    @staticmethod
    def _target_touched(setup: TransferRetestSetup, feature: EventFeature) -> bool:
        return (
            feature.bar.high >= setup.target_price
            if setup.side is Side.LONG
            else feature.bar.low <= setup.target_price
        )

    @staticmethod
    def _equilibrium_failed(setup: TransferRetestSetup, feature: EventFeature) -> bool:
        return (
            feature.bar.close
            < setup.equilibrium - EQUILIBRIUM_FAILURE_ATR * setup.atr
            if setup.side is Side.LONG
            else feature.bar.close
            > setup.equilibrium + EQUILIBRIUM_FAILURE_ATR * setup.atr
        )

    @staticmethod
    def _retest_confirmed(setup: TransferRetestSetup, feature: EventFeature) -> bool:
        aligned_flow = setup.side.sign * feature.bar.imbalance
        if setup.side is Side.LONG:
            touched = feature.bar.low <= setup.equilibrium + RETEST_ZONE_ATR * setup.atr
            held = feature.bar.close >= setup.equilibrium
        else:
            touched = feature.bar.high >= setup.equilibrium - RETEST_ZONE_ATR * setup.atr
            held = feature.bar.close <= setup.equilibrium
        return touched and held and aligned_flow > MIN_ALIGNED_RAW_IMBALANCE

    @staticmethod
    def _plan(
        setup: TransferRetestSetup,
        feature: EventFeature,
        index: int,
    ) -> ScenarioPlan:
        stop = (
            setup.path_low - STOP_BUFFER_ATR * setup.atr
            if setup.side is Side.LONG
            else setup.path_high + STOP_BUFFER_ATR * setup.atr
        )
        return ScenarioPlan(
            scenario_id=setup.scenario_id + f":confirm:{index}",
            response="CONTINUATION",
            side=setup.side,
            signal_bar_index=index,
            signal_time_ns=feature.bar.end_time_ns,
            stop_price=stop,
            target_price=setup.target_price,
            confirmation_hold_price=setup.equilibrium,
            structure_high=setup.structure_high,
            structure_low=setup.structure_low,
            structure_midpoint=setup.equilibrium,
            pulse_high=setup.path_high,
            pulse_low=setup.path_low,
            pulse_flow_score=setup.pulse_flow_score,
            pulse_move_atr=setup.pulse_move_atr,
            pulse_path_efficiency=setup.pulse_path_efficiency,
            pulse_close_location=setup.pulse_close_location,
            reason_code="INTERNAL_TRANSFER_EQUILIBRIUM_RETEST_HELD",
        )

    def on_feature(self, *, index: int, feature: EventFeature) -> list[ScenarioPlan]:
        emitted: list[ScenarioPlan] = []
        remaining: list[TransferRetestSetup] = []
        for setup in self.active:
            if feature.bar.end_time_ns <= setup.source_signal_time_ns:
                remaining.append(setup)
                continue
            if feature.bar.end_time_ns > setup.expiry_time_ns:
                self.counts["expired"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="RETEST_WINDOW_EXPIRED",
                )
                continue
            setup.path_high = max(setup.path_high, feature.bar.high)
            setup.path_low = min(setup.path_low, feature.bar.low)
            if self._target_touched(setup, feature):
                self.counts["target_consumed"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="EXTERNAL_LIQUIDITY_REACHED_BEFORE_RETEST_ENTRY",
                )
                continue
            if self._equilibrium_failed(setup, feature):
                self.counts["equilibrium_failed"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="TRANSFER_EQUILIBRIUM_FAILED",
                )
                continue
            if self._retest_confirmed(setup, feature):
                plan = self._plan(setup, feature, index)
                self.plans.append(plan)
                emitted.append(plan)
                self.counts["confirmed"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="PLAN_EMITTED",
                    reason_code=plan.reason_code,
                )
                continue
            remaining.append(setup)
        self.active = remaining
        return emitted


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
    five_minute_bars = list(
        iter_time_bars(
            iter_downloads(records),
            interval_minutes=STRUCTURE_TIMEFRAME_MINUTES,
            include_partial=False,
        ),
    )
    one_minute_bars = list(
        iter_time_bars(
            iter_downloads(records),
            interval_minutes=EXECUTION_TIMEFRAME_MINUTES,
            include_partial=False,
        ),
    )

    detector = ImpactRegimeDetector()
    accepted: list[tuple[ScenarioPlan, PulseEvent, TransferDecision]] = []
    decisions: list[TransferDecision] = []
    previous_pulses = 0
    for bar in five_minute_bars:
        detector.on_bar(bar)
        for pulse in detector.pulse_events[previous_pulses:]:
            pulse_start = pulse.bar_index - PULSE_BARS + 1
            if pulse_start <= 0:
                continue
            start_price = detector.features[pulse_start - 1].bar.close
            decision, source = classify_transfer(
                pulse=pulse,
                start_price=start_price,
            )
            decisions.append(decision)
            if source is not None:
                accepted.append((source, pulse, decision))
        previous_pulses = len(detector.pulse_events)

    arm_by_time: dict[int, list[tuple[ScenarioPlan, PulseEvent]]] = {}
    for source, pulse, _ in accepted:
        arm_by_time.setdefault(source.signal_time_ns, []).append((source, pulse))

    execution_features = [
        EventFeature(
            bar=bar,
            true_range=bar.high - bar.low,
            atr=None,
            imbalance_z=None,
        )
        for bar in one_minute_bars
    ]
    scenario = TransferRetestStateMachine()
    for index, feature in enumerate(execution_features):
        scenario.on_feature(index=index, feature=feature)
        for source, pulse in arm_by_time.get(feature.bar.end_time_ns, ()):
            scenario.arm(source=source, pulse=pulse)

    original_hold = execution_module.MAX_HOLD_BARS
    execution_module.MAX_HOLD_BARS = ONE_MINUTE_HOLD_BARS
    try:
        trades, metrics, daily, rejections = simulate(
            features=execution_features,
            plans=scenario.plans,
            evaluation_start_ns=start_ns,
            evaluation_end_ns=end_ns,
            starting_nav=float(execution["starting_nav"]),
            cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
            exit_on_boundary_reacceptance=False,
        )
    finally:
        execution_module.MAX_HOLD_BARS = original_hold

    evaluation_1m = [
        feature.bar
        for feature in execution_features
        if start_ns <= feature.bar.end_time_ns < end_ns
    ]
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    daily.to_csv(output / "daily_nav.csv", index=False)
    rejections.to_csv(output / "rejections.csv", index=False)
    pd.DataFrame(asdict(row) for row in decisions).to_csv(
        output / "transfer_decisions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.transitions).to_csv(
        output / "retest_transitions.csv",
        index=False,
    )
    payload = {
        "candidate": "5m internal transfer with 1m equilibrium retest",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "structure_timeframe_minutes": STRUCTURE_TIMEFRAME_MINUTES,
        "execution_timeframe_minutes": EXECUTION_TIMEFRAME_MINUTES,
        "retest_window_minutes": RETEST_WINDOW_MINUTES,
        "retest_zone_atr": RETEST_ZONE_ATR,
        "equilibrium_failure_atr": EQUILIBRIUM_FAILURE_ATR,
        "minimum_aligned_raw_imbalance": MIN_ALIGNED_RAW_IMBALANCE,
        "maximum_hold_minutes": ONE_MINUTE_HOLD_BARS,
        "accepted_transfer_setups": len(accepted),
        "retest_counts": dict(scenario.counts),
        "plans": len(scenario.plans),
        "evaluation_one_minute_bars": len(evaluation_1m),
        "median_one_minute_range_bps": (
            float(median([bar.range_fraction * 10_000.0 for bar in evaluation_1m]))
            if evaluation_1m
            else None
        ),
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "internal_transfer_retest_week_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-timebar-aggtrades")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-internal-transfer-retest")
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
