"""Hierarchical role graph for source-faithful EasyChart decisions.

Patterns are observations, not votes.  A trade option is executable only when
the causal episode supplies the required decision roles in time order.  Local
counter-direction evidence changes the local phase; it does not silently flip a
higher-scale positioning state.

This module is intentionally independent of price data and execution.  It is a
semantic contract used by concrete market-state engines and by case audits.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable, Mapping


class Direction(int, Enum):
    LONG = 1
    SHORT = -1

    @property
    def opposite(self) -> "Direction":
        return Direction(-int(self))


class Scale(int, Enum):
    EXECUTION = 1
    LOCAL = 2
    CONTEXT = 3
    MACRO = 4


class EvidenceRole(str, Enum):
    DIRECTION = "DIRECTION"
    LOCATION = "LOCATION"
    INTERACTION = "INTERACTION"
    RESPONSE = "RESPONSE"
    ENTRY = "ENTRY"
    INVALIDATION = "INVALIDATION"
    OBJECTIVE = "OBJECTIVE"


class EvidenceKind(str, Enum):
    SWING_STRUCTURE = "SWING_STRUCTURE"
    TRENDLINE = "TRENDLINE"
    CHANNEL = "CHANNEL"
    HORIZONTAL_RANGE = "HORIZONTAL_RANGE"
    ORDER_BLOCK = "ORDER_BLOCK"
    FVG = "FVG"
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"
    IMMEDIATE_FAKEOUT = "IMMEDIATE_FAKEOUT"
    WM_TRAP = "WM_TRAP"
    ACCEPTED_BREAK = "ACCEPTED_BREAK"
    FIRST_RETEST = "FIRST_RETEST"
    DISPLACEMENT = "DISPLACEMENT"
    OPPOSING_STRUCTURE = "OPPOSING_STRUCTURE"
    MACRO_INVALIDATION = "MACRO_INVALIDATION"
    CROSS_MARKET_STATE = "CROSS_MARKET_STATE"


class Lifecycle(str, Enum):
    ACTIVE = "ACTIVE"
    MITIGATED = "MITIGATED"
    CONSUMED = "CONSUMED"
    INVALIDATED = "INVALIDATED"


class PositioningState(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    UNRESOLVED = "UNRESOLVED"


class LocalPhase(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    IMPULSE = "IMPULSE"
    PULLBACK = "PULLBACK"
    OUTSIDE_TEST = "OUTSIDE_TEST"
    RECLAIMED = "RECLAIMED"
    ACCEPTED_BREAK = "ACCEPTED_BREAK"
    CONTINUATION = "CONTINUATION"


class ScenarioFamily(str, Enum):
    ACCEPTED_BREAK_FIRST_RETEST = "ACCEPTED_BREAK_FIRST_RETEST"
    FAILED_BREAK_REVERSAL = "FAILED_BREAK_REVERSAL"
    DOMINANT_CONTEXT_PULLBACK_CONTINUATION = (
        "DOMINANT_CONTEXT_PULLBACK_CONTINUATION"
    )


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    causal_leg_id: str
    kind: EvidenceKind
    roles: frozenset[EvidenceRole]
    direction: Direction | None
    scale: Scale
    event_time_ns: int
    observed_time_ns: int
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    price_low: float | None = None
    price_high: float | None = None
    source_status: str = "SOURCE_EXPLICIT"
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.causal_leg_id:
            raise ValueError("evidence identifiers must be non-empty")
        if self.observed_time_ns < self.event_time_ns:
            raise ValueError("evidence cannot be observed before the event")
        if not self.roles:
            raise ValueError("evidence must perform at least one decision role")
        if (self.price_low is None) != (self.price_high is None):
            raise ValueError("price bounds must both be defined or both be absent")
        if (
            self.price_low is not None
            and self.price_high is not None
            and self.price_high < self.price_low
        ):
            raise ValueError("invalid evidence price interval")

    @property
    def active(self) -> bool:
        return self.lifecycle is Lifecycle.ACTIVE


@dataclass(frozen=True, slots=True)
class PositioningContext:
    state: PositioningState = PositioningState.UNRESOLVED
    scale: Scale = Scale.MACRO
    established_by: str | None = None
    established_time_ns: int | None = None
    invalidation_price: float | None = None
    local_phase: LocalPhase = LocalPhase.UNRESOLVED
    local_phase_by: str | None = None

    @property
    def direction(self) -> Direction | None:
        if self.state is PositioningState.LONG:
            return Direction.LONG
        if self.state is PositioningState.SHORT:
            return Direction.SHORT
        return None

    def establish(
        self,
        direction: Direction,
        *,
        evidence: Evidence,
        invalidation_price: float,
    ) -> "PositioningContext":
        if EvidenceRole.DIRECTION not in evidence.roles:
            raise ValueError("positioning state requires directional evidence")
        if evidence.scale < self.scale:
            raise ValueError("lower-scale evidence cannot establish macro positioning")
        state = (
            PositioningState.LONG
            if direction is Direction.LONG
            else PositioningState.SHORT
        )
        return PositioningContext(
            state=state,
            scale=evidence.scale,
            established_by=evidence.evidence_id,
            established_time_ns=evidence.observed_time_ns,
            invalidation_price=float(invalidation_price),
            local_phase=LocalPhase.IMPULSE,
            local_phase_by=evidence.evidence_id,
        )

    def apply_local(self, evidence: Evidence) -> "PositioningContext":
        """Update local phase without silently changing the macro side.

        A local footprint against the established direction is a pullback
        observation.  A same-direction displacement is continuation.  An
        outside interaction is an outside test.  Only explicit same/higher
        scale invalidation may clear the positioning state.
        """
        if not evidence.active:
            return self
        direction = self.direction
        if evidence.kind is EvidenceKind.MACRO_INVALIDATION:
            if (
                EvidenceRole.DIRECTION not in evidence.roles
                or evidence.scale < self.scale
            ):
                raise ValueError("macro invalidation must be directional and same/higher scale")
            return PositioningContext(
                state=PositioningState.UNRESOLVED,
                scale=self.scale,
                established_by=self.established_by,
                established_time_ns=self.established_time_ns,
                invalidation_price=self.invalidation_price,
                local_phase=LocalPhase.UNRESOLVED,
                local_phase_by=evidence.evidence_id,
            )
        if evidence.kind in {
            EvidenceKind.LIQUIDITY_SWEEP,
            EvidenceKind.IMMEDIATE_FAKEOUT,
            EvidenceKind.WM_TRAP,
        }:
            phase = (
                LocalPhase.RECLAIMED
                if evidence.kind in {
                    EvidenceKind.IMMEDIATE_FAKEOUT,
                    EvidenceKind.WM_TRAP,
                }
                else LocalPhase.OUTSIDE_TEST
            )
        elif evidence.kind is EvidenceKind.ACCEPTED_BREAK:
            phase = LocalPhase.ACCEPTED_BREAK
        elif direction is not None and evidence.direction is direction.opposite:
            phase = LocalPhase.PULLBACK
        elif direction is not None and evidence.direction is direction:
            phase = LocalPhase.CONTINUATION
        else:
            phase = self.local_phase
        return replace(
            self,
            local_phase=phase,
            local_phase_by=evidence.evidence_id,
        )


@dataclass(frozen=True, slots=True)
class OptionSpecification:
    family: ScenarioFamily
    required_roles: frozenset[EvidenceRole]
    require_preexisting_location: bool = True
    require_entry_after_response: bool = True
    require_directional_context: bool = False


DEFAULT_SPECIFICATIONS: Mapping[ScenarioFamily, OptionSpecification] = {
    ScenarioFamily.ACCEPTED_BREAK_FIRST_RETEST: OptionSpecification(
        family=ScenarioFamily.ACCEPTED_BREAK_FIRST_RETEST,
        required_roles=frozenset(
            {
                EvidenceRole.LOCATION,
                EvidenceRole.INTERACTION,
                EvidenceRole.RESPONSE,
                EvidenceRole.ENTRY,
                EvidenceRole.INVALIDATION,
                EvidenceRole.OBJECTIVE,
            }
        ),
    ),
    ScenarioFamily.FAILED_BREAK_REVERSAL: OptionSpecification(
        family=ScenarioFamily.FAILED_BREAK_REVERSAL,
        required_roles=frozenset(
            {
                EvidenceRole.LOCATION,
                EvidenceRole.INTERACTION,
                EvidenceRole.RESPONSE,
                EvidenceRole.ENTRY,
                EvidenceRole.INVALIDATION,
                EvidenceRole.OBJECTIVE,
            }
        ),
    ),
    ScenarioFamily.DOMINANT_CONTEXT_PULLBACK_CONTINUATION: OptionSpecification(
        family=ScenarioFamily.DOMINANT_CONTEXT_PULLBACK_CONTINUATION,
        required_roles=frozenset(
            {
                EvidenceRole.DIRECTION,
                EvidenceRole.LOCATION,
                EvidenceRole.INTERACTION,
                EvidenceRole.RESPONSE,
                EvidenceRole.ENTRY,
                EvidenceRole.INVALIDATION,
                EvidenceRole.OBJECTIVE,
            }
        ),
        require_directional_context=True,
    ),
}


@dataclass(frozen=True, slots=True)
class OptionResolution:
    family: ScenarioFamily
    direction: Direction
    executable: bool
    disposition: str
    role_evidence: Mapping[EvidenceRole, tuple[str, ...]]
    evidence_ids: tuple[str, ...]
    causal_leg_ids: tuple[str, ...]
    observed_time_ns: int | None

    @property
    def covered_roles(self) -> frozenset[EvidenceRole]:
        return frozenset(
            role for role, ids in self.role_evidence.items() if ids
        )


def _active_directional(
    evidence: Iterable[Evidence],
    direction: Direction,
) -> list[Evidence]:
    return [
        item
        for item in evidence
        if item.active
        and (item.direction is None or item.direction is direction)
    ]


def resolve_option(
    *,
    family: ScenarioFamily,
    direction: Direction,
    evidence: Iterable[Evidence],
    context: PositioningContext,
    specification: OptionSpecification | None = None,
) -> OptionResolution:
    """Resolve minimal role completeness without counting confirmations.

    Multiple observations of the same role do not increase a score.  The
    resolution is binary only at the complete *scenario option* level: either
    each required decision role has a causal witness, or the episode remains
    unresolved.  Individual patterns are never labelled effective/ineffective
    here.
    """
    spec = specification or DEFAULT_SPECIFICATIONS[family]
    active = _active_directional(evidence, direction)
    active.sort(key=lambda item: (item.observed_time_ns, item.evidence_id))
    by_role: dict[EvidenceRole, list[Evidence]] = {
        role: [] for role in EvidenceRole
    }
    for item in active:
        for role in item.roles:
            by_role[role].append(item)

    missing = [role for role in spec.required_roles if not by_role[role]]
    if missing:
        return OptionResolution(
            family=family,
            direction=direction,
            executable=False,
            disposition="MISSING_ROLES:" + ",".join(sorted(role.value for role in missing)),
            role_evidence={role: tuple(x.evidence_id for x in items) for role, items in by_role.items()},
            evidence_ids=tuple(item.evidence_id for item in active),
            causal_leg_ids=tuple(dict.fromkeys(item.causal_leg_id for item in active)),
            observed_time_ns=None,
        )

    if spec.require_directional_context and context.direction is not direction:
        return OptionResolution(
            family=family,
            direction=direction,
            executable=False,
            disposition="DIRECTIONAL_CONTEXT_MISMATCH",
            role_evidence={role: tuple(x.evidence_id for x in items) for role, items in by_role.items()},
            evidence_ids=tuple(item.evidence_id for item in active),
            causal_leg_ids=tuple(dict.fromkeys(item.causal_leg_id for item in active)),
            observed_time_ns=None,
        )

    location_time = min(item.observed_time_ns for item in by_role[EvidenceRole.LOCATION])
    interaction_time = min(item.observed_time_ns for item in by_role[EvidenceRole.INTERACTION])
    response_time = min(item.observed_time_ns for item in by_role[EvidenceRole.RESPONSE])
    entry_time = min(item.observed_time_ns for item in by_role[EvidenceRole.ENTRY])
    invalidation_time = min(item.observed_time_ns for item in by_role[EvidenceRole.INVALIDATION])
    objective_time = min(item.observed_time_ns for item in by_role[EvidenceRole.OBJECTIVE])

    if spec.require_preexisting_location and not location_time <= interaction_time:
        disposition = "LOCATION_NOT_AVAILABLE_BEFORE_INTERACTION"
    elif not interaction_time <= response_time:
        disposition = "RESPONSE_PRECEDES_INTERACTION"
    elif spec.require_entry_after_response and not response_time <= entry_time:
        disposition = "ENTRY_PRECEDES_RESPONSE"
    elif invalidation_time > entry_time:
        disposition = "INVALIDATION_UNKNOWN_AT_ENTRY"
    elif objective_time > entry_time:
        disposition = "OBJECTIVE_UNKNOWN_AT_ENTRY"
    else:
        disposition = "EXECUTABLE"

    observed = max(
        location_time,
        interaction_time,
        response_time,
        entry_time,
        invalidation_time,
        objective_time,
    )
    return OptionResolution(
        family=family,
        direction=direction,
        executable=disposition == "EXECUTABLE",
        disposition=disposition,
        role_evidence={role: tuple(x.evidence_id for x in items) for role, items in by_role.items()},
        evidence_ids=tuple(item.evidence_id for item in active),
        causal_leg_ids=tuple(dict.fromkeys(item.causal_leg_id for item in active)),
        observed_time_ns=observed,
    )


def evidence(
    evidence_id: str,
    *,
    kind: EvidenceKind,
    roles: Iterable[EvidenceRole],
    direction: Direction | None,
    scale: Scale,
    event_time_ns: int,
    observed_time_ns: int | None = None,
    causal_leg_id: str | None = None,
    lifecycle: Lifecycle = Lifecycle.ACTIVE,
    source_status: str = "SOURCE_EXPLICIT",
    notes: str = "",
) -> Evidence:
    """Small constructor used by case contracts and tests."""
    return Evidence(
        evidence_id=evidence_id,
        causal_leg_id=causal_leg_id or evidence_id,
        kind=kind,
        roles=frozenset(roles),
        direction=direction,
        scale=scale,
        event_time_ns=event_time_ns,
        observed_time_ns=event_time_ns if observed_time_ns is None else observed_time_ns,
        lifecycle=lifecycle,
        source_status=source_status,
        notes=notes,
    )


__all__ = [
    "DEFAULT_SPECIFICATIONS",
    "Direction",
    "Evidence",
    "EvidenceKind",
    "EvidenceRole",
    "Lifecycle",
    "LocalPhase",
    "OptionResolution",
    "OptionSpecification",
    "PositioningContext",
    "PositioningState",
    "Scale",
    "ScenarioFamily",
    "evidence",
    "resolve_option",
]
