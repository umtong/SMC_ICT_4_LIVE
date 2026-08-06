#!/usr/bin/env python3
"""Funding-window initial-balance failed/accepted auction resolver.

Each UTC perpetual-funding window (00/08/16 UTC) is an independent auction.
The first two completed cost-resolved equal-notional events define a frozen
initial balance.  The first later raid beyond one edge is observed, never
traded immediately.  Over the next two completed events, failure has strict
precedence over acceptance:

* failed auction: close returns inside the raided edge with aggressive flow
  toward the opposite edge -> reversal to the opposite initial-balance edge;
* durable acceptance: two outside closes and positive cumulative aligned flow
  -> continuation to one initial-balance-width projection.

Only one resolution may trade per funding window.  The primary trades both
responses; response-specific controls isolate attribution.  Candidate logic
produces only causal ScenarioPlan objects.  Official Binance aggregate trades
are converted one-for-one to NautilusTrader TradeTick objects; NautilusTrader
alone owns orders, fills, fees, margin, positions, PnL and NAV.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
from pathlib import Path
import sys

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
from funding_window_failed_auction_v25_nautilus_week import FundingWindowBook  # noqa: E402
from impact_regime_probe import EventFeature, ImpactRegimeDetector, ScenarioPlan  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import ROUND_TRIP_COST_BPS  # noqa: E402
from mss_absorption_reacceleration_v24_nautilus_week import execution_trade_windows  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from resolved_impact_v17_nautilus_week import atomic_json, load_execution  # noqa: E402


RULES = ("two-sided-resolver", "failure-only", "acceptance-only")
CONTEXT_DAYS = 4
INITIAL_BALANCE_EVENTS = 2
RESPONSE_EVENTS = 2
STOP_BUFFER_FRACTION = 7.0 / 10_000.0


@dataclass(slots=True)
class WindowState:
    window_id: str
    start_time_ns: int
    end_time_ns: int
    event_count: int = 0
    initial_high: float | None = None
    initial_low: float | None = None
    resolved: bool = False

    def add_initial(self, feature: EventFeature) -> None:
        high = float(feature.bar.high)
        low = float(feature.bar.low)
        self.initial_high = high if self.initial_high is None else max(self.initial_high, high)
        self.initial_low = low if self.initial_low is None else min(self.initial_low, low)
        self.event_count += 1

    @property
    def ready(self) -> bool:
        return (
            self.event_count >= INITIAL_BALANCE_EVENTS
            and self.initial_high is not None
            and self.initial_low is not None
            and self.initial_high > self.initial_low
        )


@dataclass(slots=True)
class RaidSetup:
    scenario_id: str
    window_id: str
    outward_side: Side
    reversal_side: Side
    created_index: int
    expiry_index: int
    window_end_ns: int
    boundary: float
    opposite_edge: float
    balance_width: float
    path_high: float
    path_low: float
    outside_holds: int
    aligned_flow_sum_z: float


@dataclass(frozen=True, slots=True)
class ResolverTransition:
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
    continuation_target: float
    path_high: float
    path_low: float
    outside_holds: int
    aligned_flow_sum_z: float
    imbalance_z: float | None
    close: float


class InitialBalanceResolver:
    def __init__(self, *, rule: str, book: FundingWindowBook) -> None:
        if rule not in RULES:
            raise ValueError(f"unknown rule {rule}")
        self.rule = rule
        self.book = book
        self.window: WindowState | None = None
        self.setup: RaidSetup | None = None
        self.plans: list[ScenarioPlan] = []
        self.transitions: list[ResolverTransition] = []
        self.counts: Counter[str] = Counter()

    @staticmethod
    def _reverse(side: Side) -> Side:
        return Side.SHORT if side is Side.LONG else Side.LONG

    @staticmethod
    def _aligned_z(side: Side, feature: EventFeature) -> float | None:
        if feature.imbalance_z is None:
            return None
        return side.sign * float(feature.imbalance_z)

    @staticmethod
    def _outside(setup: RaidSetup, feature: EventFeature) -> bool:
        close = float(feature.bar.close)
        return close > setup.boundary if setup.outward_side is Side.LONG else close < setup.boundary

    @staticmethod
    def _continuation_target(setup: RaidSetup) -> float:
        return (
            setup.boundary + setup.balance_width
            if setup.outward_side is Side.LONG
            else setup.boundary - setup.balance_width
        )

    def _transition(
        self,
        *,
        setup: RaidSetup,
        feature: EventFeature,
        index: int,
        event_type: str,
        reason_code: str,
        selected_side: Side | None,
    ) -> None:
        self.transitions.append(
            ResolverTransition(
                scenario_id=setup.scenario_id,
                window_id=setup.window_id,
                event_type=event_type,
                event_index=index,
                event_time_ns=int(feature.bar.end_time_ns),
                reason_code=reason_code,
                outward_side=setup.outward_side.value,
                selected_side=(selected_side.value if selected_side is not None else None),
                boundary=float(setup.boundary),
                opposite_edge=float(setup.opposite_edge),
                continuation_target=float(self._continuation_target(setup)),
                path_high=float(setup.path_high),
                path_low=float(setup.path_low),
                outside_holds=int(setup.outside_holds),
                aligned_flow_sum_z=float(setup.aligned_flow_sum_z),
                imbalance_z=(
                    float(feature.imbalance_z)
                    if feature.imbalance_z is not None
                    else None
                ),
                close=float(feature.bar.close),
            ),
        )

    def _roll_window(self, *, feature: EventFeature, index: int) -> None:
        if self.setup is not None:
            self.counts["window_rolled_unresolved"] += 1
            self._transition(
                setup=self.setup,
                feature=feature,
                index=index,
                event_type="INVALIDATED",
                reason_code="FUNDING_WINDOW_ROLLED_UNRESOLVED",
                selected_side=None,
            )
        current = self.book.current
        if current is None:
            self.window = None
        else:
            self.window = WindowState(
                window_id=current.window_id,
                start_time_ns=current.start_time_ns,
                end_time_ns=current.end_time_ns,
            )
        self.setup = None

    def _emit_failure(self, *, setup: RaidSetup, feature: EventFeature, index: int) -> None:
        assert self.window is not None
        if self.rule == "acceptance-only":
            self.counts["failure_resolved_but_disabled"] += 1
            self.window.resolved = True
            return
        target_touched = (
            setup.path_low <= setup.opposite_edge
            if setup.reversal_side is Side.SHORT
            else setup.path_high >= setup.opposite_edge
        )
        if target_touched:
            self.counts["failure_target_consumed_before_entry"] += 1
            self._transition(
                setup=setup,
                feature=feature,
                index=index,
                event_type="INVALIDATED",
                reason_code="OPPOSITE_INITIAL_BALANCE_EDGE_ALREADY_CONSUMED",
                selected_side=None,
            )
            self.window.resolved = True
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
            pulse_high=float(setup.path_high),
            pulse_low=float(setup.path_low),
            pulse_flow_score=float(setup.aligned_flow_sum_z),
            pulse_move_atr=0.0,
            pulse_path_efficiency=0.0,
            pulse_close_location=0.0,
            reason_code="FUNDING_INITIAL_BALANCE_RAID_FAILED",
        )
        self.plans.append(plan)
        self.counts["failure_plans_emitted"] += 1
        self.window.resolved = True
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="PLAN_EMITTED",
            reason_code=plan.reason_code,
            selected_side=plan.side,
        )

    def _emit_acceptance(self, *, setup: RaidSetup, feature: EventFeature, index: int) -> None:
        assert self.window is not None
        if self.rule == "failure-only":
            self.counts["acceptance_resolved_but_disabled"] += 1
            self.window.resolved = True
            return
        target = self._continuation_target(setup)
        target_touched = (
            setup.path_high >= target
            if setup.outward_side is Side.LONG
            else setup.path_low <= target
        )
        if target_touched:
            self.counts["acceptance_target_consumed_before_entry"] += 1
            self._transition(
                setup=setup,
                feature=feature,
                index=index,
                event_type="INVALIDATED",
                reason_code="INITIAL_BALANCE_PROJECTION_ALREADY_CONSUMED",
                selected_side=None,
            )
            self.window.resolved = True
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
            pulse_high=float(setup.path_high),
            pulse_low=float(setup.path_low),
            pulse_flow_score=float(setup.aligned_flow_sum_z),
            pulse_move_atr=0.0,
            pulse_path_efficiency=0.0,
            pulse_close_location=0.0,
            reason_code="FUNDING_INITIAL_BALANCE_RAID_DURABLY_ACCEPTED",
        )
        self.plans.append(plan)
        self.counts["acceptance_plans_emitted"] += 1
        self.window.resolved = True
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="PLAN_EMITTED",
            reason_code=plan.reason_code,
            selected_side=plan.side,
        )

    def _manage_setup(self, *, feature: EventFeature, index: int) -> None:
        setup = self.setup
        if setup is None:
            return
        if index <= setup.created_index:
            return
        setup.path_high = max(setup.path_high, float(feature.bar.high))
        setup.path_low = min(setup.path_low, float(feature.bar.low))
        aligned = self._aligned_z(setup.outward_side, feature)
        if aligned is not None:
            setup.aligned_flow_sum_z += aligned
        if self._outside(setup, feature):
            setup.outside_holds += 1

        reversal_flow = self._aligned_z(setup.reversal_side, feature)
        failed = (
            not self._outside(setup, feature)
            and reversal_flow is not None
            and reversal_flow > 0.0
        )
        if failed:
            self.counts["failures_resolved"] += 1
            self._emit_failure(setup=setup, feature=feature, index=index)
            self.setup = None
            return

        if int(feature.bar.end_time_ns) >= setup.window_end_ns:
            self.counts["response_window_closed"] += 1
            self._transition(
                setup=setup,
                feature=feature,
                index=index,
                event_type="INVALIDATED",
                reason_code="FUNDING_WINDOW_ENDED_UNRESOLVED",
                selected_side=None,
            )
            self.setup = None
            return

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
            self.setup = None

    def _arm(self, *, feature: EventFeature, index: int, outward_side: Side) -> None:
        assert self.window is not None and self.window.ready
        assert self.window.initial_high is not None and self.window.initial_low is not None
        boundary = self.window.initial_high if outward_side is Side.LONG else self.window.initial_low
        opposite = self.window.initial_low if outward_side is Side.LONG else self.window.initial_high
        width = self.window.initial_high - self.window.initial_low
        setup = RaidSetup(
            scenario_id=(
                f"v27:{self.window.window_id}:{outward_side.value.lower()}-raid:"
                f"{index}:{feature.bar.end_time_ns}"
            ),
            window_id=self.window.window_id,
            outward_side=outward_side,
            reversal_side=self._reverse(outward_side),
            created_index=index,
            expiry_index=index + RESPONSE_EVENTS,
            window_end_ns=self.window.end_time_ns,
            boundary=float(boundary),
            opposite_edge=float(opposite),
            balance_width=float(width),
            path_high=float(feature.bar.high),
            path_low=float(feature.bar.low),
            outside_holds=(1 if (
                float(feature.bar.close) > boundary
                if outward_side is Side.LONG
                else float(feature.bar.close) < boundary
            ) else 0),
            aligned_flow_sum_z=float(self._aligned_z(outward_side, feature) or 0.0),
        )
        self.setup = setup
        self.counts["raids_armed"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="RAID_ARMED",
            reason_code="FUNDING_WINDOW_INITIAL_BALANCE_EDGE_RAIDED",
            selected_side=None,
        )

    def on_feature(self, *, feature: EventFeature, index: int) -> None:
        changed = self.book.on_feature(feature)
        if changed or self.window is None:
            self._roll_window(feature=feature, index=index)
        if self.window is None:
            return

        if self.window.event_count < INITIAL_BALANCE_EVENTS:
            self.window.add_initial(feature)
            if self.window.ready:
                self.counts["initial_balances_completed"] += 1
            return

        self._manage_setup(feature=feature, index=index)
        if self.setup is not None or self.window.resolved or not self.window.ready:
            return

        assert self.window.initial_high is not None and self.window.initial_low is not None
        high_raid = float(feature.bar.high) > self.window.initial_high
        low_raid = float(feature.bar.low) < self.window.initial_low
        if high_raid and low_raid:
            self.counts["ambiguous_two_sided_raid"] += 1
            self.window.resolved = True
            return
        if high_raid:
            self._arm(feature=feature, index=index, outward_side=Side.LONG)
        elif low_raid:
            self._arm(feature=feature, index=index, outward_side=Side.SHORT)


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
    resolver = InitialBalanceResolver(rule=args.rule, book=book)
    for bar in bars:
        detector.on_bar(bar)
        index = len(detector.features) - 1
        resolver.on_feature(feature=detector.features[index], index=index)

    plans = [
        plan
        for plan in resolver.plans
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
        label=f"BTCUSDT-v27-{args.rule}-{evaluation_start.date().isoformat()}-7d",
        trades=trades,
        plans=plans,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=args.output,
    )

    pd.DataFrame(asdict(row) for row in resolver.transitions).to_csv(
        args.output / "resolver_transitions.csv",
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
        "candidate": "funding-window initial-balance two-sided auction resolver",
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
        "initial_balance_events": INITIAL_BALANCE_EVENTS,
        "response_events": RESPONSE_EVENTS,
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "state_counts": dict(resolver.counts),
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
    atomic_json(args.output / "funding_initial_balance_resolver_v27_summary.json", payload)
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
        default=ROOT / ".cache" / "candidate-01-v27-initial-balance",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v27-initial-balance",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
