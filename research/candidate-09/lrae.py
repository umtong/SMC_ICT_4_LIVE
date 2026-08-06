"""Causal Liquidity Reaction Auction Engine (LRAE).

The module contains no execution or accounting logic.  It observes completed bars,
maps neutral liquidity breaches into explicit state transitions, and emits a trade
plan only after either:

* aggressive-flow absorption followed by range reclaim (reversal), or
* directional depletion followed by out-of-range acceptance and a held retest
  (continuation).

All rolling levels exclude the current bar.  Confirmed pivots carry separate event
and observation times so a visual pivot is never treated as known before its right
hand confirmation bars have closed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from statistics import median
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class BarSnapshot:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    trades: int

    def __post_init__(self) -> None:
        if self.ts_ns < 0:
            raise ValueError("ts_ns must be non-negative")
        values = (self.open, self.high, self.low, self.close, self.volume, self.taker_buy_volume)
        if not all(isfinite(value) for value in values):
            raise ValueError("bar values must be finite")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("bar OHLC is inconsistent")
        if self.low > self.high:
            raise ValueError("bar low cannot exceed high")
        if self.volume < 0 or self.taker_buy_volume < 0:
            raise ValueError("volumes must be non-negative")
        if self.taker_buy_volume > self.volume + 1e-9:
            raise ValueError("taker_buy_volume cannot exceed volume")
        if self.trades < 0:
            raise ValueError("trades must be non-negative")

    @property
    def signed_flow(self) -> float:
        """Aggressor imbalance in [-1, 1], based on Binance taker-buy volume."""
        if self.volume <= 0.0:
            return 0.0
        return max(-1.0, min(1.0, (2.0 * self.taker_buy_volume - self.volume) / self.volume))

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def close_location(self) -> float:
        if self.range <= 0.0:
            return 0.5
        return (self.close - self.low) / self.range

    @property
    def upper_wick_fraction(self) -> float:
        if self.range <= 0.0:
            return 0.0
        return (self.high - max(self.open, self.close)) / self.range

    @property
    def lower_wick_fraction(self) -> float:
        if self.range <= 0.0:
            return 0.0
        return (min(self.open, self.close) - self.low) / self.range


@dataclass(frozen=True, slots=True)
class Transition:
    scenario_id: str
    event_time_ns: int
    observed_time_ns: int
    previous_state: str
    next_state: str
    reason_code: str
    reference_price: float
    details: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class TradePlan:
    scenario_id: str
    scenario_type: str
    direction: str
    source_state: str
    signal_ts_ns: int
    entry: float
    stop: float
    target: float
    planned_loss_per_unit: float
    expected_net_reward_per_unit: float
    expected_net_rr: float
    features: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _PendingBreach:
    scenario_id: str
    side: str
    boundary: float
    opposite_boundary: float
    external_target: float | None
    event_time_ns: int
    observed_time_ns: int
    started_index: int
    state: str
    extreme: float
    atr: float
    initial_flow: float
    initial_volume_ratio: float
    initial_range_atr: float
    bars_waited: int = 0


def planned_loss_per_unit(entry: float, stop: float, fee_rate_per_side: float) -> float:
    """Worst planned stop loss including entry and stop-side effective costs."""
    return abs(entry - stop) + fee_rate_per_side * (abs(entry) + abs(stop))


def expected_net_reward_per_unit(entry: float, target: float, fee_rate_per_side: float) -> float:
    """Expected target reward after effective entry and exit costs."""
    return abs(target - entry) - fee_rate_per_side * (abs(entry) + abs(target))


def risk_quantity(
    *,
    nav: float,
    risk_fraction: float,
    entry: float,
    stop: float,
    fee_rate_per_side: float,
    size_increment: float,
) -> float:
    """Floor quantity so the planned stop loss never exceeds NAV risk budget."""
    if nav <= 0.0:
        raise ValueError("nav must be positive")
    if not 0.0 < risk_fraction <= 0.03:
        raise ValueError("risk_fraction must be in (0, 0.03]")
    if size_increment <= 0.0:
        raise ValueError("size_increment must be positive")
    per_unit = planned_loss_per_unit(entry, stop, fee_rate_per_side)
    if per_unit <= 0.0:
        return 0.0
    raw = nav * risk_fraction / per_unit
    units = int((raw + 1e-12) / size_increment)
    return max(0.0, units * size_increment)


class LiquidityReactionEngine:
    """Online, single-scenario liquidity state machine."""

    def __init__(self, config: Mapping[str, Any], *, variant: str = "base") -> None:
        self.config = dict(config)
        if variant not in {"base", "no_flow"}:
            raise ValueError(f"unsupported variant: {variant}")
        self.variant = variant
        self.history: list[BarSnapshot] = []
        self.pending: _PendingBreach | None = None
        self.pivot_highs: list[tuple[int, int, float]] = []
        self.pivot_lows: list[tuple[int, int, float]] = []
        self.scenario_sequence = 0
        self.bar_index = -1
        self.cooldown_until = -1
        self.last_observed_ts = -1

    @property
    def min_history(self) -> int:
        return int(self.config["min_history"])

    def observe(self, bar: BarSnapshot) -> tuple[list[Transition], TradePlan | None]:
        if bar.ts_ns < self.last_observed_ts:
            raise ValueError("bars must be observed in non-decreasing timestamp order")
        self.last_observed_ts = bar.ts_ns
        self.bar_index += 1

        transitions: list[Transition] = []
        self._confirm_pivot_with(bar)

        if len(self.history) < self.min_history:
            self.history.append(bar)
            self._trim()
            return transitions, None

        context = self._context(bar)
        plan: TradePlan | None = None

        if self.pending is not None:
            pending_transitions, plan = self._advance_pending(bar, context)
            transitions.extend(pending_transitions)

        if self.pending is None and plan is None and self.bar_index >= self.cooldown_until:
            created, immediate_plan = self._detect_new_breach(bar, context)
            transitions.extend(created)
            plan = immediate_plan

        if plan is not None:
            self.cooldown_until = self.bar_index + int(self.config["cooldown_bars"])

        self.history.append(bar)
        self._trim()
        return transitions, plan

    def _trim(self) -> None:
        keep = max(
            int(self.config["level_memory_bars"]),
            int(self.config["external_window"]),
            int(self.config["min_history"]),
        ) + 20
        if len(self.history) > keep:
            removed = len(self.history) - keep
            self.history = self.history[-keep:]
            # bar_index is absolute, pivot timestamps are used instead of list offsets.
            cutoff_ts = self.history[0].ts_ns
            self.pivot_highs = [item for item in self.pivot_highs if item[1] >= cutoff_ts]
            self.pivot_lows = [item for item in self.pivot_lows if item[1] >= cutoff_ts]

    def _confirm_pivot_with(self, current: BarSnapshot) -> None:
        right = int(self.config["pivot_right"])
        left = int(self.config["pivot_left"])
        combined = self.history + [current]
        needed = left + right + 1
        if len(combined) < needed:
            return
        pivot_pos = len(combined) - 1 - right
        pivot = combined[pivot_pos]
        left_slice = combined[pivot_pos - left : pivot_pos]
        right_slice = combined[pivot_pos + 1 : pivot_pos + 1 + right]
        if all(pivot.high > item.high for item in left_slice) and all(
            pivot.high >= item.high for item in right_slice
        ):
            self.pivot_highs.append((self.bar_index, pivot.ts_ns, pivot.high))
        if all(pivot.low < item.low for item in left_slice) and all(
            pivot.low <= item.low for item in right_slice
        ):
            self.pivot_lows.append((self.bar_index, pivot.ts_ns, pivot.low))

    def _atr(self) -> float:
        window = int(self.config["atr_window"])
        bars = self.history[-(window + 1) :]
        if len(bars) < 2:
            return 0.0
        true_ranges: list[float] = []
        for previous, current in zip(bars, bars[1:], strict=False):
            true_ranges.append(
                max(
                    current.high - current.low,
                    abs(current.high - previous.close),
                    abs(current.low - previous.close),
                )
            )
        return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

    def _flow_window(self, current: BarSnapshot) -> float:
        window = max(1, int(self.config["flow_window"]))
        bars = (self.history[-(window - 1) :] if window > 1 else []) + [current]
        total = sum(item.volume for item in bars)
        if total <= 0.0:
            return 0.0
        signed = sum(item.signed_flow * item.volume for item in bars)
        return signed / total

    def _volume_ratio(self, current: BarSnapshot) -> float:
        window = int(self.config["volume_window"])
        baseline = [item.volume for item in self.history[-window:] if item.volume > 0.0]
        if not baseline:
            return 1.0
        base = median(baseline)
        return current.volume / base if base > 0.0 else 1.0

    def _context(self, current: BarSnapshot) -> dict[str, Any]:
        internal_window = int(self.config["internal_window"])
        external_window = int(self.config["external_window"])
        recent = self.history[-internal_window:]
        external = self.history[-external_window:]
        atr = self._atr()
        if atr <= 0.0:
            atr = max(current.range, abs(current.close) * 1e-6)

        internal_high = max(item.high for item in recent)
        internal_low = min(item.low for item in recent)
        external_high = max(item.high for item in external)
        external_low = min(item.low for item in external)

        flow = self._flow_window(current)
        volume_ratio = self._volume_ratio(current)
        range_atr = current.range / atr if atr > 0.0 else 0.0

        target_above_candidates = [
            price
            for _, _, price in self.pivot_highs
            if price > current.close + float(self.config["target_separation_atr"]) * atr
        ]
        target_below_candidates = [
            price
            for _, _, price in self.pivot_lows
            if price < current.close - float(self.config["target_separation_atr"]) * atr
        ]
        if external_high > current.close + float(self.config["target_separation_atr"]) * atr:
            target_above_candidates.append(external_high)
        if external_low < current.close - float(self.config["target_separation_atr"]) * atr:
            target_below_candidates.append(external_low)

        return {
            "atr": atr,
            "internal_high": internal_high,
            "internal_low": internal_low,
            "external_high": external_high,
            "external_low": external_low,
            "target_above": min(target_above_candidates) if target_above_candidates else None,
            "target_below": max(target_below_candidates) if target_below_candidates else None,
            "flow": flow,
            "volume_ratio": volume_ratio,
            "range_atr": range_atr,
            "close_location": current.close_location,
            "upper_wick_fraction": current.upper_wick_fraction,
            "lower_wick_fraction": current.lower_wick_fraction,
        }

    def _new_scenario_id(self, side: str, ts_ns: int) -> str:
        self.scenario_sequence += 1
        return f"lrae-{side}-{ts_ns}-{self.scenario_sequence:06d}"

    def _transition(
        self,
        pending: _PendingBreach,
        bar: BarSnapshot,
        previous: str,
        next_state: str,
        reason: str,
        details: Mapping[str, Any],
    ) -> Transition:
        return Transition(
            scenario_id=pending.scenario_id,
            event_time_ns=pending.event_time_ns,
            observed_time_ns=bar.ts_ns,
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
            reference_price=pending.boundary,
            details=dict(details),
        )

    def _flow_ok(self, value: float, *, positive: bool) -> bool:
        if self.variant == "no_flow":
            return True
        threshold = float(self.config["flow_threshold"])
        return value >= threshold if positive else value <= -threshold

    def _detect_new_breach(
        self,
        bar: BarSnapshot,
        context: Mapping[str, Any],
    ) -> tuple[list[Transition], TradePlan | None]:
        atr = float(context["atr"])
        breach_buffer = float(self.config["breach_atr"]) * atr
        high_breach = bar.high >= float(context["internal_high"]) + breach_buffer
        low_breach = bar.low <= float(context["internal_low"]) - breach_buffer

        # A bar that takes both sides has unresolved intrabar ordering at 1-minute
        # granularity.  It is observed but never traded.
        if high_breach and low_breach:
            return [], None
        if not high_breach and not low_breach:
            return [], None

        side = "upper" if high_breach else "lower"
        boundary = float(context["internal_high"] if high_breach else context["internal_low"])
        opposite = float(context["internal_low"] if high_breach else context["internal_high"])
        external_target = context["target_above"] if high_breach else context["target_below"]
        scenario_id = self._new_scenario_id(side, bar.ts_ns)
        pending = _PendingBreach(
            scenario_id=scenario_id,
            side=side,
            boundary=boundary,
            opposite_boundary=opposite,
            external_target=float(external_target) if external_target is not None else None,
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            started_index=self.bar_index,
            state="BREACHED",
            extreme=bar.high if high_breach else bar.low,
            atr=atr,
            initial_flow=float(context["flow"]),
            initial_volume_ratio=float(context["volume_ratio"]),
            initial_range_atr=float(context["range_atr"]),
        )
        self.pending = pending
        transitions = [
            self._transition(
                pending,
                bar,
                "IDLE",
                "BREACHED",
                "UPPER_LIQUIDITY_BREACH" if high_breach else "LOWER_LIQUIDITY_BREACH",
                self._feature_details(bar, context),
            )
        ]

        branch_transitions, plan = self._classify_or_wait(bar, context)
        transitions.extend(branch_transitions)
        return transitions, plan

    def _advance_pending(
        self,
        bar: BarSnapshot,
        context: Mapping[str, Any],
    ) -> tuple[list[Transition], TradePlan | None]:
        pending = self.pending
        assert pending is not None
        pending.bars_waited += 1
        pending.extreme = max(pending.extreme, bar.high) if pending.side == "upper" else min(
            pending.extreme, bar.low
        )

        if pending.state == "ACCEPTED":
            return self._confirm_accepted_retest(bar, context)

        transitions, plan = self._classify_or_wait(bar, context)
        if plan is not None or self.pending is None:
            return transitions, plan

        max_wait = int(self.config["classification_bars"])
        if pending.bars_waited > max_wait:
            transitions.append(
                self._transition(
                    pending,
                    bar,
                    pending.state,
                    "EXPIRED",
                    "BREACH_NOT_RESOLVED",
                    {"bars_waited": pending.bars_waited},
                )
            )
            self.pending = None
        return transitions, None

    def _classify_or_wait(
        self,
        bar: BarSnapshot,
        context: Mapping[str, Any],
    ) -> tuple[list[Transition], TradePlan | None]:
        pending = self.pending
        assert pending is not None
        atr = pending.atr
        reclaim = float(self.config["reclaim_atr"]) * atr
        acceptance = float(self.config["acceptance_atr"]) * atr
        min_volume = float(self.config["min_volume_ratio"])
        min_wick = float(self.config["min_wick_fraction"])
        min_displacement = float(self.config["displacement_atr"])
        flow = float(context["flow"])
        vol = max(pending.initial_volume_ratio, float(context["volume_ratio"]))
        range_atr = max(pending.initial_range_atr, float(context["range_atr"]))

        if pending.side == "upper":
            reclaimed = (
                bar.close <= pending.boundary - reclaim
                and max(bar.upper_wick_fraction, self._initial_rejection_fraction(pending, bar)) >= min_wick
                and vol >= min_volume
                and self._flow_ok(pending.initial_flow, positive=True)
                and flow <= float(self.config["reclaim_opposite_flow_ceiling"])
            )
            accepted = (
                bar.close >= pending.boundary + acceptance
                and bar.close_location >= float(self.config["continuation_close_location"])
                and vol >= min_volume
                and range_atr >= min_displacement
                and self._flow_ok(max(pending.initial_flow, flow), positive=True)
            )
            if reclaimed:
                transition = self._transition(
                    pending,
                    bar,
                    pending.state,
                    "RECLAIMED",
                    "BUY_AGGRESSION_ABSORBED_AND_RANGE_RECLAIMED",
                    self._feature_details(bar, context),
                )
                plan = self._build_reversal_plan(pending, bar, context, direction="short")
                self.pending = None
                return [transition], plan
            if accepted:
                transition = self._transition(
                    pending,
                    bar,
                    pending.state,
                    "ACCEPTED",
                    "OFFER_DEPLETION_AND_OUT_OF_RANGE_ACCEPTANCE",
                    self._feature_details(bar, context),
                )
                pending.state = "ACCEPTED"
                pending.observed_time_ns = bar.ts_ns
                return [transition], None
        else:
            reclaimed = (
                bar.close >= pending.boundary + reclaim
                and max(bar.lower_wick_fraction, self._initial_rejection_fraction(pending, bar)) >= min_wick
                and vol >= min_volume
                and self._flow_ok(pending.initial_flow, positive=False)
                and flow >= -float(self.config["reclaim_opposite_flow_ceiling"])
            )
            accepted = (
                bar.close <= pending.boundary - acceptance
                and bar.close_location <= 1.0 - float(self.config["continuation_close_location"])
                and vol >= min_volume
                and range_atr >= min_displacement
                and self._flow_ok(min(pending.initial_flow, flow), positive=False)
            )
            if reclaimed:
                transition = self._transition(
                    pending,
                    bar,
                    pending.state,
                    "RECLAIMED",
                    "SELL_AGGRESSION_ABSORBED_AND_RANGE_RECLAIMED",
                    self._feature_details(bar, context),
                )
                plan = self._build_reversal_plan(pending, bar, context, direction="long")
                self.pending = None
                return [transition], plan
            if accepted:
                transition = self._transition(
                    pending,
                    bar,
                    pending.state,
                    "ACCEPTED",
                    "BID_DEPLETION_AND_OUT_OF_RANGE_ACCEPTANCE",
                    self._feature_details(bar, context),
                )
                pending.state = "ACCEPTED"
                pending.observed_time_ns = bar.ts_ns
                return [transition], None

        return [], None

    @staticmethod
    def _initial_rejection_fraction(pending: _PendingBreach, bar: BarSnapshot) -> float:
        total = max(abs(pending.extreme - pending.boundary), bar.range, 1e-12)
        if pending.side == "upper":
            return max(0.0, pending.extreme - max(bar.open, bar.close)) / total
        return max(0.0, min(bar.open, bar.close) - pending.extreme) / total

    def _confirm_accepted_retest(
        self,
        bar: BarSnapshot,
        context: Mapping[str, Any],
    ) -> tuple[list[Transition], TradePlan | None]:
        pending = self.pending
        assert pending is not None
        atr = pending.atr
        tolerance = float(self.config["retest_tolerance_atr"]) * atr
        flow = float(context["flow"])

        if pending.side == "upper":
            failed = bar.close < pending.boundary - tolerance
            held = (
                bar.low <= pending.boundary + float(self.config["retest_touch_atr"]) * atr
                and bar.close >= pending.boundary + float(self.config["retest_hold_atr"]) * atr
                and flow >= -float(self.config["retest_counterflow_tolerance"])
            )
            direction = "long"
        else:
            failed = bar.close > pending.boundary + tolerance
            held = (
                bar.high >= pending.boundary - float(self.config["retest_touch_atr"]) * atr
                and bar.close <= pending.boundary - float(self.config["retest_hold_atr"]) * atr
                and flow <= float(self.config["retest_counterflow_tolerance"])
            )
            direction = "short"

        if failed:
            transition = self._transition(
                pending,
                bar,
                "ACCEPTED",
                "FAILED",
                "ACCEPTANCE_FAILED_BACK_INSIDE_RANGE",
                self._feature_details(bar, context),
            )
            self.pending = None
            return [transition], None

        if held:
            transition = self._transition(
                pending,
                bar,
                "ACCEPTED",
                "RETEST_HELD",
                "FIRST_RETEST_HELD_OUTSIDE_BREACHED_RANGE",
                self._feature_details(bar, context),
            )
            plan = self._build_continuation_plan(pending, bar, context, direction=direction)
            self.pending = None
            return [transition], plan

        if pending.bars_waited > int(self.config["retest_bars"]):
            transition = self._transition(
                pending,
                bar,
                "ACCEPTED",
                "EXPIRED",
                "ACCEPTANCE_WITHOUT_EXECUTABLE_RETEST",
                {"bars_waited": pending.bars_waited},
            )
            self.pending = None
            return [transition], None

        return [], None

    def _build_reversal_plan(
        self,
        pending: _PendingBreach,
        bar: BarSnapshot,
        context: Mapping[str, Any],
        *,
        direction: str,
    ) -> TradePlan | None:
        atr = pending.atr
        buffer = float(self.config["stop_buffer_atr"]) * atr
        entry = bar.close
        if direction == "short":
            stop = pending.extreme + buffer
            target = pending.opposite_boundary
        else:
            stop = pending.extreme - buffer
            target = pending.opposite_boundary
        return self._validated_plan(
            pending=pending,
            bar=bar,
            context=context,
            scenario_type="absorption_reclaim_reversal",
            direction=direction,
            source_state="RECLAIMED",
            entry=entry,
            stop=stop,
            target=target,
        )

    def _build_continuation_plan(
        self,
        pending: _PendingBreach,
        bar: BarSnapshot,
        context: Mapping[str, Any],
        *,
        direction: str,
    ) -> TradePlan | None:
        if pending.external_target is None:
            return None
        atr = pending.atr
        buffer = float(self.config["stop_buffer_atr"]) * atr
        entry = bar.close
        if direction == "long":
            stop = min(pending.boundary - buffer, bar.low - 0.25 * buffer)
            target = pending.external_target
        else:
            stop = max(pending.boundary + buffer, bar.high + 0.25 * buffer)
            target = pending.external_target
        return self._validated_plan(
            pending=pending,
            bar=bar,
            context=context,
            scenario_type="depletion_acceptance_continuation",
            direction=direction,
            source_state="RETEST_HELD",
            entry=entry,
            stop=stop,
            target=target,
        )

    def _validated_plan(
        self,
        *,
        pending: _PendingBreach,
        bar: BarSnapshot,
        context: Mapping[str, Any],
        scenario_type: str,
        direction: str,
        source_state: str,
        entry: float,
        stop: float,
        target: float,
    ) -> TradePlan | None:
        if direction == "long" and not (stop < entry < target):
            return None
        if direction == "short" and not (target < entry < stop):
            return None

        atr = pending.atr
        stop_atr = abs(entry - stop) / atr if atr > 0.0 else float("inf")
        if stop_atr < float(self.config["min_stop_atr"]):
            if direction == "long":
                stop = entry - float(self.config["min_stop_atr"]) * atr
            else:
                stop = entry + float(self.config["min_stop_atr"]) * atr
            stop_atr = float(self.config["min_stop_atr"])
        if stop_atr > float(self.config["max_stop_atr"]):
            return None

        fee = float(self.config["effective_fee_rate_per_side"])
        loss = planned_loss_per_unit(entry, stop, fee)
        reward = expected_net_reward_per_unit(entry, target, fee)
        rr = reward / loss if loss > 0.0 else -1.0
        if reward <= 0.0 or rr < float(self.config["min_expected_net_rr"]):
            return None

        features = self._feature_details(bar, context)
        features = {
            **features,
            "boundary": pending.boundary,
            "opposite_boundary": pending.opposite_boundary,
            "external_target": pending.external_target,
            "stop_atr": stop_atr,
            "variant": self.variant,
        }
        return TradePlan(
            scenario_id=pending.scenario_id,
            scenario_type=scenario_type,
            direction=direction,
            source_state=source_state,
            signal_ts_ns=bar.ts_ns,
            entry=entry,
            stop=stop,
            target=target,
            planned_loss_per_unit=loss,
            expected_net_reward_per_unit=reward,
            expected_net_rr=rr,
            features=features,
        )

    @staticmethod
    def _feature_details(bar: BarSnapshot, context: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "bar_open": bar.open,
            "bar_high": bar.high,
            "bar_low": bar.low,
            "bar_close": bar.close,
            "atr": float(context["atr"]),
            "flow": float(context["flow"]),
            "volume_ratio": float(context["volume_ratio"]),
            "range_atr": float(context["range_atr"]),
            "close_location": float(context["close_location"]),
            "upper_wick_fraction": float(context["upper_wick_fraction"]),
            "lower_wick_fraction": float(context["lower_wick_fraction"]),
            "internal_high": float(context["internal_high"]),
            "internal_low": float(context["internal_low"]),
        }


def bars_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[BarSnapshot]:
    result: list[BarSnapshot] = []
    for row in rows:
        result.append(
            BarSnapshot(
                ts_ns=int(row["close_time_ns"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                taker_buy_volume=float(row["taker_buy_volume"]),
                trades=int(row["trades"]),
            )
        )
    return result
