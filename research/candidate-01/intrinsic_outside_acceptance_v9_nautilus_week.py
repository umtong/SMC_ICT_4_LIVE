#!/usr/bin/env python3
"""Outside-value acceptance and a mutually exclusive three-response portfolio.

An intrinsic pivot which sweeps the prior same-side pivot is not assumed to
reverse.  If a complete 40-bps directional change has already occurred and the
confirmation close still remains outside the old pivot, the auction retained
outside value.  A subsequent boundary retest must hold outside with aligned
aggressive flow and the 160-bps outer structure must agree before continuation
is considered.

``acceptance-only`` trades only this response.
``three-response-composite`` combines:
  * outer-aligned failed-auction reversal with flow takeover;
  * countertrend failed reversal which later accepts outside value; and
  * directly retained outside value with a holding retest.

The three states are causally distinct.  All performance comes solely from
NautilusTrader 1.230.0 on official Binance Vision one-minute execution bars.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any, Literal

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
from data import load_interval, parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import (  # noqa: E402
    DirectionalChangeDetector,
    MAXIMUM_HOLD_NS,
    RETEST_WINDOW_MINUTES,
    STOP_BUFFER_FRACTION,
)
from impact_regime_probe import ImpactRegimeDetector, ScenarioPlan  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import (  # noqa: E402
    CLOCK_SOURCE_EXTRA_DAYS,
    DAILY_CANDIDATE_MINUTES,
    ROUND_TRIP_COST_BPS,
)
from intrinsic_external_liquidity_v2_week import (  # noqa: E402
    CONTEXT_WARMUP_DAYS,
    NS_PER_HOUR,
    SweepRetestSignal,
    TargetFreeSweepRetestDetector,
    outer_state_series,
)
from intrinsic_external_liquidity_v3_router import (  # noqa: E402
    build_open_liquidity_snapshots,
    select_target_indexed,
)
from intrinsic_failed_reversal_continuation_v8_nautilus_week import (  # noqa: E402
    build_aligned_reversal_plans,
    build_confirmations,
    route_continuations,
)
from nautilus_plan_backtest import (  # noqa: E402
    NautilusExecutionConfig,
    run_nautilus_plan_backtest,
)

RULES = ("acceptance-only", "three-response-composite")
NS_PER_MINUTE = 60 * 1_000_000_000


@dataclass(slots=True)
class AcceptanceSetup:
    scenario_id: str
    side: Side
    created_index: int
    created_time_ns: int
    expiry_time_ns: int
    boundary: float
    path_high: float
    path_low: float
    initiative_flow_imbalance: float
    confirmation_flow_imbalance: float


@dataclass(frozen=True, slots=True)
class AcceptanceTransition:
    scenario_id: str
    event_type: str
    event_index: int
    event_time_ns: int
    side: str
    boundary: float
    close: float
    imbalance_z: float | None
    reason_code: str


class OutsideValueAcceptanceDetector:
    """Online retained-value state machine on completed intrinsic events."""

    def __init__(self) -> None:
        from directional_change_failed_sweep_week import DIRECTIONAL_CHANGE_FRACTION

        self.detector = DirectionalChangeDetector(
            threshold_fraction=DIRECTIONAL_CHANGE_FRACTION,
        )
        self.high_events: list[Any] = []
        self.low_events: list[Any] = []
        self.active: list[AcceptanceSetup] = []
        self.signals: list[SweepRetestSignal] = []
        self.transitions: list[AcceptanceTransition] = []
        self.counts: Counter[str] = Counter()

    def _record(
        self,
        setup: AcceptanceSetup,
        feature: Any,
        index: int,
        event_type: str,
        reason: str,
    ) -> None:
        self.transitions.append(
            AcceptanceTransition(
                scenario_id=setup.scenario_id,
                event_type=event_type,
                event_index=index,
                event_time_ns=int(feature.bar.end_time_ns),
                side=setup.side.value,
                boundary=setup.boundary,
                close=float(feature.bar.close),
                imbalance_z=(
                    None if feature.imbalance_z is None else float(feature.imbalance_z)
                ),
                reason_code=reason,
            ),
        )

    def _arm(self, event: Any, feature: Any) -> None:
        if event.event_type == "DOWN":
            prior = self.high_events[-1] if self.high_events else None
            self.high_events.append(event)
            if prior is None:
                self.counts["insufficient_same_side_liquidity"] += 1
                return
            swept = event.pivot_price > prior.pivot_price
            retained = event.confirmation_price >= prior.pivot_price
            flow = event.trend_flow_imbalance > 0.0
            side = Side.LONG
            boundary = float(prior.pivot_price)
        else:
            prior = self.low_events[-1] if self.low_events else None
            self.low_events.append(event)
            if prior is None:
                self.counts["insufficient_same_side_liquidity"] += 1
                return
            swept = event.pivot_price < prior.pivot_price
            retained = event.confirmation_price <= prior.pivot_price
            flow = event.trend_flow_imbalance < 0.0
            side = Side.SHORT
            boundary = float(prior.pivot_price)

        if not swept:
            self.counts["no_same_side_liquidity_sweep"] += 1
            return
        if not retained:
            self.counts["outside_value_not_retained"] += 1
            return
        if not flow:
            self.counts["initiative_flow_not_aligned"] += 1
            return

        setup = AcceptanceSetup(
            scenario_id=(
                f"outside-acceptance:{event.confirmation_index}:"
                f"{side.value.lower()}:{event.confirmation_time_ns}"
            ),
            side=side,
            created_index=int(event.confirmation_index),
            created_time_ns=int(event.confirmation_time_ns),
            expiry_time_ns=(
                int(event.confirmation_time_ns)
                + RETEST_WINDOW_MINUTES * NS_PER_MINUTE
            ),
            boundary=boundary,
            path_high=float(event.path_high),
            path_low=float(event.path_low),
            initiative_flow_imbalance=float(event.trend_flow_imbalance),
            confirmation_flow_imbalance=float(event.reversal_flow_imbalance),
        )
        self.active.append(setup)
        self.counts["armed"] += 1
        self._record(
            setup,
            feature,
            int(event.confirmation_index),
            "ARMED",
            "DIRECTIONAL_CHANGE_CONFIRMED_OUTSIDE_VALUE_RETENTION",
        )

    def on_feature(self, index: int, features: list[Any]) -> list[SweepRetestSignal]:
        feature = features[index]
        emitted: list[SweepRetestSignal] = []
        remaining: list[AcceptanceSetup] = []
        for setup in self.active:
            if index <= setup.created_index:
                remaining.append(setup)
                continue
            ts_ns = int(feature.bar.end_time_ns)
            if ts_ns > setup.expiry_time_ns:
                self.counts["retest_expired"] += 1
                self._record(setup, feature, index, "INVALIDATED", "OUTSIDE_RETEST_EXPIRED")
                continue
            setup.path_high = max(setup.path_high, float(feature.bar.high))
            setup.path_low = min(setup.path_low, float(feature.bar.low))

            if setup.side is Side.LONG:
                if float(feature.bar.close) < setup.boundary:
                    self.counts["outside_value_failed"] += 1
                    self._record(
                        setup, feature, index, "INVALIDATED", "CLOSE_RETURNED_INSIDE_OLD_HIGH",
                    )
                    continue
                retested = float(feature.bar.low) <= setup.boundary
            else:
                if float(feature.bar.close) > setup.boundary:
                    self.counts["outside_value_failed"] += 1
                    self._record(
                        setup, feature, index, "INVALIDATED", "CLOSE_RETURNED_INSIDE_OLD_LOW",
                    )
                    continue
                retested = float(feature.bar.high) >= setup.boundary

            aligned_flow = (
                feature.imbalance_z is not None
                and setup.side.sign * float(feature.imbalance_z) > 0.0
            )
            if not retested or not aligned_flow:
                remaining.append(setup)
                continue

            stop = (
                setup.boundary * (1.0 - STOP_BUFFER_FRACTION)
                if setup.side is Side.LONG
                else setup.boundary * (1.0 + STOP_BUFFER_FRACTION)
            )
            signal = SweepRetestSignal(
                scenario_id=setup.scenario_id + f":retest:{index}",
                side=setup.side,
                signal_bar_index=index,
                signal_time_ns=ts_ns,
                boundary=setup.boundary,
                stop_price=stop,
                path_high=setup.path_high,
                path_low=setup.path_low,
                trend_flow_imbalance=setup.initiative_flow_imbalance,
                reversal_flow_imbalance=float(feature.imbalance_z),
            )
            self.signals.append(signal)
            emitted.append(signal)
            self.counts["retest_held"] += 1
            self._record(
                setup,
                feature,
                index,
                "SIGNAL_EMITTED",
                "OUTSIDE_VALUE_BOUNDARY_RETEST_HELD",
            )
        self.active = remaining

        event = self.detector.on_feature(index=index, features=features)
        if event is not None:
            self.counts[f"directional_change_{event.event_type.lower()}"] += 1
            self._arm(event, feature)
        return emitted


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_execution(path: Path) -> NautilusExecutionConfig:
    config = NautilusExecutionConfig.from_mapping(
        json.loads(path.read_text(encoding="utf-8")),
    )
    if abs(config.risk_fraction - 0.03) > 1e-12:
        raise ValueError("authoritative candidate-01 evaluations require 3% risk")
    return config


def route_acceptance(
    *,
    signals: list[SweepRetestSignal],
    features: list[Any],
    outer_states: list[str],
    events: list[Any],
    snapshots: dict[int, Any],
    cost: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
) -> tuple[list[ScenarioPlan], list[dict[str, Any]]]:
    plans: list[ScenarioPlan] = []
    decisions: list[dict[str, Any]] = []
    for signal in signals:
        state = outer_states[signal.signal_bar_index]
        aligned = (
            (signal.side is Side.LONG and state == "BULL")
            or (signal.side is Side.SHORT and state == "BEAR")
        )
        plan = None
        target_index = None
        price_fraction = None
        net_rr = None
        if aligned:
            plan, target_index, price_fraction, net_rr = select_target_indexed(
                signal=signal,
                features=features,
                events=events,
                snapshot=snapshots[signal.signal_bar_index],
                cost=cost,
                minimum_price_risk_fraction=minimum_price_risk_fraction,
                minimum_net_reward_risk=minimum_net_reward_risk,
            )
        reason = (
            plan.reason_code
            if plan is not None
            else "OUTSIDE_VALUE_NOT_ALIGNED_WITH_OUTER_STRUCTURE"
            if not aligned
            else "NO_OPEN_CONTINUATION_TARGET_WITH_NET_GEOMETRY"
        )
        decisions.append(
            {
                "scenario_id": signal.scenario_id,
                "signal_time_ns": signal.signal_time_ns,
                "side": signal.side.value,
                "outer_state": state,
                "routed": plan is not None,
                "reason_code": reason,
                "selected_target": plan.target_price if plan is not None else None,
                "selected_target_event_index": target_index,
                "expected_price_risk_fraction": price_fraction,
                "expected_net_reward_risk": net_rr,
            },
        )
        if plan is not None:
            plan.response = "OUTSIDE_VALUE_ACCEPTANCE_CONTINUATION"
            plan.reason_code = "OUTSIDE_VALUE_BOUNDARY_RETEST_CONTINUATION"
            plans.append(plan)
    return plans, decisions


def run(args: argparse.Namespace) -> int:
    execution = load_execution(args.execution_config)
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    context_start = evaluation_start - timedelta(days=CONTEXT_WARMUP_DAYS)
    clock_source_start = context_start - timedelta(days=CLOCK_SOURCE_EXTRA_DAYS)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    signal_records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=clock_source_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    bars, calibrations = build_daily_cost_resolved_bars(
        signal_records,
        bar_start=context_start,
        bar_end=evaluation_end,
        minimum_range_bps=ROUND_TRIP_COST_BPS,
        candidate_minutes=DAILY_CANDIDATE_MINUTES,
    )
    execution_frame, execution_records = load_interval(
        symbol="BTCUSDT",
        start=evaluation_start,
        end=evaluation_end,
        cache_dir=args.cache / "execution-klines",
        warmup_minutes=2,
    )

    feature_detector = ImpactRegimeDetector()
    failed_detector = TargetFreeSweepRetestDetector()
    acceptance_detector = OutsideValueAcceptanceDetector()
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        failed_detector.on_feature(index=index, features=feature_detector.features)
        acceptance_detector.on_feature(index=index, features=feature_detector.features)
    features = feature_detector.features
    outer_states = outer_state_series(features)
    failed_signals = [
        signal for signal in failed_detector.signals
        if start_ns <= signal.signal_time_ns < end_ns
    ]
    acceptance_signals = [
        signal for signal in acceptance_detector.signals
        if start_ns <= signal.signal_time_ns < end_ns
    ]
    cost = execution.all_in_cost_bps_per_side / 10_000.0

    all_signal_indices = [signal.signal_bar_index for signal in failed_signals]
    all_signal_indices.extend(signal.signal_bar_index for signal in acceptance_signals)
    signal_snapshots = build_open_liquidity_snapshots(
        features=features,
        events=failed_detector.detector.events,
        signal_indices=all_signal_indices,
    ) if all_signal_indices else {}

    acceptance_plans, acceptance_decisions = route_acceptance(
        signals=acceptance_signals,
        features=features,
        outer_states=outer_states,
        events=failed_detector.detector.events,
        snapshots=signal_snapshots,
        cost=cost,
        minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
        minimum_net_reward_risk=execution.minimum_net_reward_risk,
    )

    reversal_plans: list[ScenarioPlan] = []
    continuation_plans: list[ScenarioPlan] = []
    continuation_setup_decisions: list[dict[str, Any]] = []
    continuation_routing_decisions: list[dict[str, Any]] = []
    reversal_decisions: list[dict[str, Any]] = []
    confirmations: list[Any] = []

    if args.rule == "three-response-composite":
        reversal_plans, reversal_decisions = build_aligned_reversal_plans(
            signals=failed_signals,
            features=features,
            outer_states=outer_states,
            events=failed_detector.detector.events,
            snapshots=signal_snapshots,
            cost=cost,
            minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
            minimum_net_reward_risk=execution.minimum_net_reward_risk,
        )
        confirmations, continuation_setup_decisions = build_confirmations(
            signals=failed_signals,
            features=features,
            outer_states=outer_states,
            events=failed_detector.detector.events,
            signal_snapshots=signal_snapshots,
            cost=cost,
            minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
            minimum_net_reward_risk=execution.minimum_net_reward_risk,
            evaluation_end_ns=end_ns,
        )
        confirmation_snapshots = build_open_liquidity_snapshots(
            features=features,
            events=failed_detector.detector.events,
            signal_indices=(row.confirmation_index for row in confirmations),
        ) if confirmations else {}
        continuation_plans, continuation_routing_decisions = route_continuations(
            confirmations=confirmations,
            features=features,
            events=failed_detector.detector.events,
            snapshots=confirmation_snapshots,
            cost=cost,
            minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
            minimum_net_reward_risk=execution.minimum_net_reward_risk,
        )

    plans = list(acceptance_plans)
    if args.rule == "three-response-composite":
        plans.extend(reversal_plans)
        plans.extend(continuation_plans)

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    evidence = run_nautilus_plan_backtest(
        label=f"BTCUSDT-{args.rule}-{evaluation_start.date().isoformat()}-7d",
        features=features,
        execution_frame=execution_frame,
        plans=plans,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=output,
    )

    pd.DataFrame(asdict(row) for row in acceptance_signals).to_csv(
        output / "acceptance_signals.csv", index=False,
    )
    pd.DataFrame(asdict(row) for row in acceptance_detector.transitions).to_csv(
        output / "acceptance_transitions.csv", index=False,
    )
    pd.DataFrame(acceptance_decisions).to_csv(
        output / "acceptance_routing_decisions.csv", index=False,
    )
    pd.DataFrame(continuation_setup_decisions).to_csv(
        output / "continuation_setup_decisions.csv", index=False,
    )
    pd.DataFrame(continuation_routing_decisions).to_csv(
        output / "continuation_routing_decisions.csv", index=False,
    )
    pd.DataFrame(reversal_decisions).to_csv(
        output / "reversal_routing_decisions.csv", index=False,
    )
    pd.DataFrame(item.to_dict() for item in calibrations).to_json(
        output / "daily_clock_calibrations.json", orient="records", indent=2,
    )

    payload = {
        "candidate": "outside-value acceptance and mutually exclusive response portfolio",
        "rule": args.rule,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "execution_market_data": "official Binance Vision USD-M one-minute klines",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "acceptance_signals": len(acceptance_signals),
        "acceptance_plans": len(acceptance_plans),
        "reversal_plans": len(reversal_plans),
        "failed_reversal_confirmations": len(confirmations),
        "failed_reversal_continuation_plans": len(continuation_plans),
        "submitted_plan_pool": len(plans),
        "acceptance_detector_counts": dict(acceptance_detector.counts),
        "acceptance_routing_counts": dict(Counter(row["reason_code"] for row in acceptance_decisions)),
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / NS_PER_HOUR,
        "metrics": evidence.metrics,
        "signal_downloads": [record.to_dict() for record in signal_records],
        "execution_downloads": [asdict(record) for record in execution_records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "intrinsic_outside_acceptance_v9_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--rule", required=True, choices=RULES)
    parser.add_argument(
        "--execution-config", type=Path, default=HERE / "nautilus_execution.json",
    )
    parser.add_argument(
        "--cache", type=Path, default=ROOT / ".cache" / "candidate-01-v9-acceptance",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts" / "candidate-01-v9-acceptance",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
