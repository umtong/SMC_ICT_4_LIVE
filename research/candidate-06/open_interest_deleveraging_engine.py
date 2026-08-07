"""Open-interest deleveraging response bifurcation for candidate-06.

A large completed OI contraction with aligned price and taker flow is treated as
an inventory shock. A later completed response decides whether the shock is
exhausted and reclaims or persists into price discovery.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Mapping

from futures_metrics_data import FuturesMetric
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass(slots=True)
class _Wave:
    scenario_id: str
    side: str
    state: str
    started_index: int
    started_ts_ns: int
    event_drop: float
    drop_threshold: float
    event_open: float
    event_high: float
    event_low: float
    event_close: float
    event_mid: float
    event_range: float
    extreme: float
    atr: float
    event_metric: FuturesMetric | None
    slow_mid: float | None
    upper_fast: float | None
    lower_fast: float | None
    branch: str | None = None
    signal_index: int | None = None


class OpenInterestDeleveragingBifurcationEngine:
    """Classify extreme deleveraging only after a separate completed response."""

    def __init__(
        self,
        params: Mapping[str, Any],
        *,
        metrics: Mapping[int, FuturesMetric],
    ) -> None:
        self.params = dict(params)
        self._metrics = dict(metrics)
        self._last_metric: FuturesMetric | None = None
        self._drop_history: deque[tuple[int, float]] = deque()
        self._bars: deque[PrimitiveSnapshot] = deque(maxlen=5)
        self._wave: _Wave | None = None
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
        wave: _Wave,
        previous: str,
        next_state: str,
        reason: str,
        reference: float | None,
        details: Mapping[str, Any] | None = None,
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=wave.scenario_id,
            event_type="OPEN_INTEREST_DELEVERAGING_TRANSITION",
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference,
            details=dict(details or {}),
        )

    @staticmethod
    def _entry_scenario_id(wave: _Wave) -> str:
        return f"{wave.scenario_id}:ENTRY"

    @classmethod
    def _entry_transition(
        cls,
        wave: _Wave,
        *,
        reason: str,
        reference: float,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=cls._entry_scenario_id(wave),
            event_type="OIDB_ENTRY_TRANSITION",
            previous_state="IDLE",
            next_state="ENTRY_ARMED",
            reason_code=reason,
            reference_price=reference,
            details={
                "context_scenario_id": wave.scenario_id,
                "branch": wave.branch,
                **dict(details),
            },
        )

    def _prune(self, index: int) -> None:
        cutoff = index - int(self.params.get("oidb_history_minutes", 1440))
        while self._drop_history and self._drop_history[0][0] < cutoff:
            self._drop_history.popleft()

    def _drop_threshold(self) -> float | None:
        values = [value for _, value in self._drop_history]
        if len(values) < int(self.params.get("oidb_min_prior_drops", 36)):
            return None
        return self._quantile(values, float(self.params.get("oidb_drop_quantile", 0.85)))

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
        side: str,
        event_drop: float,
        threshold: float,
        metric: FuturesMetric | None,
        reason: str,
    ) -> ScenarioTransition | None:
        bar = self._five_minute_bar()
        if bar is None:
            return None
        open_, high, low, close = bar
        self._sequence += 1
        scenario_id = f"OIDB-{snapshot.observation.ts_ns}-{self._sequence:06d}"
        wave = _Wave(
            scenario_id=scenario_id,
            side=side,
            state="DELEVERAGING_WAVE_OBSERVED",
            started_index=snapshot.index,
            started_ts_ns=snapshot.observation.ts_ns,
            event_drop=event_drop,
            drop_threshold=threshold,
            event_open=open_,
            event_high=high,
            event_low=low,
            event_close=close,
            event_mid=(open_ + close) / 2.0,
            event_range=max(high - low, snapshot.atr * 0.25),
            extreme=low if side == "SELL" else high,
            atr=max(snapshot.atr, 1e-12),
            event_metric=metric,
            slow_mid=snapshot.slow_mid,
            upper_fast=snapshot.upper_fast,
            lower_fast=snapshot.lower_fast,
        )
        self._wave = wave
        return self._transition(
            wave,
            "IDLE",
            "DELEVERAGING_WAVE_OBSERVED",
            reason,
            close,
            {
                "forced_side": side,
                "open_interest_drop_fraction": event_drop,
                "prior_only_drop_threshold": threshold,
                "event_open": open_,
                "event_high": high,
                "event_low": low,
                "event_close": close,
                "metric_taker_ratio": None if metric is None else metric.taker_buy_sell_ratio,
            },
        )

    def _maybe_start_metric_wave(
        self,
        snapshot: PrimitiveSnapshot,
        metric: FuturesMetric,
    ) -> ScenarioTransition | None:
        prior = self._last_metric
        if prior is None or prior.open_interest <= 0.0:
            return None
        change = (metric.open_interest - prior.open_interest) / prior.open_interest
        if change >= 0.0:
            return None
        drop = -change
        threshold = self._drop_threshold()
        if threshold is None or drop < threshold:
            return None
        bar = self._five_minute_bar()
        if bar is None:
            return None
        open_, _, _, close = bar
        move_atr = (close - open_) / max(snapshot.atr, 1e-12)
        flow = metric.signed_taker_ratio
        minimum_move = float(self.params.get("oidb_event_move_atr", 0.30))
        flow_floor = float(self.params.get("oidb_metric_flow_floor", 0.06))
        if move_atr <= -minimum_move and flow <= -flow_floor:
            side = "SELL"
        elif move_atr >= minimum_move and flow >= flow_floor:
            side = "BUY"
        else:
            return None
        return self._start_wave(
            snapshot,
            side,
            drop,
            threshold,
            metric,
            "EXTREME_OPEN_INTEREST_CONTRACTION_WITH_ALIGNED_FLOW",
        )

    def _maybe_start_reference_wave(self, snapshot: PrimitiveSnapshot) -> ScenarioTransition | None:
        bar = self._five_minute_bar()
        if bar is None:
            return None
        open_, _, _, close = bar
        move_atr = (close - open_) / max(snapshot.atr, 1e-12)
        floor = float(self.params.get("oidb_event_move_atr", 0.30))
        flow_floor = float(self.params.get("oidb_metric_flow_floor", 0.06))
        metric = self._metrics.get(snapshot.observation.ts_ns)
        flow = snapshot.flow_ratio if metric is None else metric.signed_taker_ratio
        if move_atr <= -floor and flow <= -flow_floor:
            side = "SELL"
        elif move_atr >= floor and flow >= flow_floor:
            side = "BUY"
        else:
            return None
        return self._start_wave(
            snapshot,
            side,
            abs(move_atr),
            abs(move_atr),
            metric,
            "PRICE_FLOW_SHOCK_WITHOUT_OPEN_INTEREST_ABLATION",
        )

    def _target_reversal(self, wave: _Wave, direction: str, entry: float, stop: float) -> tuple[float, str] | None:
        if direction == "LONG":
            raw = [
                (wave.event_open, "DELEVERAGING_IMPULSE_ORIGIN"),
                (wave.slow_mid, "PRE_SHOCK_DEALING_RANGE_EQUILIBRIUM"),
                (wave.upper_fast, "PRE_SHOCK_FAST_RANGE_LIQUIDITY"),
            ]
            candidates = sorted((float(level), reason) for level, reason in raw if level is not None and float(level) > entry)
        else:
            raw = [
                (wave.event_open, "DELEVERAGING_IMPULSE_ORIGIN"),
                (wave.slow_mid, "PRE_SHOCK_DEALING_RANGE_EQUILIBRIUM"),
                (wave.lower_fast, "PRE_SHOCK_FAST_RANGE_LIQUIDITY"),
            ]
            candidates = sorted(
                ((float(level), reason) for level, reason in raw if level is not None and float(level) < entry),
                reverse=True,
            )
        risk = abs(entry - stop)
        minimum = float(self.params.get("minimum_structural_rr", 0.75))
        for target, reason in candidates:
            if risk > 0.0 and abs(target - entry) / risk >= minimum:
                return target, reason
        return None

    def _signal_reversal(self, snapshot: PrimitiveSnapshot, wave: _Wave) -> ScenarioStep:
        obs = snapshot.observation
        direction = "LONG" if wave.side == "SELL" else "SHORT"
        buffer = float(self.params.get("oidb_stop_buffer_atr", 0.08)) * wave.atr
        stop = wave.extreme - buffer if direction == "LONG" else wave.extreme + buffer
        target = self._target_reversal(wave, direction, obs.close, stop)
        if target is None:
            transition = self._transition(
                wave,
                wave.state,
                "RESET",
                "NO_DELEVERAGING_REVERSAL_OBJECTIVE_WITH_SUFFICIENT_SPACE",
                obs.close,
            )
            self._wave = None
            return ScenarioStep(transitions=(transition,))
        wave.state = "EXHAUSTION_REVERSAL_SIGNALLED"
        wave.branch = "REVERSAL"
        wave.signal_index = snapshot.index
        context_transition = self._transition(
            wave,
            "DELEVERAGING_WAVE_OBSERVED",
            wave.state,
            "DELEVERAGING_EXHAUSTION_AND_OPPOSITE_RECLAIM_CONFIRMED",
            obs.close,
            {"stop": stop, "target": target[0], "target_reason": target[1]},
        )
        entry_transition = self._entry_transition(
            wave,
            reason="DELEVERAGING_EXHAUSTION_ENTRY_ARMED",
            reference=obs.close,
            details={"stop": stop, "target": target[0], "target_reason": target[1]},
        )
        signal = ScenarioSignal(
            scenario_id=self._entry_scenario_id(wave),
            family="OIDB_R",
            direction=direction,
            observed_ts_ns=obs.ts_ns,
            reference_entry=obs.close,
            stop_price=stop,
            target_price=target[0],
            target_reason=target[1],
            atr=wave.atr,
            liquidity_level=wave.extreme,
            details={
                "open_interest_drop_fraction": wave.event_drop,
                "prior_only_drop_threshold": wave.drop_threshold,
                "causal_exit_reason_codes": ("DELEVERAGING_REVERSAL_THESIS_INVALIDATED",),
                "causal_exit_open_position": True,
            },
        )
        return ScenarioStep(transitions=(context_transition, entry_transition), signal=signal)

    def _signal_continuation(self, snapshot: PrimitiveSnapshot, wave: _Wave) -> ScenarioStep:
        obs = snapshot.observation
        direction = "SHORT" if wave.side == "SELL" else "LONG"
        buffer = float(self.params.get("oidb_stop_buffer_atr", 0.08)) * wave.atr
        stop = wave.event_mid + buffer if direction == "SHORT" else wave.event_mid - buffer
        distance = max(wave.event_range, wave.atr) * float(self.params.get("oidb_projection_fraction", 1.0))
        target = obs.close - distance if direction == "SHORT" else obs.close + distance
        risk = abs(obs.close - stop)
        if risk <= 0.0 or distance / risk < float(self.params.get("minimum_structural_rr", 0.75)):
            transition = self._transition(
                wave,
                wave.state,
                "RESET",
                "NO_DELEVERAGING_CONTINUATION_OBJECTIVE_WITH_SUFFICIENT_SPACE",
                obs.close,
            )
            self._wave = None
            return ScenarioStep(transitions=(transition,))
        wave.state = "PERSISTENCE_CONTINUATION_SIGNALLED"
        wave.branch = "CONTINUATION"
        wave.signal_index = snapshot.index
        context_transition = self._transition(
            wave,
            "DELEVERAGING_WAVE_OBSERVED",
            wave.state,
            "OPEN_INTEREST_CONTRACTION_PERSISTED_WITH_PRICE_DISCOVERY",
            obs.close,
            {"stop": stop, "target": target},
        )
        entry_transition = self._entry_transition(
            wave,
            reason="DELEVERAGING_PERSISTENCE_ENTRY_ARMED",
            reference=obs.close,
            details={"stop": stop, "target": target},
        )
        signal = ScenarioSignal(
            scenario_id=self._entry_scenario_id(wave),
            family="OIDB_C",
            direction=direction,
            observed_ts_ns=obs.ts_ns,
            reference_entry=obs.close,
            stop_price=stop,
            target_price=target,
            target_reason="DELEVERAGING_RANGE_EXTENSION",
            atr=wave.atr,
            liquidity_level=wave.extreme,
            details={
                "open_interest_drop_fraction": wave.event_drop,
                "prior_only_drop_threshold": wave.drop_threshold,
                "causal_exit_reason_codes": ("DELEVERAGING_CONTINUATION_THESIS_INVALIDATED",),
                "causal_exit_open_position": True,
            },
        )
        return ScenarioStep(transitions=(context_transition, entry_transition), signal=signal)

    def _next_metric_change(self, metric: FuturesMetric | None) -> float | None:
        if metric is None or self._last_metric is None or self._last_metric.open_interest <= 0.0:
            return None
        return (metric.open_interest - self._last_metric.open_interest) / self._last_metric.open_interest

    def _advance_signalled(self, snapshot: PrimitiveSnapshot, wave: _Wave) -> ScenarioStep:
        assert wave.signal_index is not None
        obs = snapshot.observation
        floor = float(self.params.get("oidb_response_flow_ratio", 0.05))
        if wave.branch == "REVERSAL":
            invalid = (
                (wave.side == "SELL" and obs.close < wave.extreme and snapshot.flow_ratio <= -floor)
                or (wave.side == "BUY" and obs.close > wave.extreme and snapshot.flow_ratio >= floor)
            )
            reason = "DELEVERAGING_REVERSAL_THESIS_INVALIDATED"
        else:
            invalid = (
                (wave.side == "SELL" and obs.close > wave.event_mid and snapshot.flow_ratio >= floor)
                or (wave.side == "BUY" and obs.close < wave.event_mid and snapshot.flow_ratio <= -floor)
            )
            reason = "DELEVERAGING_CONTINUATION_THESIS_INVALIDATED"
        if invalid:
            transition = self._transition(wave, wave.state, "RESET", reason, obs.close)
            self._wave = None
            self._cooldown_until = snapshot.index + int(self.params.get("oidb_cooldown_bars", 2))
            return ScenarioStep(transitions=(transition,))
        if snapshot.index - wave.signal_index >= int(self.params.get("oidb_invalidation_observation_bars", 6)):
            transition = self._transition(
                wave,
                wave.state,
                "RESET",
                "DELEVERAGING_POST_SIGNAL_CONTEXT_MATURED",
                obs.close,
            )
            self._wave = None
            self._cooldown_until = snapshot.index + int(self.params.get("oidb_cooldown_bars", 2))
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
            return self._advance_signalled(snapshot, wave)
        if snapshot.index <= wave.started_index:
            return ScenarioStep()
        obs = snapshot.observation
        prior_extreme = wave.extreme
        wave.extreme = min(wave.extreme, obs.low) if wave.side == "SELL" else max(wave.extreme, obs.high)
        bars = snapshot.index - wave.started_index
        if bars > int(self.params.get("oidb_response_bars", 6)):
            transition = self._transition(
                wave,
                wave.state,
                "RESET",
                "DELEVERAGING_RESPONSE_EXPIRED",
                obs.close,
            )
            self._wave = None
            self._cooldown_until = snapshot.index + int(self.params.get("oidb_cooldown_bars", 2))
            return ScenarioStep(transitions=(transition,))

        floor = float(self.params.get("oidb_response_flow_ratio", 0.05))
        reclaim = float(self.params.get("oidb_reclaim_close_location", 0.58))
        if wave.side == "SELL":
            reversal = (
                bool(self.params.get("oidb_enable_reversal", True))
                and obs.close >= wave.event_mid
                and snapshot.flow_ratio >= floor
                and snapshot.close_location >= reclaim
            )
        else:
            reversal = (
                bool(self.params.get("oidb_enable_reversal", True))
                and obs.close <= wave.event_mid
                and snapshot.flow_ratio <= -floor
                and snapshot.close_location <= 1.0 - reclaim
            )
        if reversal:
            return self._signal_reversal(snapshot, wave)

        next_change = self._next_metric_change(metric)
        persistence = (
            next_change is not None
            and next_change <= -wave.event_drop * float(self.params.get("oidb_persistence_fraction", 0.35))
        )
        extension = float(self.params.get("oidb_extension_atr", 0.05)) * wave.atr
        if wave.side == "SELL":
            continuation = (
                bool(self.params.get("oidb_enable_continuation", True))
                and persistence
                and obs.close <= prior_extreme - extension
                and snapshot.flow_ratio <= -floor
                and snapshot.close_location <= 1.0 - reclaim
            )
        else:
            continuation = (
                bool(self.params.get("oidb_enable_continuation", True))
                and persistence
                and obs.close >= prior_extreme + extension
                and snapshot.flow_ratio >= floor
                and snapshot.close_location >= reclaim
            )
        if continuation:
            return self._signal_continuation(snapshot, wave)
        return ScenarioStep()

    def _ingest_metric_history(self, snapshot: PrimitiveSnapshot, metric: FuturesMetric | None) -> None:
        if metric is None:
            return
        if self._last_metric is not None and self._last_metric.open_interest > 0.0:
            change = (metric.open_interest - self._last_metric.open_interest) / self._last_metric.open_interest
            if change < 0.0:
                self._drop_history.append((snapshot.index, -change))
        self._last_metric = metric
        self._prune(snapshot.index)

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool = True) -> ScenarioStep:
        self._bars.append(snapshot)
        metric = self._metrics.get(snapshot.observation.ts_ns)
        transitions: list[ScenarioTransition] = []
        signal = None
        if self._wave is not None:
            step = self._advance_wave(snapshot, metric)
            transitions.extend(step.transitions)
            signal = step.signal
        elif allow_new and snapshot.ready and snapshot.index > self._cooldown_until and metric is not None:
            if bool(self.params.get("oidb_use_open_interest", True)):
                transition = self._maybe_start_metric_wave(snapshot, metric)
            else:
                transition = self._maybe_start_reference_wave(snapshot)
            if transition is not None:
                transitions.append(transition)
        self._ingest_metric_history(snapshot, metric)
        return ScenarioStep(transitions=tuple(transitions), signal=signal)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
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
