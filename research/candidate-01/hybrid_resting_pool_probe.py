#!/usr/bin/env python3
"""Hybrid repeated-swing liquidity pools with one-minute confirmation.

Resting pools and sweeps remain causally defined on completed five-minute bars.
Only the confirmation clock changes: after a valid raid and re-entry, the
strategy watches completed one-minute bars for the first internal-structure
break with strong opposite aggressive flow, then enters on the next minute.
This separates scarce structural opportunities from confirmation latency.

Predeclared variants:

* one-minute-strong-flow: current aligned 1m flow z >= 1.7 and displacement;
* one-minute-persistent-flow: aligned flow persists across two completed
  minutes with a cumulative displacement;
* source-three-touch: the strong-flow event also comes from a pool with at
  least three independent touches;
* aged-two-sided: both source and target pools predate the sweep by two hours.

The shared portfolio simulator preserves one global position, one-bar delay,
structural loss sizing, 7 bps-per-side stress cost, NAV and margin accounting.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from math import log
from pathlib import Path
import statistics
import sys
from typing import Any, Deque

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from core import AuctionBar, CandidateConfig, Response, Side, TradePlan  # noqa: E402
from data import load_interval, to_auction_bars  # noqa: E402
from portfolio_probe import Pending, Variant, _aggregate_variant, simulate  # noqa: E402
from resting_liquidity_pool_probe import (  # noqa: E402
    RestingLiquidityPoolDetector,
    SweepAttempt,
    aggregate_five_minute,
    week_segments,
)


RISK_RATES = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08)
RULES = (
    "one-minute-strong-flow",
    "one-minute-persistent-flow",
    "source-three-touch",
    "aged-two-sided",
)
ONE_MINUTE_NS = 60 * 1_000_000_000


class SweepOnlyStructure(RestatingLiquidityPoolDetector if False else RestingLiquidityPoolDetector):
    """Use the inherited 5m pool/sweep state but never confirm on 5m bars."""

    def _confirm_attempt(self, *, bar: AuctionBar, state: Any) -> None:
        # Hybrid confirmation occurs on the independent one-minute state below.
        return


@dataclass(frozen=True, slots=True)
class MinuteState:
    atr: float | None
    flow_z: float | None
    volume_z: float | None
    body_atr: float | None


class MinuteTracker:
    def __init__(self, config: CandidateConfig) -> None:
        self.config = config
        self.true_ranges: Deque[float] = deque(maxlen=config.atr_lookback)
        self.flows: Deque[float] = deque(maxlen=config.flow_lookback)
        self.log_volumes: Deque[float] = deque(maxlen=config.volume_lookback)
        self.previous_close: float | None = None

    @staticmethod
    def _zscore(value: float, history: Deque[float]) -> float | None:
        if len(history) < 20:
            return None
        mean = statistics.fmean(history)
        variance = statistics.fmean((item - mean) ** 2 for item in history)
        return 0.0 if variance <= 0.0 else (value - mean) / variance**0.5

    def observe(self, bar: AuctionBar) -> MinuteState:
        atr = statistics.fmean(self.true_ranges) if len(self.true_ranges) >= 20 else None
        flow_z = self._zscore(bar.aggressive_imbalance, self.flows)
        volume_z = self._zscore(log(max(bar.quote_volume, 1e-12)), self.log_volumes)
        body_atr = (bar.close - bar.open) / atr if atr is not None and atr > 0.0 else None
        return MinuteState(atr=atr, flow_z=flow_z, volume_z=volume_z, body_atr=body_atr)

    def update(self, bar: AuctionBar) -> None:
        previous = self.previous_close
        true_range = (
            bar.high - bar.low
            if previous is None
            else max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous))
        )
        self.true_ranges.append(true_range)
        self.flows.append(bar.aggressive_imbalance)
        self.log_volumes.append(log(max(bar.quote_volume, 1e-12)))
        self.previous_close = bar.close


@dataclass(slots=True)
class HybridAttempt:
    source: SweepAttempt
    sweep_close_time_ns: int
    expiry_time_ns: int
    previous_aligned_flow_z: float | None = None
    previous_close: float | None = None
    one_minute_extreme: float | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    rule: str
    scenario_id: str
    signal_time_ns: int
    side: str
    source_pool_id: int
    target_pool_id: int
    source_touches: int
    target_touches: int
    source_age_bars: int
    target_age_bars: int
    sweep_excursion_atr: float
    sweep_flow_z_5m: float
    sweep_volume_z_5m: float
    confirmation_flow_z_1m: float
    previous_flow_z_1m: float | None
    confirmation_volume_z_1m: float
    confirmation_body_atr_1m: float
    two_minute_displacement_atr: float | None
    confirmation_latency_minutes: int
    stop_distance_atr_5m: float
    target_distance_atr_5m: float


class HybridRestingPoolDetector:
    def __init__(self, config: CandidateConfig) -> None:
        self.config = config
        self.structure = SweepOnlyStructure(config)
        self.minute = MinuteTracker(config)
        self.active: HybridAttempt | None = None
        self.cooldown_until_ns = 0
        self.schedules: dict[str, dict[int, list[Pending]]] = {
            rule: {} for rule in RULES
        }
        self.evidence: list[Evidence] = []
        self.rejections: dict[str, int] = {
            "attempt_replaced": 0,
            "expired_attempt": 0,
            "invalid_geometry": 0,
            "target_touched": 0,
            "minute_state_unavailable": 0,
            "cooldown": 0,
        }

    def on_five_minute(self, bar: AuctionBar) -> None:
        self.structure.on_bar(bar)
        attempt = self.structure.attempt
        if attempt is None:
            return
        # The inherited sweep detector consumes the source pool and selects the
        # nearest opposing active pool.  Move that frozen context to the minute
        # confirmation state immediately so no later 5m bar can change it.
        if self.active is not None:
            self.rejections["attempt_replaced"] += 1
        self.active = HybridAttempt(
            source=attempt,
            sweep_close_time_ns=bar.ts_event_ns,
            expiry_time_ns=bar.ts_event_ns + 20 * ONE_MINUTE_NS,
            one_minute_extreme=attempt.sweep_extreme,
        )
        self.structure.attempt = None

    @staticmethod
    def _minimum_stop(entry: float, stop: float, side: Side, atr: float) -> float:
        minimum = 0.75 * atr
        return min(stop, entry - minimum) if side is Side.LONG else max(stop, entry + minimum)

    def _emit(
        self,
        *,
        rule: str,
        bar: AuctionBar,
        state: MinuteState,
        attempt: HybridAttempt,
        aligned_flow: float,
        previous_aligned_flow: float | None,
        two_minute_displacement_atr: float | None,
    ) -> Pending | None:
        source = attempt.source
        side = source.side
        entry = bar.close
        sweep_extreme = float(attempt.one_minute_extreme or source.sweep_extreme)
        raw_stop = (
            sweep_extreme - 0.15 * source.atr
            if side is Side.LONG
            else sweep_extreme + 0.15 * source.atr
        )
        stop = self._minimum_stop(entry, raw_stop, side, source.atr)
        target = source.target_level
        untouched = bar.high < target if side is Side.LONG else bar.low > target
        if not untouched:
            self.rejections["target_touched"] += 1
            return None
        geometry = stop < entry < target if side is Side.LONG else target < entry < stop
        if not geometry:
            self.rejections["invalid_geometry"] += 1
            return None
        risk = abs(entry - stop)
        reward = abs(target - entry)
        rr = reward / risk if risk > 0.0 else 0.0
        if rr < 1.35:
            self.rejections["invalid_geometry"] += 1
            return None
        scenario_id = (
            f"hybrid-pool:{rule}:{source.source_pool_id}:{source.target_pool_id}:"
            f"{side.value.lower()}:{bar.ts_event_ns}"
        )
        plan = TradePlan(
            scenario_id=scenario_id,
            side=side,
            response=Response.SWEEP_FAILURE,
            signal_time_ns=bar.ts_event_ns,
            observed_time_ns=bar.ts_event_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            anchor_high=max(source.source_level, source.target_level),
            anchor_low=min(source.source_level, source.target_level),
            sweep_extreme=sweep_extreme,
            atr=source.atr,
            estimated_reward_risk=rr,
            max_hold_bars=180,
            reason_code="RESTING_POOL_ONE_MINUTE_DISPLACEMENT_CONFIRMED",
        )
        pending = Pending(symbol="BTCUSDT", horizon=60, plan=plan)
        self.schedules[rule].setdefault(bar.ts_event_ns, []).append(pending)
        self.evidence.append(
            Evidence(
                rule=rule,
                scenario_id=scenario_id,
                signal_time_ns=bar.ts_event_ns,
                side=side.value,
                source_pool_id=source.source_pool_id,
                target_pool_id=source.target_pool_id,
                source_touches=source.source_touches,
                target_touches=source.target_touches,
                source_age_bars=source.source_age_bars,
                target_age_bars=source.target_age_bars,
                sweep_excursion_atr=source.sweep_excursion_atr,
                sweep_flow_z_5m=source.sweep_flow_z,
                sweep_volume_z_5m=source.sweep_volume_z,
                confirmation_flow_z_1m=aligned_flow,
                previous_flow_z_1m=previous_aligned_flow,
                confirmation_volume_z_1m=float(state.volume_z or 0.0),
                confirmation_body_atr_1m=side.sign * float(state.body_atr or 0.0),
                two_minute_displacement_atr=two_minute_displacement_atr,
                confirmation_latency_minutes=int(
                    (bar.ts_event_ns - attempt.sweep_close_time_ns) // ONE_MINUTE_NS,
                ),
                stop_distance_atr_5m=abs(entry - stop) / source.atr,
                target_distance_atr_5m=abs(target - entry) / source.atr,
            ),
        )
        return pending

    def on_one_minute(self, bar: AuctionBar) -> None:
        state = self.minute.observe(bar)
        attempt = self.active
        if attempt is not None and bar.ts_event_ns > attempt.sweep_close_time_ns:
            if bar.ts_event_ns > attempt.expiry_time_ns:
                self.rejections["expired_attempt"] += 1
                self.active = None
            elif bar.ts_event_ns <= self.cooldown_until_ns:
                self.rejections["cooldown"] += 1
            elif state.atr is None or state.flow_z is None or state.body_atr is None or state.volume_z is None:
                self.rejections["minute_state_unavailable"] += 1
            else:
                source = attempt.source
                side = source.side
                if side is Side.LONG:
                    attempt.one_minute_extreme = min(
                        float(attempt.one_minute_extreme or bar.low),
                        bar.low,
                    )
                    structure_break = bar.close >= source.internal_break + 0.03 * source.atr
                else:
                    attempt.one_minute_extreme = max(
                        float(attempt.one_minute_extreme or bar.high),
                        bar.high,
                    )
                    structure_break = bar.close <= source.internal_break - 0.03 * source.atr
                aligned_flow = side.sign * state.flow_z
                aligned_body = side.sign * state.body_atr
                previous_aligned_flow = attempt.previous_aligned_flow_z
                two_minute_displacement_atr = (
                    side.sign * (bar.close - attempt.previous_close) / state.atr
                    if attempt.previous_close is not None and state.atr > 0.0
                    else None
                )
                strong = structure_break and aligned_flow >= 1.70 and aligned_body >= 0.50
                persistent = (
                    structure_break
                    and aligned_flow >= 1.00
                    and previous_aligned_flow is not None
                    and previous_aligned_flow >= 0.50
                    and aligned_body >= 0.30
                    and two_minute_displacement_atr is not None
                    and two_minute_displacement_atr >= 0.65
                )
                emitted = False
                if strong:
                    emitted = self._emit(
                        rule="one-minute-strong-flow",
                        bar=bar,
                        state=state,
                        attempt=attempt,
                        aligned_flow=aligned_flow,
                        previous_aligned_flow=previous_aligned_flow,
                        two_minute_displacement_atr=two_minute_displacement_atr,
                    ) is not None
                    if source.source_touches >= 3:
                        self._emit(
                            rule="source-three-touch",
                            bar=bar,
                            state=state,
                            attempt=attempt,
                            aligned_flow=aligned_flow,
                            previous_aligned_flow=previous_aligned_flow,
                            two_minute_displacement_atr=two_minute_displacement_atr,
                        )
                    if source.source_age_bars >= 24 and source.target_age_bars >= 24:
                        self._emit(
                            rule="aged-two-sided",
                            bar=bar,
                            state=state,
                            attempt=attempt,
                            aligned_flow=aligned_flow,
                            previous_aligned_flow=previous_aligned_flow,
                            two_minute_displacement_atr=two_minute_displacement_atr,
                        )
                if persistent:
                    emitted = self._emit(
                        rule="one-minute-persistent-flow",
                        bar=bar,
                        state=state,
                        attempt=attempt,
                        aligned_flow=aligned_flow,
                        previous_aligned_flow=previous_aligned_flow,
                        two_minute_displacement_atr=two_minute_displacement_atr,
                    ) is not None or emitted
                if emitted:
                    self.cooldown_until_ns = bar.ts_event_ns + 10 * ONE_MINUTE_NS
                    self.active = None
                else:
                    attempt.previous_aligned_flow_z = aligned_flow
                    attempt.previous_close = bar.close
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
    risk_rates = tuple(float(value) for value in args.risk_rates.split(","))
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    aggregate_rows: dict[str, list[dict[str, Any]]] = {rule: [] for rule in RULES}
    manifest: list[dict[str, Any]] = []
    plan_counts: dict[str, dict[str, int]] = {}

    for label, start, end in week_segments(research):
        frame, records = load_interval(
            symbol="BTCUSDT",
            start=start,
            end=end,
            cache_dir=args.cache,
            warmup_minutes=3 * 24 * 60,
        )
        manifest.extend(asdict(record) for record in records)
        minute_bars = to_auction_bars(frame)
        five_minute_map = {
            bar.ts_event_ns: bar for bar in aggregate_five_minute(frame)
        }
        detector = HybridRestingPoolDetector(candidate)
        for minute_bar in minute_bars:
            five_minute_bar = five_minute_map.get(minute_bar.ts_event_ns)
            if five_minute_bar is not None:
                detector.on_five_minute(five_minute_bar)
            detector.on_one_minute(minute_bar)
        pd.DataFrame(asdict(row) for row in detector.evidence).to_csv(
            output / f"{label}_evidence.csv",
            index=False,
        )
        atomic_json(output / f"{label}_rejections.json", detector.rejections)
        plan_counts[label] = {
            rule: sum(len(rows) for rows in detector.schedules[rule].values())
            for rule in RULES
        }

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
                risk_rates=risk_rates,
                allowed_scenario_ids=frozenset(),
                external_plans_by_signal_time=schedule,
            )
            destination = output / rule / label
            destination.mkdir(parents=True, exist_ok=True)
            trades.to_csv(destination / "trades.csv", index=False)
            atomic_json(destination / "metrics.json", metrics)
            for risk, rows in daily.items():
                pd.DataFrame(rows).to_csv(
                    destination / f"daily_nav_{risk:.4f}.csv",
                    index=False,
                )
            aggregate_rows[rule].append(metrics)

    aggregates: dict[str, Any] = {}
    for rule, rows in aggregate_rows.items():
        aggregate = _aggregate_variant(rows, risk_rates)
        aggregate["all_segments_positive_at_one_percent_risk"] = all(
            float(row["risk_metrics"]["0.0100"]["total_return"]) > 0.0
            for row in rows
        )
        aggregate["minimum_segment_trades"] = min(int(row["trades"]) for row in rows)
        aggregates[rule] = aggregate
        atomic_json(output / rule / "aggregate_metrics.json", aggregate)

    files = pd.DataFrame(manifest).drop_duplicates(["symbol", "month"])
    atomic_json(
        output / "download_manifest.json",
        {"provider": "Binance Vision", "records": files.to_dict(orient="records")},
    )
    summary = {
        "scenario": "five-minute resting pools with one-minute displacement confirmation",
        "structure_timeframe_minutes": 5,
        "confirmation_timeframe_minutes": 1,
        "execution_timeframe_minutes": 1,
        "long_evaluation_run": False,
        "one_global_position": True,
        "one_bar_execution_delay": True,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "plan_counts": plan_counts,
        "aggregates": aggregates,
    }
    atomic_json(output / "hybrid_resting_pool_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-resting-pools",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-hybrid-resting-pools",
    )
    parser.add_argument(
        "--risk-rates",
        default=",".join(str(value) for value in RISK_RATES),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
