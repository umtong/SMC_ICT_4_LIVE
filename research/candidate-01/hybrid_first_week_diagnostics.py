#!/usr/bin/env python3
"""Controlled first-week diagnosis for the hybrid resting-pool scenario.

This script changes exactly one layer at a time: the one-minute confirmation
condition. Pool construction, five-minute sweep definition, opposing-pool
target, structural stop, next-minute execution, costs, risk sizing and the
single-position portfolio path remain unchanged.

Only the first frozen BTC week is evaluated. The diagnostic records every
causal sweep, first one-minute internal-structure break, confirmation state,
geometry rejection and executed trade. It is a research gate, not a long-run
optimizer.
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

from core import CandidateConfig, Response, Side, TradePlan  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402
from hybrid_resting_pool_probe import MinuteTracker, ONE_MINUTE_NS, SweepOnlyStructure  # noqa: E402
from portfolio_probe import Pending, Variant, simulate  # noqa: E402
from resting_liquidity_pool_probe import SweepAttempt, aggregate_five_minute  # noqa: E402


RULES = (
    "structure-break",
    "directional-break",
    "moderate-displacement",
    "strong-displacement",
    "persistent-displacement",
)
RISK_RATE = 0.03


@dataclass(slots=True)
class ActiveAttempt:
    source: SweepAttempt
    sweep_close_time_ns: int
    expiry_time_ns: int
    previous_aligned_flow_z: float | None = None
    previous_close: float | None = None
    one_minute_extreme: float | None = None


@dataclass(frozen=True, slots=True)
class BreakEvent:
    sweep_time_ns: int
    break_time_ns: int
    side: str
    source_pool_id: int
    target_pool_id: int
    source_touches: int
    target_touches: int
    source_age_bars: int
    target_age_bars: int
    latency_minutes: int
    aligned_flow_z: float
    previous_aligned_flow_z: float | None
    aligned_body_atr: float
    volume_z: float
    two_minute_displacement_atr: float | None
    stop_distance_atr: float
    target_distance_atr: float
    raw_reward_risk: float
    target_touched: bool
    geometry_valid: bool
    reward_risk_valid: bool
    eligible_rules: str
    emitted_rules: str


class ControlledDetector:
    def __init__(self, config: CandidateConfig, *, evaluation_start_ns: int) -> None:
        self.config = config
        self.evaluation_start_ns = evaluation_start_ns
        self.structure = SweepOnlyStructure(config)
        self.minute = MinuteTracker(config)
        self.active: ActiveAttempt | None = None
        self.schedules: dict[str, dict[int, list[Pending]]] = {
            rule: {} for rule in RULES
        }
        self.break_events: list[BreakEvent] = []
        self.stage_counts: Counter[str] = Counter()
        self.rule_counts: dict[str, Counter[str]] = {
            rule: Counter() for rule in RULES
        }

    def on_five_minute(self, bar: Any) -> None:
        self.structure.on_bar(bar)
        attempt = self.structure.attempt
        if attempt is None:
            return
        self.structure.attempt = None
        if bar.ts_event_ns < self.evaluation_start_ns:
            self.active = None
            return
        self.stage_counts["sweep_attempts"] += 1
        if self.active is not None:
            self.stage_counts["attempts_replaced"] += 1
        self.active = ActiveAttempt(
            source=attempt,
            sweep_close_time_ns=bar.ts_event_ns,
            expiry_time_ns=bar.ts_event_ns + 20 * ONE_MINUTE_NS,
            one_minute_extreme=attempt.sweep_extreme,
        )

    @staticmethod
    def _minimum_stop(entry: float, stop: float, side: Side, atr: float) -> float:
        minimum = 0.75 * atr
        return min(stop, entry - minimum) if side is Side.LONG else max(stop, entry + minimum)

    def _plan_geometry(
        self,
        *,
        attempt: ActiveAttempt,
        entry: float,
        bar: Any,
    ) -> tuple[float, float, float, bool, bool, bool]:
        source = attempt.source
        side = source.side
        sweep_extreme = float(attempt.one_minute_extreme or source.sweep_extreme)
        raw_stop = (
            sweep_extreme - 0.15 * source.atr
            if side is Side.LONG
            else sweep_extreme + 0.15 * source.atr
        )
        stop = self._minimum_stop(entry, raw_stop, side, source.atr)
        target = source.target_level
        target_touched = bar.high >= target if side is Side.LONG else bar.low <= target
        geometry_valid = stop < entry < target if side is Side.LONG else target < entry < stop
        risk = abs(entry - stop)
        reward = abs(target - entry)
        rr = reward / risk if risk > 0.0 else 0.0
        return stop, target, rr, target_touched, geometry_valid, rr >= 1.35

    def _emit(
        self,
        *,
        rule: str,
        bar: Any,
        attempt: ActiveAttempt,
        stop: float,
        target: float,
        rr: float,
    ) -> None:
        source = attempt.source
        side = source.side
        scenario_id = (
            f"hybrid-diagnostic:{rule}:{source.source_pool_id}:"
            f"{source.target_pool_id}:{side.value.lower()}:{bar.ts_event_ns}"
        )
        plan = TradePlan(
            scenario_id=scenario_id,
            side=side,
            response=Response.SWEEP_FAILURE,
            signal_time_ns=bar.ts_event_ns,
            observed_time_ns=bar.ts_event_ns,
            expected_entry=bar.close,
            stop_price=stop,
            target_price=target,
            anchor_high=max(source.source_level, source.target_level),
            anchor_low=min(source.source_level, source.target_level),
            sweep_extreme=float(attempt.one_minute_extreme or source.sweep_extreme),
            atr=source.atr,
            estimated_reward_risk=rr,
            max_hold_bars=180,
            reason_code="RESTING_POOL_FIRST_ONE_MINUTE_STRUCTURE_BREAK",
        )
        pending = Pending(symbol="BTCUSDT", horizon=60, plan=plan)
        self.schedules[rule].setdefault(bar.ts_event_ns, []).append(pending)
        self.rule_counts[rule]["plans_emitted"] += 1

    def on_one_minute(self, bar: Any) -> None:
        state = self.minute.observe(bar)
        active = self.active
        if active is not None and bar.ts_event_ns > active.sweep_close_time_ns:
            source = active.source
            side = source.side
            if side is Side.LONG:
                active.one_minute_extreme = min(
                    float(active.one_minute_extreme or bar.low),
                    bar.low,
                )
            else:
                active.one_minute_extreme = max(
                    float(active.one_minute_extreme or bar.high),
                    bar.high,
                )

            if bar.ts_event_ns > active.expiry_time_ns:
                self.stage_counts["expired_without_break"] += 1
                self.active = None
            elif (
                state.atr is None
                or state.flow_z is None
                or state.body_atr is None
                or state.volume_z is None
            ):
                self.stage_counts["minute_state_unavailable"] += 1
            else:
                structure_break = (
                    bar.close >= source.internal_break + 0.03 * source.atr
                    if side is Side.LONG
                    else bar.close <= source.internal_break - 0.03 * source.atr
                )
                aligned_flow = side.sign * state.flow_z
                aligned_body = side.sign * state.body_atr
                previous_aligned = active.previous_aligned_flow_z
                two_minute_displacement = (
                    side.sign * (bar.close - active.previous_close) / state.atr
                    if active.previous_close is not None and state.atr > 0.0
                    else None
                )
                if structure_break:
                    self.stage_counts["first_structure_breaks"] += 1
                    eligibility = {
                        "structure-break": True,
                        "directional-break": aligned_flow >= 0.0 and aligned_body >= 0.10,
                        "moderate-displacement": aligned_flow >= 0.75 and aligned_body >= 0.30,
                        "strong-displacement": aligned_flow >= 1.70 and aligned_body >= 0.50,
                        "persistent-displacement": (
                            aligned_flow >= 0.75
                            and previous_aligned is not None
                            and previous_aligned >= 0.25
                            and aligned_body >= 0.20
                            and two_minute_displacement is not None
                            and two_minute_displacement >= 0.50
                        ),
                    }
                    stop, target, rr, target_touched, geometry_valid, rr_valid = self._plan_geometry(
                        attempt=active,
                        entry=bar.close,
                        bar=bar,
                    )
                    if target_touched:
                        self.stage_counts["target_touched_before_entry"] += 1
                    if not geometry_valid:
                        self.stage_counts["invalid_geometry_at_break"] += 1
                    elif not rr_valid:
                        self.stage_counts["reward_risk_below_1_35"] += 1
                    emitted: list[str] = []
                    for rule, eligible in eligibility.items():
                        if not eligible:
                            continue
                        self.rule_counts[rule]["eligible_breaks"] += 1
                        if target_touched:
                            self.rule_counts[rule]["target_touched"] += 1
                        elif not geometry_valid:
                            self.rule_counts[rule]["invalid_geometry"] += 1
                        elif not rr_valid:
                            self.rule_counts[rule]["insufficient_reward_risk"] += 1
                        else:
                            self._emit(
                                rule=rule,
                                bar=bar,
                                attempt=active,
                                stop=stop,
                                target=target,
                                rr=rr,
                            )
                            emitted.append(rule)
                    self.break_events.append(
                        BreakEvent(
                            sweep_time_ns=active.sweep_close_time_ns,
                            break_time_ns=bar.ts_event_ns,
                            side=side.value,
                            source_pool_id=source.source_pool_id,
                            target_pool_id=source.target_pool_id,
                            source_touches=source.source_touches,
                            target_touches=source.target_touches,
                            source_age_bars=source.source_age_bars,
                            target_age_bars=source.target_age_bars,
                            latency_minutes=int(
                                (bar.ts_event_ns - active.sweep_close_time_ns)
                                // ONE_MINUTE_NS,
                            ),
                            aligned_flow_z=aligned_flow,
                            previous_aligned_flow_z=previous_aligned,
                            aligned_body_atr=aligned_body,
                            volume_z=state.volume_z,
                            two_minute_displacement_atr=two_minute_displacement,
                            stop_distance_atr=abs(bar.close - stop) / source.atr,
                            target_distance_atr=abs(target - bar.close) / source.atr,
                            raw_reward_risk=rr,
                            target_touched=target_touched,
                            geometry_valid=geometry_valid,
                            reward_risk_valid=rr_valid,
                            eligible_rules="|".join(
                                rule for rule, value in eligibility.items() if value
                            ),
                            emitted_rules="|".join(emitted),
                        ),
                    )
                    self.active = None
                else:
                    active.previous_aligned_flow_z = aligned_flow
                    active.previous_close = bar.close
        self.minute.update(bar)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
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
    detector = ControlledDetector(candidate, evaluation_start_ns=start_ns)
    for minute_bar in minute_bars:
        five_minute_bar = five_minute_map.get(minute_bar.ts_event_ns)
        if five_minute_bar is not None:
            detector.on_five_minute(five_minute_bar)
        detector.on_one_minute(minute_bar)

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(asdict(row) for row in detector.break_events).to_csv(
        output / "break_events.csv",
        index=False,
    )

    rule_results: dict[str, Any] = {}
    for rule in RULES:
        schedule = {
            timestamp: tuple(rows)
            for timestamp, rows in detector.schedules[rule].items()
        }
        trades, metrics, daily = simulate(
            variant=Variant(rule, ("BTCUSDT",), (60,)),
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
        destination = output / rule
        destination.mkdir(parents=True, exist_ok=True)
        trades.to_csv(destination / "trades.csv", index=False)
        atomic_json(destination / "metrics.json", metrics)
        pd.DataFrame(daily[RISK_RATE]).to_csv(
            destination / "daily_nav.csv",
            index=False,
        )
        rule_results[rule] = {
            "stage_counts": dict(detector.rule_counts[rule]),
            "metrics": metrics,
        }

    payload = {
        "scenario": "controlled first-week one-minute confirmation diagnosis",
        "evaluation_start_utc": start.isoformat(),
        "evaluation_end_utc": end.isoformat(),
        "risk_fraction": RISK_RATE,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "stage_counts": dict(detector.stage_counts),
        "rules": rule_results,
        "downloads": [asdict(record) for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "hybrid_first_week_diagnostics.json", payload)
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
        default=ROOT / "artifacts" / "candidate-01-hybrid-first-week-diagnostics",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
