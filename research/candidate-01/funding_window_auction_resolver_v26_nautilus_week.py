#!/usr/bin/env python3
"""Resolve prior funding-window edge raids as failure or durable acceptance.

The immediately completed eight-hour funding window supplies a causal dealing
range.  A raid beyond either edge arms one mutually exclusive auction response:

* failure has precedence: price closes back inside with aggressive flow toward
  the opposite edge, producing a reversal plan to that edge;
* otherwise, after two completed equal-notional response events, two outside
  closes plus positive cumulative aligned flow produce a continuation plan to
  one completed-range-width projection.

The primary trades both resolved outcomes.  Failure-only and acceptance-only
controls identify which response contributes.  Execution, fees, margin,
positions, PnL and NAV remain entirely inside NautilusTrader.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Literal

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from adaptive_aggtrade_clock import build_daily_cost_resolved_bars  # noqa: E402
from aggtrade_data import download_aggtrade_days  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import MAXIMUM_HOLD_NS  # noqa: E402
from funding_window_failed_auction_v25_nautilus_week import (  # noqa: E402
    FundingWindowBook,
)
from impact_regime_probe import EventFeature, ImpactRegimeDetector, ScenarioPlan  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import ROUND_TRIP_COST_BPS  # noqa: E402
from mss_absorption_reacceleration_v24_nautilus_week import execution_trade_windows  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from resolved_impact_v17_nautilus_week import atomic_json, load_execution  # noqa: E402


RULES = ("two-sided-resolver", "failure-only", "acceptance-only")
CONTEXT_DAYS = 7
RESPONSE_EVENTS = 2
STOP_BUFFER_FRACTION = 7.0 / 10_000.0


@dataclass(slots=True)
class AuctionSetup:
    scenario_id: str
    window_id: str
    outward_side: Side
    reversal_side: Side
    created_index: int
    expiry_index: int
    expiry_time_ns: int
    boundary: float
    opposite_edge: float
    range_width: float
    path_high: float
    path_low: float
    outside_holds: int = 0
    aligned_flow_sum_z: float = 0.0


@dataclass(frozen=True, slots=True)
class AuctionTransition:
    scenario_id: str
    window_id: str
    event_type: str
    event_index: int
    event_time_ns: int
    reason_code: str
    outward_side: str
    selected_side: str | None
    boundary: float
    opposite_edge: float
    projection: float
    path_high: float
    path_low: float
    outside_holds: int
    aligned_flow_sum_z: float
    imbalance_z: float | None
    close: float


class FundingAuctionResolver:
    def __init__(self, *, rule: str, book: FundingWindowBook) -> None:
        if rule not in RULES:
            raise ValueError(rule)
        self.rule = rule
        self.book = book
        self.setups: list[AuctionSetup] = []
        self.plans: list[ScenarioPlan] = []
        self.transitions: list[AuctionTransition] = []
        self.counts: Counter[str] = Counter()
        self.high_armed = False
        self.low_armed = False

    @staticmethod
    def _reverse(side: Side) -> Side:
        return Side.SHORT if side is Side.LONG else Side.LONG

    def _transition(
        self,
        *,
        setup: AuctionSetup,
        feature: EventFeature,
        index: int,
        event_type: str,
        reason_code: str,
        selected_side: Side | None,
    ) -> None:
        projection = (
            setup.boundary + setup.range_width
            if setup.outward_side is Side.LONG
            else setup.boundary - setup.range_width
        )
        self.transitions.append(
            AuctionTransition(
                scenario_id=setup.scenario_id,
                window_id=setup.window_id,
                event_type=event_type,
                event_index=index,
                event_time_ns=int(feature.bar.end_time_ns),
                reason_code=reason_code,
                outward_side=setup.outward_side.value,
                selected_side=(selected_side.value if selected_side else None),
                boundary=setup.boundary,
                opposite_edge=setup.opposite_edge,
                projection=projection,
                path_high=setup.path_high,
                path_low=setup.path_low,
                outside_holds=setup.outside_holds,
                aligned_flow_sum_z=setup.aligned_flow_sum_z,
                imbalance_z=(
                    float(feature.imbalance_z)
                    if feature.imbalance_z is not None
                    else None
                ),
                close=float(feature.bar.close),
            ),
        )

    @staticmethod
    def _outside(setup: AuctionSetup, feature: EventFeature) -> bool:
        return (
            float(feature.bar.close) > setup.boundary
            if setup.outward_side is Side.LONG
            else float(feature.bar.close) < setup.boundary
        )

    @staticmethod
    def _aligned_z(side: Side, feature: EventFeature) -> float | None:
        return (
            side.sign * float(feature.imbalance_z)
            if feature.imbalance_z is not None
            else None
        )

    def _emit_reversal(
        self,
        *,
        setup: AuctionSetup,
        feature: EventFeature,
        index: int,
    ) -> None:
        if self.rule == "acceptance-only":
            self.counts["failure_resolved_but_disabled"] += 1
            return
        stop = (
            setup.path_high * (1.0 + STOP_BUFFER_FRACTION)
            if setup.reversal_side is Side.SHORT
            else setup.path_low * (1.0 - STOP_BUFFER_FRACTION)
        )
        plan = ScenarioPlan(
            scenario_id=setup.scenario_id + f":failure:{index}",
            response="EXHAUSTION_REVERSAL",
            side=setup.reversal_side,
            signal_bar_index=index,
            signal_time_ns=int(feature.bar.end_time_ns),
            stop_price=float(stop),
            target_price=float(setup.opposite_edge),
            confirmation_hold_price=float(setup.boundary),
            structure_high=max(setup.path_high, setup.boundary, setup.opposite_edge),
            structure_low=min(setup.path_low, setup.boundary, setup.opposite_edge),
            structure_midpoint=0.5 * (setup.boundary + setup.opposite_edge),
            pulse_high=setup.path_high,
            pulse_low=setup.path_low,
            pulse_flow_score=setup.aligned_flow_sum_z,
            pulse_move_atr=0.0,
            pulse_path_efficiency=0.0,
            pulse_close_location=0.0,
            reason_code="FUNDING_EDGE_RAID_FAILED_AND_REENTERED",
        )
        self.plans.append(plan)
        self.counts["failure_plans_emitted"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="PLAN_EMITTED",
            reason_code=plan.reason_code,
            selected_side=plan.side,
        )

    def _emit_acceptance(
        self,
        *,
        setup: AuctionSetup,
        feature: EventFeature,
        index: int,
    ) -> None:
        if self.rule == "failure-only":
            self.counts["acceptance_resolved_but_disabled"] += 1
            return
        target = (
            setup.boundary + setup.range_width
            if setup.outward_side is Side.LONG
            else setup.boundary - setup.range_width
        )
        target_consumed = (
            float(feature.bar.high) >= target
            if setup.outward_side is Side.LONG
            else float(feature.bar.low) <= target
        )
        if target_consumed:
            self.counts["projection_consumed_before_entry"] += 1
            self._transition(
                setup=setup,
                feature=feature,
                index=index,
                event_type="INVALIDATED",
                reason_code="MEASURED_PROJECTION_ALREADY_CONSUMED",
                selected_side=None,
            )
            return
        stop = (
            setup.boundary * (1.0 - STOP_BUFFER_FRACTION)
            if setup.outward_side is Side.LONG
            else setup.boundary * (1.0 + STOP_BUFFER_FRACTION)
        )
        plan = ScenarioPlan(
            scenario_id=setup.scenario_id + f":acceptance:{index}",
            response="CONTINUATION",
            side=setup.outward_side,
            signal_bar_index=index,
            signal_time_ns=int(feature.bar.end_time_ns),
            stop_price=float(stop),
            target_price=float(target),
            confirmation_hold_price=float(setup.boundary),
            structure_high=max(setup.path_high, setup.boundary, target),
            structure_low=min(setup.path_low, setup.boundary, target),
            structure_midpoint=0.5 * (setup.boundary + setup.opposite_edge),
            pulse_high=setup.path_high,
            pulse_low=setup.path_low,
            pulse_flow_score=setup.aligned_flow_sum_z,
            pulse_move_atr=0.0,
            pulse_path_efficiency=0.0,
            pulse_close_location=0.0,
            reason_code="FUNDING_EDGE_RAID_DURABLY_ACCEPTED",
        )
        self.plans.append(plan)
        self.counts["acceptance_plans_emitted"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="PLAN_EMITTED",
            reason_code=plan.reason_code,
            selected_side=plan.side,
        )

    def _update(self, *, feature: EventFeature, index: int) -> None:
        remaining: list[AuctionSetup] = []
        for setup in self.setups:
            if index <= setup.created_index:
                remaining.append(setup)
                continue
            setup.path_high = max(setup.path_high, float(feature.bar.high))
            setup.path_low = min(setup.path_low, float(feature.bar.low))
            outward_z = self._aligned_z(setup.outward_side, feature)
            if outward_z is not None:
                setup.aligned_flow_sum_z += outward_z
            if self._outside(setup, feature):
                setup.outside_holds += 1

            failed = (
                not self._outside(setup, feature)
                and self._aligned_z(setup.reversal_side, feature) is not None
                and float(self._aligned_z(setup.reversal_side, feature) or 0.0) > 0.0
            )
            if failed:
                self.counts["failures_resolved"] += 1
                self._emit_reversal(setup=setup, feature=feature, index=index)
                continue

            if int(feature.bar.end_time_ns) >= setup.expiry_time_ns:
                self.counts["funding_window_expired"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="FUNDING_WINDOW_ENDED_UNRESOLVED",
                    selected_side=None,
                )
                continue

            if index >= setup.expiry_index:
                durable = (
                    self._outside(setup, feature)
                    and setup.outside_holds >= 2
                    and setup.aligned_flow_sum_z > 0.0
                )
                if durable:
                    self.counts["acceptances_resolved"] += 1
                    self._emit_acceptance(setup=setup, feature=feature, index=index)
                else:
                    self.counts["response_unresolved"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="INVALIDATED",
                        reason_code="NO_FAILED_AUCTION_OR_DURABLE_ACCEPTANCE",
                        selected_side=None,
                    )
                continue
            remaining.append(setup)
        self.setups = remaining

    def _arm(
        self,
        *,
        feature: EventFeature,
        index: int,
        outward_side: Side,
        boundary: float,
        opposite_edge: float,
        window_id: str,
        window_end_ns: int,
    ) -> None:
        width = abs(boundary - opposite_edge)
        if width <= 0.0:
            self.counts["zero_width_reference"] += 1
            return
        setup = AuctionSetup(
            scenario_id=(
                f"v26:{window_id}:{outward_side.value.lower()}-raid:"
                f"{index}:{feature.bar.end_time_ns}"
            ),
            window_id=window_id,
            outward_side=outward_side,
            reversal_side=self._reverse(outward_side),
            created_index=index,
            expiry_index=index + RESPONSE_EVENTS,
            expiry_time_ns=window_end_ns,
            boundary=float(boundary),
            opposite_edge=float(opposite_edge),
            range_width=float(width),
            path_high=float(feature.bar.high),
            path_low=float(feature.bar.low),
            outside_holds=(1 if (
                float(feature.bar.close) > boundary
                if outward_side is Side.LONG
                else float(feature.bar.close) < boundary
            ) else 0),
            aligned_flow_sum_z=float(self._aligned_z(outward_side, feature) or 0.0),
        )
        self.setups.append(setup)
        self.counts["raids_armed"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="RAID_ARMED",
            reason_code="PRIOR_FUNDING_WINDOW_EDGE_RAIDED",
            selected_side=None,
        )

    def on_feature(self, *, feature: EventFeature, index: int) -> None:
        changed = self.book.on_feature(feature)
        if changed:
            for setup in self.setups:
                self.counts["funding_window_expired"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="FUNDING_WINDOW_ROLLED",
                    selected_side=None,
                )
            self.setups = []
            self.high_armed = False
            self.low_armed = False

        reference = self.book.reference
        current = self.book.current
        if reference is None or current is None:
            return
        self._update(feature=feature, index=index)

        high_raid = float(feature.bar.high) > reference.high
        low_raid = float(feature.bar.low) < reference.low
        if high_raid and not self.high_armed:
            self.high_armed = True
            self._arm(
                feature=feature,
                index=index,
                outward_side=Side.LONG,
                boundary=reference.high,
                opposite_edge=reference.low,
                window_id=current.window_id,
                window_end_ns=current.end_time_ns,
            )
        if low_raid and not self.low_armed:
            self.low_armed = True
            self._arm(
                feature=feature,
                index=index,
                outward_side=Side.SHORT,
                boundary=reference.low,
                opposite_edge=reference.high,
                window_id=current.window_id,
                window_end_ns=current.end_time_ns,
            )


def run(args: argparse.Namespace) -> int:
    execution = load_execution(args.execution_config)
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    context_start = evaluation_start - timedelta(days=CONTEXT_DAYS)
    source_start = context_start - timedelta(days=1)
    download_end = evaluation_end + timedelta(minutes=1)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=source_start,
        end=download_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    bars, calibrations = build_daily_cost_resolved_bars(
        records,
        bar_start=context_start,
        bar_end=evaluation_end,
        minimum_range_bps=ROUND_TRIP_COST_BPS,
    )
    detector = ImpactRegimeDetector()
    book = FundingWindowBook()
    scenario = FundingAuctionResolver(rule=args.rule, book=book)
    for bar in bars:
        detector.on_bar(bar)
        index = len(detector.features) - 1
        scenario.on_feature(feature=detector.features[index], index=index)

    plans = [
        plan
        for plan in scenario.plans
        if start_ns <= int(plan.signal_time_ns) < end_ns
    ]
    trades, windows = execution_trade_windows(
        records,
        plans=plans,
        start_ns=start_ns,
        end_ns=end_ns,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    evidence = run_nautilus_tick_plan_backtest(
        label=f"BTCUSDT-v26-{args.rule}-{evaluation_start.date().isoformat()}-7d",
        trades=trades,
        plans=plans,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=args.output,
    )

    pd.DataFrame(asdict(row) for row in scenario.transitions).to_csv(
        args.output / "auction_transitions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in book.completed).to_csv(
        args.output / "completed_funding_windows.csv",
        index=False,
    )
    pd.DataFrame(asdict(plan) for plan in plans).to_csv(
        args.output / "scenario_plans.csv",
        index=False,
    )
    atomic_json(
        args.output / "daily_clock_calibrations.json",
        {"calibrations": [row.to_dict() for row in calibrations]},
    )
    payload = {
        "candidate": "funding-window edge raid two-sided auction resolver",
        "rule": args.rule,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "execution_data_type": "TradeTick",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "context_days": CONTEXT_DAYS,
        "funding_window_hours": 8,
        "response_events": RESPONSE_EVENTS,
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "state_counts": dict(scenario.counts),
        "selected_plan_count": len(plans),
        "completed_funding_window_count": len(book.completed),
        "official_execution_trade_ticks": len(trades),
        "execution_tick_windows": [list(row) for row in windows],
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / 3_600_000_000_000,
        "metrics": evidence.metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(args.output / "funding_window_auction_resolver_v26_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--rule", required=True, choices=RULES)
    parser.add_argument(
        "--execution-config",
        type=Path,
        default=HERE / "nautilus_execution.json",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-v26-resolver",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v26-resolver",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
