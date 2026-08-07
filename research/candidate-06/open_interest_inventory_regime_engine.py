"""Open-interest inventory regime relay for candidate-06.

Completed five-minute OI changes classify whether directional price movement
was accompanied by fresh inventory formation or by position deleveraging.
No event is traded immediately.  Later completed OI and one-minute price/flow
responses determine continuation versus counter-inventory reversal.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Mapping

from futures_metrics_data import FuturesMetric
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass(slots=True)
class _InventoryWave:
    scenario_id: str
    regime: str  # BUILD or UNWIND
    side: str  # BUY or SELL
    state: str
    started_index: int
    started_ts_ns: int
    event_change: float
    change_threshold: float
    baseline_oi: float
    event_oi: float
    event_open: float
    event_high: float
    event_low: float
    event_close: float
    event_mid: float
    event_range: float
    extreme: float
    atr: float
    slow_mid: float | None
    upper_fast: float | None
    lower_fast: float | None
    metric_at_event: FuturesMetric
    retained_metric_seen: bool = False
    pullback_index: int | None = None
    pullback_high: float | None = None
    pullback_low: float | None = None
    branch: str | None = None
    signal_index: int | None = None


class OpenInterestInventoryRegimeRelayEngine:
    """Trade only after completed inventory-state confirmation."""

    def __init__(
        self,
        params: Mapping[str, Any],
        *,
        metrics: Mapping[int, FuturesMetric],
    ) -> None:
        self.params = dict(params)
        self._metrics = dict(metrics)
        self._last_metric: FuturesMetric | None = None
        self._increase_history: deque[tuple[int, float]] = deque()
        self._drop_history: deque[tuple[int, float]] = deque()
        self._bars: deque[PrimitiveSnapshot] = deque(maxlen=5)
        self._wave: _InventoryWave | None = None
        self._sequence = 0
        self._cooldown_until = -1

    @staticmethod
    def _quantile(values: list[float], q: float) -> float:
        ordered = sorted(values)
        if not ordered:
            return 0.0
        position = min(max(q, 0.0), 1.0) * (len(ordered) - 1)
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    def _transition(
        self,
        wave: _InventoryWave,
        previous: str,
        next_state: str,
        reason: str,
        reference: float | None,
        details: Mapping[str, Any] | None = None,
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=wave.scenario_id,
            event_type="OPEN_INTEREST_INVENTORY_REGIME_TRANSITION",
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference,
            details=dict(details or {}),
        )

    @staticmethod
    def _entry_scenario_id(wave: _InventoryWave) -> str:
        return f"{wave.scenario_id}:ENTRY"

    @classmethod
    def _entry_transition(
        cls,
        wave: _InventoryWave,
        *,
        reason: str,
        reference: float,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=cls._entry_scenario_id(wave),
            event_type="OIIR_ENTRY_TRANSITION",
            previous_state="IDLE",
            next_state="ENTRY_ARMED",
            reason_code=reason,
            reference_price=reference,
            details={
                "context_scenario_id": wave.scenario_id,
                "regime": wave.regime,
                "branch": wave.branch,
                **dict(details),
            },
        )

    def _prune(self, index: int) -> None:
        cutoff = index - int(self.params.get("oiir_history_minutes", 1440))
        for history in (self._increase_history, self._drop_history):
            while history and history[0][0] < cutoff:
                history.popleft()

    def _threshold(self, regime: str) -> float | None:
        history = self._increase_history if regime == "BUILD" else self._drop_history
        values = [value for _, value in history]
        minimum = int(self.params.get("oiir_min_prior_changes", 36))
        if len(values) < minimum:
            return None
        quantile = float(self.params.get("oiir_change_quantile", 0.85))
        return self._quantile(values, quantile)

    def _five_minute_bar(self) -> tuple[float, float, float, float] | None:
        if len(self._bars) < 5:
            return None
        items = list(self._bars)
        return (
            items[0].observation.open,
            max(item.observation.high for item in items),
            min(item.observation.low for item in items),
            items[-1].observation.close,
        )

    def _start_wave(
        self,
        snapshot: PrimitiveSnapshot,
        *,
        regime: str,
        side: str,
        event_change: float,
        threshold: float,
        baseline_oi: float,
        metric: FuturesMetric,
    ) -> ScenarioTransition | None:
        bar = self._five_minute_bar()
        if bar is None:
            return None
        open_, high, low, close = bar
        self._sequence += 1
        scenario_id = f"OIIR-{snapshot.observation.ts_ns}-{self._sequence:06d}"
        state = "NEW_INVENTORY_IMPULSE_OBSERVED" if regime == "BUILD" else "DELEVERAGING_IMPULSE_OBSERVED"
        reason = (
            "EXTREME_OPEN_INTEREST_EXPANSION_WITH_ALIGNED_PRICE_AND_FLOW"
            if regime == "BUILD"
            else "EXTREME_OPEN_INTEREST_CONTRACTION_WITH_ALIGNED_PRICE_AND_FLOW"
        )
        wave = _InventoryWave(
            scenario_id=scenario_id,
            regime=regime,
            side=side,
            state=state,
            started_index=snapshot.index,
            started_ts_ns=snapshot.observation.ts_ns,
            event_change=event_change,
            change_threshold=threshold,
            baseline_oi=baseline_oi,
            event_oi=metric.open_interest,
            event_open=open_,
            event_high=high,
            event_low=low,
            event_close=close,
            event_mid=(open_ + close) / 2.0,
            event_range=max(high - low, snapshot.atr * 0.25),
            extreme=high if side == "BUY" else low,
            atr=max(snapshot.atr, 1e-12),
            slow_mid=snapshot.slow_mid,
            upper_fast=snapshot.upper_fast,
            lower_fast=snapshot.lower_fast,
            metric_at_event=metric,
        )
        self._wave = wave
        return self._transition(
            wave,
            "IDLE",
            state,
            reason,
            close,
            {
                "inventory_regime": regime,
                "directional_side": side,
                "open_interest_change_fraction": event_change,
                "prior_only_change_threshold": threshold,
                "baseline_open_interest": baseline_oi,
                "event_open_interest": metric.open_interest,
                "event_open": open_,
                "event_high": high,
                "event_low": low,
                "event_close": close,
                "metric_taker_ratio": metric.taker_buy_sell_ratio,
            },
        )

    def _maybe_start_wave(
        self,
        snapshot: PrimitiveSnapshot,
        metric: FuturesMetric,
    ) -> ScenarioTransition | None:
        prior = self._last_metric
        if prior is None or prior.open_interest <= 0.0:
            return None
        change = (metric.open_interest - prior.open_interest) / prior.open_interest
        if change == 0.0:
            return None
        regime = "BUILD" if change > 0.0 else "UNWIND"
        if regime == "BUILD" and not bool(self.params.get("oiir_enable_build", True)):
            return None
        if regime == "UNWIND" and not bool(self.params.get("oiir_enable_unwind", True)):
            return None
        magnitude = abs(change)
        threshold = self._threshold(regime)
        if threshold is None or magnitude < threshold:
            return None
        bar = self._five_minute_bar()
        if bar is None:
            return None
        open_, _, _, close = bar
        move_atr = (close - open_) / max(snapshot.atr, 1e-12)
        minimum_move = float(self.params.get("oiir_event_move_atr", 0.30))
        flow_floor = float(self.params.get("oiir_metric_flow_floor", 0.06))
        flow = metric.signed_taker_ratio
        if move_atr >= minimum_move and flow >= flow_floor:
            side = "BUY"
        elif move_atr <= -minimum_move and flow <= -flow_floor:
            side = "SELL"
        else:
            return None
        return self._start_wave(
            snapshot,
            regime=regime,
            side=side,
            event_change=change,
            threshold=threshold,
            baseline_oi=prior.open_interest,
            metric=metric,
        )

    def _target_reversal(
        self,
        wave: _InventoryWave,
        direction: str,
        entry: float,
        stop: float,
    ) -> tuple[float, str] | None:
        if direction == "LONG":
            raw = [
                (wave.event_open, "INVENTORY_IMPULSE_ORIGIN"),
                (wave.slow_mid, "PRE_SHOCK_DEALING_RANGE_EQUILIBRIUM"),
                (wave.upper_fast, "PRE_SHOCK_FAST_RANGE_LIQUIDITY"),
            ]
            candidates = sorted(
                (float(level), reason)
                for level, reason in raw
                if level is not None and float(level) > entry
            )
        else:
            raw = [
                (wave.event_open, "INVENTORY_IMPULSE_ORIGIN"),
                (wave.slow_mid, "PRE_SHOCK_DEALING_RANGE_EQUILIBRIUM"),
                (wave.lower_fast, "PRE_SHOCK_FAST_RANGE_LIQUIDITY"),
            ]
            candidates = sorted(
                (
                    (float(level), reason)
                    for level, reason in raw
                    if level is not None and float(level) < entry
                ),
                reverse=True,
            )
        risk = abs(entry - stop)
        minimum = float(self.params.get("minimum_structural_rr", 0.75))
        for target, reason in candidates:
            if risk > 0.0 and abs(target - entry) / risk >= minimum:
                return target, reason
        return None

    def _projection_target(
        self,
        wave: _InventoryWave,
        direction: str,
        entry: float,
        stop: float,
    ) -> tuple[float, str] | None:
        distance = max(wave.event_range, wave.atr) * float(
            self.params.get("oiir_projection_fraction", 1.0),
        )
        target = entry + distance if direction == "LONG" else entry - distance
        risk = abs(entry - stop)
        minimum = float(self.params.get("minimum_structural_rr", 0.75))
        if risk <= 0.0 or abs(target - entry) / risk < minimum:
            return None
        return target, "INVENTORY_IMPULSE_RANGE_EXTENSION"

    def _emit(
        self,
        snapshot: PrimitiveSnapshot,
        wave: _InventoryWave,
        *,
        family: str,
        direction: str,
        stop: float,
        target: float,
        target_reason: str,
        reason: str,
        exit_reason: str,
    ) -> ScenarioStep:
        obs = snapshot.observation
        wave.branch = family
        wave.signal_index = snapshot.index
        previous = wave.state
        wave.state = f"{family}_SIGNALLED"
        context_transition = self._transition(
            wave,
            previous,
            wave.state,
            reason,
            obs.close,
            {
                "stop": stop,
                "target": target,
                "target_reason": target_reason,
            },
        )
        entry_transition = self._entry_transition(
            wave,
            reason=f"{family}_ENTRY_ARMED",
            reference=obs.close,
            details={
                "stop": stop,
                "target": target,
                "target_reason": target_reason,
            },
        )
        signal = ScenarioSignal(
            scenario_id=self._entry_scenario_id(wave),
            family=family,
            direction=direction,
            observed_ts_ns=obs.ts_ns,
            reference_entry=obs.close,
            stop_price=stop,
            target_price=target,
            target_reason=target_reason,
            atr=wave.atr,
            liquidity_level=wave.extreme,
            details={
                "inventory_regime": wave.regime,
                "open_interest_change_fraction": wave.event_change,
                "prior_only_change_threshold": wave.change_threshold,
                "causal_exit_reason_codes": (exit_reason,),
                "causal_exit_open_position": True,
            },
        )
        return ScenarioStep(
            transitions=(context_transition, entry_transition),
            signal=signal,
        )

    def _metric_change(self, metric: FuturesMetric | None) -> float | None:
        if metric is None or self._last_metric is None or self._last_metric.open_interest <= 0.0:
            return None
        return (metric.open_interest - self._last_metric.open_interest) / self._last_metric.open_interest

    def _advance_build(
        self,
        snapshot: PrimitiveSnapshot,
        wave: _InventoryWave,
        metric: FuturesMetric | None,
    ) -> ScenarioStep:
        obs = snapshot.observation
        floor = float(self.params.get("oiir_response_flow_ratio", 0.05))
        close_floor = float(self.params.get("oiir_reclaim_close_location", 0.58))
        buffer = float(self.params.get("oiir_stop_buffer_atr", 0.08)) * wave.atr

        if metric is not None and metric.ts_ns > wave.started_ts_ns:
            retained_fraction = float(self.params.get("oiir_inventory_retention_fraction", 0.35))
            event_inventory = max(wave.event_oi - wave.baseline_oi, 0.0)
            retained_floor = wave.baseline_oi + event_inventory * retained_fraction
            if metric.open_interest < retained_floor:
                transition = self._transition(
                    wave,
                    wave.state,
                    "RESET",
                    "NEW_INVENTORY_NOT_RETAINED_AFTER_IMPULSE",
                    obs.close,
                    {
                        "retained_floor": retained_floor,
                        "observed_open_interest": metric.open_interest,
                    },
                )
                self._wave = None
                return ScenarioStep(transitions=(transition,))
            if not wave.retained_metric_seen:
                previous = wave.state
                wave.state = "NEW_INVENTORY_RETENTION_CONFIRMED"
                wave.retained_metric_seen = True
                return ScenarioStep(
                    transitions=(
                        self._transition(
                            wave,
                            previous,
                            wave.state,
                            "FRESH_DIRECTIONAL_INVENTORY_REMAINED_OPEN",
                            obs.close,
                            {
                                "retained_floor": retained_floor,
                                "observed_open_interest": metric.open_interest,
                            },
                        ),
                    ),
                )

        if not wave.retained_metric_seen:
            return ScenarioStep()

        if wave.pullback_index is None:
            if wave.side == "BUY":
                pullback = (
                    snapshot.flow_ratio <= -floor
                    and obs.low <= wave.event_mid + 0.10 * wave.atr
                    and obs.close >= wave.event_mid
                )
            else:
                pullback = (
                    snapshot.flow_ratio >= floor
                    and obs.high >= wave.event_mid - 0.10 * wave.atr
                    and obs.close <= wave.event_mid
                )
            if pullback:
                wave.pullback_index = snapshot.index
                wave.pullback_high = obs.high
                wave.pullback_low = obs.low
                previous = wave.state
                wave.state = "OPPOSING_PULLBACK_FAILED_TO_REVERSE_FRESH_INVENTORY"
                return ScenarioStep(
                    transitions=(
                        self._transition(
                            wave,
                            previous,
                            wave.state,
                            "FIRST_OPPOSING_FLOW_PULLBACK_HELD_INVENTORY_VALUE",
                            obs.close,
                            {
                                "pullback_high": obs.high,
                                "pullback_low": obs.low,
                            },
                        ),
                    ),
                )
            return ScenarioStep()

        if snapshot.index <= wave.pullback_index:
            return ScenarioStep()
        extension = float(self.params.get("oiir_extension_atr", 0.05)) * wave.atr
        if wave.side == "BUY":
            resumed = (
                obs.close >= float(wave.pullback_high) + extension
                and snapshot.flow_ratio >= floor
                and snapshot.close_location >= close_floor
            )
            direction = "LONG"
            stop = float(wave.pullback_low) - buffer
        else:
            resumed = (
                obs.close <= float(wave.pullback_low) - extension
                and snapshot.flow_ratio <= -floor
                and snapshot.close_location <= 1.0 - close_floor
            )
            direction = "SHORT"
            stop = float(wave.pullback_high) + buffer
        if not resumed:
            return ScenarioStep()
        target = self._projection_target(wave, direction, obs.close, stop)
        if target is None:
            transition = self._transition(
                wave,
                wave.state,
                "RESET",
                "NO_FRESH_INVENTORY_OBJECTIVE_WITH_SUFFICIENT_SPACE",
                obs.close,
            )
            self._wave = None
            return ScenarioStep(transitions=(transition,))
        return self._emit(
            snapshot,
            wave,
            family="OIIR_B",
            direction=direction,
            stop=stop,
            target=target[0],
            target_reason=target[1],
            reason="FRESH_INVENTORY_RETAINED_PULLBACK_AND_RESUMPTION_CONFIRMED",
            exit_reason="FRESH_INVENTORY_CONTINUATION_THESIS_INVALIDATED",
        )

    def _advance_unwind(
        self,
        snapshot: PrimitiveSnapshot,
        wave: _InventoryWave,
        metric: FuturesMetric | None,
        prior_extreme: float,
    ) -> ScenarioStep:
        obs = snapshot.observation
        floor = float(self.params.get("oiir_response_flow_ratio", 0.05))
        close_floor = float(self.params.get("oiir_reclaim_close_location", 0.58))
        extension = float(self.params.get("oiir_extension_atr", 0.05)) * wave.atr
        buffer = float(self.params.get("oiir_stop_buffer_atr", 0.08)) * wave.atr
        next_change = self._metric_change(metric)

        require_rebuild = bool(self.params.get("oiir_require_counter_inventory_rebuild", True))
        rebuild_fraction = float(self.params.get("oiir_counter_rebuild_fraction", 0.35))
        event_removed = max(wave.baseline_oi - wave.event_oi, 0.0)
        rebuild_confirmed = (
            metric is not None
            and metric.ts_ns > wave.started_ts_ns
            and metric.open_interest >= wave.event_oi + event_removed * rebuild_fraction
        )
        if not require_rebuild:
            rebuild_confirmed = True

        if wave.side == "SELL":
            reversal = (
                bool(self.params.get("oiir_enable_unwind_reversal", True))
                and rebuild_confirmed
                and obs.close >= wave.event_mid
                and snapshot.flow_ratio >= floor
                and snapshot.close_location >= close_floor
            )
            reversal_direction = "LONG"
            reversal_stop = wave.extreme - buffer
        else:
            reversal = (
                bool(self.params.get("oiir_enable_unwind_reversal", True))
                and rebuild_confirmed
                and obs.close <= wave.event_mid
                and snapshot.flow_ratio <= -floor
                and snapshot.close_location <= 1.0 - close_floor
            )
            reversal_direction = "SHORT"
            reversal_stop = wave.extreme + buffer
        if reversal:
            target = self._target_reversal(
                wave,
                reversal_direction,
                obs.close,
                reversal_stop,
            )
            if target is None:
                transition = self._transition(
                    wave,
                    wave.state,
                    "RESET",
                    "NO_COUNTER_INVENTORY_REVERSAL_OBJECTIVE_WITH_SUFFICIENT_SPACE",
                    obs.close,
                )
                self._wave = None
                return ScenarioStep(transitions=(transition,))
            return self._emit(
                snapshot,
                wave,
                family="OIIR_UR",
                direction=reversal_direction,
                stop=reversal_stop,
                target=target[0],
                target_reason=target[1],
                reason="COUNTER_INVENTORY_REBUILT_AND_DELEVERAGING_RECLAIM_CONFIRMED",
                exit_reason="COUNTER_INVENTORY_REVERSAL_THESIS_INVALIDATED",
            )

        persistence_fraction = float(self.params.get("oiir_unwind_persistence_fraction", 0.35))
        persistence = (
            next_change is not None
            and next_change <= -abs(wave.event_change) * persistence_fraction
        )
        if wave.side == "SELL":
            continuation = (
                bool(self.params.get("oiir_enable_unwind_continuation", True))
                and persistence
                and obs.close <= prior_extreme - extension
                and snapshot.flow_ratio <= -floor
                and snapshot.close_location <= 1.0 - close_floor
            )
            direction = "SHORT"
            stop = wave.event_mid + buffer
        else:
            continuation = (
                bool(self.params.get("oiir_enable_unwind_continuation", True))
                and persistence
                and obs.close >= prior_extreme + extension
                and snapshot.flow_ratio >= floor
                and snapshot.close_location >= close_floor
            )
            direction = "LONG"
            stop = wave.event_mid - buffer
        if not continuation:
            return ScenarioStep()
        target = self._projection_target(wave, direction, obs.close, stop)
        if target is None:
            transition = self._transition(
                wave,
                wave.state,
                "RESET",
                "NO_UNWIND_CONTINUATION_OBJECTIVE_WITH_SUFFICIENT_SPACE",
                obs.close,
            )
            self._wave = None
            return ScenarioStep(transitions=(transition,))
        return self._emit(
            snapshot,
            wave,
            family="OIIR_UC",
            direction=direction,
            stop=stop,
            target=target[0],
            target_reason=target[1],
            reason="DELEVERAGING_PERSISTED_WITH_PRICE_DISCOVERY",
            exit_reason="DELEVERAGING_CONTINUATION_THESIS_INVALIDATED",
        )

    def _advance_signalled(
        self,
        snapshot: PrimitiveSnapshot,
        wave: _InventoryWave,
        metric: FuturesMetric | None,
    ) -> ScenarioStep:
        assert wave.signal_index is not None
        obs = snapshot.observation
        floor = float(self.params.get("oiir_response_flow_ratio", 0.05))
        if wave.branch == "OIIR_B":
            inventory_lost = (
                metric is not None
                and metric.open_interest <= wave.baseline_oi
            )
            price_lost = (
                (wave.side == "BUY" and obs.close < wave.event_open and snapshot.flow_ratio <= -floor)
                or (wave.side == "SELL" and obs.close > wave.event_open and snapshot.flow_ratio >= floor)
            )
            invalid = inventory_lost or price_lost
            reason = "FRESH_INVENTORY_CONTINUATION_THESIS_INVALIDATED"
        elif wave.branch == "OIIR_UR":
            invalid = (
                (wave.side == "SELL" and obs.close < wave.extreme and snapshot.flow_ratio <= -floor)
                or (wave.side == "BUY" and obs.close > wave.extreme and snapshot.flow_ratio >= floor)
            )
            reason = "COUNTER_INVENTORY_REVERSAL_THESIS_INVALIDATED"
        else:
            invalid = (
                (wave.side == "SELL" and obs.close > wave.event_mid and snapshot.flow_ratio >= floor)
                or (wave.side == "BUY" and obs.close < wave.event_mid and snapshot.flow_ratio <= -floor)
            )
            reason = "DELEVERAGING_CONTINUATION_THESIS_INVALIDATED"
        if invalid:
            transition = self._transition(
                wave,
                wave.state,
                "RESET",
                reason,
                obs.close,
            )
            self._wave = None
            self._cooldown_until = snapshot.index + int(self.params.get("oiir_cooldown_bars", 2))
            return ScenarioStep(transitions=(transition,))
        if snapshot.index - wave.signal_index >= int(
            self.params.get("oiir_invalidation_observation_bars", 6),
        ):
            transition = self._transition(
                wave,
                wave.state,
                "RESET",
                "INVENTORY_REGIME_POST_SIGNAL_CONTEXT_MATURED",
                obs.close,
            )
            self._wave = None
            self._cooldown_until = snapshot.index + int(self.params.get("oiir_cooldown_bars", 2))
            return ScenarioStep(transitions=(transition,))
        return ScenarioStep()

    def _advance_wave(
        self,
        snapshot: PrimitiveSnapshot,
        metric: FuturesMetric | None,
    ) -> ScenarioStep:
        wave = self._wave
        assert wave is not None
        if wave.state.endswith("SIGNALLED"):
            return self._advance_signalled(snapshot, wave, metric)
        if snapshot.index <= wave.started_index:
            return ScenarioStep()
        obs = snapshot.observation
        prior_extreme = wave.extreme
        wave.extreme = (
            max(wave.extreme, obs.high)
            if wave.side == "BUY"
            else min(wave.extreme, obs.low)
        )
        if snapshot.index - wave.started_index > int(
            self.params.get("oiir_response_bars", 15),
        ):
            transition = self._transition(
                wave,
                wave.state,
                "RESET",
                "INVENTORY_REGIME_RESPONSE_EXPIRED",
                obs.close,
            )
            self._wave = None
            self._cooldown_until = snapshot.index + int(self.params.get("oiir_cooldown_bars", 2))
            return ScenarioStep(transitions=(transition,))
        if wave.regime == "BUILD":
            return self._advance_build(snapshot, wave, metric)
        return self._advance_unwind(snapshot, wave, metric, prior_extreme)

    def _ingest_metric_history(
        self,
        snapshot: PrimitiveSnapshot,
        metric: FuturesMetric | None,
    ) -> None:
        if metric is None:
            return
        if self._last_metric is not None and self._last_metric.open_interest > 0.0:
            change = (metric.open_interest - self._last_metric.open_interest) / self._last_metric.open_interest
            if change > 0.0:
                self._increase_history.append((snapshot.index, change))
            elif change < 0.0:
                self._drop_history.append((snapshot.index, -change))
        self._last_metric = metric
        self._prune(snapshot.index)

    def observe(
        self,
        snapshot: PrimitiveSnapshot,
        *,
        allow_new: bool = True,
    ) -> ScenarioStep:
        self._bars.append(snapshot)
        metric = self._metrics.get(snapshot.observation.ts_ns)
        transitions: list[ScenarioTransition] = []
        signal = None
        if self._wave is not None:
            step = self._advance_wave(snapshot, metric)
            transitions.extend(step.transitions)
            signal = step.signal
        elif (
            allow_new
            and snapshot.ready
            and snapshot.index > self._cooldown_until
            and metric is not None
        ):
            transition = self._maybe_start_wave(snapshot, metric)
            if transition is not None:
                transitions.append(transition)
        self._ingest_metric_history(snapshot, metric)
        return ScenarioStep(transitions=tuple(transitions), signal=signal)

    def abort_active(
        self,
        snapshot: PrimitiveSnapshot,
        reason: str,
    ) -> ScenarioStep:
        if self._wave is None:
            return ScenarioStep()
        transition = self._transition(
            self._wave,
            self._wave.state,
            "RESET",
            reason,
            snapshot.observation.close,
        )
        self._wave = None
        return ScenarioStep(transitions=(transition,))
