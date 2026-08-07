"""Adaptive signed-flow/price-impact bifurcation for candidate-06.

The engine does not trade an aggressive-flow shock by itself.  It first
normalizes completed Binance USD-M aggTrade flow against a prior-only robust
baseline, then asks whether the shock moved price efficiently or was absorbed.
A different completed minute must confirm either continuation or reversal.

This is a market-mechanism state machine, not a candle-pattern lookup:

* efficient surprise + directional follow-through -> continue toward the next
  unresolved liquidity objective or a measured shock-range projection;
* extreme surprise + weak/opposite impact + opposite response -> revert toward
  prior-side liquidity.

The initiating minute can never emit an entry.  All baselines exclude the
current decision minute.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable, Mapping

from agg_trade_profile_data import AggMinuteStat
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass(slots=True)
class _FlowShockEpisode:
    scenario_id: str
    branch: str  # CONTINUATION or REVERSAL
    direction: str  # intended trade direction
    shock_flow_direction: str  # BUY or SELL
    state: str
    started_index: int
    started_ts_ns: int
    shock_open: float
    shock_high: float
    shock_low: float
    shock_close: float
    shock_midpoint: float
    shock_range: float
    flow_ratio: float
    flow_z: float
    volume_ratio: float
    trade_ratio: float
    signed_impact_atr: float


class AdaptiveFlowImpactBifurcationEngine:
    """Classify aggressive-flow shocks by realized impact before trading."""

    def __init__(
        self,
        params: Mapping[str, Any],
        *,
        minute_stats: Mapping[int, AggMinuteStat],
    ) -> None:
        self.params = dict(params)
        self._minute_stats = dict(minute_stats)
        self._flow_history: list[float] = []
        self._volume_history: list[float] = []
        self._trade_history: list[float] = []
        self._episode: _FlowShockEpisode | None = None
        self._sequence = 0
        self._cooldown_until = -1

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        minute = self._minute_stats.get(snapshot.observation.ts_ns)
        if minute is None:
            raise RuntimeError(
                "missing completed aggTrade minute context for "
                f"ts_ns={snapshot.observation.ts_ns}",
            )

        flow_score = self._flow_score(minute.flow_ratio)
        volume_ratio = self._ratio_to_prior_median(
            minute.total_volume,
            self._volume_history,
        )
        trade_ratio = self._ratio_to_prior_median(
            float(minute.trades),
            self._trade_history,
        )

        transitions: list[ScenarioTransition] = []
        signal: ScenarioSignal | None = None
        if self._episode is not None:
            advanced = self._advance_episode(
                snapshot,
                flow_score=flow_score,
                allow_new=allow_new,
            )
            transitions.extend(advanced.transitions)
            signal = advanced.signal

        if (
            signal is None
            and self._episode is None
            and allow_new
            and snapshot.index >= self._cooldown_until
        ):
            started = self._maybe_start_shock(
                snapshot,
                minute,
                flow_score=flow_score,
                volume_ratio=volume_ratio,
                trade_ratio=trade_ratio,
            )
            if started is not None:
                transitions.append(started)

        # Prior-only contract: the current completed minute becomes baseline
        # information only after all decisions for this timestamp are finished.
        self._flow_history.append(minute.flow_ratio)
        self._volume_history.append(minute.total_volume)
        self._trade_history.append(float(minute.trades))
        maximum = max(
            int(self.params.get("afib_flow_history", 180)),
            int(self.params.get("afib_activity_history", 120)),
        ) + 8
        if len(self._flow_history) > maximum:
            self._flow_history = self._flow_history[-maximum:]
            self._volume_history = self._volume_history[-maximum:]
            self._trade_history = self._trade_history[-maximum:]
        return ScenarioStep(transitions=tuple(transitions), signal=signal)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        if self._episode is None:
            return ScenarioStep()
        transition = self._transition(
            self._episode,
            self._episode.state,
            "RESET",
            reason,
            snapshot.observation.close,
            {"aborted": True},
        )
        self._episode = None
        return ScenarioStep(transitions=(transition,))

    def _flow_score(self, current: float) -> float | None:
        minimum = int(self.params.get("afib_minimum_history", 90))
        lookback = int(self.params.get("afib_flow_history", 180))
        history = self._flow_history[-lookback:]
        if len(history) < minimum:
            return None
        if not bool(self.params.get("afib_use_robust_surprise", True)):
            threshold = float(self.params.get("afib_raw_flow_ratio_threshold", 0.08))
            if threshold <= 0.0:
                raise ValueError("afib_raw_flow_ratio_threshold must be positive")
            return current / threshold
        center = median(history)
        deviations = [abs(value - center) for value in history]
        scale = 1.4826 * median(deviations)
        floor = float(self.params.get("afib_flow_scale_floor", 0.005))
        return (current - center) / max(scale, floor)

    def _ratio_to_prior_median(self, current: float, history: Iterable[float]) -> float | None:
        minimum = int(self.params.get("afib_minimum_history", 90))
        lookback = int(self.params.get("afib_activity_history", 120))
        prior = list(history)[-lookback:]
        if len(prior) < minimum:
            return None
        baseline = median(prior)
        return current / baseline if baseline > 0.0 else None

    def _maybe_start_shock(
        self,
        snapshot: PrimitiveSnapshot,
        minute: AggMinuteStat,
        *,
        flow_score: float | None,
        volume_ratio: float | None,
        trade_ratio: float | None,
    ) -> ScenarioTransition | None:
        if (
            not snapshot.ready
            or snapshot.atr <= 0.0
            or flow_score is None
            or volume_ratio is None
            or trade_ratio is None
        ):
            return None
        threshold = (
            float(self.params.get("afib_flow_z_threshold", 2.0))
            if bool(self.params.get("afib_use_robust_surprise", True))
            else 1.0
        )
        if abs(flow_score) < threshold:
            return None
        if volume_ratio < float(self.params.get("afib_min_volume_ratio", 1.10)):
            return None
        if trade_ratio < float(self.params.get("afib_min_trade_ratio", 0.95)):
            return None
        if snapshot.range_atr < float(self.params.get("afib_min_range_atr", 0.35)):
            return None

        obs = snapshot.observation
        flow_sign = 1.0 if flow_score > 0.0 else -1.0
        flow_direction = "BUY" if flow_sign > 0.0 else "SELL"
        signed_impact_atr = flow_sign * (obs.close - obs.open) / snapshot.atr
        favorable_close_location = (
            snapshot.close_location if flow_sign > 0.0 else 1.0 - snapshot.close_location
        )
        adverse_wick = (
            snapshot.upper_wick_fraction if flow_sign > 0.0 else snapshot.lower_wick_fraction
        )

        continuation = (
            bool(self.params.get("afib_enable_continuation", True))
            and signed_impact_atr
            >= float(self.params.get("afib_continuation_impact_atr", 0.18))
            and snapshot.body_atr
            >= float(self.params.get("afib_continuation_body_atr", 0.18))
            and favorable_close_location
            >= float(self.params.get("afib_continuation_close_location", 0.68))
        )
        reversal = (
            bool(self.params.get("afib_enable_reversal", True))
            and signed_impact_atr
            <= float(self.params.get("afib_absorption_impact_atr", 0.08))
            and (
                adverse_wick
                >= float(self.params.get("afib_absorption_wick_fraction", 0.25))
                or favorable_close_location
                <= float(self.params.get("afib_absorption_close_location_ceiling", 0.58))
                or signed_impact_atr <= 0.0
            )
        )

        self._sequence += 1
        scenario_id = f"AFIB-{obs.ts_ns}-{self._sequence:06d}"
        if not continuation and not reversal:
            return ScenarioTransition(
                scenario_id=scenario_id,
                event_type="AFIB_SHOCK_TRANSITION",
                previous_state="IDLE",
                next_state="RESET",
                reason_code="FLOW_SURPRISE_IMPACT_NOT_CLASSIFIED",
                reference_price=obs.close,
                details={
                    "flow_direction": flow_direction,
                    "flow_ratio": minute.flow_ratio,
                    "flow_score": flow_score,
                    "volume_ratio": volume_ratio,
                    "trade_ratio": trade_ratio,
                    "signed_impact_atr": signed_impact_atr,
                    "favorable_close_location": favorable_close_location,
                    "adverse_wick_fraction": adverse_wick,
                },
            )

        branch = "CONTINUATION" if continuation else "REVERSAL"
        if branch == "CONTINUATION":
            direction = "LONG" if flow_sign > 0.0 else "SHORT"
            reason = "SURPRISING_AGGRESSION_MOVED_PRICE_EFFICIENTLY"
        else:
            direction = "SHORT" if flow_sign > 0.0 else "LONG"
            reason = "SURPRISING_AGGRESSION_WAS_ABSORBED"
        self._episode = _FlowShockEpisode(
            scenario_id=scenario_id,
            branch=branch,
            direction=direction,
            shock_flow_direction=flow_direction,
            state="FLOW_SHOCK_CLASSIFIED",
            started_index=snapshot.index,
            started_ts_ns=obs.ts_ns,
            shock_open=obs.open,
            shock_high=obs.high,
            shock_low=obs.low,
            shock_close=obs.close,
            shock_midpoint=(obs.high + obs.low) / 2.0,
            shock_range=max(obs.high - obs.low, snapshot.atr * 0.05),
            flow_ratio=minute.flow_ratio,
            flow_z=flow_score,
            volume_ratio=volume_ratio,
            trade_ratio=trade_ratio,
            signed_impact_atr=signed_impact_atr,
        )
        return self._transition(
            self._episode,
            "IDLE",
            "FLOW_SHOCK_CLASSIFIED",
            reason,
            obs.close,
            {
                "branch": branch,
                "trade_direction": direction,
                "shock_flow_direction": flow_direction,
                "flow_ratio": minute.flow_ratio,
                "flow_score": flow_score,
                "volume_ratio": volume_ratio,
                "trade_ratio": trade_ratio,
                "signed_impact_atr": signed_impact_atr,
                "favorable_close_location": favorable_close_location,
                "adverse_wick_fraction": adverse_wick,
                "shock_midpoint": (obs.high + obs.low) / 2.0,
            },
        )

    def _advance_episode(
        self,
        snapshot: PrimitiveSnapshot,
        *,
        flow_score: float | None,
        allow_new: bool,
    ) -> ScenarioStep:
        episode = self._episode
        if episode is None or snapshot.index <= episode.started_index:
            return ScenarioStep()
        obs = snapshot.observation
        elapsed = snapshot.index - episode.started_index
        if elapsed > int(self.params.get("afib_confirmation_bars", 3)):
            return self._reset(snapshot, "FLOW_IMPACT_RESPONSE_EXPIRED", {"elapsed_bars": elapsed})
        if flow_score is None:
            return ScenarioStep()

        tolerance = float(self.params.get("afib_midpoint_tolerance_atr", 0.03)) * snapshot.atr
        body_floor = float(self.params.get("afib_confirmation_body_atr", 0.10))
        flow_floor = float(self.params.get("afib_confirmation_flow_z", 0.35))
        location = float(self.params.get("afib_confirmation_close_location", 0.58))

        if episode.branch == "CONTINUATION":
            if episode.direction == "LONG":
                if obs.close < episode.shock_midpoint - tolerance:
                    return self._reset(snapshot, "EFFICIENT_BUY_SHOCK_LOST_MIDPOINT", {})
                confirmed = (
                    obs.close > episode.shock_high
                    and obs.close > obs.open
                    and snapshot.body_atr >= body_floor
                    and flow_score >= flow_floor
                    and snapshot.close_location >= location
                )
            else:
                if obs.close > episode.shock_midpoint + tolerance:
                    return self._reset(snapshot, "EFFICIENT_SELL_SHOCK_LOST_MIDPOINT", {})
                confirmed = (
                    obs.close < episode.shock_low
                    and obs.close < obs.open
                    and snapshot.body_atr >= body_floor
                    and flow_score <= -flow_floor
                    and snapshot.close_location <= 1.0 - location
                )
        else:
            if episode.direction == "SHORT":
                if obs.close > episode.shock_high + tolerance:
                    return self._reset(snapshot, "ABSORBED_BUY_SHOCK_RESUMED_HIGHER", {})
                confirmed = (
                    obs.close < episode.shock_midpoint
                    and obs.close < obs.open
                    and snapshot.body_atr >= body_floor
                    and flow_score <= -flow_floor
                    and snapshot.close_location <= 1.0 - location
                )
            else:
                if obs.close < episode.shock_low - tolerance:
                    return self._reset(snapshot, "ABSORBED_SELL_SHOCK_RESUMED_LOWER", {})
                confirmed = (
                    obs.close > episode.shock_midpoint
                    and obs.close > obs.open
                    and snapshot.body_atr >= body_floor
                    and flow_score >= flow_floor
                    and snapshot.close_location >= location
                )

        if not confirmed:
            return ScenarioStep()
        if not allow_new:
            return self._reset(snapshot, "ENTRY_SLOT_UNAVAILABLE_AT_FLOW_RESPONSE", {})
        return self._emit(snapshot, flow_score)

    def _emit(self, snapshot: PrimitiveSnapshot, flow_score: float) -> ScenarioStep:
        episode = self._episode
        if episode is None:
            return ScenarioStep()
        obs = snapshot.observation
        buffer_value = float(self.params.get("afib_stop_buffer_atr", 0.05)) * snapshot.atr
        entry = obs.close
        if episode.branch == "CONTINUATION":
            if episode.direction == "LONG":
                stop = episode.shock_midpoint - buffer_value
                measured = episode.shock_high + (
                    float(self.params.get("afib_projection_fraction", 1.0)) * episode.shock_range
                )
                candidates = [
                    value
                    for value in (snapshot.upper_fast, snapshot.upper_slow, measured)
                    if value is not None and value > entry
                ]
            else:
                stop = episode.shock_midpoint + buffer_value
                measured = episode.shock_low - (
                    float(self.params.get("afib_projection_fraction", 1.0)) * episode.shock_range
                )
                candidates = [
                    value
                    for value in (snapshot.lower_fast, snapshot.lower_slow, measured)
                    if value is not None and value < entry
                ]
            target_reason = "EFFICIENT_FLOW_SHOCK_LIQUIDITY_OR_MEASURED_EXTENSION"
        else:
            if episode.direction == "SHORT":
                stop = episode.shock_high + buffer_value
                candidates = [
                    value
                    for value in (episode.shock_low, snapshot.lower_fast, snapshot.lower_slow)
                    if value is not None and value < entry
                ]
            else:
                stop = episode.shock_low - buffer_value
                candidates = [
                    value
                    for value in (episode.shock_high, snapshot.upper_fast, snapshot.upper_slow)
                    if value is not None and value > entry
                ]
            target_reason = "ABSORBED_FLOW_SHOCK_PRIOR_SIDE_LIQUIDITY"

        risk = abs(entry - stop)
        minimum_rr = float(self.params.get("minimum_structural_rr", 0.80))
        ordered = sorted(candidates, key=lambda value: abs(value - entry))
        target = next(
            (
                value
                for value in ordered
                if risk > 0.0 and abs(value - entry) / risk >= minimum_rr
            ),
            None,
        )
        if target is None or risk <= 0.0:
            return self._reset(
                snapshot,
                "NO_COST_WORTHY_STRUCTURAL_OBJECTIVE_AFTER_FLOW_RESPONSE",
                {
                    "entry": entry,
                    "stop": stop,
                    "candidate_targets": ordered,
                    "minimum_structural_rr": minimum_rr,
                },
            )

        transition = self._transition(
            episode,
            episode.state,
            "ENTRY_ARMED",
            (
                "EFFICIENT_FLOW_IMPACT_FOLLOW_THROUGH_CONFIRMED"
                if episode.branch == "CONTINUATION"
                else "ABSORBED_FLOW_IMPACT_OPPOSITE_RESPONSE_CONFIRMED"
            ),
            entry,
            {
                "branch": episode.branch,
                "direction": episode.direction,
                "confirmation_flow_score": flow_score,
                "entry": entry,
                "stop": stop,
                "target": target,
                "gross_structural_rr": abs(target - entry) / risk,
                "target_reason": target_reason,
            },
        )
        signal = ScenarioSignal(
            scenario_id=episode.scenario_id,
            family=(
                "AFIB_CONTINUATION"
                if episode.branch == "CONTINUATION"
                else "AFIB_REVERSAL"
            ),
            direction=episode.direction,
            observed_ts_ns=obs.ts_ns,
            reference_entry=entry,
            stop_price=stop,
            target_price=target,
            target_reason=target_reason,
            atr=snapshot.atr,
            liquidity_level=episode.shock_midpoint,
            details={
                "branch": episode.branch,
                "shock_started_ts_ns": episode.started_ts_ns,
                "shock_flow_direction": episode.shock_flow_direction,
                "shock_flow_ratio": episode.flow_ratio,
                "shock_flow_score": episode.flow_z,
                "shock_volume_ratio": episode.volume_ratio,
                "shock_trade_ratio": episode.trade_ratio,
                "shock_signed_impact_atr": episode.signed_impact_atr,
                "confirmation_flow_score": flow_score,
                "gross_structural_rr": abs(target - entry) / risk,
            },
        )
        self._episode = None
        self._cooldown_until = snapshot.index + int(self.params.get("afib_cooldown_bars", 2))
        return ScenarioStep(transitions=(transition,), signal=signal)

    def _reset(
        self,
        snapshot: PrimitiveSnapshot,
        reason: str,
        details: Mapping[str, Any],
    ) -> ScenarioStep:
        episode = self._episode
        if episode is None:
            return ScenarioStep()
        transition = self._transition(
            episode,
            episode.state,
            "RESET",
            reason,
            snapshot.observation.close,
            details,
        )
        self._episode = None
        self._cooldown_until = snapshot.index + int(self.params.get("afib_cooldown_bars", 2))
        return ScenarioStep(transitions=(transition,))

    @staticmethod
    def _transition(
        episode: _FlowShockEpisode,
        previous: str,
        next_state: str,
        reason: str,
        reference: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="AFIB_SHOCK_TRANSITION",
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference,
            details=dict(details),
        )
