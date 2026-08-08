#!/usr/bin/env python3
"""Candidate-02 V153: quarter-hour initiative -> completed auction-leg continuation.

This module intentionally preserves Candidate 13 V9's portfolio/execution interface
while changing only the alpha logic.  A synchronized quarter-hour burst is context,
not an entry.  A follower must subsequently complete:

COMMON_FLOW_INITIATIVE
-> OUTSIDE_DELIVERY
-> POST_INITIATIVE_FVG
-> FVG_RETRACE_HELD
-> FRESH_REACCELERATION_FVG
-> PASSIVE_ENTRY_ARMED

All observations are completed one-minute bars.  NautilusTrader remains the sole
owner of clocks, orders, fills, fees, positions, margin, and NAV.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
import statistics
from typing import Any, Mapping

from logic import (
    BarObs,
    Direction,
    LogicConfig,
    MINUTE_NS,
    ResearchEvent,
    Scenario,
    TradePlan,
)

QH_LOGIC_KEY = "PORTFOLIO::QUARTER_HOUR_COMMON_FLOW"
QH_MODULE = "QH_INITIATIVE_COMPLETED_AUCTION_LEG"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
EPISODE_LIFETIME_MINUTES = 60


@dataclass(frozen=True, slots=True)
class FiveMinuteImpulse:
    symbol: str
    start_ts_ns: int
    end_ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    atr: float

    @property
    def body(self) -> float:
        return self.close - self.open

    @property
    def direction(self) -> Direction | None:
        if self.body > 0.0:
            return Direction.LONG
        if self.body < 0.0:
            return Direction.SHORT
        return None

    @property
    def signed_flow(self) -> float:
        if self.volume <= 0.0:
            return 0.0
        return max(-1.0, min(1.0, 2.0 * self.taker_buy_volume / self.volume - 1.0))

    @property
    def standardized_body(self) -> float:
        return abs(self.body) / max(self.atr, self.close * 1e-12)

    @property
    def origin(self) -> float:
        if self.direction is Direction.LONG:
            return min(self.open, self.low)
        if self.direction is Direction.SHORT:
            return max(self.open, self.high)
        return self.open


@dataclass(slots=True)
class AuctionEpisode:
    event_id: str
    symbol: str
    direction: Direction
    owner_symbol: str
    accepted_symbols: tuple[str, ...]
    impulse: FiveMinuteImpulse
    created_ts_ns: int
    expiry_ts_ns: int
    state: str = "AWAIT_OUTSIDE_DELIVERY"
    delivery_ts_ns: int | None = None
    delivery_extreme: float | None = None
    fvg_ts_ns: int | None = None
    fvg_lower: float | None = None
    fvg_upper: float | None = None
    retrace_ts_ns: int | None = None
    retrace_extreme: float | None = None
    retrace_reference: float | None = None
    measured_objective: float | None = None
    state_sequence: list[str] = field(
        default_factory=lambda: ["COMMON_FLOW_INITIATIVE"]
    )


class QuarterHourCommonFlowEngine:
    """Use quarter-hour common flow as an initiative source, never as entry proof."""

    def __init__(
        self,
        config: LogicConfig,
        instrument_id: str = "PORTFOLIO.GLOBAL",
    ) -> None:
        self.config = config
        self.instrument_id = instrument_id
        self.events: list[ResearchEvent] = []
        self.skips: Counter[str] = Counter()
        history = max(360, config.atr_period + 20)
        self._bars: dict[str, deque[BarObs]] = {
            symbol: deque(maxlen=history) for symbol in SYMBOLS
        }
        self._episodes: dict[str, AuctionEpisode] = {}
        self._plan_states: dict[str, str] = {}
        self._active_scenario_id: str | None = None
        self._sequence = 0
        self._last_window_end_ns = -1

    @staticmethod
    def _ts(value: Any) -> int:
        return int(getattr(value, "ts_ns", value))

    def _event(
        self,
        *,
        scenario_id: str,
        event_type: str,
        event_time_ns: int,
        observed_time_ns: int,
        previous_state: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None,
        details: dict[str, Any],
    ) -> None:
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type=event_type,
                event_time_ns=int(event_time_ns),
                observed_time_ns=int(observed_time_ns),
                previous_state=previous_state,
                next_state=next_state,
                reason_code=reason_code,
                reference_price=(
                    None if reference_price is None else format(reference_price, ".10f")
                ),
                details=details,
            )
        )

    @staticmethod
    def _true_range(bar: BarObs, previous_close: float | None) -> float:
        if previous_close is None:
            return max(bar.high - bar.low, 1e-12)
        return max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
            1e-12,
        )

    def _atr(self, symbol: str) -> float | None:
        bars = list(self._bars[symbol])
        if len(bars) < self.config.atr_period + 1:
            return None
        ranges: list[float] = []
        for index in range(len(bars) - self.config.atr_period, len(bars)):
            previous = bars[index - 1].close if index > 0 else None
            ranges.append(self._true_range(bars[index], previous))
        return statistics.fmean(ranges) if ranges else None

    @staticmethod
    def _is_quarter_hour_window_end(ts_ns: int) -> bool:
        stamp = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        return (
            stamp.second == 0
            and stamp.microsecond == 0
            and stamp.minute % 15 == 5
        )

    def _impulse(self, symbol: str, ts_ns: int) -> FiveMinuteImpulse | None:
        bars = list(self._bars[symbol])
        if len(bars) < max(5, self.config.atr_period + 1):
            return None
        parts = bars[-5:]
        if parts[-1].ts_ns != ts_ns:
            return None
        expected = [parts[0].ts_ns + offset * MINUTE_NS for offset in range(5)]
        if [bar.ts_ns for bar in parts] != expected:
            self.skips["QH_NONCONTIGUOUS_FIVE_MINUTE_WINDOW"] += 1
            return None
        atr = self._atr(symbol)
        if atr is None or atr <= 0.0:
            return None
        return FiveMinuteImpulse(
            symbol=symbol,
            start_ts_ns=parts[0].ts_ns - MINUTE_NS,
            end_ts_ns=parts[-1].ts_ns,
            open=parts[0].open,
            high=max(bar.high for bar in parts),
            low=min(bar.low for bar in parts),
            close=parts[-1].close,
            volume=sum(bar.volume for bar in parts),
            taker_buy_volume=sum(bar.taker_buy_volume for bar in parts),
            atr=atr,
        )

    def _qualified(self, impulse: FiveMinuteImpulse, direction: Direction) -> bool:
        same_body = (
            impulse.body > 0.0
            if direction is Direction.LONG
            else impulse.body < 0.0
        )
        same_flow = (
            impulse.signed_flow >= self.config.displacement_flow_min
            if direction is Direction.LONG
            else impulse.signed_flow <= -self.config.displacement_flow_min
        )
        return (
            same_body
            and same_flow
            and impulse.standardized_body >= self.config.displacement_body_atr
        )

    @staticmethod
    def _directional_body(bar: BarObs, direction: Direction) -> bool:
        return (
            bar.close > bar.open
            if direction is Direction.LONG
            else bar.close < bar.open
        )

    @staticmethod
    def _flow_aligned(
        bar: BarObs,
        direction: Direction,
        minimum: float,
    ) -> bool:
        return (
            bar.signed_flow >= minimum
            if direction is Direction.LONG
            else bar.signed_flow <= -minimum
        )

    def _fvg(
        self,
        symbol: str,
        direction: Direction,
        *,
        minimum_body_atr: float,
        minimum_flow: float,
        after_ts_ns: int,
    ) -> tuple[float, float, int] | None:
        bars = list(self._bars[symbol])
        if len(bars) < 3:
            return None
        first, _, third = bars[-3:]
        if first.ts_ns <= after_ts_ns:
            return None
        atr = self._atr(symbol)
        if atr is None or atr <= 0.0:
            return None
        if not self._directional_body(third, direction):
            return None
        if third.body < minimum_body_atr * atr:
            return None
        if not self._flow_aligned(third, direction, minimum_flow):
            return None
        if direction is Direction.LONG:
            if third.low <= first.high:
                return None
            return first.high, third.low, third.ts_ns
        if third.high >= first.low:
            return None
        return third.high, first.low, third.ts_ns

    def _episode_invalidated(
        self,
        episode: AuctionEpisode,
        bar: BarObs,
        atr: float,
    ) -> bool:
        buffer = self.config.stop_buffer_atr * atr
        if episode.direction is Direction.LONG:
            return bar.close < episode.impulse.origin - buffer
        return bar.close > episode.impulse.origin + buffer

    def _terminate_episode(
        self,
        episode: AuctionEpisode,
        *,
        ts_ns: int,
        reason: str,
        reference: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._event(
            scenario_id=episode.event_id,
            event_type="QH_AUCTION_EPISODE_TERMINAL",
            event_time_ns=ts_ns,
            observed_time_ns=ts_ns,
            previous_state=episode.state,
            next_state="TERMINAL",
            reason_code=reason,
            reference_price=reference,
            details=details or {},
        )
        self._episodes.pop(episode.symbol, None)
        self.skips[reason] += 1

    def _advance(
        self,
        episode: AuctionEpisode,
        *,
        ts_ns: int,
        next_state: str,
        reason: str,
        reference: float | None,
        details: dict[str, Any],
    ) -> None:
        previous = episode.state
        episode.state = next_state
        episode.state_sequence.append(next_state)
        self._event(
            scenario_id=episode.event_id,
            event_type="QH_AUCTION_STATE_TRANSITION",
            event_time_ns=ts_ns,
            observed_time_ns=ts_ns,
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference,
            details=details,
        )

    def _plan(
        self,
        episode: AuctionEpisode,
        *,
        bar: BarObs,
        ts_ns: int,
        fresh_fvg: tuple[float, float, int],
        atr: float,
    ) -> TradePlan | None:
        lower, upper, fvg_ts_ns = fresh_fvg
        entry = (lower + upper) / 2.0
        if episode.retrace_extreme is None or episode.delivery_extreme is None:
            return None
        allowance = self.config.stop_buffer_atr * atr
        if episode.direction is Direction.LONG:
            stop = episode.retrace_extreme - allowance
            target = episode.measured_objective
            passive = (
                target is not None
                and stop < entry < bar.close < target
            )
        else:
            stop = episode.retrace_extreme + allowance
            target = episode.measured_objective
            passive = (
                target is not None
                and target < bar.close < entry < stop
            )
        if not passive or target is None:
            self.skips["QH_AUCTION_OBJECTIVE_ALREADY_CONSUMED"] += 1
            return None
        risk = abs(entry - stop)
        gross_gain = abs(target - entry)
        if risk <= 0.0 or risk / atr < self.config.min_stop_atr:
            self.skips["QH_AUCTION_STOP_DISTANCE_BELOW_EXECUTION_FLOOR"] += 1
            return None
        loss = (
            risk
            + entry * self.config.effective_maker_rate
            + stop * self.config.effective_taker_rate
        )
        net_gain = (
            gross_gain
            - entry * self.config.effective_maker_rate
            - target * self.config.effective_maker_rate
        )
        net_r = net_gain / loss if loss > 0.0 else float("-inf")
        if (
            not isfinite(net_r)
            or net_gain <= 0.0
            or net_r < self.config.min_net_r
        ):
            self.skips["QH_AUCTION_INSUFFICIENT_COSTED_STRUCTURAL_R"] += 1
            return None
        scenario_id = f"{episode.event_id}-{episode.symbol}-NEW-LEG"
        details = {
            "_logic_key": QH_LOGIC_KEY,
            "module": QH_MODULE,
            "route": "COMMON_FLOW_THEN_COMPLETED_AUCTION_LEG",
            "independent_episode_key": episode.event_id,
            "clock_phase": "UTC_QUARTER_HOUR_FIRST_FIVE_MINUTES",
            "owner_symbol": episode.owner_symbol,
            "accepted_symbols": episode.accepted_symbols,
            "direction": episode.direction.value,
            "initiative_start_ts_ns": episode.impulse.start_ts_ns,
            "initiative_end_ts_ns": episode.impulse.end_ts_ns,
            "outside_delivery_ts_ns": episode.delivery_ts_ns,
            "post_initiative_fvg_ts_ns": episode.fvg_ts_ns,
            "fvg_retrace_ts_ns": episode.retrace_ts_ns,
            "fresh_reacceleration_fvg_ts_ns": fvg_ts_ns,
            "state_sequence": tuple(
                episode.state_sequence + ["FRESH_REACCELERATION_FVG", "ENTRY_ARMED"]
            ),
            "initiative_standardized_body": episode.impulse.standardized_body,
            "initiative_signed_flow": episode.impulse.signed_flow,
            "delivery_extreme": episode.delivery_extreme,
            "retrace_extreme": episode.retrace_extreme,
            "post_initiative_fvg_lower": episode.fvg_lower,
            "post_initiative_fvg_upper": episode.fvg_upper,
            "fresh_fvg_lower": lower,
            "fresh_fvg_upper": upper,
            "measured_objective": target,
            "entry_model": "PASSIVE_FRESH_REACCELERATION_FVG_MIDPOINT",
            "stop_model": "COMPLETED_PULLBACK_EXTREME_INVALIDATION",
            "target_model": "PRE_ENTRY_DELIVERY_PULLBACK_MEASURED_OBJECTIVE",
            "entry_cost_assumption": "MAKER",
            "stop_cost_assumption": "TAKER",
            "target_cost_assumption": "MAKER",
        }
        plan = TradePlan(
            scenario_id=scenario_id,
            scenario=Scenario.AAC,
            direction=episode.direction,
            observed_ts_ns=ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            atr=atr,
            loss_per_unit=loss,
            gain_per_unit=net_gain,
            net_r=net_r,
            reason_code="QH_INITIATIVE_COMPLETED_AUCTION_LEG",
            expire_ts_ns=ts_ns + self.config.retrace_expiry_bars * MINUTE_NS,
            entry_order_type="LIMIT",
            entry_post_only=True,
            details=details,
        )
        self._plan_states[scenario_id] = "PENDING_ENTRY"
        self._event(
            scenario_id=scenario_id,
            event_type="QH_NEW_LEG_PLAN_CONFIRMED",
            event_time_ns=fvg_ts_ns,
            observed_time_ns=ts_ns,
            previous_state="FRESH_REACCELERATION_FVG",
            next_state="PENDING_ENTRY",
            reason_code=plan.reason_code,
            reference_price=entry,
            details={
                "entry": entry,
                "stop": stop,
                "target": target,
                "net_r": net_r,
                **details,
            },
        )
        return plan

    def _process_episode(
        self,
        episode: AuctionEpisode,
        bar: BarObs,
        ts_ns: int,
    ) -> TradePlan | None:
        if ts_ns > episode.expiry_ts_ns:
            self._terminate_episode(
                episode,
                ts_ns=ts_ns,
                reason="QH_AUCTION_EPISODE_EXPIRED",
                reference=bar.close,
            )
            return None
        atr = self._atr(episode.symbol)
        if atr is None or atr <= 0.0:
            return None
        if self._episode_invalidated(episode, bar, atr):
            self._terminate_episode(
                episode,
                ts_ns=ts_ns,
                reason="QH_INITIATIVE_ORIGIN_INVALIDATED",
                reference=bar.close,
            )
            return None

        if episode.state == "AWAIT_OUTSIDE_DELIVERY":
            delivered = (
                bar.close > episode.impulse.high
                if episode.direction is Direction.LONG
                else bar.close < episode.impulse.low
            )
            if not delivered:
                return None
            episode.delivery_ts_ns = ts_ns
            episode.delivery_extreme = (
                bar.high if episode.direction is Direction.LONG else bar.low
            )
            self._advance(
                episode,
                ts_ns=ts_ns,
                next_state="AWAIT_POST_INITIATIVE_FVG",
                reason="COMMON_FLOW_FOLLOWER_DELIVERED_OUTSIDE_IMPULSE",
                reference=bar.close,
                details={
                    "delivery_extreme": episode.delivery_extreme,
                    "impulse_high": episode.impulse.high,
                    "impulse_low": episode.impulse.low,
                },
            )
            return None

        if episode.state == "AWAIT_POST_INITIATIVE_FVG":
            if episode.direction is Direction.LONG:
                episode.delivery_extreme = max(
                    float(episode.delivery_extreme), bar.high
                )
            else:
                episode.delivery_extreme = min(
                    float(episode.delivery_extreme), bar.low
                )
            fvg = self._fvg(
                episode.symbol,
                episode.direction,
                minimum_body_atr=self.config.displacement_body_atr,
                minimum_flow=self.config.displacement_flow_min,
                after_ts_ns=int(episode.delivery_ts_ns or episode.created_ts_ns),
            )
            if fvg is None:
                return None
            episode.fvg_lower, episode.fvg_upper, episode.fvg_ts_ns = fvg
            self._advance(
                episode,
                ts_ns=ts_ns,
                next_state="AWAIT_FVG_RETRACE",
                reason="POST_INITIATIVE_DISPLACEMENT_LEFT_FVG",
                reference=(episode.fvg_lower + episode.fvg_upper) / 2.0,
                details={
                    "fvg_lower": episode.fvg_lower,
                    "fvg_upper": episode.fvg_upper,
                    "delivery_extreme": episode.delivery_extreme,
                },
            )
            return None

        if episode.state == "AWAIT_FVG_RETRACE":
            assert episode.fvg_lower is not None
            assert episode.fvg_upper is not None
            midpoint = (episode.fvg_lower + episode.fvg_upper) / 2.0
            if episode.direction is Direction.LONG:
                episode.delivery_extreme = max(
                    float(episode.delivery_extreme), bar.high
                )
                touched = bar.low <= midpoint
                held = bar.close > episode.fvg_lower and bar.close > episode.impulse.high
            else:
                episode.delivery_extreme = min(
                    float(episode.delivery_extreme), bar.low
                )
                touched = bar.high >= midpoint
                held = bar.close < episode.fvg_upper and bar.close < episode.impulse.low
            if not touched:
                return None
            if not held:
                self._terminate_episode(
                    episode,
                    ts_ns=ts_ns,
                    reason="QH_POST_INITIATIVE_FVG_RETRACE_FAILED",
                    reference=bar.close,
                    details={"fvg_midpoint": midpoint},
                )
                return None
            episode.retrace_ts_ns = ts_ns
            episode.retrace_extreme = (
                bar.low if episode.direction is Direction.LONG else bar.high
            )
            episode.retrace_reference = (
                bar.high if episode.direction is Direction.LONG else bar.low
            )
            leg_distance = abs(
                float(episode.delivery_extreme) - float(episode.retrace_extreme)
            )
            episode.measured_objective = (
                float(episode.delivery_extreme) + leg_distance
                if episode.direction is Direction.LONG
                else float(episode.delivery_extreme) - leg_distance
            )
            self._advance(
                episode,
                ts_ns=ts_ns,
                next_state="AWAIT_FRESH_REACCELERATION",
                reason="POST_INITIATIVE_FVG_RETRACE_HELD_OUTSIDE",
                reference=bar.close,
                details={
                    "retrace_extreme": episode.retrace_extreme,
                    "retrace_reference": episode.retrace_reference,
                    "measured_objective": episode.measured_objective,
                },
            )
            return None

        if episode.state == "AWAIT_FRESH_REACCELERATION":
            assert episode.retrace_extreme is not None
            assert episode.retrace_reference is not None
            if episode.direction is Direction.LONG:
                episode.retrace_extreme = min(episode.retrace_extreme, bar.low)
                break_structure = (
                    bar.close > episode.retrace_reference
                    and bar.close > float(episode.fvg_upper)
                )
            else:
                episode.retrace_extreme = max(episode.retrace_extreme, bar.high)
                break_structure = (
                    bar.close < episode.retrace_reference
                    and bar.close < float(episode.fvg_lower)
                )
            if not break_structure:
                return None
            fresh = self._fvg(
                episode.symbol,
                episode.direction,
                minimum_body_atr=self.config.reacceleration_body_atr,
                minimum_flow=self.config.reacceleration_flow_min,
                after_ts_ns=int(episode.retrace_ts_ns or ts_ns),
            )
            if fresh is None:
                return None
            plan = self._plan(
                episode,
                bar=bar,
                ts_ns=ts_ns,
                fresh_fvg=fresh,
                atr=atr,
            )
            self._episodes.pop(episode.symbol, None)
            if plan is None:
                self.skips["QH_FRESH_LEG_NOT_EXECUTABLE"] += 1
            return plan
        raise RuntimeError(f"unknown quarter-hour episode state: {episode.state}")

    def _detect_initiative(self, ts_ns: int) -> None:
        if not self._is_quarter_hour_window_end(ts_ns):
            return
        if ts_ns == self._last_window_end_ns:
            return
        self._last_window_end_ns = ts_ns
        impulses = [self._impulse(symbol, ts_ns) for symbol in SYMBOLS]
        if any(item is None for item in impulses):
            self.skips["QH_WARMUP_OR_INCOMPLETE_WINDOW"] += 1
            return
        materialized = [item for item in impulses if item is not None]
        long = [
            item for item in materialized if self._qualified(item, Direction.LONG)
        ]
        short = [
            item for item in materialized if self._qualified(item, Direction.SHORT)
        ]
        if len(long) >= 3 and len(short) >= 3:
            self.skips["QH_AMBIGUOUS_DUAL_DIRECTION_BREADTH"] += 1
            return
        if len(long) >= 3:
            direction, accepted = Direction.LONG, long
        elif len(short) >= 3:
            direction, accepted = Direction.SHORT, short
        else:
            self.skips["QH_COMMON_FLOW_BREADTH_BELOW_THREE"] += 1
            return
        owner = max(
            accepted,
            key=lambda item: (
                item.standardized_body,
                abs(item.signed_flow),
                item.symbol,
            ),
        )
        self._sequence += 1
        event_id = (
            f"QHLEG-{ts_ns}-{self._sequence:06d}-{direction.value}-{owner.symbol}"
        )
        accepted_symbols = tuple(item.symbol for item in accepted)
        self._event(
            scenario_id=event_id,
            event_type="QH_COMMON_FLOW_INITIATIVE_SOURCE",
            event_time_ns=min(item.start_ts_ns for item in accepted),
            observed_time_ns=ts_ns,
            previous_state="IDLE",
            next_state="COMMON_FLOW_INITIATIVE",
            reason_code="THREE_MARKET_QUARTER_HOUR_COMMON_FLOW_CONTEXT_ONLY",
            reference_price=owner.close,
            details={
                "direction": direction.value,
                "owner_symbol": owner.symbol,
                "accepted_symbols": accepted_symbols,
                "standardized_bodies": {
                    item.symbol: item.standardized_body for item in accepted
                },
                "signed_flows": {
                    item.symbol: item.signed_flow for item in accepted
                },
                "entry_emitted": False,
                "required_next_states": (
                    "OUTSIDE_DELIVERY",
                    "POST_INITIATIVE_FVG",
                    "FVG_RETRACE_HELD",
                    "FRESH_REACCELERATION_FVG",
                ),
            },
        )
        for impulse in accepted:
            if impulse.symbol == owner.symbol:
                continue
            existing = self._episodes.get(impulse.symbol)
            if existing is not None:
                self.skips["QH_OVERLAPPING_FOLLOWER_EPISODE_REJECTED"] += 1
                continue
            self._episodes[impulse.symbol] = AuctionEpisode(
                event_id=f"{event_id}-{impulse.symbol}",
                symbol=impulse.symbol,
                direction=direction,
                owner_symbol=owner.symbol,
                accepted_symbols=accepted_symbols,
                impulse=impulse,
                created_ts_ns=ts_ns,
                expiry_ts_ns=ts_ns + EPISODE_LIFETIME_MINUTES * MINUTE_NS,
            )

    def on_batch(
        self,
        ts_ns: int,
        bars: Mapping[str, BarObs],
    ) -> list[tuple[str, TradePlan]]:
        for symbol in SYMBOLS:
            observation = bars.get(symbol)
            if observation is None:
                self.skips["QH_SYNCHRONIZED_SYMBOL_MISSING"] += 1
                return []
            self._bars[symbol].append(observation)

        plans: list[tuple[str, TradePlan]] = []
        for symbol, episode in list(self._episodes.items()):
            plan = self._process_episode(episode, bars[symbol], ts_ns)
            if plan is not None:
                plans.append((symbol, plan))

        self._detect_initiative(ts_ns)
        if not plans and self._episodes:
            self.skips["QH_CONTEXT_ACTIVE_NO_COMPLETED_NEW_LEG_YET"] += 1
        return plans

    def _transition(
        self,
        plan: TradePlan,
        *,
        ts_ns: int,
        next_state: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        previous = self._plan_states.get(plan.scenario_id)
        if previous is None or previous == "TERMINAL":
            return
        self._event(
            scenario_id=plan.scenario_id,
            event_type="QH_PLAN_LIFECYCLE",
            event_time_ns=ts_ns,
            observed_time_ns=ts_ns,
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
            reference_price=plan.expected_entry,
            details=details or {},
        )
        self._plan_states[plan.scenario_id] = next_state

    def mark_rejected(
        self,
        plan: TradePlan,
        ts_or_bar: Any,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._transition(
            plan,
            ts_ns=self._ts(ts_or_bar),
            next_state="TERMINAL",
            reason=reason,
            details=details,
        )

    def mark_submitted(
        self,
        plan: TradePlan,
        quantity: Any,
        details: dict[str, Any],
    ) -> None:
        payload = dict(details)
        payload.update({"quantity": str(quantity), "module": QH_MODULE})
        self._transition(
            plan,
            ts_ns=plan.observed_ts_ns,
            next_state="SUBMITTED",
            reason="NAUTILUS_BRACKET_SUBMITTED",
            details=payload,
        )
        self._active_scenario_id = plan.scenario_id

    def mark_entry_filled(self, ts_ns: int, details: dict[str, Any]) -> None:
        scenario_id = str(
            details.get("scenario_id", self._active_scenario_id or "")
        )
        if self._plan_states.get(scenario_id) != "SUBMITTED":
            return
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type="QH_ENTRY_FILLED",
                event_time_ns=int(ts_ns),
                observed_time_ns=int(ts_ns),
                previous_state="SUBMITTED",
                next_state="POSITION_OPEN",
                reason_code="NAUTILUS_PARENT_FILLED",
                reference_price=None,
                details=dict(details),
            )
        )
        self._plan_states[scenario_id] = "POSITION_OPEN"
        self._active_scenario_id = scenario_id

    def mark_trade_terminal(self, ts_ns: int, reason: str) -> None:
        scenario_id = self._active_scenario_id
        if not scenario_id:
            return
        previous = self._plan_states.get(scenario_id)
        if previous not in {"SUBMITTED", "POSITION_OPEN"}:
            return
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type="QH_TRADE_TERMINAL",
                event_time_ns=int(ts_ns),
                observed_time_ns=int(ts_ns),
                previous_state=previous,
                next_state="TERMINAL",
                reason_code=reason,
                reference_price=None,
                details={"module": QH_MODULE},
            )
        )
        self._plan_states[scenario_id] = "TERMINAL"
        self._active_scenario_id = None


def _self_test() -> None:
    """Synthetic causal state test; performance is deliberately not asserted."""
    config = LogicConfig(
        atr_period=10,
        displacement_body_atr=0.20,
        displacement_flow_min=0.03,
        reacceleration_body_atr=0.18,
        reacceleration_flow_min=0.04,
        min_net_r=0.50,
    )
    engine = QuarterHourCommonFlowEngine(config)
    start = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1e9)
    prices = {symbol: 100.0 + index * 20.0 for index, symbol in enumerate(SYMBOLS)}

    def batch(minute: int, moves: Mapping[str, float], *, flow: float = 0.70) -> None:
        bars: dict[str, BarObs] = {}
        for symbol in SYMBOLS:
            open_price = prices[symbol]
            move = float(moves.get(symbol, 0.0))
            close = open_price + move
            high = max(open_price, close) + 0.02
            low = min(open_price, close) - 0.02
            volume = 1_000.0
            taker = volume * flow if move > 0 else volume * (1.0 - flow) if move < 0 else volume * 0.5
            bars[symbol] = BarObs(
                start + minute * MINUTE_NS,
                open_price,
                high,
                low,
                close,
                volume,
                taker,
            )
            prices[symbol] = close
        generated.extend(engine.on_batch(start + minute * MINUTE_NS, bars))

    generated: list[tuple[str, TradePlan]] = []
    for minute in range(1, 31):
        batch(minute, {})
    # 00:01..00:05 of a quarter-hour window: three aligned followers and one owner.
    for minute in range(31, 36):
        batch(
            minute,
            {
                "BTCUSDT": 0.20,
                "ETHUSDT": 0.18,
                "SOLUSDT": 0.16,
                "XRPUSDT": 0.00,
            },
        )
    assert engine._episodes, engine.skips
    assert not generated, "initiative must never emit an entry"

    # Outside delivery and a post-initiative bullish FVG.
    batch(36, {"BTCUSDT": 0.30, "ETHUSDT": 0.28, "SOLUSDT": 0.26})
    batch(37, {"BTCUSDT": 0.08, "ETHUSDT": 0.08, "SOLUSDT": 0.08})
    batch(38, {"BTCUSDT": 0.35, "ETHUSDT": 0.32, "SOLUSDT": 0.30})
    batch(39, {"BTCUSDT": 0.12, "ETHUSDT": 0.12, "SOLUSDT": 0.12})

    # Retrace into the first FVG while keeping acceptance outside the initiative.
    batch(40, {"BTCUSDT": -0.34, "ETHUSDT": -0.32, "SOLUSDT": -0.30}, flow=0.30)
    batch(41, {"BTCUSDT": 0.03, "ETHUSDT": 0.03, "SOLUSDT": 0.03})

    # Fresh three-bar reacceleration FVG.
    batch(42, {"BTCUSDT": 0.30, "ETHUSDT": 0.28, "SOLUSDT": 0.26})
    batch(43, {"BTCUSDT": 0.12, "ETHUSDT": 0.12, "SOLUSDT": 0.12})
    batch(44, {"BTCUSDT": 0.36, "ETHUSDT": 0.34, "SOLUSDT": 0.32})

    assert generated, engine.skips
    for _, plan in generated:
        assert plan.details["module"] == QH_MODULE
        assert plan.details["state_sequence"][-1] == "ENTRY_ARMED"
        assert plan.entry_order_type == "LIMIT" and plan.entry_post_only
        assert plan.observed_ts_ns > plan.details["fvg_retrace_ts_ns"]
        assert plan.details["initiative_end_ts_ns"] < plan.observed_ts_ns


if __name__ == "__main__":
    _self_test()
    print("v153 synthetic causal state test: OK")
