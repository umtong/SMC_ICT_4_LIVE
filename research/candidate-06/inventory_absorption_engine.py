"""Accepted inventory imbalance, opposing-flow absorption, and pullback continuation.

This module deliberately separates the market hypothesis from execution. It
only consumes completed one-minute observations, aggregates completed higher-
timeframe auctions, advances an explicit causal state machine, and emits a
ScenarioSignal. NautilusTrader remains responsible for orders, fills, fees,
position accounting and NAV.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping

from causal_clock import source_bar_datetime
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass(frozen=True, slots=True)
class _TrendBar:
    start_ts_ns: int
    end_ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    trades: int

    @property
    def candle_range(self) -> float:
        return max(self.high - self.low, 0.0)

    @property
    def body_fraction(self) -> float:
        return abs(self.close - self.open) / self.candle_range if self.candle_range > 0.0 else 0.0

    @property
    def close_location(self) -> float:
        return (self.close - self.low) / self.candle_range if self.candle_range > 0.0 else 0.5

    @property
    def flow_ratio(self) -> float:
        if self.volume <= 0.0:
            return 0.0
        return (2.0 * self.taker_buy_volume - self.volume) / self.volume


@dataclass(slots=True)
class _TrendRegime:
    scenario_id: str
    direction: str
    state: str
    boundary: float
    regime_open: float
    regime_high: float
    regime_low: float
    regime_close: float
    atr_htf: float
    created_index: int
    expires_index: int
    range_atr: float
    body_fraction: float
    flow_ratio: float
    relative_volume: float
    extreme: float
    pullback_started_index: int | None = None
    pullback_low: float | None = None
    pullback_high: float | None = None
    pullback_volume: float = 0.0
    pullback_buy_volume: float = 0.0
    pullback_bars: int = 0
    previous_bar_high: float | None = None
    previous_bar_low: float | None = None


class InventoryAbsorptionPullbackEngine:
    """Continue an accepted trend only after opposing aggression is absorbed.

    State sequence::

        completed HTF range acceptance
        -> opposing one-minute pullback with signed taker flow
        -> accepted boundary remains defended
        -> separate one-minute structure/flow resumption
        -> structural bracket signal

    The core distinction from a generic breakout retest is that a valid pullback
    must contain *opposing* aggressive flow. Price holding the accepted
    boundary despite that effort is the hypothesised evidence of passive
    inventory absorption. The entry still waits for a separate directional
    response bar, so the touch/start bar can never confirm itself.
    """

    def __init__(self, params: Mapping[str, Any]):
        self.params = dict(params)
        self._period = int(self.params.get("iapc_period_minutes", 15))
        if self._period < 5 or 1440 % self._period != 0:
            raise ValueError("iapc_period_minutes must be at least 5 and divide one UTC day")
        self._history: list[_TrendBar] = []
        self._true_ranges: list[float] = []
        self._volumes: list[float] = []
        self._current: dict[str, Any] | None = None
        self._regime: _TrendRegime | None = None
        self._sequence = 0

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        signal: ScenarioSignal | None = None

        # Existing state sees this completed one-minute bar first. A new HTF
        # regime completed by the same bar is created only afterwards and thus
        # cannot consume its own closing bar as a pullback observation.
        if self._regime is not None:
            advanced = self._advance(snapshot, allow_new=allow_new)
            transitions.extend(advanced.transitions)
            signal = advanced.signal

        completed = self._accumulate(snapshot)
        if completed is not None:
            if self._regime is None and signal is None and allow_new:
                started = self._start_regime(completed, snapshot)
                if started is not None:
                    transitions.append(started)
            self._append_history(completed)

        return ScenarioStep(transitions=tuple(transitions), signal=signal)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        regime = self._regime
        if regime is None:
            return ScenarioStep()
        transition = self._transition(
            regime,
            regime.state,
            "RESET",
            reason,
            snapshot.observation.close,
            {"aborted": True},
        )
        self._regime = None
        return ScenarioStep(transitions=(transition,))

    def _accumulate(self, snapshot: PrimitiveSnapshot) -> _TrendBar | None:
        observation = snapshot.observation
        source = source_bar_datetime(observation.ts_ns)
        source_minute = int(source.timestamp() // 60)
        bucket = source_minute // self._period
        position = source_minute % self._period

        if self._current is None or int(self._current["bucket"]) != bucket:
            self._current = {
                "bucket": bucket,
                "start_ts_ns": observation.ts_ns,
                "open": observation.open,
                "high": observation.high,
                "low": observation.low,
                "close": observation.close,
                "volume": observation.volume,
                "taker_buy_volume": observation.taker_buy_volume,
                "trades": observation.trades,
            }
        else:
            current = self._current
            current["high"] = max(float(current["high"]), observation.high)
            current["low"] = min(float(current["low"]), observation.low)
            current["close"] = observation.close
            current["volume"] = float(current["volume"]) + observation.volume
            current["taker_buy_volume"] = float(current["taker_buy_volume"]) + observation.taker_buy_volume
            current["trades"] = int(current["trades"]) + observation.trades

        if position != self._period - 1:
            return None

        assert self._current is not None
        current = self._current
        completed = _TrendBar(
            start_ts_ns=int(current["start_ts_ns"]),
            end_ts_ns=observation.ts_ns,
            open=float(current["open"]),
            high=float(current["high"]),
            low=float(current["low"]),
            close=float(current["close"]),
            volume=float(current["volume"]),
            taker_buy_volume=float(current["taker_buy_volume"]),
            trades=int(current["trades"]),
        )
        self._current = None
        return completed

    def _append_history(self, bar: _TrendBar) -> None:
        previous_close = self._history[-1].close if self._history else bar.close
        true_range = max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
        self._history.append(bar)
        self._true_ranges.append(true_range)
        self._volumes.append(bar.volume)
        capacity = max(
            48,
            int(self.params.get("iapc_atr_bars", 12)) + 8,
            int(self.params.get("iapc_volume_bars", 12)) + 8,
            int(self.params.get("iapc_breakout_lookback", 4)) + 8,
        )
        if len(self._history) > capacity:
            self._history = self._history[-capacity:]
            self._true_ranges = self._true_ranges[-capacity:]
            self._volumes = self._volumes[-capacity:]

    def _start_regime(
        self,
        bar: _TrendBar,
        snapshot: PrimitiveSnapshot,
    ) -> ScenarioTransition | None:
        atr_bars = int(self.params.get("iapc_atr_bars", 12))
        volume_bars = int(self.params.get("iapc_volume_bars", 12))
        breakout_lookback = int(self.params.get("iapc_breakout_lookback", 4))
        required = max(atr_bars, volume_bars, breakout_lookback)
        if len(self._history) < required:
            return None

        atr_htf = sum(self._true_ranges[-atr_bars:]) / atr_bars
        baseline_volume = median(self._volumes[-volume_bars:])
        if atr_htf <= 0.0 or baseline_volume <= 0.0 or bar.candle_range <= 0.0:
            return None

        prior = self._history[-breakout_lookback:]
        prior_high = max(item.high for item in prior)
        prior_low = min(item.low for item in prior)
        acceptance = float(self.params.get("iapc_acceptance_close_atr", 0.02)) * atr_htf
        range_atr = bar.candle_range / atr_htf
        relative_volume = bar.volume / baseline_volume

        minimum_range = float(self.params.get("iapc_regime_range_atr", 0.70))
        minimum_body = float(self.params.get("iapc_regime_body_fraction", 0.50))
        minimum_volume = float(self.params.get("iapc_regime_relative_volume", 0.95))
        minimum_flow = float(self.params.get("iapc_regime_flow_ratio", 0.04))
        outer_close = float(self.params.get("iapc_regime_close_location", 0.68))

        direction: str | None = None
        boundary = 0.0
        if (
            bar.close > prior_high + acceptance
            and bar.close > bar.open
            and range_atr >= minimum_range
            and bar.body_fraction >= minimum_body
            and relative_volume >= minimum_volume
            and bar.flow_ratio >= minimum_flow
            and bar.close_location >= outer_close
        ):
            direction = "LONG"
            boundary = prior_high
        elif (
            bar.close < prior_low - acceptance
            and bar.close < bar.open
            and range_atr >= minimum_range
            and bar.body_fraction >= minimum_body
            and relative_volume >= minimum_volume
            and bar.flow_ratio <= -minimum_flow
            and bar.close_location <= 1.0 - outer_close
        ):
            direction = "SHORT"
            boundary = prior_low
        if direction is None:
            return None

        self._sequence += 1
        scenario_id = f"IAPC-{bar.end_ts_ns}-{self._sequence:06d}"
        lifetime_periods = float(self.params.get("iapc_regime_lifetime_periods", 4.0))
        self._regime = _TrendRegime(
            scenario_id=scenario_id,
            direction=direction,
            state="ACCEPTED_TREND_PULLBACK_WAIT",
            boundary=boundary,
            regime_open=bar.open,
            regime_high=bar.high,
            regime_low=bar.low,
            regime_close=bar.close,
            atr_htf=atr_htf,
            created_index=snapshot.index,
            expires_index=snapshot.index + max(1, int(self._period * lifetime_periods)),
            range_atr=range_atr,
            body_fraction=bar.body_fraction,
            flow_ratio=bar.flow_ratio,
            relative_volume=relative_volume,
            extreme=bar.high if direction == "LONG" else bar.low,
        )
        return self._transition(
            self._regime,
            "IDLE",
            "ACCEPTED_TREND_PULLBACK_WAIT",
            "HIGHER_TIMEFRAME_INVENTORY_IMBALANCE_ACCEPTED",
            bar.close,
            {
                "period_minutes": self._period,
                "direction": direction,
                "boundary": boundary,
                "prior_high": prior_high,
                "prior_low": prior_low,
                "regime_open": bar.open,
                "regime_high": bar.high,
                "regime_low": bar.low,
                "regime_close": bar.close,
                "atr_htf": atr_htf,
                "range_atr": range_atr,
                "body_fraction": bar.body_fraction,
                "flow_ratio": bar.flow_ratio,
                "relative_volume": relative_volume,
            },
        )

    def _advance(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        regime = self._regime
        assert regime is not None
        observation = snapshot.observation

        if snapshot.index <= regime.created_index:
            return ScenarioStep()
        if snapshot.index > regime.expires_index:
            return self._reset(snapshot, regime, "TREND_REGIME_EXPIRED")

        boundary_tolerance = float(self.params.get("iapc_boundary_loss_atr", 0.08)) * regime.atr_htf
        if regime.direction == "LONG":
            regime.extreme = max(regime.extreme, observation.high)
            if observation.close < regime.boundary - boundary_tolerance:
                return self._reset(snapshot, regime, "BULLISH_ACCEPTED_BOUNDARY_LOST")
        else:
            regime.extreme = min(regime.extreme, observation.low)
            if observation.close > regime.boundary + boundary_tolerance:
                return self._reset(snapshot, regime, "BEARISH_ACCEPTED_BOUNDARY_LOST")

        if regime.state == "ACCEPTED_TREND_PULLBACK_WAIT":
            minimum_retrace = float(self.params.get("iapc_pullback_min_atr", 0.08)) * regime.atr_htf
            start_flow = float(self.params.get("iapc_pullback_start_flow", 0.02))
            if regime.direction == "LONG":
                retraced = regime.extreme - observation.low >= minimum_retrace
                opposing = observation.close < observation.open and snapshot.flow_ratio <= -start_flow
            else:
                retraced = observation.high - regime.extreme >= minimum_retrace
                opposing = observation.close > observation.open and snapshot.flow_ratio >= start_flow
            if not (retraced and opposing):
                return ScenarioStep()

            regime.state = "OPPOSING_FLOW_PULLBACK_BUILD"
            regime.pullback_started_index = snapshot.index
            regime.pullback_low = observation.low
            regime.pullback_high = observation.high
            regime.pullback_volume = observation.volume
            regime.pullback_buy_volume = observation.taker_buy_volume
            regime.pullback_bars = 1
            regime.previous_bar_high = observation.high
            regime.previous_bar_low = observation.low
            return ScenarioStep(
                transitions=(
                    self._transition(
                        regime,
                        "ACCEPTED_TREND_PULLBACK_WAIT",
                        "OPPOSING_FLOW_PULLBACK_BUILD",
                        "OPPOSING_ORDER_FLOW_PULLBACK_STARTED",
                        observation.close,
                        {
                            "pullback_low": observation.low,
                            "pullback_high": observation.high,
                            "flow_ratio": snapshot.flow_ratio,
                        },
                    ),
                ),
            )

        assert regime.pullback_started_index is not None
        assert regime.pullback_low is not None and regime.pullback_high is not None
        assert regime.previous_bar_high is not None and regime.previous_bar_low is not None

        if regime.pullback_bars >= int(self.params.get("iapc_pullback_max_bars", 8)):
            return self._reset(snapshot, regime, "PULLBACK_RESPONSE_WINDOW_EXPIRED")

        if regime.direction == "LONG":
            retrace_atr = (regime.extreme - min(regime.pullback_low, observation.low)) / regime.atr_htf
            response = (
                observation.close > observation.open
                and observation.close > regime.previous_bar_high
                and snapshot.body_atr >= float(self.params.get("iapc_response_body_atr_1m", 0.12))
                and snapshot.flow_ratio >= float(self.params.get("iapc_response_flow_ratio", 0.03))
                and snapshot.close_location >= float(self.params.get("iapc_response_close_location", 0.58))
            )
        else:
            retrace_atr = (max(regime.pullback_high, observation.high) - regime.extreme) / regime.atr_htf
            response = (
                observation.close < observation.open
                and observation.close < regime.previous_bar_low
                and snapshot.body_atr >= float(self.params.get("iapc_response_body_atr_1m", 0.12))
                and snapshot.flow_ratio <= -float(self.params.get("iapc_response_flow_ratio", 0.03))
                and snapshot.close_location <= 1.0 - float(self.params.get("iapc_response_close_location", 0.58))
            )

        # Measure opposing effort only over bars already classified as the
        # pullback. The response bar is intentionally excluded so its aligned
        # flow cannot dilute the evidence of absorption.
        pullback_flow = (
            (2.0 * regime.pullback_buy_volume - regime.pullback_volume) / regime.pullback_volume
            if regime.pullback_volume > 0.0
            else 0.0
        )
        mode = str(self.params.get("iapc_pullback_mode", "FLOW_ABSORPTION")).upper()
        minimum_opposing = float(self.params.get("iapc_absorption_opposing_flow", 0.03))
        if mode == "FLOW_ABSORPTION":
            flow_ok = pullback_flow <= -minimum_opposing if regime.direction == "LONG" else pullback_flow >= minimum_opposing
        elif mode == "STRUCTURAL_PULLBACK":
            flow_ok = True
        else:
            raise ValueError(f"unsupported iapc_pullback_mode: {mode}")

        min_retrace = float(self.params.get("iapc_pullback_min_atr", 0.08))
        max_retrace = float(self.params.get("iapc_pullback_max_atr", 0.55))
        retrace_ok = min_retrace <= retrace_atr <= max_retrace
        minimum_bars = int(self.params.get("iapc_pullback_min_bars", 2))

        response_mode = str(self.params.get("iapc_response_mode", "BREAK_LAST_BAR")).upper()
        if response_mode == "BREAK_PULLBACK_STRUCTURE":
            if response:
                if regime.direction == "LONG":
                    response = observation.close > regime.pullback_high
                else:
                    response = observation.close < regime.pullback_low
        elif response_mode != "BREAK_LAST_BAR":
            raise ValueError(f"unsupported iapc_response_mode: {response_mode}")

        if response and flow_ok and retrace_ok and regime.pullback_bars >= minimum_bars:
            if not allow_new:
                return self._reset(snapshot, regime, "ENTRY_SLOT_UNAVAILABLE_AT_PULLBACK_RESPONSE")
            return self._emit(snapshot, regime, pullback_flow, retrace_atr)

        if retrace_atr > max_retrace:
            return self._reset(snapshot, regime, "PULLBACK_DEPTH_EXCEEDED_ABSORPTION_LIMIT")

        regime.pullback_low = min(regime.pullback_low, observation.low)
        regime.pullback_high = max(regime.pullback_high, observation.high)
        regime.pullback_volume += observation.volume
        regime.pullback_buy_volume += observation.taker_buy_volume
        regime.pullback_bars += 1
        regime.previous_bar_high = observation.high
        regime.previous_bar_low = observation.low
        return ScenarioStep()

    def _emit(
        self,
        snapshot: PrimitiveSnapshot,
        regime: _TrendRegime,
        pullback_flow: float,
        retrace_atr: float,
    ) -> ScenarioStep:
        observation = snapshot.observation
        buffer_value = float(self.params.get("iapc_stop_buffer_atr", 0.04)) * regime.atr_htf
        extension = float(self.params.get("iapc_extension_atr", 0.75)) * regime.atr_htf

        if regime.direction == "LONG":
            stop = float(regime.pullback_low) - buffer_value
            candidates = [
                (regime.extreme, "ACCEPTED_TREND_EXTREME"),
                (snapshot.upper_fast, "PRIOR_FAST_BUYSIDE_LIQUIDITY"),
                (snapshot.upper_slow, "PRIOR_SLOW_BUYSIDE_LIQUIDITY"),
                (regime.extreme + extension, "ACCEPTED_TREND_EXTENSION"),
            ]
        else:
            stop = float(regime.pullback_high) + buffer_value
            candidates = [
                (regime.extreme, "ACCEPTED_TREND_EXTREME"),
                (snapshot.lower_fast, "PRIOR_FAST_SELLSIDE_LIQUIDITY"),
                (snapshot.lower_slow, "PRIOR_SLOW_SELLSIDE_LIQUIDITY"),
                (regime.extreme - extension, "ACCEPTED_TREND_EXTENSION"),
            ]

        target = self._select_target(regime.direction, observation.close, stop, candidates)
        if target is None:
            return self._reset(snapshot, regime, "NO_PULLBACK_CONTINUATION_OBJECTIVE_WITH_SUFFICIENT_SPACE")
        target_price, target_reason = target

        transition = self._transition(
            regime,
            "OPPOSING_FLOW_PULLBACK_BUILD",
            "ENTRY_ARMED",
            "PULLBACK_ABSORPTION_AND_TREND_RESPONSE_CONFIRMED",
            observation.close,
            {
                "direction": regime.direction,
                "pullback_mode": self.params.get("iapc_pullback_mode", "FLOW_ABSORPTION"),
                "response_mode": self.params.get("iapc_response_mode", "BREAK_LAST_BAR"),
                "pullback_bars": regime.pullback_bars,
                "pullback_flow_ratio": pullback_flow,
                "retrace_atr_htf": retrace_atr,
                "stop_price": stop,
                "target_price": target_price,
                "target_reason": target_reason,
            },
        )
        signal = ScenarioSignal(
            scenario_id=regime.scenario_id,
            family="IAPC",
            direction=regime.direction,
            observed_ts_ns=observation.ts_ns,
            reference_entry=observation.close,
            stop_price=stop,
            target_price=target_price,
            target_reason=target_reason,
            atr=snapshot.atr,
            liquidity_level=regime.boundary,
            details={
                "period_minutes": self._period,
                "boundary": regime.boundary,
                "atr_htf": regime.atr_htf,
                "regime_open": regime.regime_open,
                "regime_high": regime.regime_high,
                "regime_low": regime.regime_low,
                "regime_close": regime.regime_close,
                "regime_range_atr": regime.range_atr,
                "regime_body_fraction": regime.body_fraction,
                "regime_flow_ratio": regime.flow_ratio,
                "regime_relative_volume": regime.relative_volume,
                "pullback_bars": regime.pullback_bars,
                "pullback_flow_ratio": pullback_flow,
                "retrace_atr_htf": retrace_atr,
                "pullback_low": regime.pullback_low,
                "pullback_high": regime.pullback_high,
            },
        )
        self._regime = None
        return ScenarioStep(transitions=(transition,), signal=signal)

    def _select_target(
        self,
        direction: str,
        entry: float,
        stop: float,
        candidates: list[tuple[float | None, str]],
    ) -> tuple[float, str] | None:
        risk = abs(entry - stop)
        if risk <= 0.0:
            return None
        minimum_rr = float(self.params.get("minimum_structural_rr", 1.05))
        valid: list[tuple[float, str]] = []
        for price, reason in candidates:
            if price is None:
                continue
            reward = float(price) - entry if direction == "LONG" else entry - float(price)
            if reward > 0.0 and reward / risk >= minimum_rr:
                valid.append((float(price), reason))
        valid.sort(key=lambda item: abs(item[0] - entry))
        return valid[0] if valid else None

    def _reset(self, snapshot: PrimitiveSnapshot, regime: _TrendRegime, reason: str) -> ScenarioStep:
        transition = self._transition(
            regime,
            regime.state,
            "RESET",
            reason,
            snapshot.observation.close,
            {},
        )
        self._regime = None
        return ScenarioStep(transitions=(transition,))

    @staticmethod
    def _transition(
        regime: _TrendRegime,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=regime.scenario_id,
            event_type="IAPC_STATE_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )
