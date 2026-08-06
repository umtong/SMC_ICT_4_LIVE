"""Sequential effective-impact acceptance before hierarchical continuation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from adaptive_fresh_hierarchical_engine import AdaptiveFreshHierarchicalEngine
from hierarchical_sweep_engine import _AuctionBar, _Bias, _SweepEpisode
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition
from surprise_impact_hierarchical_engine import SurpriseImpactHierarchicalEngine


@dataclass(slots=True)
class _PersistenceCandidate:
    sequence_id: str
    direction: str
    first_end_ts_ns: int
    first_index: int
    first_open: float
    first_high: float
    first_low: float
    first_close: float
    first_assessment: dict[str, Any]


class SequentialImpactPersistenceRelayEngine(SurpriseImpactHierarchicalEngine):
    """Create direction only after effective impact persists across auctions.

    A single breakout can be temporary price discovery, inventory transfer or a
    liquidity vacuum.  When sequencing is enabled, the first completed HTF
    breakout only creates a pending state.  The immediately following completed
    HTF auction must independently satisfy the same structural acceptance in the
    same direction, which requires a new close beyond the first auction's
    extreme through the inherited prior-range rule.  When impact classification
    is enabled, both auctions must also convert direction-aligned residual flow
    into at least prior-median displacement efficiency.

    Only after that sequence is complete does the inherited confirmed 5-minute
    swing/equal-liquidity sweep and separate one-minute response become tradable.
    """

    def __init__(self, params: Mapping[str, Any]):
        super().__init__(params)
        self._persistence_candidate: _PersistenceCandidate | None = None
        self._sipr_by_context: dict[str, dict[str, Any]] = {}
        self._sipr_sequence = 0

    def _sequence_enabled(self) -> bool:
        return bool(self.params.get("sipr_use_sequential_acceptance", True))

    def _sipr_impact_enabled(self) -> bool:
        return bool(self.params.get("sipr_use_impact_efficiency", True))

    @staticmethod
    def _sequence_transition(
        *,
        scenario_id: str,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=scenario_id,
            event_type="SIPR_SEQUENCE_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )

    def _impact_contract(
        self,
        bar: _AuctionBar,
        direction: str,
    ) -> tuple[bool, dict[str, Any]]:
        if not self._sipr_impact_enabled():
            return True, {
                "enabled": False,
                "ready": True,
                "passed": True,
                "classification": "IMPACT_EFFICIENCY_ABLATED",
                "direction": direction,
            }
        assessment = self._assess_surprise_impact(bar, direction)
        self._last_siar_assessment = assessment
        details = {"enabled": True, "direction": direction, **assessment.details()}
        return bool(assessment.ready and assessment.passed), details

    def _clear_context(self, context_id: str | None) -> None:
        super()._clear_context(context_id)
        if context_id is not None:
            self._sipr_by_context.pop(context_id, None)

    def _suspend_active_context(
        self,
        *,
        reference_price: float,
        pending_direction: str,
    ) -> list[ScenarioTransition]:
        transitions: list[ScenarioTransition] = []
        if self._sweep is not None:
            transitions.append(
                self._sweep_transition(
                    self._sweep,
                    self._sweep.state,
                    "RESET",
                    "FIRST_PERSISTENCE_AUCTION_SUSPENDS_ACTIVE_SWEEP",
                    reference_price,
                    {"pending_direction": pending_direction},
                ),
            )
            self._sweep = None
        if self._bias is not None:
            context_id = self._bias.context_id
            transitions.append(
                self._bias_transition(
                    self._bias,
                    "BIAS_ACTIVE",
                    "RESET",
                    "FIRST_PERSISTENCE_AUCTION_SUSPENDS_ACTIVE_CONTEXT",
                    reference_price,
                    {"pending_direction": pending_direction},
                ),
            )
            self._bias = None
            self._clear_context(context_id)
        return transitions

    def _reset_candidate(
        self,
        *,
        reason: str,
        reference_price: float,
        details: Mapping[str, Any],
    ) -> ScenarioTransition | None:
        candidate = self._persistence_candidate
        if candidate is None:
            return None
        transition = self._sequence_transition(
            scenario_id=candidate.sequence_id,
            previous_state="FIRST_ACCEPTANCE",
            next_state="RESET",
            reason=reason,
            reference_price=reference_price,
            details={
                "first_direction": candidate.direction,
                "first_end_ts_ns": candidate.first_end_ts_ns,
                **dict(details),
            },
        )
        self._persistence_candidate = None
        return transition

    def _start_candidate(
        self,
        *,
        bar: _AuctionBar,
        snapshot: PrimitiveSnapshot,
        direction: str,
        assessment: Mapping[str, Any],
    ) -> list[ScenarioTransition]:
        transitions = self._suspend_active_context(
            reference_price=bar.close,
            pending_direction=direction,
        )
        self._sipr_sequence += 1
        candidate = _PersistenceCandidate(
            sequence_id=f"SIPR-SEQUENCE-{bar.end_ts_ns}-{self._sipr_sequence:06d}",
            direction=direction,
            first_end_ts_ns=bar.end_ts_ns,
            first_index=snapshot.index,
            first_open=bar.open,
            first_high=bar.high,
            first_low=bar.low,
            first_close=bar.close,
            first_assessment=dict(assessment),
        )
        self._persistence_candidate = candidate
        transitions.append(
            self._sequence_transition(
                scenario_id=candidate.sequence_id,
                previous_state="IDLE",
                next_state="FIRST_ACCEPTANCE",
                reason="FIRST_EFFECTIVE_AUCTION_ACCEPTED_PENDING_PERSISTENCE",
                reference_price=bar.close,
                details={
                    "direction": direction,
                    "first_end_ts_ns": bar.end_ts_ns,
                    "first_open": bar.open,
                    "first_high": bar.high,
                    "first_low": bar.low,
                    "first_close": bar.close,
                    "impact_contract": dict(assessment),
                },
            ),
        )
        return transitions

    def _activate_parent_bias(
        self,
        *,
        bar: _AuctionBar,
        snapshot: PrimitiveSnapshot,
        contract: Mapping[str, Any],
    ) -> tuple[ScenarioTransition, ...]:
        previous_context = self._bias.context_id if self._bias is not None else None
        transitions = AdaptiveFreshHierarchicalEngine._evaluate_completed_bias(
            self,
            bar,
            snapshot,
        )
        bias = self._bias
        if bias is not None and bias.context_id != previous_context:
            self._sipr_by_context = {bias.context_id: dict(contract)}
        return transitions

    def _assessment_rejection(
        self,
        *,
        bar: _AuctionBar,
        direction: str,
        assessment: Mapping[str, Any],
        stage: str,
    ) -> ScenarioTransition:
        return self._sequence_transition(
            scenario_id=f"SIPR-ASSESSMENT-{bar.end_ts_ns}-{stage}",
            previous_state="IDLE",
            next_state="RESET",
            reason=str(assessment.get("classification", "IMPACT_ACCEPTANCE_REJECTED")),
            reference_price=bar.close,
            details={
                "direction": direction,
                "stage": stage,
                "impact_contract": dict(assessment),
            },
        )

    def _evaluate_completed_bias(
        self,
        bar: _AuctionBar,
        snapshot: PrimitiveSnapshot,
    ) -> tuple[ScenarioTransition, ...]:
        direction = AdaptiveFreshHierarchicalEngine._baseline_acceptance_direction(self, bar)
        transitions: list[ScenarioTransition] = []
        pending = self._persistence_candidate

        if pending is not None:
            if direction == pending.direction:
                accepted, second_assessment = self._impact_contract(bar, direction)
                if accepted:
                    confirmed = self._reset_candidate(
                        reason="CONSECUTIVE_EFFECTIVE_AUCTIONS_CONFIRMED",
                        reference_price=bar.close,
                        details={
                            "second_end_ts_ns": bar.end_ts_ns,
                            "second_close": bar.close,
                            "second_impact_contract": second_assessment,
                        },
                    )
                    if confirmed is not None:
                        transitions.append(confirmed)
                    contract = {
                        "sequential_acceptance": True,
                        "impact_efficiency": self._sipr_impact_enabled(),
                        "direction": direction,
                        "first_end_ts_ns": pending.first_end_ts_ns,
                        "first_open": pending.first_open,
                        "first_high": pending.first_high,
                        "first_low": pending.first_low,
                        "first_close": pending.first_close,
                        "first_impact_contract": pending.first_assessment,
                        "second_end_ts_ns": bar.end_ts_ns,
                        "second_open": bar.open,
                        "second_high": bar.high,
                        "second_low": bar.low,
                        "second_close": bar.close,
                        "second_impact_contract": second_assessment,
                    }
                    transitions.extend(
                        self._activate_parent_bias(
                            bar=bar,
                            snapshot=snapshot,
                            contract=contract,
                        ),
                    )
                    return tuple(transitions)

                reset = self._reset_candidate(
                    reason="SECOND_AUCTION_FAILED_EFFECTIVE_IMPACT",
                    reference_price=bar.close,
                    details={
                        "second_end_ts_ns": bar.end_ts_ns,
                        "second_impact_contract": second_assessment,
                    },
                )
                if reset is not None:
                    transitions.append(reset)
                transitions.append(
                    self._assessment_rejection(
                        bar=bar,
                        direction=direction,
                        assessment=second_assessment,
                        stage="SECOND",
                    ),
                )
                return tuple(transitions)

            reset = self._reset_candidate(
                reason="NEXT_AUCTION_DID_NOT_PERSIST_IN_FIRST_DIRECTION",
                reference_price=bar.close,
                details={
                    "second_end_ts_ns": bar.end_ts_ns,
                    "observed_direction": direction,
                },
            )
            if reset is not None:
                transitions.append(reset)

        if direction is None:
            return tuple(transitions)

        accepted, assessment = self._impact_contract(bar, direction)
        if not accepted:
            transitions.append(
                self._assessment_rejection(
                    bar=bar,
                    direction=direction,
                    assessment=assessment,
                    stage="FIRST",
                ),
            )
            return tuple(transitions)

        if self._sequence_enabled():
            transitions.extend(
                self._start_candidate(
                    bar=bar,
                    snapshot=snapshot,
                    direction=direction,
                    assessment=assessment,
                ),
            )
            return tuple(transitions)

        contract = {
            "sequential_acceptance": False,
            "impact_efficiency": self._sipr_impact_enabled(),
            "direction": direction,
            "single_end_ts_ns": bar.end_ts_ns,
            "single_open": bar.open,
            "single_high": bar.high,
            "single_low": bar.low,
            "single_close": bar.close,
            "single_impact_contract": assessment,
        }
        transitions.extend(
            self._activate_parent_bias(
                bar=bar,
                snapshot=snapshot,
                contract=contract,
            ),
        )
        return tuple(transitions)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        transitions = list(super().abort_active(snapshot, reason).transitions)
        candidate = self._persistence_candidate
        if candidate is not None:
            transitions.append(
                self._sequence_transition(
                    scenario_id=candidate.sequence_id,
                    previous_state="FIRST_ACCEPTANCE",
                    next_state="RESET",
                    reason=reason,
                    reference_price=snapshot.observation.close,
                    details={"aborted": True},
                ),
            )
            self._persistence_candidate = None
        return ScenarioStep(transitions=tuple(transitions))

    def _emit(self, snapshot: PrimitiveSnapshot, bias: _Bias, sweep: _SweepEpisode) -> ScenarioStep:
        contract = self._sipr_by_context.get(bias.context_id, {})
        step = AdaptiveFreshHierarchicalEngine._emit(self, snapshot, bias, sweep)
        if step.signal is None:
            return step
        details = {
            **dict(step.signal.details),
            "sequential_impact_persistence_contract": contract,
            "sipr_ablation_contract": {
                "sequential_acceptance": self._sequence_enabled(),
                "impact_efficiency": self._sipr_impact_enabled(),
                "completed_close_freshness": self._freshness_enabled(),
                "sweep_flow": self._stage_flag("hff_use_sweep_flow"),
                "response_flow": self._stage_flag("hff_use_response_flow"),
            },
        }
        signal: ScenarioSignal = replace(step.signal, family="SIPR", details=details)
        return ScenarioStep(transitions=step.transitions, signal=signal)
