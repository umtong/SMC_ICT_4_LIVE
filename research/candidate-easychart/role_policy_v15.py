"""Typed, causal EasyChart decision policy.

The source cases do not trade each surface tool independently and do not require
all tools at once.  This module therefore models the *roles* needed by a
complete trade episode and lets different source observations satisfy those
roles.  It deliberately contains no confidence score, confluence count, daily
limit, trade-count limit, or risk multiplier.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math
from typing import Iterable, Sequence

from domain_v3 import ArmedSetup, Side, TargetMode


class RuleOrigin(str, Enum):
    SOURCE_EXPLICIT = "SOURCE_EXPLICIT"
    SOURCE_IMPLIED = "SOURCE_IMPLIED"
    HUMAN_NATURAL = "HUMAN_NATURAL"
    EXTERNAL_OPERATIONALIZATION = "EXTERNAL_OPERATIONALIZATION"
    UNRESOLVED = "UNRESOLVED"


class Role(str, Enum):
    CONTEXT = "CONTEXT"
    LIQUIDITY = "LIQUIDITY"
    INTERACTION = "INTERACTION"
    STATE_TRANSITION = "STATE_TRANSITION"
    ENTRY = "ENTRY"
    INVALIDATION = "INVALIDATION"
    OBJECTIVE = "OBJECTIVE"


class ObservationKind(str, Enum):
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"
    TRENDLINE = "TRENDLINE"
    CHANNEL = "CHANNEL"
    RANGE_HIGH = "RANGE_HIGH"
    RANGE_LOW = "RANGE_LOW"
    ORDER_BLOCK = "ORDER_BLOCK"
    FVG = "FVG"
    IMMEDIATE_FAKEOUT = "IMMEDIATE_FAKEOUT"
    WM_TRAP = "WM_TRAP"
    ACCEPTED_BREAK = "ACCEPTED_BREAK"
    RECLAIM = "RECLAIM"
    FIRST_RETEST = "FIRST_RETEST"
    DISPLACEMENT = "DISPLACEMENT"


@dataclass(frozen=True, slots=True)
class RoleEvidence:
    evidence_id: str
    kind: ObservationKind
    roles: frozenset[Role]
    origin: RuleOrigin
    observed_time_ns: int
    event_time_ns: int
    side: Side | None = None
    level: float | None = None
    zone_low: float | None = None
    zone_high: float | None = None
    parent_event_id: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.roles:
            raise ValueError("evidence must be identified and fulfil at least one role")
        if self.observed_time_ns < self.event_time_ns:
            raise ValueError("evidence observed before its event")
        values = [value for value in (self.level, self.zone_low, self.zone_high) if value is not None]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("evidence prices must be finite")
        if self.zone_low is not None and self.zone_high is not None and self.zone_high < self.zone_low:
            raise ValueError("invalid evidence zone")


@dataclass(frozen=True, slots=True)
class ObjectiveCandidate:
    objective_id: str
    kind: ObservationKind
    level: float
    observed_time_ns: int
    event_time_ns: int
    active: bool = True

    def __post_init__(self) -> None:
        if not self.objective_id or not math.isfinite(self.level):
            raise ValueError("invalid objective")
        if self.observed_time_ns < self.event_time_ns:
            raise ValueError("objective observed before event")


@dataclass(frozen=True, slots=True)
class EntryZone:
    zone_id: str
    kind: ObservationKind
    side: Side
    low: float
    high: float
    invalidation: float
    observed_time_ns: int
    causal_parent_id: str
    fresh: bool = True

    def __post_init__(self) -> None:
        if not self.high >= self.low:
            raise ValueError("invalid entry zone")
        if not all(math.isfinite(value) for value in (self.low, self.high, self.invalidation)):
            raise ValueError("entry-zone prices must be finite")

    @property
    def executable_price(self) -> float:
        return self.high if self.side is Side.LONG else self.low


@dataclass(frozen=True, slots=True)
class EpisodeHypothesis:
    episode_id: str
    symbol: str
    family: str
    side: Side
    observed_time_ns: int
    interaction_time_ns: int
    interaction_extreme: float
    evidence: tuple[RoleEvidence, ...]
    entry_zones: tuple[EntryZone, ...]
    objectives: tuple[ObjectiveCandidate, ...]
    far_objective: ObjectiveCandidate | None = None

    def __post_init__(self) -> None:
        if not self.episode_id or not self.symbol or not self.family:
            raise ValueError("episode identifiers must be non-empty")
        if self.observed_time_ns < self.interaction_time_ns:
            raise ValueError("episode cannot be observed before interaction")
        if not math.isfinite(self.interaction_extreme):
            raise ValueError("interaction extreme must be finite")

    def fulfilled_roles(self) -> frozenset[Role]:
        roles: set[Role] = set()
        for item in self.evidence:
            roles.update(item.roles)
        if self.entry_zones:
            roles.add(Role.ENTRY)
            roles.add(Role.INVALIDATION)
        if self.objectives or self.far_objective is not None:
            roles.add(Role.OBJECTIVE)
        return frozenset(roles)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    setup: ArmedSetup | None
    reason: str
    chosen_entry_zone: EntryZone | None = None
    chosen_objective: ObjectiveCandidate | None = None
    duplicate_evidence_groups: tuple[tuple[str, ...], ...] = ()


REQUIRED_ROLES = frozenset(
    {
        Role.CONTEXT,
        Role.LIQUIDITY,
        Role.INTERACTION,
        Role.STATE_TRANSITION,
        Role.ENTRY,
        Role.INVALIDATION,
        Role.OBJECTIVE,
    },
)


def _same_causal_claim(a: RoleEvidence, b: RoleEvidence) -> bool:
    return bool(a.parent_event_id) and a.parent_event_id == b.parent_event_id


def duplicate_evidence_groups(evidence: Sequence[RoleEvidence]) -> tuple[tuple[str, ...], ...]:
    """Identify labels that describe one causal event instead of independent votes."""
    groups: dict[str, list[str]] = {}
    for item in evidence:
        if item.parent_event_id:
            groups.setdefault(item.parent_event_id, []).append(item.evidence_id)
    return tuple(
        tuple(sorted(values))
        for values in groups.values()
        if len(values) > 1
    )


def choose_entry_zone(
    episode: EpisodeHypothesis,
    *,
    reference_level: float | None,
) -> tuple[EntryZone | None, str]:
    """Choose executable geometry without a confluence score.

    A fresh EasyChart OB generated by the same response has priority only when
    it actually contains the interacted/flipped structure.  Otherwise an
    explicitly reclaimed or flipped boundary may be used.  Multiple materially
    different zones with no source-defined priority are UNRESOLVED rather than
    ranked by historical outcome.
    """
    eligible = [
        zone
        for zone in episode.entry_zones
        if zone.fresh
        and zone.side is episode.side
        and zone.observed_time_ns <= episode.observed_time_ns
    ]
    if not eligible:
        return None, "NO_FRESH_ENTRY_ZONE"
    same_parent = [zone for zone in eligible if zone.causal_parent_id == episode.episode_id]
    if same_parent:
        eligible = same_parent
    if reference_level is not None:
        overlapping_ob = [
            zone
            for zone in eligible
            if zone.kind is ObservationKind.ORDER_BLOCK
            and zone.low <= reference_level <= zone.high
        ]
        if len(overlapping_ob) == 1:
            return overlapping_ob[0], "RESPONSE_OB_OVERLAPS_INTERACTED_STRUCTURE"
        if len(overlapping_ob) > 1:
            earliest = min(zone.observed_time_ns for zone in overlapping_ob)
            first = [zone for zone in overlapping_ob if zone.observed_time_ns == earliest]
            if len(first) == 1:
                return first[0], "EARLIEST_RESPONSE_ORIGIN_OB"
            return None, "MULTIPLE_RESPONSE_OBS_UNRESOLVED"
    boundary = [
        zone
        for zone in eligible
        if zone.kind in {
            ObservationKind.RANGE_HIGH,
            ObservationKind.RANGE_LOW,
            ObservationKind.TRENDLINE,
            ObservationKind.CHANNEL,
        }
    ]
    if len(boundary) == 1:
        return boundary[0], "EXPLICIT_RECLAIM_OR_ROLE_FLIP_BOUNDARY"
    if len(eligible) == 1:
        return eligible[0], "ONLY_CAUSALLY_ELIGIBLE_ZONE"
    geometry = {
        (
            round(zone.executable_price, 12),
            round(zone.invalidation, 12),
        )
        for zone in eligible
    }
    if len(geometry) == 1:
        return sorted(eligible, key=lambda zone: (zone.observed_time_ns, zone.zone_id))[0], "EQUIVALENT_GEOMETRY"
    return None, "CONFLICTING_ENTRY_GEOMETRY_UNRESOLVED"


def choose_first_objective(
    episode: EpisodeHypothesis,
    *,
    entry: float,
    stop: float,
    setup_high: float,
    setup_low: float,
    minimum_gross_rr: float = 1.0,
) -> tuple[ObjectiveCandidate | None, str]:
    """Choose the first still-active objective and never skip it for a farther R."""
    if abs(minimum_gross_rr - 1.0) > 1e-12:
        raise ValueError("candidate-easychart RR gate is fixed at 1.0")
    candidates = []
    for item in episode.objectives:
        if not item.active or item.observed_time_ns >= episode.observed_time_ns:
            continue
        if episode.side is Side.LONG:
            if item.level <= entry or setup_high >= item.level:
                continue
        else:
            if item.level >= entry or setup_low <= item.level:
                continue
        candidates.append(item)
    candidates.sort(key=lambda item: abs(item.level - entry))
    chosen = candidates[0] if candidates else episode.far_objective
    if chosen is None or not chosen.active or chosen.observed_time_ns >= episode.observed_time_ns:
        return None, "NO_ACTIVE_OBJECTIVE"
    if episode.side is Side.LONG and not entry < chosen.level:
        return None, "OBJECTIVE_NOT_DIRECTIONAL"
    if episode.side is Side.SHORT and not chosen.level < entry:
        return None, "OBJECTIVE_NOT_DIRECTIONAL"
    gross_rr = abs(chosen.level - entry) / abs(entry - stop)
    if gross_rr < 1.0 - 1e-12:
        return None, "FIRST_ACTIVE_OBJECTIVE_RR_LT_1"
    return chosen, "FIRST_ACTIVE_OBJECTIVE"


def decide_episode(
    episode: EpisodeHypothesis,
    *,
    reference_level: float | None,
    setup_high: float,
    setup_low: float,
    tick_size: float,
    sequence: int,
) -> PolicyDecision:
    if not math.isfinite(tick_size) or tick_size <= 0.0:
        raise ValueError("tick size must be positive")
    duplicates = duplicate_evidence_groups(episode.evidence)
    fulfilled = episode.fulfilled_roles()
    missing = sorted(role.value for role in REQUIRED_ROLES - fulfilled)
    if missing:
        return PolicyDecision(None, "MISSING_ROLES:" + ",".join(missing), duplicate_evidence_groups=duplicates)
    zone, zone_reason = choose_entry_zone(episode, reference_level=reference_level)
    if zone is None:
        return PolicyDecision(None, zone_reason, duplicate_evidence_groups=duplicates)
    entry = zone.executable_price
    stop = (
        min(episode.interaction_extreme, zone.invalidation) - tick_size
        if episode.side is Side.LONG
        else max(episode.interaction_extreme, zone.invalidation) + tick_size
    )
    objective, objective_reason = choose_first_objective(
        episode,
        entry=entry,
        stop=stop,
        setup_high=setup_high,
        setup_low=setup_low,
    )
    if objective is None:
        return PolicyDecision(None, objective_reason, zone, duplicate_evidence_groups=duplicates)
    gross_rr = abs(objective.level - entry) / abs(entry - stop)
    setup = ArmedSetup(
        setup_id=f"ec15-{episode.symbol}-{sequence:08d}",
        causal_event_id=episode.episode_id,
        symbol=episode.symbol,
        family=episode.family,
        side=episode.side,
        observed_time_ns=episode.observed_time_ns,
        entry=entry,
        stop=stop,
        target_mode=TargetMode.FIXED_STRUCTURE,
        initial_target=objective.level,
        fixed_target_id=objective.objective_id,
        source_pool_id=next(
            (item.evidence_id for item in episode.evidence if Role.LIQUIDITY in item.roles),
            episode.episode_id,
        ),
        zone_low=zone.low,
        zone_high=zone.high,
        formation_extreme=zone.invalidation,
        body_ratio=0.0,
        context_bias=(
            f"ROLE_POLICY_V15|ENTRY_REASON={zone_reason}|OBJECTIVE_REASON={objective_reason}|"
            f"DUPLICATE_CAUSAL_LABEL_GROUPS={len(duplicates)}"
        ),
        source_timeframe_minutes=0,
    )
    # Re-run the fixed user gate through the shared domain contract.
    if setup.executable(objective.level, target_id=objective.objective_id, min_gross_rr=1.0) is None:
        return PolicyDecision(None, "SHARED_DOMAIN_RR_GATE_REJECTED", zone, objective, duplicates)
    assert gross_rr >= 1.0 - 1e-12
    return PolicyDecision(setup, "ACCEPTED", zone, objective, duplicates)


def resolve_competing_decisions(decisions: Iterable[PolicyDecision]) -> tuple[PolicyDecision | None, str]:
    """Resolve identical geometry; never rank contradictory hypotheses by backtest score."""
    accepted = [item for item in decisions if item.setup is not None]
    if not accepted:
        return None, "NO_ACCEPTED_DECISION"
    if len(accepted) == 1:
        return accepted[0], "ONLY_ACCEPTED_DECISION"
    first = accepted[0].setup
    assert first is not None
    equivalent = all(
        item.setup is not None
        and item.setup.side is first.side
        and math.isclose(item.setup.entry, first.entry, rel_tol=1e-10, abs_tol=1e-10)
        and math.isclose(item.setup.stop, first.stop, rel_tol=1e-10, abs_tol=1e-10)
        and math.isclose(item.setup.initial_target, first.initial_target, rel_tol=1e-10, abs_tol=1e-10)
        for item in accepted[1:]
    )
    if equivalent:
        ordered = sorted(accepted, key=lambda item: (item.setup.family, item.setup.causal_event_id))
        return ordered[0], "EQUIVALENT_GEOMETRY_MERGED"
    return None, "CONFLICTING_HYPOTHESES_UNRESOLVED"


__all__ = [
    "EntryZone",
    "EpisodeHypothesis",
    "ObjectiveCandidate",
    "ObservationKind",
    "PolicyDecision",
    "REQUIRED_ROLES",
    "Role",
    "RoleEvidence",
    "RuleOrigin",
    "choose_entry_zone",
    "choose_first_objective",
    "decide_episode",
    "duplicate_evidence_groups",
    "resolve_competing_decisions",
]
