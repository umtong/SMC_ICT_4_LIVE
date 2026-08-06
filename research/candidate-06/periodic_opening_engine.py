"""Quarter-hour opening imbalance bias with confirmed LTF liquidity relay."""

from __future__ import annotations

from dataclasses import replace
from statistics import median
from typing import Any, Mapping

from causal_clock import source_bar_datetime
from hierarchical_multi_liquidity_engine import HierarchicalMultiLiquidityEngine
from hierarchical_sweep_engine import _AuctionBar, _Bias, _SweepEpisode
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


class PeriodicOpeningLiquidityRelayEngine(HierarchicalMultiLiquidityEngine):
    """Create a finite directional context from periodic opening order imbalance.

    The context is not a generic clock filter. At the first completed one-minute
    bar of each UTC quarter hour, the engine compares signed aggressive volume,
    total volume and price displacement with prior quarter-hour openings using
    prior observations only. An unusually strong aligned opening creates or
    replaces a finite bias. Entries still require a pre-existing confirmed LTF
    liquidity pool, a counter-bias sweep/reclaim and a separate response bar.
    """

    def __init__(self, params: Mapping[str, Any]):
        super().__init__(params)
        self._opening_volumes: list[float] = []
        self._opening_abs_signed: list[float] = []
        self._opening_ranges: list[float] = []
        self._quarter_current: dict[str, Any] | None = None
        self._quarter_history: list[_AuctionBar] = []
        self._quarter_true_ranges: list[float] = []
        self._periodic_sequence = 0

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        periodic_transitions: list[ScenarioTransition] = []
        source = source_bar_datetime(snapshot.observation.ts_ns)
        is_opening = source.minute % 15 == 0
        if is_opening:
            periodic_transitions.extend(self._evaluate_opening(snapshot))

        step = super().observe(snapshot, allow_new=allow_new)
        self._accumulate_quarter(snapshot)
        if is_opening:
            self._append_opening(snapshot)
        return ScenarioStep(
            transitions=tuple((*periodic_transitions, *step.transitions)),
            signal=step.signal,
        )

    def _evaluate_completed_bias(
        self,
        bar: _AuctionBar,
        snapshot: PrimitiveSnapshot,
    ) -> tuple[ScenarioTransition, ...]:
        # The parent 60-minute accumulator remains useful for diagnostics, but
        # periodic opening imbalance is the sole source of directional context.
        return ()

    def _evaluate_opening(self, snapshot: PrimitiveSnapshot) -> tuple[ScenarioTransition, ...]:
        minimum_history = int(self.params.get("poil_opening_history", 16))
        quarter_atr_bars = int(self.params.get("poil_quarter_atr_bars", 8))
        if (
            len(self._opening_volumes) < minimum_history
            or len(self._opening_abs_signed) < minimum_history
            or len(self._quarter_true_ranges) < quarter_atr_bars
        ):
            return ()

        observation = snapshot.observation
        volume_baseline = median(self._opening_volumes[-minimum_history:])
        pressure_baseline = median(self._opening_abs_signed[-minimum_history:])
        atr_context = sum(self._quarter_true_ranges[-quarter_atr_bars:]) / quarter_atr_bars
        candle_range = max(observation.high - observation.low, 0.0)
        if volume_baseline <= 0.0 or pressure_baseline <= 0.0 or atr_context <= 0.0 or candle_range <= 0.0:
            return ()

        signed_pressure = observation.flow_ratio * observation.volume
        volume_multiple = observation.volume / volume_baseline
        pressure_multiple = abs(signed_pressure) / pressure_baseline
        body = abs(observation.close - observation.open)
        body_fraction = body / candle_range
        range_atr_1m = candle_range / snapshot.atr if snapshot.atr > 0.0 else 0.0
        body_atr_1m = body / snapshot.atr if snapshot.atr > 0.0 else 0.0
        close_location = (observation.close - observation.low) / candle_range

        minimum_volume = float(self.params.get("poil_opening_volume_multiple", 1.15))
        minimum_pressure = float(self.params.get("poil_opening_pressure_multiple", 1.50))
        minimum_flow = float(self.params.get("poil_opening_flow_ratio", 0.12))
        minimum_body_atr = float(self.params.get("poil_opening_body_atr_1m", 0.25))
        minimum_range_atr = float(self.params.get("poil_opening_range_atr_1m", 0.50))
        minimum_body_fraction = float(self.params.get("poil_opening_body_fraction", 0.45))
        outer_close = float(self.params.get("poil_opening_close_location", 0.65))

        common = (
            volume_multiple >= minimum_volume
            and pressure_multiple >= minimum_pressure
            and body_atr_1m >= minimum_body_atr
            and range_atr_1m >= minimum_range_atr
            and body_fraction >= minimum_body_fraction
        )
        direction: str | None = None
        if (
            common
            and observation.close > observation.open
            and observation.flow_ratio >= minimum_flow
            and close_location >= outer_close
        ):
            direction = "LONG"
        elif (
            common
            and observation.close < observation.open
            and observation.flow_ratio <= -minimum_flow
            and close_location <= 1.0 - outer_close
        ):
            direction = "SHORT"
        if direction is None:
            return ()

        transitions: list[ScenarioTransition] = []
        if self._sweep is not None:
            transitions.append(
                self._sweep_transition(
                    self._sweep,
                    self._sweep.state,
                    "RESET",
                    "PERIODIC_OPENING_BIAS_REFRESHED",
                    observation.close,
                    {"replacement_direction": direction},
                ),
            )
            self._sweep = None
        if self._bias is not None:
            transitions.append(
                self._bias_transition(
                    self._bias,
                    "BIAS_ACTIVE",
                    "RESET",
                    "PERIODIC_OPENING_BIAS_REPLACED",
                    observation.close,
                    {"replacement_direction": direction},
                ),
            )

        self._periodic_sequence += 1
        horizon_minutes = int(self.params.get("poil_bias_horizon_minutes", 240))
        context_id = f"POIL-BIAS-{observation.ts_ns}-{self._periodic_sequence:06d}"
        self._bias = _Bias(
            context_id=context_id,
            direction=direction,
            boundary=observation.low if direction == "LONG" else observation.high,
            origin=observation.open,
            high=observation.high,
            low=observation.low,
            close=observation.close,
            extreme=observation.high if direction == "LONG" else observation.low,
            atr_htf=atr_context,
            created_index=snapshot.index,
            expires_index=snapshot.index + max(1, horizon_minutes),
            range_atr=candle_range / atr_context,
            body_fraction=body_fraction,
            flow_ratio=observation.flow_ratio,
            relative_volume=volume_multiple,
        )
        transitions.append(
            self._bias_transition(
                self._bias,
                "IDLE",
                "BIAS_ACTIVE",
                "QUARTER_HOUR_OPENING_IMBALANCE_ACCEPTED",
                observation.close,
                {
                    "direction": direction,
                    "source_minute_utc": source.minute,
                    "opening_volume_multiple": volume_multiple,
                    "opening_pressure_multiple": pressure_multiple,
                    "opening_flow_ratio": observation.flow_ratio,
                    "opening_range_atr_1m": range_atr_1m,
                    "opening_body_atr_1m": body_atr_1m,
                    "opening_body_fraction": body_fraction,
                    "opening_close_location": close_location,
                    "quarter_atr_context": atr_context,
                    "horizon_minutes": horizon_minutes,
                    "boundary": self._bias.boundary,
                    "high": observation.high,
                    "low": observation.low,
                },
            ),
        )
        return tuple(transitions)

    def _append_opening(self, snapshot: PrimitiveSnapshot) -> None:
        observation = snapshot.observation
        self._opening_volumes.append(observation.volume)
        self._opening_abs_signed.append(abs(observation.flow_ratio * observation.volume))
        self._opening_ranges.append(max(observation.high - observation.low, 0.0))
        capacity = max(64, int(self.params.get("poil_opening_history", 16)) + 16)
        if len(self._opening_volumes) > capacity:
            self._opening_volumes = self._opening_volumes[-capacity:]
            self._opening_abs_signed = self._opening_abs_signed[-capacity:]
            self._opening_ranges = self._opening_ranges[-capacity:]

    def _accumulate_quarter(self, snapshot: PrimitiveSnapshot) -> None:
        observation = snapshot.observation
        source = source_bar_datetime(observation.ts_ns)
        source_minute = int(source.timestamp() // 60)
        bucket = source_minute // 15
        position = source_minute % 15
        current = self._quarter_current
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
            self._quarter_current = current
        else:
            current["high"] = max(float(current["high"]), observation.high)
            current["low"] = min(float(current["low"]), observation.low)
            current["close"] = observation.close
            current["volume"] = float(current["volume"]) + observation.volume
            current["taker_buy_volume"] = float(current["taker_buy_volume"]) + observation.taker_buy_volume
            current["trades"] = int(current["trades"]) + observation.trades

        if position != 14:
            return
        bar = _AuctionBar(
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
        previous_close = self._quarter_history[-1].close if self._quarter_history else bar.close
        true_range = max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
        self._quarter_history.append(bar)
        self._quarter_true_ranges.append(true_range)
        capacity = max(48, int(self.params.get("poil_quarter_atr_bars", 8)) + 16)
        if len(self._quarter_history) > capacity:
            self._quarter_history = self._quarter_history[-capacity:]
            self._quarter_true_ranges = self._quarter_true_ranges[-capacity:]
        self._quarter_current = None

    def _emit(self, snapshot: PrimitiveSnapshot, bias: _Bias, sweep: _SweepEpisode) -> ScenarioStep:
        step = super()._emit(snapshot, bias, sweep)
        if step.signal is None:
            return step
        details = {
            **dict(step.signal.details),
            "periodic_context": True,
            "periodic_bias_horizon_minutes": self.params.get("poil_bias_horizon_minutes", 240),
            "periodic_opening_interval_minutes": 15,
        }
        signal: ScenarioSignal = replace(step.signal, family="POIL", details=details)
        return ScenarioStep(transitions=step.transitions, signal=signal)
