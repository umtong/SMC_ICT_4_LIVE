#!/usr/bin/env python3
"""Cost-floor intrinsic structure control, executed only by NautilusTrader.

The established outer-flow-takeover reversal is unchanged except for one
market-representation variable.  The local directional-change threshold is the
minimum price-risk resolution implied by 14 bps round-trip cost and the required
65% price-risk share:

    14 bps * 0.65 / (1 - 0.65) = 26 bps

The previous candidate added another 14-bps buffer and used 40 bps.  This
control removes only that extra buffer.  The 160-bps outer structure, sweep,
re-entry, boundary retest, flow takeover, nearest open opposing-liquidity
target, path-extreme stop, 7-bps/side costs, 3% current-NAV risk and four-hour
hold all remain fixed.

Official Binance aggregate trades are supplied as NautilusTrader ``TradeTick``
objects over outcome-independent causal plan windows.  No custom fill, PnL or
NAV engine exists here.
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

from adaptive_aggtrade_clock import build_daily_cost_resolved_bars  # noqa: E402
from aggtrade_data import download_aggtrade_days  # noqa: E402
from data import parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import (  # noqa: E402
    DirectionalChangeDetector,
    MAXIMUM_HOLD_NS,
    MINIMUM_COST_RESOLVABLE_RISK_BPS,
)
from impact_regime_probe import ImpactRegimeDetector  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import (  # noqa: E402
    CLOCK_SOURCE_EXTRA_DAYS,
    DAILY_CANDIDATE_MINUTES,
    ROUND_TRIP_COST_BPS,
)
from intrinsic_external_liquidity_v2_week import (  # noqa: E402
    CONTEXT_WARMUP_DAYS,
    NS_PER_HOUR,
    TargetFreeSweepRetestDetector,
    outer_state_series,
)
from intrinsic_external_liquidity_v3_router import (  # noqa: E402
    build_open_liquidity_snapshots,
)
from intrinsic_failed_reversal_continuation_v8_nautilus_week import (  # noqa: E402
    build_aligned_reversal_plans,
)
from intrinsic_tick_window_v13_nautilus_week import (  # noqa: E402
    FLUSH_TICKS,
    execution_trade_windows,
)
from nautilus_plan_backtest import NautilusExecutionConfig  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402

COST_FLOOR_DIRECTIONAL_CHANGE_FRACTION = (
    MINIMUM_COST_RESOLVABLE_RISK_BPS / 10_000.0
)


class CostFloorTargetFreeSweepRetestDetector(TargetFreeSweepRetestDetector):
    """Identical state machine with only the local threshold changed."""

    def __init__(self) -> None:
        self.detector = DirectionalChangeDetector(
            threshold_fraction=COST_FLOOR_DIRECTIONAL_CHANGE_FRACTION,
        )
        self.high_events = []
        self.low_events = []
        self.active = []
        self.signals = []
        self.transitions = []
        self.counts = Counter()


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


def run(args: argparse.Namespace) -> int:
    execution = load_execution(args.execution_config)
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    context_start = evaluation_start - timedelta(days=CONTEXT_WARMUP_DAYS)
    clock_source_start = context_start - timedelta(days=CLOCK_SOURCE_EXTRA_DAYS)
    execution_download_end = evaluation_end + timedelta(minutes=1)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=clock_source_start,
        end=execution_download_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    bars, calibrations = build_daily_cost_resolved_bars(
        records,
        bar_start=context_start,
        bar_end=evaluation_end,
        minimum_range_bps=ROUND_TRIP_COST_BPS,
        candidate_minutes=DAILY_CANDIDATE_MINUTES,
    )

    feature_detector = ImpactRegimeDetector()
    detector = CostFloorTargetFreeSweepRetestDetector()
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
    snapshots = build_open_liquidity_snapshots(
        features=features,
        events=detector.detector.events,
        signal_indices=(signal.signal_bar_index for signal in signals),
    )
    cost = execution.all_in_cost_bps_per_side / 10_000.0
    plans, decisions = build_aligned_reversal_plans(
        signals=signals,
        features=features,
        outer_states=outer_states,
        events=detector.detector.events,
        snapshots=snapshots,
        cost=cost,
        minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
        minimum_net_reward_risk=execution.minimum_net_reward_risk,
    )
    execution_trades, execution_windows = execution_trade_windows(
        records,
        plans=plans,
        start_ns=start_ns,
        end_ns=end_ns,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    evidence = run_nautilus_tick_plan_backtest(
        label=f"BTCUSDT-cost-floor-{evaluation_start.date().isoformat()}-7d",
        trades=execution_trades,
        plans=plans,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=output,
    )

    pd.DataFrame(asdict(row) for row in signals).to_csv(
        output / "retest_signals.csv",
        index=False,
    )
    pd.DataFrame(decisions).to_csv(output / "routing_decisions.csv", index=False)
    pd.DataFrame(asdict(row) for row in detector.detector.events).to_csv(
        output / "directional_change_events.csv",
        index=False,
    )
    pd.DataFrame(item.to_dict() for item in calibrations).to_json(
        output / "daily_clock_calibrations.json",
        orient="records",
        indent=2,
    )

    payload = {
        "candidate": "cost-floor intrinsic failed-auction outer-flow takeover",
        "controlled_change": "local directional-change threshold 40 bps -> 26 bps",
        "local_directional_change_bps": MINIMUM_COST_RESOLVABLE_RISK_BPS,
        "outer_directional_change_bps": 160.0,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "execution_data_type": "TradeTick",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "retest_signals": len(signals),
        "routed_plans": len(plans),
        "official_execution_trade_ticks": len(execution_trades),
        "tick_selection": (
            "outcome-independent union of signal-minus-60s through "
            "signal-plus-fixed-hold-plus-120s windows"
        ),
        "execution_tick_windows": [list(row) for row in execution_windows],
        "decision_counts": dict(Counter(row["reason_code"] for row in decisions)),
        "detector_counts": dict(detector.counts),
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / NS_PER_HOUR,
        "metrics": evidence.metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "intrinsic_cost_floor_v14_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument(
        "--execution-config",
        type=Path,
        default=HERE / "nautilus_execution.json",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-v14-cost-floor",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v14-cost-floor",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
