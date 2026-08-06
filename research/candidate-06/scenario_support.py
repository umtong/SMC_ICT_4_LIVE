"""Target selection, selective reset, and signal emission for LRB scenarios."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from lrb_types import (
    PrimitiveSnapshot,
    ScenarioSignal,
    ScenarioStep,
    ScenarioTransition,
    SweepPrimitive,
    _Episode,
)


class ScenarioSupportMixin:
    """Shared decision helpers; concrete engines provide params and episode state."""

    def _continuation_target(
        self,
        snapshot: PrimitiveSnapshot,
        episode: _Episode,
        *,
        entry: float | None = None,
        stop: float | None = None,
    ) -> tuple[float, str] | None:
        reference = snapshot.observation.close if entry is None else entry
        if episode.direction == "LONG":
            external = snapshot.upper_slow
            projection = episode.level + (
                episode.level - episode.lower_fast_at_start
            ) * float(self.params["range_projection_fraction"])
            candidates = [(external, "NEXT_EXTERNAL_LIQUIDITY"), (projection, "ACCEPTED_RANGE_PROJECTION")]
        else:
            external = snapshot.lower_slow
            projection = episode.level - (
                episode.upper_fast_at_start - episode.level
            ) * float(self.params["range_projection_fraction"])
            candidates = [(external, "NEXT_EXTERNAL_LIQUIDITY"), (projection, "ACCEPTED_RANGE_PROJECTION")]
        if stop is None:
            valid = [
                (price, reason)
                for price, reason in candidates
                if price is not None and ((episode.direction == "LONG" and price > reference) or (episode.direction == "SHORT" and price < reference))
            ]
            return valid[0] if valid else None
        return self._select_target(direction=episode.direction, entry=reference, stop=stop, candidates=candidates)

    def _select_target(
        self,
        *,
        direction: str,
        entry: float,
        stop: float,
        candidates: Iterable[tuple[float | None, str]],
    ) -> tuple[float, str] | None:
        risk = abs(entry - stop)
        if risk <= 0.0:
            return None
        min_rr = float(self.params["minimum_structural_rr"])
        materialized: list[tuple[float, str]] = []
        for price, reason in candidates:
            if price is None:
                continue
            reward = price - entry if direction == "LONG" else entry - price
            if reward > 0.0:
                materialized.append((price, reason))
        materialized.sort(key=lambda value: abs(value[0] - entry))
        for price, reason in materialized:
            reward = price - entry if direction == "LONG" else entry - price
            if reward / risk >= min_rr:
                return price, reason
        return None

    def _emit_signal(
        self,
        snapshot: PrimitiveSnapshot,
        episode: _Episode,
        stop: float,
        target: float,
        target_reason: str,
        reason: str,
    ) -> ScenarioStep:
        signal = ScenarioSignal(
            scenario_id=episode.scenario_id,
            family=episode.family,
            direction=episode.direction,
            observed_ts_ns=snapshot.observation.ts_ns,
            reference_entry=snapshot.observation.close,
            stop_price=stop,
            target_price=target,
            target_reason=target_reason,
            atr=snapshot.atr,
            liquidity_level=episode.level,
            details={
                "flow_ratio": snapshot.flow_ratio,
                "rel_volume": snapshot.rel_volume,
                "elapsed_bars": snapshot.index - episode.started_index,
                "target_reason": target_reason,
            },
        )
        transition = self._transition(
            episode,
            snapshot,
            next_state="ENTRY_ARMED",
            reason=reason,
            reference_price=snapshot.observation.close,
            extra={
                "direction": episode.direction,
                "family": episode.family,
                "stop_price": stop,
                "target_price": target,
                "target_reason": target_reason,
            },
        )
        self._episode = None
        self._cooldown_until = snapshot.index + int(self.params["cooldown_bars"])
        return ScenarioStep(transitions=(transition,), signal=signal)

    def _reset(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        transition = self._transition(
            episode,
            snapshot,
            next_state="RESET",
            reason=reason,
            reference_price=snapshot.observation.close,
        )
        self._episode = None
        self._cooldown_until = snapshot.index + int(self.params["cooldown_bars"])
        return ScenarioStep(transitions=(transition,))

    def _ambiguous(
        self,
        snapshot: PrimitiveSnapshot,
        reason: str,
        sweeps: Iterable[SweepPrimitive],
    ) -> ScenarioStep:
        self._sequence += 1
        scenario_id = f"LRB-{snapshot.observation.ts_ns}-{self._sequence:06d}"
        details = {
            "sweeps": [
                {
                    "side": value.side,
                    "level": value.level,
                    "depth_atr": value.depth_atr,
                    "pool_touches": value.pool_touches,
                }
                for value in sweeps
            ],
            "flow_ratio": snapshot.flow_ratio,
            "rel_volume": snapshot.rel_volume,
        }
        self._cooldown_until = snapshot.index + max(1, int(self.params["ambiguous_cooldown_bars"]))
        return ScenarioStep(
            transitions=(
                ScenarioTransition(
                    scenario_id=scenario_id,
                    event_type="SCENARIO_TRANSITION",
                    previous_state="IDLE",
                    next_state="AMBIGUOUS",
                    reason_code=reason,
                    reference_price=snapshot.observation.close,
                    details=details,
                ),
                ScenarioTransition(
                    scenario_id=scenario_id,
                    event_type="SCENARIO_TRANSITION",
                    previous_state="AMBIGUOUS",
                    next_state="RESET",
                    reason_code="SELECTIVE_ABSTENTION",
                    reference_price=snapshot.observation.close,
                    details={},
                ),
            ),
        )

    def _new_episode(
        self,
        snapshot: PrimitiveSnapshot,
        sweep: SweepPrimitive,
        *,
        family: str,
        direction: str,
    ) -> _Episode:
        self._sequence += 1
        obs = snapshot.observation
        assert snapshot.lower_fast is not None
        assert snapshot.upper_fast is not None
        assert snapshot.lower_slow is not None
        assert snapshot.upper_slow is not None
        return _Episode(
            scenario_id=f"LRB-{obs.ts_ns}-{self._sequence:06d}",
            family=family,
            direction=direction,
            side=sweep.side,
            state=f"{sweep.side}_{family}_OBSERVATION",
            level=sweep.level,
            extreme=obs.high if sweep.side == "UPPER" else obs.low,
            started_index=snapshot.index,
            started_ts_ns=obs.ts_ns,
            atr_at_start=snapshot.atr,
            flow_at_start=snapshot.flow_ratio,
            rel_volume_at_start=snapshot.rel_volume,
            midpoint=(obs.high + obs.low) / 2.0,
            lower_fast_at_start=snapshot.lower_fast,
            upper_fast_at_start=snapshot.upper_fast,
            lower_slow_at_start=snapshot.lower_slow,
            upper_slow_at_start=snapshot.upper_slow,
        )

    def _transition(
        self,
        episode: _Episode,
        snapshot: PrimitiveSnapshot,
        *,
        next_state: str,
        reason: str,
        reference_price: float | None,
        previous_state: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> ScenarioTransition:
        before = episode.state if previous_state is None else previous_state
        transition = ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="SCENARIO_TRANSITION",
            previous_state=before,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(extra or {}),
        )
        episode.state = next_state
        return transition

    @staticmethod
    def _snapshot_details(snapshot: PrimitiveSnapshot, sweep: SweepPrimitive) -> dict[str, Any]:
        return {
            "side": sweep.side,
            "level": sweep.level,
            "depth_atr": sweep.depth_atr,
            "pool_touches": sweep.pool_touches,
            "external_to_slow_range": sweep.external_to_slow_range,
            "atr": snapshot.atr,
            "flow_ratio": snapshot.flow_ratio,
            "rel_volume": snapshot.rel_volume,
            "body_atr": snapshot.body_atr,
            "range_atr": snapshot.range_atr,
            "upper_wick_fraction": snapshot.upper_wick_fraction,
            "lower_wick_fraction": snapshot.lower_wick_fraction,
            "range_position": snapshot.range_position,
        }
