"""Higher-timeframe accepted structure with lower-timeframe liquidity sweep continuation."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping

from causal_clock import source_bar_datetime
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass(frozen=True, slots=True)
class _AuctionBar:
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
class _Bias:
    context_id: str
    direction: str
    boundary: float
    origin: float
    high: float
    low: float
    close: float
    extreme: float
    atr_htf: float
    created_index: int
    expires_index: int
    range_atr: float
    body_fraction: float
    flow_ratio: float
    relative_volume: float


@dataclass(slots=True)
class _SweepEpisode:
    scenario_id: str
    direction: str
    state: str
    level: float
    level_ts_ns: int
    started_index: int
    started_ts_ns: int
    sweep_low: float
    sweep_high: float
    previous_high: float
    previous_low: float
    impulse_position: float
    sweep_flow_ratio: float


class HierarchicalLiquiditySweepContinuationEngine:
    """Use HTF acceptance as context and LTF counter-direction sweeps as entries.

    A completed higher-timeframe auction must accept price beyond a prior
    multi-auction range with aligned displacement, volume and taker flow. While
    that accepted boundary remains valid, a lower-timeframe liquidity pool may
    be swept by aggressive flow *against* the bias. A separate one-minute bar
    must then break the sweep structure with aligned body and flow before a
    signal is emitted. Each completed lower-timeframe pool is consumed at most
    once, allowing several distinct opportunities inside one persistent bias
    without treating repeated touches as independent trades.
    """

    def __init__(self, params: Mapping[str, Any]):
        self.params = dict(params)
        self._bias_period = int(self.params.get("hsc_bias_period_minutes", 30))
        self._liquidity_period = int(self.params.get("hsc_liquidity_period_minutes", 5))
        for name, value in (("bias", self._bias_period), ("liquidity", self._liquidity_period)):
            if value < 5 or 1440 % value != 0:
                raise ValueError(f"hsc {name} period must be at least 5 and divide one UTC day")
        if self._liquidity_period >= self._bias_period:
            raise ValueError("hsc liquidity period must be smaller than bias period")

        self._bias_history: list[_AuctionBar] = []
        self._bias_true_ranges: list[float] = []
        self._bias_volumes: list[float] = []
        self._liquidity_history: list[_AuctionBar] = []
        self._bias_current: dict[str, Any] | None = None
        self._liquidity_current: dict[str, Any] | None = None
        self._bias: _Bias | None = None
        self._sweep: _SweepEpisode | None = None
        self._bias_sequence = 0
        self._sweep_sequence = 0
        self._cooldown_until = -1
        self._consumed_levels: set[tuple[int, str]] = set()

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        signal: ScenarioSignal | None = None

        if self._bias is not None:
            context_step = self._advance_bias(snapshot)
            transitions.extend(context_step.transitions)

        if self._sweep is not None:
            sweep_step = self._advance_sweep(snapshot, allow_new=allow_new)
            transitions.extend(sweep_step.transitions)
            signal = sweep_step.signal

        if (
            signal is None
            and self._bias is not None
            and self._sweep is None
            and allow_new
            and snapshot.index >= self._cooldown_until
        ):
            started = self._maybe_start_sweep(snapshot)
            if started is not None:
                transitions.append(started)

        completed_bias = self._accumulate(snapshot, period=self._bias_period, kind="bias")
        completed_liquidity = self._accumulate(snapshot, period=self._liquidity_period, kind="liquidity")

        if completed_bias is not None:
            transitions.extend(self._evaluate_completed_bias(completed_bias, snapshot))
            self._append_bias_history(completed_bias)
        if completed_liquidity is not None:
            self._liquidity_history.append(completed_liquidity)
            if len(self._liquidity_history) > 16:
                self._liquidity_history = self._liquidity_history[-16:]

        return ScenarioStep(transitions=tuple(transitions), signal=signal)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        if self._sweep is not None:
            transitions.append(
                self._sweep_transition(
                    self._sweep,
                    self._sweep.state,
                    "RESET",
                    reason,
                    snapshot.observation.close,
                    {"aborted": True},
                ),
            )
            self._sweep = None
        if self._bias is not None:
            transitions.append(
                self._bias_transition(
                    self._bias,
                    "BIAS_ACTIVE",
                    "RESET",
                    reason,
                    snapshot.observation.close,
                    {"aborted": True},
                ),
            )
            self._bias = None
        return ScenarioStep(transitions=tuple(transitions))

    def _accumulate(
        self,
        snapshot: PrimitiveSnapshot,
        *,
        period: int,
        kind: str,
    ) -> _AuctionBar | None:
        observation = snapshot.observation
        source = source_bar_datetime(observation.ts_ns)
        source_minute = int(source.timestamp() // 60)
        bucket = source_minute // period
        position = source_minute % period
        attribute = "_bias_current" if kind == "bias" else "_liquidity_current"
        current = getattr(self, attribute)
        if current is None or int(current["bucket"]) != bucket:
            current = {
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
            setattr(self, attribute, current)
        else:
            current["high"] = max(float(current["high"]), observation.high)
            current["low"] = min(float(current["low"]), observation.low)
            current["close"] = observation.close
            current["volume"] = float(current["volume"]) + observation.volume
            current["taker_buy_volume"] = float(current["taker_buy_volume"]) + observation.taker_buy_volume
            current["trades"] = int(current["trades"]) + observation.trades

        if position != period - 1:
            return None
        completed = _AuctionBar(
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
        setattr(self, attribute, None)
        return completed

    def _append_bias_history(self, bar: _AuctionBar) -> None:
        previous_close = self._bias_history[-1].close if self._bias_history else bar.close
        true_range = max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
        self._bias_history.append(bar)
        self._bias_true_ranges.append(true_range)
        self._bias_volumes.append(bar.volume)
        capacity = max(
            40,
            int(self.params.get("hsc_bias_atr_bars", 12)) + 8,
            int(self.params.get("hsc_bias_volume_bars", 12)) + 8,
            int(self.params.get("hsc_bias_breakout_lookback", 4)) + 8,
        )
        if len(self._bias_history) > capacity:
            self._bias_history = self._bias_history[-capacity:]
            self._bias_true_ranges = self._bias_true_ranges[-capacity:]
            self._bias_volumes = self._bias_volumes[-capacity:]

    def _evaluate_completed_bias(
        self,
        bar: _AuctionBar,
        snapshot: PrimitiveSnapshot,
    ) -> tuple[ScenarioTransition, ...]:
        atr_bars = int(self.params.get("hsc_bias_atr_bars", 12))
        volume_bars = int(self.params.get("hsc_bias_volume_bars", 12))
        lookback = int(self.params.get("hsc_bias_breakout_lookback", 4))
        required = max(atr_bars, volume_bars, lookback)
        if len(self._bias_history) < required:
            return ()

        atr_htf = sum(self._bias_true_ranges[-atr_bars:]) / atr_bars
        baseline_volume = median(self._bias_volumes[-volume_bars:])
        if atr_htf <= 0.0 or baseline_volume <= 0.0 or bar.candle_range <= 0.0:
            return ()

        prior = self._bias_history[-lookback:]
        prior_high = max(value.high for value in prior)
        prior_low = min(value.low for value in prior)
        acceptance = float(self.params.get("hsc_bias_acceptance_close_atr", 0.02)) * atr_htf
        range_atr = bar.candle_range / atr_htf
        relative_volume = bar.volume / baseline_volume
        minimum_range = float(self.params.get("hsc_bias_range_atr", 0.75))
        minimum_body = float(self.params.get("hsc_bias_body_fraction", 0.50))
        minimum_volume = float(self.params.get("hsc_bias_relative_volume", 0.95))
        minimum_flow = float(self.params.get("hsc_bias_flow_ratio", 0.04))
        outer_close = float(self.params.get("hsc_bias_close_location", 0.68))
        use_flow = bool(self.params.get("hsc_use_flow_proxy", True))

        direction: str | None = None
        boundary = 0.0
        if (
            bar.close > prior_high + acceptance
            and bar.close > bar.open
            and range_atr >= minimum_range
            and bar.body_fraction >= minimum_body
            and relative_volume >= minimum_volume
            and ((bar.flow_ratio >= minimum_flow) if use_flow else True)
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
            and ((bar.flow_ratio <= -minimum_flow) if use_flow else True)
            and bar.close_location <= 1.0 - outer_close
        ):
            direction = "SHORT"
            boundary = prior_low
        if direction is None:
            return ()

        transitions: list[ScenarioTransition] = []
        if self._sweep is not None:
            transitions.append(
                self._sweep_transition(
                    self._sweep,
                    self._sweep.state,
                    "RESET",
                    "HIGHER_TIMEFRAME_BIAS_REFRESHED",
                    bar.close,
                    {},
                ),
            )
            self._sweep = None
        if self._bias is not None:
            transitions.append(
                self._bias_transition(
                    self._bias,
                    "BIAS_ACTIVE",
                    "RESET",
                    "HIGHER_TIMEFRAME_BIAS_REPLACED",
                    bar.close,
                    {"replacement_direction": direction},
                ),
            )

        self._bias_sequence += 1
        lifetime = float(self.params.get("hsc_bias_lifetime_periods", 3.0))
        self._bias = _Bias(
            context_id=f"HSC-BIAS-{bar.end_ts_ns}-{self._bias_sequence:06d}",
            direction=direction,
            boundary=boundary,
            origin=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            extreme=bar.high if direction == "LONG" else bar.low,
            atr_htf=atr_htf,
            created_index=snapshot.index,
            expires_index=snapshot.index + max(1, int(self._bias_period * lifetime)),
            range_atr=range_atr,
            body_fraction=bar.body_fraction,
            flow_ratio=bar.flow_ratio,
            relative_volume=relative_volume,
        )
        transitions.append(
            self._bias_transition(
                self._bias,
                "IDLE",
                "BIAS_ACTIVE",
                "COMPLETED_HIGHER_TIMEFRAME_RANGE_ACCEPTED",
                bar.close,
                {
                    "direction": direction,
                    "bias_period_minutes": self._bias_period,
                    "boundary": boundary,
                    "origin": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "atr_htf": atr_htf,
                    "range_atr": range_atr,
                    "body_fraction": bar.body_fraction,
                    "flow_ratio": bar.flow_ratio,
                    "relative_volume": relative_volume,
                },
            ),
        )
        return tuple(transitions)

    def _advance_bias(self, snapshot: PrimitiveSnapshot) -> ScenarioStep:
        bias = self._bias
        assert bias is not None
        observation = snapshot.observation
        if snapshot.index <= bias.created_index:
            return ScenarioStep()
        if snapshot.index > bias.expires_index:
            return self._reset_bias_and_sweep(snapshot, "HIGHER_TIMEFRAME_BIAS_EXPIRED")

        tolerance = float(self.params.get("hsc_bias_boundary_loss_atr", 0.08)) * bias.atr_htf
        if bias.direction == "LONG":
            bias.extreme = max(bias.extreme, observation.high)
            if observation.close < bias.boundary - tolerance:
                return self._reset_bias_and_sweep(snapshot, "BULLISH_ACCEPTED_BOUNDARY_LOST")
        else:
            bias.extreme = min(bias.extreme, observation.low)
            if observation.close > bias.boundary + tolerance:
                return self._reset_bias_and_sweep(snapshot, "BEARISH_ACCEPTED_BOUNDARY_LOST")
        return ScenarioStep()

    def _reset_bias_and_sweep(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        if self._sweep is not None:
            transitions.append(
                self._sweep_transition(
                    self._sweep,
                    self._sweep.state,
                    "RESET",
                    reason,
                    snapshot.observation.close,
                    {},
                ),
            )
            self._sweep = None
        if self._bias is not None:
            transitions.append(
                self._bias_transition(
                    self._bias,
                    "BIAS_ACTIVE",
                    "RESET",
                    reason,
                    snapshot.observation.close,
                    {},
                ),
            )
            self._bias = None
        return ScenarioStep(transitions=tuple(transitions))

    def _maybe_start_sweep(self, snapshot: PrimitiveSnapshot) -> ScenarioTransition | None:
        bias = self._bias
        if bias is None or not self._liquidity_history or snapshot.index <= bias.created_index:
            return None
        level_bar = self._liquidity_history[-1]
        key = (level_bar.end_ts_ns, bias.direction)
        if key in self._consumed_levels:
            return None

        observation = snapshot.observation
        depth = float(self.params.get("hsc_sweep_min_atr_1m", 0.10)) * snapshot.atr
        opposing_flow = float(self.params.get("hsc_sweep_opposing_flow_ratio", 0.03))
        reclaim_tolerance = float(self.params.get("hsc_sweep_reclaim_tolerance_atr_1m", 0.02)) * snapshot.atr
        impulse_width = abs(bias.extreme - bias.boundary)
        if impulse_width <= 0.0:
            return None

        if bias.direction == "LONG":
            level = level_bar.low
            impulse_position = (level - bias.boundary) / impulse_width
            position_ok = -0.10 <= impulse_position <= float(self.params.get("hsc_max_impulse_position", 0.70))
            swept = observation.low <= level - depth
            reclaimed = observation.close >= level - reclaim_tolerance
            flow_ok = snapshot.flow_ratio <= -opposing_flow if bool(self.params.get("hsc_use_flow_proxy", True)) else True
        else:
            level = level_bar.high
            impulse_position = (bias.boundary - level) / impulse_width
            position_ok = -0.10 <= impulse_position <= float(self.params.get("hsc_max_impulse_position", 0.70))
            swept = observation.high >= level + depth
            reclaimed = observation.close <= level + reclaim_tolerance
            flow_ok = snapshot.flow_ratio >= opposing_flow if bool(self.params.get("hsc_use_flow_proxy", True)) else True
        if not (position_ok and swept and reclaimed and flow_ok):
            return None

        self._consumed_levels.add(key)
        self._sweep_sequence += 1
        self._sweep = _SweepEpisode(
            scenario_id=f"HSC-{snapshot.observation.ts_ns}-{self._sweep_sequence:06d}",
            direction=bias.direction,
            state="COUNTER_DIRECTION_LIQUIDITY_SWEEP",
            level=level,
            level_ts_ns=level_bar.end_ts_ns,
            started_index=snapshot.index,
            started_ts_ns=snapshot.observation.ts_ns,
            sweep_low=observation.low,
            sweep_high=observation.high,
            previous_high=observation.high,
            previous_low=observation.low,
            impulse_position=impulse_position,
            sweep_flow_ratio=snapshot.flow_ratio,
        )
        return self._sweep_transition(
            self._sweep,
            "IDLE",
            "COUNTER_DIRECTION_LIQUIDITY_SWEEP",
            "LOWER_TIMEFRAME_LIQUIDITY_SWEPT_AGAINST_ACCEPTED_BIAS",
            level,
            {
                "direction": bias.direction,
                "bias_context_id": bias.context_id,
                "bias_boundary": bias.boundary,
                "bias_extreme": bias.extreme,
                "liquidity_period_minutes": self._liquidity_period,
                "level": level,
                "level_ts_ns": level_bar.end_ts_ns,
                "impulse_position": impulse_position,
                "sweep_low": observation.low,
                "sweep_high": observation.high,
                "sweep_flow_ratio": snapshot.flow_ratio,
            },
        )

    def _advance_sweep(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        sweep = self._sweep
        bias = self._bias
        if sweep is None:
            return ScenarioStep()
        if bias is None:
            transition = self._sweep_transition(
                sweep,
                sweep.state,
                "RESET",
                "HIGHER_TIMEFRAME_BIAS_NOT_AVAILABLE",
                snapshot.observation.close,
                {},
            )
            self._sweep = None
            return ScenarioStep(transitions=(transition,))
        if snapshot.index <= sweep.started_index:
            return ScenarioStep()

        observation = snapshot.observation
        elapsed = snapshot.index - sweep.started_index
        if elapsed > int(self.params.get("hsc_response_bars", 3)):
            transition = self._sweep_transition(
                sweep,
                sweep.state,
                "RESET",
                "LOWER_TIMEFRAME_RESPONSE_EXPIRED",
                observation.close,
                {"elapsed_bars": elapsed},
            )
            self._sweep = None
            self._cooldown_until = snapshot.index + int(self.params.get("hsc_cooldown_bars", 2))
            return ScenarioStep(transitions=(transition,))

        body_floor = float(self.params.get("hsc_response_body_atr_1m", 0.20))
        response_flow = float(self.params.get("hsc_response_flow_ratio", 0.05))
        close_location = float(self.params.get("hsc_response_close_location", 0.62))
        mode = str(self.params.get("hsc_response_mode", "BREAK_SWEEP_BAR")).upper()

        if sweep.direction == "LONG":
            sweep.sweep_low = min(sweep.sweep_low, observation.low)
            required_break = sweep.sweep_high if mode == "BREAK_SWEEP_BAR" else sweep.previous_high
            confirmed = (
                observation.close > observation.open
                and observation.close > required_break
                and snapshot.body_atr >= body_floor
                and ((snapshot.flow_ratio >= response_flow) if bool(self.params.get("hsc_use_flow_proxy", True)) else True)
                and snapshot.close_location >= close_location
            )
        else:
            sweep.sweep_high = max(sweep.sweep_high, observation.high)
            required_break = sweep.sweep_low if mode == "BREAK_SWEEP_BAR" else sweep.previous_low
            confirmed = (
                observation.close < observation.open
                and observation.close < required_break
                and snapshot.body_atr >= body_floor
                and ((snapshot.flow_ratio <= -response_flow) if bool(self.params.get("hsc_use_flow_proxy", True)) else True)
                and snapshot.close_location <= 1.0 - close_location
            )
        if mode not in {"BREAK_SWEEP_BAR", "BREAK_LAST_BAR"}:
            raise ValueError(f"unsupported hsc_response_mode: {mode}")

        if confirmed:
            if not allow_new:
                transition = self._sweep_transition(
                    sweep,
                    sweep.state,
                    "RESET",
                    "ENTRY_SLOT_UNAVAILABLE_AT_LTF_RESPONSE",
                    observation.close,
                    {},
                )
                self._sweep = None
                return ScenarioStep(transitions=(transition,))
            return self._emit(snapshot, bias, sweep)

        sweep.previous_high = observation.high
        sweep.previous_low = observation.low
        return ScenarioStep()

    def _emit(self, snapshot: PrimitiveSnapshot, bias: _Bias, sweep: _SweepEpisode) -> ScenarioStep:
        observation = snapshot.observation
        buffer_value = float(self.params.get("hsc_stop_buffer_atr_htf", 0.025)) * bias.atr_htf
        extension = float(self.params.get("hsc_extension_atr_htf", 0.50)) * bias.atr_htf
        if sweep.direction == "LONG":
            stop = sweep.sweep_low - buffer_value
            candidates = [
                (bias.extreme, "HIGHER_TIMEFRAME_ACCEPTED_EXTREME"),
                (snapshot.upper_fast, "PRIOR_FAST_BUYSIDE_LIQUIDITY"),
                (snapshot.upper_slow, "PRIOR_SLOW_BUYSIDE_LIQUIDITY"),
                (bias.extreme + extension, "HIGHER_TIMEFRAME_EXTENSION"),
            ]
        else:
            stop = sweep.sweep_high + buffer_value
            candidates = [
                (bias.extreme, "HIGHER_TIMEFRAME_ACCEPTED_EXTREME"),
                (snapshot.lower_fast, "PRIOR_FAST_SELLSIDE_LIQUIDITY"),
                (snapshot.lower_slow, "PRIOR_SLOW_SELLSIDE_LIQUIDITY"),
                (bias.extreme - extension, "HIGHER_TIMEFRAME_EXTENSION"),
            ]
        target = self._select_target(sweep.direction, observation.close, stop, candidates)
        if target is None:
            transition = self._sweep_transition(
                sweep,
                sweep.state,
                "RESET",
                "NO_HIERARCHICAL_OBJECTIVE_WITH_SUFFICIENT_SPACE",
                observation.close,
                {},
            )
            self._sweep = None
            return ScenarioStep(transitions=(transition,))
        target_price, target_reason = target

        transition = self._sweep_transition(
            sweep,
            sweep.state,
            "ENTRY_ARMED",
            "HTF_BIAS_LTF_SWEEP_AND_RESPONSE_CONFIRMED",
            observation.close,
            {
                "direction": sweep.direction,
                "bias_context_id": bias.context_id,
                "bias_boundary": bias.boundary,
                "bias_extreme": bias.extreme,
                "level": sweep.level,
                "impulse_position": sweep.impulse_position,
                "sweep_flow_ratio": sweep.sweep_flow_ratio,
                "stop_price": stop,
                "target_price": target_price,
                "target_reason": target_reason,
            },
        )
        signal = ScenarioSignal(
            scenario_id=sweep.scenario_id,
            family="HSC",
            direction=sweep.direction,
            observed_ts_ns=observation.ts_ns,
            reference_entry=observation.close,
            stop_price=stop,
            target_price=target_price,
            target_reason=target_reason,
            atr=snapshot.atr,
            liquidity_level=sweep.level,
            details={
                "bias_context_id": bias.context_id,
                "bias_period_minutes": self._bias_period,
                "liquidity_period_minutes": self._liquidity_period,
                "bias_boundary": bias.boundary,
                "bias_origin": bias.origin,
                "bias_extreme": bias.extreme,
                "bias_atr_htf": bias.atr_htf,
                "bias_range_atr": bias.range_atr,
                "bias_body_fraction": bias.body_fraction,
                "bias_flow_ratio": bias.flow_ratio,
                "bias_relative_volume": bias.relative_volume,
                "swept_level": sweep.level,
                "swept_level_ts_ns": sweep.level_ts_ns,
                "impulse_position": sweep.impulse_position,
                "sweep_flow_ratio": sweep.sweep_flow_ratio,
                "sweep_low": sweep.sweep_low,
                "sweep_high": sweep.sweep_high,
            },
        )
        self._sweep = None
        self._cooldown_until = snapshot.index + int(self.params.get("hsc_cooldown_bars", 2))
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
        minimum_rr = float(self.params.get("minimum_structural_rr", 0.75))
        valid: list[tuple[float, str]] = []
        for price, reason in candidates:
            if price is None:
                continue
            reward = float(price) - entry if direction == "LONG" else entry - float(price)
            if reward > 0.0 and reward / risk >= minimum_rr:
                valid.append((float(price), reason))
        valid.sort(key=lambda value: abs(value[0] - entry))
        return valid[0] if valid else None

    @staticmethod
    def _bias_transition(
        bias: _Bias,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=bias.context_id,
            event_type="HSC_BIAS_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )

    @staticmethod
    def _sweep_transition(
        sweep: _SweepEpisode,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=sweep.scenario_id,
            event_type="HSC_SWEEP_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )
