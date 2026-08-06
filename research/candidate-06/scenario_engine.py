"""Candidate-06 v0.2 state machine: sweep shock and response are separate observations."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from lrb_types import PrimitiveSnapshot, ScenarioStep, ScenarioTransition, SweepPrimitive, _Episode
from primitives import LiquiditySweepDetector
from scenario_arm import ScenarioArmMixin
from scenario_confirm import ScenarioConfirmMixin
from scenario_support import ScenarioSupportMixin


class LiquidityResponseScenarioEngine(ScenarioConfirmMixin, ScenarioArmMixin, ScenarioSupportMixin):
    """Classify only bars *after* a sweep as rejection or acceptance.

    v0.1 intentionally exposed the implementation path quickly, but it classified
    the sweep bar itself as the market response.  That conflated the liquidity
    shock with the reaction to the shock.  v0.2 makes the causal order explicit:

    ``IDLE -> SWEEP_RESPONSE_OBSERVATION -> SRR_CONFIRMED | SAC_OBSERVATION``.
    """

    def __init__(self, params: Mapping[str, Any]):
        self.params = dict(params)
        self.detector = LiquiditySweepDetector(params)
        self._episode: _Episode | None = None
        self._sequence = 0
        self._cooldown_until = -1

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        if not snapshot.ready:
            return ScenarioStep()
        if self._episode is not None:
            return self._advance_episode(snapshot)
        if not allow_new or snapshot.index < self._cooldown_until:
            return ScenarioStep()

        sweeps = self.detector.detect(snapshot)
        if not sweeps:
            return ScenarioStep()
        if len({event.side for event in sweeps}) > 1:
            return self._ambiguous(snapshot, "BOTH_SIDES_SWEPT_IN_ONE_BAR", sweeps)

        sweep = max(sweeps, key=lambda value: value.depth_atr)
        return self._start_pending(snapshot, sweep)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        if self._episode is None:
            return ScenarioStep()
        episode = self._episode
        self._episode = None
        self._cooldown_until = snapshot.index + int(self.params.get("cooldown_bars", 1))
        return ScenarioStep(
            transitions=(
                self._transition(
                    episode,
                    snapshot,
                    next_state="RESET",
                    reason=reason,
                    reference_price=snapshot.observation.close,
                ),
            ),
        )

    def _start_pending(self, snapshot: PrimitiveSnapshot, sweep: SweepPrimitive) -> ScenarioStep:
        episode = self._new_episode(snapshot, sweep, family="PENDING", direction="NONE")
        episode.state = f"{sweep.side}_SWEEP_RESPONSE_OBSERVATION"
        self._episode = episode
        transition = ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="SCENARIO_TRANSITION",
            previous_state="IDLE",
            next_state=episode.state,
            reason_code=f"{sweep.side}_LIQUIDITY_SWEEP_SHOCK_OBSERVED",
            reference_price=sweep.level,
            details=self._snapshot_details(snapshot, sweep),
        )
        return ScenarioStep(transitions=(transition,))

    def _advance_episode(self, snapshot: PrimitiveSnapshot) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        if episode.family == "PENDING":
            return self._classify_response(snapshot, episode)
        return ScenarioConfirmMixin._advance_episode(self, snapshot)

    def _classify_response(self, snapshot: PrimitiveSnapshot, episode: _Episode) -> ScenarioStep:
        elapsed = snapshot.index - episode.started_index
        maximum = int(self.params.get("response_observation_bars", 3))
        if elapsed > maximum:
            return self._pending_ambiguous(snapshot, episode, "SWEEP_RESPONSE_OBSERVATION_EXPIRED")

        observation = snapshot.observation
        tolerance = float(self.params.get("rejection_close_tolerance_atr", 0.05)) * snapshot.atr
        body_floor = float(self.params.get("confirm_body_atr", 0.35))
        flow_floor = float(self.params.get("confirm_flow_ratio", 0.08))
        midpoint_shift = float(self.params.get("confirm_midpoint_atr", 0.05)) * snapshot.atr
        slow_midpoint = (episode.lower_slow_at_start + episode.upper_slow_at_start) / 2.0

        if episode.side == "UPPER":
            rejection = (
                episode.level >= slow_midpoint
                and observation.close <= episode.level + tolerance
                and observation.close < observation.open
                and snapshot.body_atr >= body_floor
                and (
                    snapshot.flow_ratio <= -flow_floor
                    or observation.close <= episode.midpoint - midpoint_shift
                )
            )
            acceptance = (
                observation.close >= episode.level + float(self.params["acceptance_close_atr"]) * snapshot.atr
                and snapshot.body_atr >= float(self.params["acceptance_body_atr"])
                and snapshot.close_location >= float(self.params["acceptance_close_location"])
                and snapshot.flow_ratio >= float(self.params["acceptance_flow_ratio"])
                and snapshot.rel_volume >= float(self.params["min_relative_volume"])
            )
            rejection_direction = "SHORT"
            acceptance_direction = "LONG"
        else:
            rejection = (
                episode.level <= slow_midpoint
                and observation.close >= episode.level - tolerance
                and observation.close > observation.open
                and snapshot.body_atr >= body_floor
                and (
                    snapshot.flow_ratio >= flow_floor
                    or observation.close >= episode.midpoint + midpoint_shift
                )
            )
            acceptance = (
                observation.close <= episode.level - float(self.params["acceptance_close_atr"]) * snapshot.atr
                and snapshot.body_atr >= float(self.params["acceptance_body_atr"])
                and snapshot.close_location <= 1.0 - float(self.params["acceptance_close_location"])
                and snapshot.flow_ratio <= -float(self.params["acceptance_flow_ratio"])
                and snapshot.rel_volume >= float(self.params["min_relative_volume"])
            )
            rejection_direction = "LONG"
            acceptance_direction = "SHORT"

        if rejection and acceptance:
            return self._pending_ambiguous(snapshot, episode, "REJECTION_AND_ACCEPTANCE_CONFLICT")
        if rejection:
            previous = episode.state
            episode.family = "SRR"
            episode.direction = rejection_direction
            episode.state = f"{episode.side}_SRR_RESPONSE_CONFIRMED"
            classification = ScenarioTransition(
                scenario_id=episode.scenario_id,
                event_type="SCENARIO_TRANSITION",
                previous_state=previous,
                next_state=episode.state,
                reason_code="POST_SWEEP_RECLAIM_AND_OPPOSITE_DISPLACEMENT",
                reference_price=observation.close,
                details={
                    "elapsed_bars": elapsed,
                    "flow_ratio": snapshot.flow_ratio,
                    "body_atr": snapshot.body_atr,
                    "close": observation.close,
                    "liquidity_level": episode.level,
                },
            )
            armed = self._arm_reversal(snapshot, episode)
            return ScenarioStep(transitions=(classification, *armed.transitions), signal=armed.signal)
        if acceptance:
            previous = episode.state
            episode.family = "SAC"
            episode.direction = acceptance_direction
            episode.state = f"{episode.side}_SAC_OBSERVATION"
            return ScenarioStep(
                transitions=(
                    ScenarioTransition(
                        scenario_id=episode.scenario_id,
                        event_type="SCENARIO_TRANSITION",
                        previous_state=previous,
                        next_state=episode.state,
                        reason_code="POST_SWEEP_ACCEPTANCE_WITH_FLOW_AND_DISPLACEMENT",
                        reference_price=observation.close,
                        details={
                            "elapsed_bars": elapsed,
                            "flow_ratio": snapshot.flow_ratio,
                            "body_atr": snapshot.body_atr,
                            "close": observation.close,
                            "liquidity_level": episode.level,
                        },
                    ),
                ),
            )
        return ScenarioStep()

    def _pending_ambiguous(self, snapshot: PrimitiveSnapshot, episode: _Episode, reason: str) -> ScenarioStep:
        ambiguous_state = "AMBIGUOUS"
        first = ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="SCENARIO_TRANSITION",
            previous_state=episode.state,
            next_state=ambiguous_state,
            reason_code=reason,
            reference_price=snapshot.observation.close,
            details={
                "elapsed_bars": snapshot.index - episode.started_index,
                "flow_ratio": snapshot.flow_ratio,
                "body_atr": snapshot.body_atr,
            },
        )
        second = ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="SCENARIO_TRANSITION",
            previous_state=ambiguous_state,
            next_state="RESET",
            reason_code="SELECTIVE_ABSTENTION",
            reference_price=snapshot.observation.close,
            details={},
        )
        self._episode = None
        self._cooldown_until = snapshot.index + max(1, int(self.params.get("ambiguous_cooldown_bars", 1)))
        return ScenarioStep(transitions=(first, second))
