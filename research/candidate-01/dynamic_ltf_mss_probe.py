#!/usr/bin/env python3
"""First-week comparison of frozen versus dynamically updated one-minute MSS.

The external event is unchanged: a causally confirmed repeated five-minute
liquidity-pool sweep. The frozen implementation waits for the last opposing
one-minute pivot observable at the sweep close. The dynamic implementation may
replace that threshold only with a more recent causally confirmed lower high
for a bullish reversal or higher low for a bearish reversal. This represents
the actual last internal structure opposing the reversal, not a looser flow
filter or a longer timeout.

Everything else is fixed: ten-minute response window, next-minute entry,
confirmation hold at the delayed entry, sweep-extreme structural stop,
nearest untouched opposing repeated pool target, 7 bps per side, 3% NAV risk,
and one global position.
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

from core import CandidateConfig, Side  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402
import ltf_mss_resting_pool_probe as base  # noqa: E402
from portfolio_probe import Variant, simulate  # noqa: E402
from resting_liquidity_pool_probe import aggregate_five_minute  # noqa: E402


RULES = ("dynamic-mss-market", "dynamic-mss-flow")
RISK_RATE = 0.03
RESPONSE_WINDOW_MINUTES = 10


@dataclass(frozen=True, slots=True)
class DynamicEvent:
    sweep_time_ns: int
    mss_time_ns: int
    side: str
    source_pool_id: int
    target_pool_id: int
    original_break: float
    dynamic_break: float
    pivot_time_ns: int
    pivot_updates: int
    latency_minutes: int
    aligned_flow_z: float
    aligned_body_atr: float
    volume_z: float
    raw_reward_risk: float
    emitted_rules: str


class DynamicLtfMssDetector(base.LtfMssDetector):
    """Update only to a newer, more accessible opposing one-minute pivot."""

    def __init__(self, config: CandidateConfig, *, evaluation_start_ns: int) -> None:
        super().__init__(config, evaluation_start_ns=evaluation_start_ns)
        self.schedules.update({rule: {} for rule in RULES})
        self.rule_counts.update({rule: Counter() for rule in RULES})
        self.original_break_by_sweep: dict[int, float] = {}
        self.pivot_updates_by_sweep: Counter[int] = Counter()
        self.dynamic_events: list[DynamicEvent] = []

    def on_five_minute(self, bar: Any) -> None:
        previous_active = self.active
        super().on_five_minute(bar)
        if self.active is not None and self.active is not previous_active:
            self.original_break_by_sweep[self.active.sweep_time_ns] = self.active.internal_break

    def _update_dynamic_break(self, *, current_close: float) -> None:
        active = self.active
        if active is None:
            return
        side = active.source.side
        pivot = (
            self.minute.latest_high
            if side is Side.LONG
            else self.minute.latest_low
        )
        if pivot is None or pivot.time_ns <= active.pivot_time_ns:
            return
        if side is Side.LONG:
            accessible = (
                active.sweep_extreme < pivot.price < active.internal_break
            )
        else:
            accessible = (
                active.internal_break < pivot.price < active.sweep_extreme
            )
        if not accessible:
            self.stage_counts["new_pivot_not_more_accessible"] += 1
            return
        active.internal_break = pivot.price
        active.pivot_time_ns = pivot.time_ns
        self.pivot_updates_by_sweep[active.sweep_time_ns] += 1
        self.stage_counts["dynamic_pivot_updates"] += 1
        already_broken = (
            current_close >= pivot.price
            if side is Side.LONG
            else current_close <= pivot.price
        )
        if already_broken:
            self.stage_counts["pivot_confirmed_with_break"] += 1

    def on_one_minute(self, bar: Any) -> None:
        state = self.minute.observe(bar)
        active = self.active
        if active is None or bar.ts_event_ns <= active.sweep_time_ns:
            return
        source = active.source
        side = source.side
        active.sweep_extreme = (
            min(active.sweep_extreme, bar.low)
            if side is Side.LONG
            else max(active.sweep_extreme, bar.high)
        )
        target_reached = (
            bar.high >= source.target_level
            if side is Side.LONG
            else bar.low <= source.target_level
        )
        if target_reached:
            self.stage_counts["target_reached_before_mss"] += 1
            self.active = None
            return
        if bar.ts_event_ns > active.expiry_time_ns:
            self.stage_counts["expired_without_ltf_mss"] += 1
            self.active = None
            return
        if state.atr is None or state.flow_z is None or state.body_atr is None or state.volume_z is None:
            self.stage_counts["minute_state_unavailable"] += 1
            return

        self._update_dynamic_break(current_close=bar.close)
        active = self.active
        if active is None:
            return
        mss = (
            bar.close >= active.internal_break + 0.05 * state.atr
            if side is Side.LONG
            else bar.close <= active.internal_break - 0.05 * state.atr
        )
        if not mss:
            return
        self.stage_counts["dynamic_ltf_mss_events"] += 1
        aligned_flow = side.sign * state.flow_z
        aligned_body = side.sign * state.body_atr
        emitted: list[str] = []
        stop = self._structural_stop(source, active.sweep_extreme)
        risk = abs(bar.close - stop)
        raw_rr = (
            abs(source.target_level - bar.close) / risk
            if risk > 0.0
            else 0.0
        )
        if aligned_body >= 0.20 and aligned_flow >= 0.0:
            self.rule_counts["dynamic-mss-market"]["eligible_mss"] += 1
            pending, reason, _, _, _ = self._build_plan(
                rule="dynamic-mss-market",
                signal_bar=bar,
                source=source,
                sweep_extreme=active.sweep_extreme,
                reason_code="DYNAMIC_LTF_MSS_DIRECTIONAL_BREAK_CONFIRMED",
                confirmation_hold_price=active.internal_break,
            )
            if pending is not None:
                emitted.append("dynamic-mss-market")
            else:
                self.rule_counts["dynamic-mss-market"][reason] += 1
        if aligned_body >= 0.35 and aligned_flow >= 0.50:
            self.rule_counts["dynamic-mss-flow"]["eligible_mss"] += 1
            pending, reason, _, _, _ = self._build_plan(
                rule="dynamic-mss-flow",
                signal_bar=bar,
                source=source,
                sweep_extreme=active.sweep_extreme,
                reason_code="DYNAMIC_LTF_MSS_FLOW_DISPLACEMENT_CONFIRMED",
                confirmation_hold_price=active.internal_break,
            )
            if pending is not None:
                emitted.append("dynamic-mss-flow")
            else:
                self.rule_counts["dynamic-mss-flow"][reason] += 1
        if not emitted:
            self.stage_counts["dynamic_mss_without_executable_plan"] += 1
        self.dynamic_events.append(
            DynamicEvent(
                sweep_time_ns=active.sweep_time_ns,
                mss_time_ns=bar.ts_event_ns,
                side=side.value,
                source_pool_id=source.source_pool_id,
                target_pool_id=source.target_pool_id,
                original_break=self.original_break_by_sweep.get(
                    active.sweep_time_ns,
                    active.internal_break,
                ),
                dynamic_break=active.internal_break,
                pivot_time_ns=active.pivot_time_ns,
                pivot_updates=int(
                    self.pivot_updates_by_sweep[active.sweep_time_ns],
                ),
                latency_minutes=int(
                    (bar.ts_event_ns - active.sweep_time_ns)
                    // base.ONE_MINUTE_NS,
                ),
                aligned_flow_z=aligned_flow,
                aligned_body_atr=aligned_body,
                volume_z=state.volume_z,
                raw_reward_risk=raw_rr,
                emitted_rules="|".join(emitted),
            ),
        )
        self.active = None


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    base.MSS_EXPIRY_MINUTES = RESPONSE_WINDOW_MINUTES
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    start = parse_utc_date(str(research["discovery_week"]))
    end = start + timedelta(days=7)
    start_ns = int(pd.Timestamp(start).as_unit("ns").value)
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0

    frame, records = load_interval(
        symbol="BTCUSDT",
        start=start,
        end=end,
        cache_dir=args.cache,
        warmup_minutes=3 * 24 * 60,
    )
    minute_bars = to_auction_bars(frame)
    five_minute_map = {
        bar.ts_event_ns: bar for bar in aggregate_five_minute(frame)
    }
    frozen = base.LtfMssDetector(candidate, evaluation_start_ns=start_ns)
    dynamic = DynamicLtfMssDetector(candidate, evaluation_start_ns=start_ns)
    for minute_bar in minute_bars:
        five_minute_bar = five_minute_map.get(minute_bar.ts_event_ns)
        if five_minute_bar is not None:
            frozen.on_five_minute(five_minute_bar)
            dynamic.on_five_minute(five_minute_bar)
        frozen.on_one_minute(minute_bar)
        dynamic.on_one_minute(minute_bar)

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(asdict(row) for row in dynamic.dynamic_events).to_csv(
        output / "dynamic_mss_events.csv",
        index=False,
    )
    cases = {
        "frozen-mss-market": (
            frozen,
            "ltf-mss-market",
        ),
        "dynamic-mss-market": (
            dynamic,
            "dynamic-mss-market",
        ),
        "dynamic-mss-flow": (
            dynamic,
            "dynamic-mss-flow",
        ),
    }
    results: dict[str, Any] = {}
    for label, (detector, schedule_name) in cases.items():
        schedule = {
            timestamp: tuple(rows)
            for timestamp, rows in detector.schedules[schedule_name].items()
        }
        trades, metrics, daily = simulate(
            variant=Variant(label, ("BTCUSDT",), (60,)),
            bars_by_symbol={"BTCUSDT": minute_bars},
            evaluation_start=start,
            evaluation_end=end,
            base_candidate=candidate,
            cost=cost,
            minimum_price_risk_fraction=float(execution["minimum_price_risk_fraction"]),
            minimum_net_reward_risk=float(execution["minimum_net_reward_risk"]),
            starting_nav=float(execution["starting_nav"]),
            risk_rates=(RISK_RATE,),
            allowed_scenario_ids=frozenset(),
            external_plans_by_signal_time=schedule,
        )
        destination = output / label
        destination.mkdir(parents=True, exist_ok=True)
        trades.to_csv(destination / "trades.csv", index=False)
        atomic_json(destination / "metrics.json", metrics)
        pd.DataFrame(daily[RISK_RATE]).to_csv(
            destination / "daily_nav.csv",
            index=False,
        )
        results[label] = {
            "stage_counts": dict(detector.stage_counts),
            "rule_counts": dict(detector.rule_counts[schedule_name]),
            "metrics": metrics,
        }

    payload = {
        "scenario": "frozen versus dynamic one-minute MSS after repeated-pool sweep",
        "evaluation_start_utc": start.isoformat(),
        "evaluation_end_utc": end.isoformat(),
        "response_window_minutes": RESPONSE_WINDOW_MINUTES,
        "risk_fraction": RISK_RATE,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "results": results,
        "downloads": [asdict(record) for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "dynamic_ltf_mss_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-hybrid-first-week",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-dynamic-ltf-mss",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
