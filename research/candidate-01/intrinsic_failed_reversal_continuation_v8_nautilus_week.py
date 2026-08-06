#!/usr/bin/env python3
"""Mutually exclusive failed-auction responses, executed by NautilusTrader.

The first response trades a failed sweep only when the reversal side agrees
with the causally confirmed 160-bps outer structure and opposite aggressive flow
has taken control of the initiative flow.

The second response handles the complementary state.  When a valid local
failed-auction reversal points against the outer structure, no fade is entered.
For thirty minutes the scenario waits for one of two terminal events:

1. the reversal's nearest open opposing-liquidity objective trades first -- the
   reversal succeeded and the continuation setup is cancelled; or
2. price closes through the complete failed-auction path extreme with outer-
   direction aggressive flow -- the reversal failed and outside value is now
   accepted.  The old swept boundary is the structural invalidation and the
   nearest still-open outer-direction intrinsic pivot is the target.

Signals are generated only from completed equal-notional events.  Orders,
fills, bracket handling, commissions, margin, positions and NAV are produced
exclusively by NautilusTrader 1.230.0 from official Binance Vision one-minute
bars.  This module contains no fill, PnL or NAV simulator.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, replace
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
from nautilus_plan_backtest import (  # noqa: E402
    NautilusExecutionConfig,
    run_nautilus_plan_backtest,
)

RULES = ("continuation-only", "composite")
NS_PER_MINUTE = 60 * 1_000_000_000


@dataclass(frozen=True, slots=True)
class ContinuationConfirmation:
    source_scenario_id: str
    source_signal_index: int
    source_signal_time_ns: int
    source_side: str
    outer_state: str
    continuation_side: str
    confirmation_index: int
    confirmation_time_ns: int
    break_level: float
    invalidation_boundary: float
    stop_price: float
    original_reversal_target: float
    confirmation_close: float
    confirmation_imbalance_z: float
    observed_path_high: float
    observed_path_low: float


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


def outer_side(state: str) -> Side | None:
    if state == "BULL":
        return Side.LONG
    if state == "BEAR":
        return Side.SHORT
    return None


def flow_takeover(signal: SweepRetestSignal) -> bool:
    return abs(signal.reversal_flow_imbalance) >= abs(signal.trend_flow_imbalance)


def target_touched(plan: ScenarioPlan, feature: Any) -> bool:
    return (
        feature.bar.high >= plan.target_price
        if plan.side is Side.LONG
        else feature.bar.low <= plan.target_price
    )


def build_confirmations(
    *,
    signals: list[SweepRetestSignal],
    features: list[Any],
    outer_states: list[str],
    events: list[Any],
    signal_snapshots: dict[int, Any],
    cost: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
    evaluation_end_ns: int,
) -> tuple[list[ContinuationConfirmation], list[dict[str, Any]]]:
    confirmations: list[ContinuationConfirmation] = []
    decisions: list[dict[str, Any]] = []

    for signal in signals:
        state = outer_states[signal.signal_bar_index]
        continuation_side = outer_side(state)
        if continuation_side is None:
            decisions.append(
                {
                    "scenario_id": signal.scenario_id,
                    "signal_time_ns": signal.signal_time_ns,
                    "accepted": False,
                    "reason_code": "OUTER_STRUCTURE_NOT_DIRECTIONAL",
                },
            )
            continue
        if continuation_side is signal.side:
            decisions.append(
                {
                    "scenario_id": signal.scenario_id,
                    "signal_time_ns": signal.signal_time_ns,
                    "accepted": False,
                    "reason_code": "REVERSAL_SIDE_ALREADY_OUTER_ALIGNED",
                },
            )
            continue

        # A continuation is meaningful only if the countertrend reversal itself
        # had a real, open liquidity objective and executable cost geometry.
        reversal_plan, _, _, _ = select_target_indexed(
            signal=signal,
            features=features,
            events=events,
            snapshot=signal_snapshots[signal.signal_bar_index],
            cost=cost,
            minimum_price_risk_fraction=minimum_price_risk_fraction,
            minimum_net_reward_risk=minimum_net_reward_risk,
        )
        if reversal_plan is None:
            decisions.append(
                {
                    "scenario_id": signal.scenario_id,
                    "signal_time_ns": signal.signal_time_ns,
                    "accepted": False,
                    "reason_code": "COUNTERTREND_REVERSAL_LACKED_OPEN_TARGET_GEOMETRY",
                },
            )
            continue

        expiry_ns = min(
            signal.signal_time_ns + RETEST_WINDOW_MINUTES * NS_PER_MINUTE,
            evaluation_end_ns,
        )
        path_high = float(signal.path_high)
        path_low = float(signal.path_low)
        terminal_reason = "REVERSAL_FAILURE_WINDOW_EXPIRED"

        for index in range(signal.signal_bar_index + 1, len(features)):
            feature = features[index]
            ts_ns = int(feature.bar.end_time_ns)
            if ts_ns > expiry_ns:
                break
            path_high = max(path_high, float(feature.bar.high))
            path_low = min(path_low, float(feature.bar.low))

            # Conservative same-event ambiguity: if the reversal objective and
            # its failure level both trade in one event, the setup is cancelled.
            if target_touched(reversal_plan, feature):
                terminal_reason = "REVERSAL_TARGET_CONSUMED_BEFORE_FAILURE"
                break

            imbalance_z = feature.imbalance_z
            if imbalance_z is None or continuation_side.sign * imbalance_z <= 0.0:
                continue
            if continuation_side is Side.LONG:
                break_level = float(signal.path_high)
                failed = float(feature.bar.close) > break_level
                stop = float(signal.boundary) * (1.0 - STOP_BUFFER_FRACTION)
            else:
                break_level = float(signal.path_low)
                failed = float(feature.bar.close) < break_level
                stop = float(signal.boundary) * (1.0 + STOP_BUFFER_FRACTION)
            if not failed:
                continue

            confirmations.append(
                ContinuationConfirmation(
                    source_scenario_id=signal.scenario_id,
                    source_signal_index=signal.signal_bar_index,
                    source_signal_time_ns=signal.signal_time_ns,
                    source_side=signal.side.value,
                    outer_state=state,
                    continuation_side=continuation_side.value,
                    confirmation_index=index,
                    confirmation_time_ns=ts_ns,
                    break_level=break_level,
                    invalidation_boundary=float(signal.boundary),
                    stop_price=stop,
                    original_reversal_target=float(reversal_plan.target_price),
                    confirmation_close=float(feature.bar.close),
                    confirmation_imbalance_z=float(imbalance_z),
                    observed_path_high=path_high,
                    observed_path_low=path_low,
                ),
            )
            terminal_reason = "COUNTERTREND_REVERSAL_FAILED_WITH_OUTER_FLOW"
            break

        decisions.append(
            {
                "scenario_id": signal.scenario_id,
                "signal_time_ns": signal.signal_time_ns,
                "accepted": terminal_reason == "COUNTERTREND_REVERSAL_FAILED_WITH_OUTER_FLOW",
                "reason_code": terminal_reason,
                "outer_state": state,
                "source_side": signal.side.value,
                "continuation_side": continuation_side.value,
                "reversal_target": reversal_plan.target_price,
            },
        )

    return confirmations, decisions


def route_continuations(
    *,
    confirmations: list[ContinuationConfirmation],
    features: list[Any],
    events: list[Any],
    snapshots: dict[int, Any],
    cost: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
) -> tuple[list[ScenarioPlan], list[dict[str, Any]]]:
    plans: list[ScenarioPlan] = []
    decisions: list[dict[str, Any]] = []
    for row in confirmations:
        side = Side.LONG if row.continuation_side == "LONG" else Side.SHORT
        pseudo = SweepRetestSignal(
            scenario_id=(
                f"failed-reversal-continuation:{row.source_signal_index}:"
                f"{side.value.lower()}:{row.confirmation_time_ns}"
            ),
            side=side,
            signal_bar_index=row.confirmation_index,
            signal_time_ns=row.confirmation_time_ns,
            boundary=row.break_level,
            stop_price=row.stop_price,
            path_high=row.observed_path_high,
            path_low=row.observed_path_low,
            trend_flow_imbalance=row.confirmation_imbalance_z,
            reversal_flow_imbalance=row.confirmation_imbalance_z,
        )
        plan, target_index, price_fraction, net_rr = select_target_indexed(
            signal=pseudo,
            features=features,
            events=events,
            snapshot=snapshots[row.confirmation_index],
            cost=cost,
            minimum_price_risk_fraction=minimum_price_risk_fraction,
            minimum_net_reward_risk=minimum_net_reward_risk,
        )
        if plan is not None:
            plan = replace(
                plan,
                scenario_id=pseudo.scenario_id + f":outer-target:{target_index}",
                response="FAILED_REVERSAL_CONTINUATION",
                reason_code="COUNTERTREND_REVERSAL_FAILED_OUTER_CONTINUATION",
            )
            plans.append(plan)
        decisions.append(
            {
                **asdict(row),
                "routed": plan is not None,
                "reason_code": (
                    plan.reason_code
                    if plan is not None
                    else "NO_OPEN_OUTER_DIRECTION_POOL_WITH_NET_GEOMETRY"
                ),
                "selected_target": plan.target_price if plan is not None else None,
                "selected_target_event_index": target_index,
                "expected_price_risk_fraction": price_fraction,
                "expected_net_reward_risk": net_rr,
            },
        )
    return plans, decisions


def build_aligned_reversal_plans(
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
        state_side = outer_side(outer_states[signal.signal_bar_index])
        aligned = state_side is signal.side
        takeover = flow_takeover(signal)
        plan = None
        target_index = None
        price_fraction = None
        net_rr = None
        if aligned and takeover:
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
            else "OUTER_STRUCTURE_NOT_ALIGNED"
            if not aligned
            else "REVERSAL_FLOW_DID_NOT_TAKE_CONTROL"
            if not takeover
            else "NO_OPEN_REVERSAL_TARGET_WITH_NET_GEOMETRY"
        )
        decisions.append(
            {
                "scenario_id": signal.scenario_id,
                "signal_time_ns": signal.signal_time_ns,
                "side": signal.side.value,
                "outer_state": outer_states[signal.signal_bar_index],
                "flow_takeover": takeover,
                "routed": plan is not None,
                "reason_code": reason,
                "selected_target": plan.target_price if plan is not None else None,
                "selected_target_event_index": target_index,
                "expected_price_risk_fraction": price_fraction,
                "expected_net_reward_risk": net_rr,
            },
        )
        if plan is not None:
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
    detector = TargetFreeSweepRetestDetector()
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        detector.on_feature(index=index, features=feature_detector.features)
    features = feature_detector.features
    outer_states = outer_state_series(features)
    signals = [
        signal
        for signal in detector.signals
        if start_ns <= signal.signal_time_ns < end_ns
    ]
    cost = execution.all_in_cost_bps_per_side / 10_000.0
    signal_snapshots = build_open_liquidity_snapshots(
        features=features,
        events=detector.detector.events,
        signal_indices=(signal.signal_bar_index for signal in signals),
    )

    confirmations, setup_decisions = build_confirmations(
        signals=signals,
        features=features,
        outer_states=outer_states,
        events=detector.detector.events,
        signal_snapshots=signal_snapshots,
        cost=cost,
        minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
        minimum_net_reward_risk=execution.minimum_net_reward_risk,
        evaluation_end_ns=end_ns,
    )
    confirmation_snapshots = build_open_liquidity_snapshots(
        features=features,
        events=detector.detector.events,
        signal_indices=(row.confirmation_index for row in confirmations),
    ) if confirmations else {}
    continuation_plans, continuation_decisions = route_continuations(
        confirmations=confirmations,
        features=features,
        events=detector.detector.events,
        snapshots=confirmation_snapshots,
        cost=cost,
        minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
        minimum_net_reward_risk=execution.minimum_net_reward_risk,
    )
    reversal_plans, reversal_decisions = build_aligned_reversal_plans(
        signals=signals,
        features=features,
        outer_states=outer_states,
        events=detector.detector.events,
        snapshots=signal_snapshots,
        cost=cost,
        minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
        minimum_net_reward_risk=execution.minimum_net_reward_risk,
    )

    plans = list(continuation_plans)
    if args.rule == "composite":
        plans.extend(reversal_plans)

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

    pd.DataFrame(asdict(row) for row in signals).to_csv(output / "retest_signals.csv", index=False)
    pd.DataFrame(setup_decisions).to_csv(output / "continuation_setup_decisions.csv", index=False)
    pd.DataFrame(asdict(row) for row in confirmations).to_csv(output / "continuation_confirmations.csv", index=False)
    pd.DataFrame(continuation_decisions).to_csv(output / "continuation_routing_decisions.csv", index=False)
    pd.DataFrame(reversal_decisions).to_csv(output / "reversal_routing_decisions.csv", index=False)
    pd.DataFrame(asdict(row) for row in detector.detector.events).to_csv(
        output / "directional_change_events.csv", index=False,
    )
    pd.DataFrame(item.to_dict() for item in calibrations).to_json(
        output / "daily_clock_calibrations.json", orient="records", indent=2,
    )

    payload = {
        "candidate": "mutually exclusive failed-auction reversal and continuation",
        "rule": args.rule,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "execution_market_data": "official Binance Vision USD-M one-minute klines",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "retest_signals": len(signals),
        "continuation_confirmations": len(confirmations),
        "continuation_plans": len(continuation_plans),
        "aligned_flow_takeover_reversal_plans": len(reversal_plans),
        "submitted_plan_pool": len(plans),
        "continuation_setup_counts": dict(Counter(row["reason_code"] for row in setup_decisions)),
        "continuation_routing_counts": dict(Counter(row["reason_code"] for row in continuation_decisions)),
        "reversal_routing_counts": dict(Counter(row["reason_code"] for row in reversal_decisions)),
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / NS_PER_HOUR,
        "metrics": evidence.metrics,
        "signal_downloads": [record.to_dict() for record in signal_records],
        "execution_downloads": [asdict(record) for record in execution_records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "intrinsic_failed_reversal_continuation_v8_summary.json", payload)
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
        "--cache", type=Path, default=ROOT / ".cache" / "candidate-01-v8-continuation",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts" / "candidate-01-v8-continuation",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
