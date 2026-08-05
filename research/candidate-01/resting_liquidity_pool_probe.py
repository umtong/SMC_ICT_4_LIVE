#!/usr/bin/env python3
"""Causal repeated-swing resting-liquidity sweep scenarios.

Fixed clock ranges are only proxies for where orders rest.  This detector builds
explicit five-minute liquidity pools from causally confirmed swing highs/lows.
A pool becomes tradable only after repeated touches separated in time.  The
scenario then requires:

1. a completed bar sweeps the resting pool and closes back through it;
2. sweep-direction effort/volume confirms that liquidity was actually taken;
3. within four completed five-minute bars, opposite displacement breaks the
   latest internal swing with a strong, predeclared flow shock;
4. the next one-minute bar enters, invalidates beyond the sweep extreme, and
   targets the nearest still-untouched opposing resting-liquidity pool.

The detector emits plans only.  The common candidate-01 simulator preserves the
one-global-position constraint, next-minute execution delay, structural loss
sizing, 7 bps-per-side stress cost, NAV and margin accounting.  Five frozen
weeks run before any long evaluation.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
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
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402
from portfolio_probe import Pending, Variant, _aggregate_variant, simulate  # noqa: E402


RISK_RATES = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08)
RULES = (
    "two-touch-resting-pool",
    "three-touch-source-pool",
    "three-touch-both-pools",
    "aged-two-sided-pools",
)
FIVE_MINUTES_NS = 5 * 60 * 1_000_000_000
POOL_MAX_AGE_BARS = 576  # 48 hours on five-minute bars.


@dataclass(frozen=True, slots=True)
class FiveMinuteState:
    atr: float | None
    flow_z: float | None
    volume_z: float | None
    body_atr: float | None


@dataclass(frozen=True, slots=True)
class Pivot:
    side: Literal["HIGH", "LOW"]
    price: float
    bar_index: int
    time_ns: int
    atr: float


@dataclass(slots=True)
class LiquidityPool:
    pool_id: int
    side: Literal["HIGH", "LOW"]
    level: float
    touch_prices: list[float]
    first_touch_index: int
    last_touch_index: int
    activated_index: int
    touches: int = 1
    consumed: bool = False
    accepted_outside_count: int = 0

    def add_touch(self, pivot: Pivot, activation_index: int) -> None:
        self.touch_prices.append(pivot.price)
        self.level = float(statistics.median(self.touch_prices))
        self.last_touch_index = pivot.bar_index
        self.activated_index = activation_index
        self.touches += 1
        self.accepted_outside_count = 0

    def age(self, current_index: int) -> int:
        return current_index - self.first_touch_index


@dataclass(slots=True)
class SweepAttempt:
    side: Side
    source_pool_id: int
    target_pool_id: int
    source_level: float
    target_level: float
    source_touches: int
    target_touches: int
    source_age_bars: int
    target_age_bars: int
    sweep_extreme: float
    atr: float
    internal_break: float
    started_index: int
    expiry_index: int
    sweep_flow_z: float
    sweep_volume_z: float
    sweep_excursion_atr: float


@dataclass(frozen=True, slots=True)
class Evidence:
    rule: str
    scenario_id: str
    signal_time_ns: int
    side: str
    source_pool_id: int
    target_pool_id: int
    source_level: float
    target_level: float
    source_touches: int
    target_touches: int
    source_age_bars: int
    target_age_bars: int
    sweep_excursion_atr: float
    sweep_flow_z: float
    sweep_volume_z: float
    confirmation_flow_z: float
    confirmation_volume_z: float
    confirmation_body_atr: float
    structure_break_atr: float
    stop_distance_atr: float
    target_distance_atr: float


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def aggregate_five_minute(frame: pd.DataFrame) -> list[AuctionBar]:
    values = frame.copy()
    values["bucket"] = pd.to_datetime(values["open_dt"], utc=True).dt.floor("5min")
    rows: list[AuctionBar] = []
    for _, group in values.groupby("bucket", sort=True):
        group = group.sort_values("open_dt", kind="stable")
        if len(group) != 5:
            continue
        first = group.iloc[0]
        last = group.iloc[-1]
        rows.append(
            AuctionBar(
                ts_event_ns=int(
                    pd.Timestamp(last["close_dt"]).as_unit("ns").value,
                ),
                open=float(first["open"]),
                high=float(group["high"].max()),
                low=float(group["low"].min()),
                close=float(last["close"]),
                base_volume=float(group["base_volume"].sum()),
                quote_volume=float(group["quote_volume"].sum()),
                taker_buy_quote_volume=float(
                    group["taker_buy_quote_volume"].sum(),
                ),
            ),
        )
    return rows


class RestingLiquidityPoolDetector:
    def __init__(self, config: CandidateConfig) -> None:
        self.config = config
        self.index = -1
        self.true_ranges: Deque[float] = deque(maxlen=60)
        self.flows: Deque[float] = deque(maxlen=60)
        self.log_volumes: Deque[float] = deque(maxlen=60)
        self.previous_close: float | None = None
        self.bars: list[AuctionBar] = []
        self.states: list[FiveMinuteState] = []
        self.pools: list[LiquidityPool] = []
        self.next_pool_id = 1
        self.latest_swing_high: Pivot | None = None
        self.latest_swing_low: Pivot | None = None
        self.attempt: SweepAttempt | None = None
        self.cooldown_until = -1
        self.schedules: dict[str, dict[int, list[Pending]]] = {
            rule: {} for rule in RULES
        }
        self.evidence: list[Evidence] = []
        self.rejections: dict[str, int] = {
            "insufficient_state": 0,
            "unclustered_pivot": 0,
            "ambiguous_sweep": 0,
            "no_internal_break": 0,
            "no_opposing_pool": 0,
            "expired_attempt": 0,
            "target_already_touched": 0,
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

    def _state(self, bar: AuctionBar) -> FiveMinuteState:
        atr = statistics.fmean(self.true_ranges) if len(self.true_ranges) >= 20 else None
        flow_z = self._zscore(bar.aggressive_imbalance, self.flows)
        volume_z = self._zscore(log(max(bar.quote_volume, 1e-12)), self.log_volumes)
        body_atr = (bar.close - bar.open) / atr if atr is not None and atr > 0.0 else None
        return FiveMinuteState(
            atr=atr,
            flow_z=flow_z,
            volume_z=volume_z,
            body_atr=body_atr,
        )

    def _confirmed_pivot(self) -> Pivot | None:
        if len(self.bars) < 5:
            return None
        candidate_index = len(self.bars) - 3
        candidate = self.bars[candidate_index]
        candidate_state = self.states[candidate_index]
        if candidate_state.atr is None or candidate_state.atr <= 0.0:
            return None
        left = self.bars[candidate_index - 2 : candidate_index]
        right = self.bars[candidate_index + 1 : candidate_index + 3]
        swing_high = (
            all(candidate.high > bar.high for bar in left)
            and all(candidate.high >= bar.high for bar in right)
        )
        swing_low = (
            all(candidate.low < bar.low for bar in left)
            and all(candidate.low <= bar.low for bar in right)
        )
        if swing_high and swing_low:
            return None
        if swing_high:
            return Pivot(
                side="HIGH",
                price=candidate.high,
                bar_index=candidate_index,
                time_ns=candidate.ts_event_ns,
                atr=candidate_state.atr,
            )
        if swing_low:
            return Pivot(
                side="LOW",
                price=candidate.low,
                bar_index=candidate_index,
                time_ns=candidate.ts_event_ns,
                atr=candidate_state.atr,
            )
        return None

    def _register_pivot(self, pivot: Pivot) -> None:
        if pivot.side == "HIGH":
            self.latest_swing_high = pivot
        else:
            self.latest_swing_low = pivot
        tolerance = 0.18 * pivot.atr
        candidates = [
            pool
            for pool in self.pools
            if not pool.consumed
            and pool.side == pivot.side
            and self.index - pool.last_touch_index >= 4
            and abs(pool.level - pivot.price) <= tolerance
        ]
        if candidates:
            chosen = min(candidates, key=lambda pool: abs(pool.level - pivot.price))
            chosen.add_touch(pivot, self.index)
            return
        self.pools.append(
            LiquidityPool(
                pool_id=self.next_pool_id,
                side=pivot.side,
                level=pivot.price,
                touch_prices=[pivot.price],
                first_touch_index=pivot.bar_index,
                last_touch_index=pivot.bar_index,
                activated_index=self.index,
            ),
        )
        self.next_pool_id += 1
        self.rejections["unclustered_pivot"] += 1

    def _expire_and_accept_pools(self, bar: AuctionBar, state: FiveMinuteState) -> None:
        atr = state.atr
        for pool in self.pools:
            if pool.consumed:
                continue
            if self.index - pool.last_touch_index > POOL_MAX_AGE_BARS:
                pool.consumed = True
                continue
            if atr is None:
                continue
            outside = (
                bar.close > pool.level + 0.15 * atr
                if pool.side == "HIGH"
                else bar.close < pool.level - 0.15 * atr
            )
            pool.accepted_outside_count = (
                pool.accepted_outside_count + 1 if outside else 0
            )
            if pool.accepted_outside_count >= 2:
                pool.consumed = True

    def _nearest_opposing_pool(
        self,
        *,
        side: Side,
        entry_reference: float,
    ) -> LiquidityPool | None:
        desired = "HIGH" if side is Side.LONG else "LOW"
        candidates = [
            pool
            for pool in self.pools
            if not pool.consumed
            and pool.touches >= 2
            and pool.side == desired
            and pool.activated_index < self.index
            and (
                pool.level > entry_reference
                if side is Side.LONG
                else pool.level < entry_reference
            )
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda pool: abs(pool.level - entry_reference))

    def _detect_sweep(
        self,
        *,
        bar: AuctionBar,
        state: FiveMinuteState,
    ) -> None:
        if (
            self.attempt is not None
            or self.index <= self.cooldown_until
            or state.atr is None
            or state.flow_z is None
            or state.volume_z is None
        ):
            if state.atr is None:
                self.rejections["insufficient_state"] += 1
            return
        atr = state.atr
        high_candidates: list[tuple[LiquidityPool, float]] = []
        low_candidates: list[tuple[LiquidityPool, float]] = []
        for pool in self.pools:
            if (
                pool.consumed
                or pool.touches < 2
                or pool.activated_index >= self.index
            ):
                continue
            if pool.side == "HIGH":
                excursion = (bar.high - pool.level) / atr
                if (
                    0.08 <= excursion <= 1.50
                    and bar.close <= pool.level - 0.02 * atr
                    and (state.flow_z >= 0.50 or state.volume_z >= 0.75)
                ):
                    high_candidates.append((pool, excursion))
            else:
                excursion = (pool.level - bar.low) / atr
                if (
                    0.08 <= excursion <= 1.50
                    and bar.close >= pool.level + 0.02 * atr
                    and (state.flow_z <= -0.50 or state.volume_z >= 0.75)
                ):
                    low_candidates.append((pool, excursion))
        if high_candidates and low_candidates:
            self.rejections["ambiguous_sweep"] += 1
            return
        candidates = high_candidates or low_candidates
        if not candidates:
            return
        source, excursion = sorted(
            candidates,
            key=lambda row: (-row[0].touches, row[1], row[0].pool_id),
        )[0]
        side = Side.SHORT if source.side == "HIGH" else Side.LONG
        internal = (
            self.latest_swing_low if side is Side.SHORT else self.latest_swing_high
        )
        if internal is None:
            self.rejections["no_internal_break"] += 1
            return
        internal_break = internal.price
        if not (
            internal_break < bar.close
            if side is Side.SHORT
            else internal_break > bar.close
        ):
            self.rejections["no_internal_break"] += 1
            return
        target = self._nearest_opposing_pool(
            side=side,
            entry_reference=bar.close,
        )
        if target is None or target.pool_id == source.pool_id:
            self.rejections["no_opposing_pool"] += 1
            return
        source.consumed = True
        self.attempt = SweepAttempt(
            side=side,
            source_pool_id=source.pool_id,
            target_pool_id=target.pool_id,
            source_level=source.level,
            target_level=target.level,
            source_touches=source.touches,
            target_touches=target.touches,
            source_age_bars=source.age(self.index),
            target_age_bars=target.age(self.index),
            sweep_extreme=(bar.high if side is Side.SHORT else bar.low),
            atr=atr,
            internal_break=internal_break,
            started_index=self.index,
            expiry_index=self.index + 4,
            sweep_flow_z=state.flow_z,
            sweep_volume_z=state.volume_z,
            sweep_excursion_atr=excursion,
        )

    @staticmethod
    def _minimum_stop(entry: float, stop: float, side: Side, atr: float) -> float:
        minimum = 0.75 * atr
        return min(stop, entry - minimum) if side is Side.LONG else max(stop, entry + minimum)

    def _emit_plan(
        self,
        *,
        rule: str,
        bar: AuctionBar,
        state: FiveMinuteState,
        attempt: SweepAttempt,
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
        target = attempt.target_level
        target_untouched = (
            bar.high < target if side is Side.LONG else bar.low > target
        )
        if not target_untouched:
            self.rejections["target_already_touched"] += 1
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
            f"resting-pool:{rule}:{attempt.source_pool_id}:{attempt.target_pool_id}:"
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
            anchor_high=max(attempt.source_level, attempt.target_level),
            anchor_low=min(attempt.source_level, attempt.target_level),
            sweep_extreme=attempt.sweep_extreme,
            atr=attempt.atr,
            estimated_reward_risk=rr,
            max_hold_bars=180,
            reason_code="RESTING_LIQUIDITY_POOL_SWEEP_FAILURE_CONFIRMED",
        )
        pending = Pending(symbol="BTCUSDT", horizon=60, plan=plan)
        self.schedules[rule].setdefault(bar.ts_event_ns, []).append(pending)
        structure_break_atr = (
            side.sign * (bar.close - attempt.internal_break) / attempt.atr
        )
        self.evidence.append(
            Evidence(
                rule=rule,
                scenario_id=scenario_id,
                signal_time_ns=bar.ts_event_ns,
                side=side.value,
                source_pool_id=attempt.source_pool_id,
                target_pool_id=attempt.target_pool_id,
                source_level=attempt.source_level,
                target_level=attempt.target_level,
                source_touches=attempt.source_touches,
                target_touches=attempt.target_touches,
                source_age_bars=attempt.source_age_bars,
                target_age_bars=attempt.target_age_bars,
                sweep_excursion_atr=attempt.sweep_excursion_atr,
                sweep_flow_z=attempt.sweep_flow_z,
                sweep_volume_z=attempt.sweep_volume_z,
                confirmation_flow_z=side.sign * state.flow_z,
                confirmation_volume_z=state.volume_z,
                confirmation_body_atr=side.sign * state.body_atr,
                structure_break_atr=structure_break_atr,
                stop_distance_atr=abs(entry - stop) / attempt.atr,
                target_distance_atr=abs(target - entry) / attempt.atr,
            ),
        )
        return pending

    def _confirm_attempt(
        self,
        *,
        bar: AuctionBar,
        state: FiveMinuteState,
    ) -> None:
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
        emitted: list[Pending] = []
        base = self._emit_plan(
            rule="two-touch-resting-pool",
            bar=bar,
            state=state,
            attempt=attempt,
        )
        if base is not None:
            emitted.append(base)
        if attempt.source_touches >= 3:
            value = self._emit_plan(
                rule="three-touch-source-pool",
                bar=bar,
                state=state,
                attempt=attempt,
            )
            if value is not None:
                emitted.append(value)
        if attempt.source_touches >= 3 and attempt.target_touches >= 3:
            value = self._emit_plan(
                rule="three-touch-both-pools",
                bar=bar,
                state=state,
                attempt=attempt,
            )
            if value is not None:
                emitted.append(value)
        if attempt.source_age_bars >= 24 and attempt.target_age_bars >= 24:
            value = self._emit_plan(
                rule="aged-two-sided-pools",
                bar=bar,
                state=state,
                attempt=attempt,
            )
            if value is not None:
                emitted.append(value)
        if emitted:
            self.cooldown_until = self.index + 12
        self.attempt = None

    def on_bar(self, bar: AuctionBar) -> None:
        self.index += 1
        state = self._state(bar)
        self.bars.append(bar)
        self.states.append(state)
        pivot = self._confirmed_pivot()
        if pivot is not None:
            self._register_pivot(pivot)
        self._confirm_attempt(bar=bar, state=state)
        self._expire_and_accept_pools(bar, state)
        if self.index <= self.cooldown_until:
            self.rejections["cooldown"] += 1
        else:
            self._detect_sweep(bar=bar, state=state)

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


def week_segments(research: dict[str, Any]) -> list[tuple[str, datetime, datetime]]:
    def week(label: str, value: str) -> tuple[str, datetime, datetime]:
        start = parse_utc_date(value)
        return label, start, start + timedelta(days=7)

    return [
        week("discovery", str(research["discovery_week"])),
        *[
            week(f"confirmation-{index + 1}", value)
            for index, value in enumerate(research["confirmation_weeks"])
        ],
        *[
            week(f"untouched-{index + 1}", value)
            for index, value in enumerate(research.get("additional_random_weeks", []))
        ],
    ]


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
        one_minute_bars = to_auction_bars(frame)
        detector = RestingLiquidityPoolDetector(candidate)
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
                bars_by_symbol={"BTCUSDT": one_minute_bars},
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
        "scenario": "causally confirmed repeated-swing resting liquidity pool sweep",
        "structure_timeframe_minutes": 5,
        "execution_timeframe_minutes": 1,
        "long_evaluation_run": False,
        "one_global_position": True,
        "one_bar_execution_delay": True,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "plan_counts": plan_counts,
        "aggregates": aggregates,
    }
    atomic_json(output / "resting_liquidity_pool_summary.json", summary)
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
        default=ROOT / "artifacts" / "candidate-01-resting-pools",
    )
    parser.add_argument(
        "--risk-rates",
        default=",".join(str(value) for value in RISK_RATES),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
