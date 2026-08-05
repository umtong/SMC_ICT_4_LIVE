#!/usr/bin/env python3
"""Causal prior-day and prior-week external-liquidity sweep scenarios.

Completed prior-day and prior-week highs/lows are explicit, widely observed
external liquidity references.  This detector does not assume every touch
reverses.  A setup requires a completed five-minute raid and close back through
the reference, sweep-direction effort, then a strong opposite-flow displacement
through pre-sweep internal structure.  Entry occurs on the next one-minute bar.
Targets are the completed period midpoint or its opposing external-liquidity
edge; stops remain beyond the actual raid extreme.

The shared candidate-01 portfolio simulator provides one global position,
structural loss sizing, 7 bps-per-side stress cost, NAV and margin accounting.
Only five frozen weeks are evaluated initially.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
from math import log
from pathlib import Path
import statistics
import sys
from typing import Any, Deque, Literal

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
from resting_liquidity_pool_probe import aggregate_five_minute, week_segments  # noqa: E402


RISK_RATES = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08)
RULES = (
    "prior-day-midpoint",
    "prior-day-opposite-edge",
    "prior-week-midpoint",
    "prior-week-opposite-edge",
    "calendar-pool-composite",
)
FIVE_MINUTES_NS = 5 * 60 * 1_000_000_000
ONE_MILLISECOND_NS = 1_000_000


@dataclass(slots=True)
class PeriodRange:
    key: str
    high: float
    low: float
    open: float
    close: float
    bars: int = 1

    def update(self, bar: AuctionBar) -> None:
        self.high = max(self.high, bar.high)
        self.low = min(self.low, bar.low)
        self.close = bar.close
        self.bars += 1

    @property
    def midpoint(self) -> float:
        return 0.5 * (self.high + self.low)


@dataclass(frozen=True, slots=True)
class State:
    atr: float | None
    flow_z: float | None
    volume_z: float | None
    body_atr: float | None


@dataclass(slots=True)
class Attempt:
    period: Literal["DAY", "WEEK"]
    side: Side
    source_level: float
    midpoint_target: float
    opposite_target: float
    sweep_extreme: float
    atr: float
    internal_break: float
    started_index: int
    expiry_index: int
    sweep_flow_z: float
    sweep_volume_z: float
    sweep_excursion_atr: float
    period_key: str


@dataclass(frozen=True, slots=True)
class Evidence:
    rule: str
    scenario_id: str
    signal_time_ns: int
    period: str
    period_key: str
    side: str
    source_level: float
    target_level: float
    sweep_extreme: float
    sweep_excursion_atr: float
    sweep_flow_z: float
    sweep_volume_z: float
    confirmation_flow_z: float
    confirmation_volume_z: float
    confirmation_body_atr: float
    structure_break_atr: float
    stop_distance_atr: float
    target_distance_atr: float


class CalendarLiquidityPoolDetector:
    def __init__(self, config: CandidateConfig) -> None:
        self.config = config
        self.index = -1
        self.true_ranges: Deque[float] = deque(maxlen=60)
        self.flows: Deque[float] = deque(maxlen=60)
        self.log_volumes: Deque[float] = deque(maxlen=60)
        self.recent_highs: Deque[float] = deque(maxlen=6)
        self.recent_lows: Deque[float] = deque(maxlen=6)
        self.previous_close: float | None = None
        self.day_builder: PeriodRange | None = None
        self.week_builder: PeriodRange | None = None
        self.prior_day: PeriodRange | None = None
        self.prior_week: PeriodRange | None = None
        self.current_day_key: str | None = None
        self.current_week_key: str | None = None
        self.day_consumed = {"HIGH": False, "LOW": False}
        self.week_consumed = {"HIGH": False, "LOW": False}
        self.day_outside = {"HIGH": 0, "LOW": 0}
        self.week_outside = {"HIGH": 0, "LOW": 0}
        self.attempt: Attempt | None = None
        self.cooldown_until = -1
        self.schedules: dict[str, dict[int, list[Pending]]] = {
            rule: {} for rule in RULES
        }
        self.evidence: list[Evidence] = []
        self.rejections: dict[str, int] = {
            "insufficient_history": 0,
            "ambiguous_sweep": 0,
            "no_internal_break": 0,
            "expired_attempt": 0,
            "target_touched": 0,
            "invalid_geometry": 0,
            "cooldown": 0,
        }

    @staticmethod
    def _zscore(value: float, history: Deque[float]) -> float | None:
        if len(history) < 20:
            return None
        mean = statistics.fmean(history)
        variance = statistics.fmean((item - mean) ** 2 for item in history)
        return 0.0 if variance <= 0.0 else (value - mean) / variance**0.5

    @staticmethod
    def _keys(ts_event_ns: int) -> tuple[str, str]:
        open_ns = ts_event_ns + ONE_MILLISECOND_NS - FIVE_MINUTES_NS
        opened = datetime.fromtimestamp(open_ns / 1_000_000_000, tz=timezone.utc)
        day_key = opened.date().isoformat()
        monday = opened.date() - timedelta(days=opened.weekday())
        return day_key, monday.isoformat()

    def _state(self, bar: AuctionBar) -> State:
        atr = statistics.fmean(self.true_ranges) if len(self.true_ranges) >= 20 else None
        flow_z = self._zscore(bar.aggressive_imbalance, self.flows)
        volume_z = self._zscore(log(max(bar.quote_volume, 1e-12)), self.log_volumes)
        body_atr = (bar.close - bar.open) / atr if atr is not None and atr > 0.0 else None
        return State(atr=atr, flow_z=flow_z, volume_z=volume_z, body_atr=body_atr)

    def _roll_ranges(self, bar: AuctionBar) -> None:
        day_key, week_key = self._keys(bar.ts_event_ns)
        if day_key != self.current_day_key:
            if self.day_builder is not None and self.day_builder.bars >= 250:
                self.prior_day = self.day_builder
            self.day_builder = PeriodRange(
                key=day_key,
                high=bar.high,
                low=bar.low,
                open=bar.open,
                close=bar.close,
            )
            self.current_day_key = day_key
            self.day_consumed = {"HIGH": False, "LOW": False}
            self.day_outside = {"HIGH": 0, "LOW": 0}
        else:
            assert self.day_builder is not None
            self.day_builder.update(bar)

        if week_key != self.current_week_key:
            if self.week_builder is not None and self.week_builder.bars >= 1_500:
                self.prior_week = self.week_builder
            self.week_builder = PeriodRange(
                key=week_key,
                high=bar.high,
                low=bar.low,
                open=bar.open,
                close=bar.close,
            )
            self.current_week_key = week_key
            self.week_consumed = {"HIGH": False, "LOW": False}
            self.week_outside = {"HIGH": 0, "LOW": 0}
        else:
            assert self.week_builder is not None
            self.week_builder.update(bar)

    def _acceptance_update(self, bar: AuctionBar, state: State) -> None:
        if state.atr is None:
            return
        for period, anchor, consumed, outside in (
            ("DAY", self.prior_day, self.day_consumed, self.day_outside),
            ("WEEK", self.prior_week, self.week_consumed, self.week_outside),
        ):
            if anchor is None:
                continue
            high_outside = bar.close > anchor.high + 0.15 * state.atr
            low_outside = bar.close < anchor.low - 0.15 * state.atr
            outside["HIGH"] = outside["HIGH"] + 1 if high_outside else 0
            outside["LOW"] = outside["LOW"] + 1 if low_outside else 0
            if outside["HIGH"] >= 2:
                consumed["HIGH"] = True
            if outside["LOW"] >= 2:
                consumed["LOW"] = True

    def _detect_sweep(self, bar: AuctionBar, state: State) -> None:
        if (
            self.attempt is not None
            or self.index <= self.cooldown_until
            or state.atr is None
            or state.flow_z is None
            or state.volume_z is None
        ):
            if state.atr is None:
                self.rejections["insufficient_history"] += 1
            return
        atr = state.atr
        candidates: list[tuple[int, str, PeriodRange, Side, float, float]] = []
        for priority, period, anchor, consumed in (
            (1, "DAY", self.prior_day, self.day_consumed),
            (2, "WEEK", self.prior_week, self.week_consumed),
        ):
            if anchor is None:
                continue
            high_excursion = (bar.high - anchor.high) / atr
            if (
                not consumed["HIGH"]
                and 0.08 <= high_excursion <= 1.75
                and bar.close <= anchor.high - 0.02 * atr
                and (state.flow_z >= 0.50 or state.volume_z >= 0.75)
            ):
                candidates.append(
                    (priority, period, anchor, Side.SHORT, high_excursion, bar.high),
                )
            low_excursion = (anchor.low - bar.low) / atr
            if (
                not consumed["LOW"]
                and 0.08 <= low_excursion <= 1.75
                and bar.close >= anchor.low + 0.02 * atr
                and (state.flow_z <= -0.50 or state.volume_z >= 0.75)
            ):
                candidates.append(
                    (priority, period, anchor, Side.LONG, low_excursion, bar.low),
                )
        if not candidates:
            return
        if {row[3] for row in candidates} == {Side.LONG, Side.SHORT}:
            self.rejections["ambiguous_sweep"] += 1
            return
        priority, period, anchor, side, excursion, extreme = sorted(
            candidates,
            key=lambda row: (-row[0], row[4]),
        )[0]
        internal_break = (
            max(self.recent_highs)
            if side is Side.LONG and self.recent_highs
            else min(self.recent_lows)
            if side is Side.SHORT and self.recent_lows
            else None
        )
        if internal_break is None:
            self.rejections["no_internal_break"] += 1
            return
        if not (
            internal_break > bar.close
            if side is Side.LONG
            else internal_break < bar.close
        ):
            self.rejections["no_internal_break"] += 1
            return
        consumed = self.week_consumed if period == "WEEK" else self.day_consumed
        consumed["LOW" if side is Side.LONG else "HIGH"] = True
        self.attempt = Attempt(
            period=period,
            side=side,
            source_level=(anchor.low if side is Side.LONG else anchor.high),
            midpoint_target=anchor.midpoint,
            opposite_target=(anchor.high if side is Side.LONG else anchor.low),
            sweep_extreme=extreme,
            atr=atr,
            internal_break=internal_break,
            started_index=self.index,
            expiry_index=self.index + 5,
            sweep_flow_z=state.flow_z,
            sweep_volume_z=state.volume_z,
            sweep_excursion_atr=excursion,
            period_key=anchor.key,
        )

    @staticmethod
    def _minimum_stop(entry: float, stop: float, side: Side, atr: float) -> float:
        minimum = 0.75 * atr
        return min(stop, entry - minimum) if side is Side.LONG else max(stop, entry + minimum)

    def _emit(
        self,
        *,
        rule: str,
        bar: AuctionBar,
        state: State,
        attempt: Attempt,
        target: float,
    ) -> Pending | None:
        assert state.flow_z is not None
        assert state.volume_z is not None
        assert state.body_atr is not None
        side = attempt.side
        entry = bar.close
        raw_stop = (
            attempt.sweep_extreme - 0.15 * attempt.atr
            if side is Side.LONG
            else attempt.sweep_extreme + 0.15 * attempt.atr
        )
        stop = self._minimum_stop(entry, raw_stop, side, attempt.atr)
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
            f"calendar:{rule}:{attempt.period}:{attempt.period_key}:"
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
            anchor_high=max(attempt.midpoint_target, attempt.opposite_target, attempt.source_level),
            anchor_low=min(attempt.midpoint_target, attempt.opposite_target, attempt.source_level),
            sweep_extreme=attempt.sweep_extreme,
            atr=attempt.atr,
            estimated_reward_risk=rr,
            max_hold_bars=240,
            reason_code="CALENDAR_EXTERNAL_LIQUIDITY_SWEEP_FAILURE_CONFIRMED",
        )
        pending = Pending(symbol="BTCUSDT", horizon=60, plan=plan)
        self.schedules[rule].setdefault(bar.ts_event_ns, []).append(pending)
        self.evidence.append(
            Evidence(
                rule=rule,
                scenario_id=scenario_id,
                signal_time_ns=bar.ts_event_ns,
                period=attempt.period,
                period_key=attempt.period_key,
                side=side.value,
                source_level=attempt.source_level,
                target_level=target,
                sweep_extreme=attempt.sweep_extreme,
                sweep_excursion_atr=attempt.sweep_excursion_atr,
                sweep_flow_z=attempt.sweep_flow_z,
                sweep_volume_z=attempt.sweep_volume_z,
                confirmation_flow_z=side.sign * state.flow_z,
                confirmation_volume_z=state.volume_z,
                confirmation_body_atr=side.sign * state.body_atr,
                structure_break_atr=(
                    side.sign * (bar.close - attempt.internal_break) / attempt.atr
                ),
                stop_distance_atr=abs(entry - stop) / attempt.atr,
                target_distance_atr=abs(target - entry) / attempt.atr,
            ),
        )
        return pending

    def _confirm(self, bar: AuctionBar, state: State) -> None:
        attempt = self.attempt
        if attempt is None:
            return
        if self.index > attempt.expiry_index:
            self.rejections["expired_attempt"] += 1
            self.attempt = None
            return
        if self.index <= attempt.started_index:
            return
        if state.flow_z is None or state.body_atr is None or state.volume_z is None:
            return
        if attempt.side is Side.LONG:
            attempt.sweep_extreme = min(attempt.sweep_extreme, bar.low)
            confirmed = (
                bar.close >= attempt.internal_break + 0.03 * attempt.atr
                and state.body_atr >= 0.50
                and state.flow_z >= 1.70
            )
        else:
            attempt.sweep_extreme = max(attempt.sweep_extreme, bar.high)
            confirmed = (
                bar.close <= attempt.internal_break - 0.03 * attempt.atr
                and state.body_atr <= -0.50
                and state.flow_z <= -1.70
            )
        if not confirmed:
            return
        if attempt.period == "DAY":
            midpoint_rule = "prior-day-midpoint"
            opposite_rule = "prior-day-opposite-edge"
        else:
            midpoint_rule = "prior-week-midpoint"
            opposite_rule = "prior-week-opposite-edge"
        emitted = self._emit(
            rule=midpoint_rule,
            bar=bar,
            state=state,
            attempt=attempt,
            target=attempt.midpoint_target,
        )
        self._emit(
            rule=opposite_rule,
            bar=bar,
            state=state,
            attempt=attempt,
            target=attempt.opposite_target,
        )
        if emitted is not None:
            self._emit(
                rule="calendar-pool-composite",
                bar=bar,
                state=state,
                attempt=attempt,
                target=attempt.midpoint_target,
            )
            self.cooldown_until = self.index + 12
        self.attempt = None

    def on_bar(self, bar: AuctionBar) -> None:
        self.index += 1
        state = self._state(bar)
        self._roll_ranges(bar)
        self._confirm(bar, state)
        self._acceptance_update(bar, state)
        if self.index <= self.cooldown_until:
            self.rejections["cooldown"] += 1
        else:
            self._detect_sweep(bar, state)
        previous = self.previous_close
        true_range = (
            bar.high - bar.low
            if previous is None
            else max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous))
        )
        self.true_ranges.append(true_range)
        self.flows.append(bar.aggressive_imbalance)
        self.log_volumes.append(log(max(bar.quote_volume, 1e-12)))
        self.recent_highs.append(bar.high)
        self.recent_lows.append(bar.low)
        self.previous_close = bar.close


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
            warmup_minutes=10 * 24 * 60,
        )
        manifest.extend(asdict(record) for record in records)
        one_minute = to_auction_bars(frame)
        detector = CalendarLiquidityPoolDetector(candidate)
        for bar in aggregate_five_minute(frame):
            detector.on_bar(bar)
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
                bars_by_symbol={"BTCUSDT": one_minute},
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
        "scenario": "prior-day and prior-week external-liquidity sweep failure",
        "structure_timeframe_minutes": 5,
        "execution_timeframe_minutes": 1,
        "long_evaluation_run": False,
        "one_global_position": True,
        "one_bar_execution_delay": True,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "plan_counts": plan_counts,
        "aggregates": aggregates,
    }
    atomic_json(output / "calendar_liquidity_pool_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-calendar-pools",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-calendar-pools",
    )
    parser.add_argument(
        "--risk-rates",
        default=",".join(str(value) for value in RISK_RATES),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
