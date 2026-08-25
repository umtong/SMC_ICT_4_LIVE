"""Categorical semantic ownership for completed structural auctions.

The module answers one question only: does the completed structural event own
the proposed direction?  Entry geometry, reward/risk and account arbitration
are deliberately outside this decision.  Missing observations never become a
positive vote and no scalar score is constructed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .common_episode_ledger import (
    CommonCandidateAuthorization,
    CommonEpisodeFamily,
)
from .cross_market_roles import (
    AcceptedRepricingPhase,
    CrossMarketAuctionRoles,
    EventLeadershipRole,
    PeerParticipation,
    SourceOwnershipDecision,
    SourceOwnershipRole,
)
from .directional_context import PreEventAuthority
from .flow_price_delivery import (
    FlowPriceDeliveryObservation,
    FlowPriceDeliveryRole,
)
from .inventory_ownership import InventoryDecision, InventoryInterpretation
from .structural_campaign import CampaignHypothesis, StructuralOpportunity


class SemanticAuthorityVerdict(str, Enum):
    """Final categorical disposition of one completed opportunity."""

    AUTHORIZE = "AUTHORIZE"
    INSUFFICIENT_CAUSAL_EVIDENCE = "INSUFFICIENT_CAUSAL_EVIDENCE"
    REJECT_CONTRADICTION = "REJECT_CONTRADICTION"


class AuctionDirectionOwnership(str, Enum):
    """The causal relationship that owns the proposed direction."""

    NONE = "NONE"
    CARRIED_PRIOR_DELIVERY = "CARRIED_PRIOR_DELIVERY"
    GENUINE_LOCAL_ACCEPTED_TRANSFER = "GENUINE_LOCAL_ACCEPTED_TRANSFER"
    GENUINE_BROAD_ACCEPTED_TRANSFER = "GENUINE_BROAD_ACCEPTED_TRANSFER"
    GENUINE_LOCAL_FAILED_TRANSFER = "GENUINE_LOCAL_FAILED_TRANSFER"
    GENUINE_BROAD_COMMON_FAILED_TRANSFER = (
        "GENUINE_BROAD_COMMON_FAILED_TRANSFER"
    )
    TRAPPED_NEW_INVENTORY_LOCAL_TRANSFER = (
        "TRAPPED_NEW_INVENTORY_LOCAL_TRANSFER"
    )
    TRAPPED_NEW_INVENTORY_BROAD_COMMON_TRANSFER = (
        "TRAPPED_NEW_INVENTORY_BROAD_COMMON_TRANSFER"
    )
    EXHAUSTED_FORCED_FLOW_LOCAL_TRANSFER = (
        "EXHAUSTED_FORCED_FLOW_LOCAL_TRANSFER"
    )
    EXHAUSTED_FORCED_FLOW_BROAD_COMMON_TRANSFER = (
        "EXHAUSTED_FORCED_FLOW_BROAD_COMMON_TRANSFER"
    )


class AttackFailureMechanism(str, Enum):
    """Why the outward attack ceased to own price delivery."""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"
    AGGRESSION_WITHOUT_PROGRESS = "AGGRESSION_WITHOUT_PROGRESS"
    OPPOSED_DELIVERY = "OPPOSED_DELIVERY"
    TRAPPED_NEW_INVENTORY = "TRAPPED_NEW_INVENTORY"
    EXHAUSTED_FORCED_FLOW = "EXHAUSTED_FORCED_FLOW"


class InventoryRelation(str, Enum):
    """How the optional inventory observation relates to the hypothesis."""

    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"
    SUPPORTS_ACCEPTED_DELIVERY = "SUPPORTS_ACCEPTED_DELIVERY"
    SUPPORTS_FAILED_DELIVERY = "SUPPORTS_FAILED_DELIVERY"
    TRAPPED_SPONSORSHIP_COMPATIBLE_WITH_FAILURE = (
        "TRAPPED_SPONSORSHIP_COMPATIBLE_WITH_FAILURE"
    )
    NEUTRAL = "NEUTRAL"
    CONTRADICTS_ACCEPTED_DELIVERY = "CONTRADICTS_ACCEPTED_DELIVERY"
    CONTRADICTS_EVENT_ATTACK = "CONTRADICTS_EVENT_ATTACK"


class SemanticAuthorityReason(str, Enum):
    """Stable explanations; callers need not parse prose."""

    AUTHORIZED_CARRIED_DELIVERY = "AUTHORIZED_CARRIED_DELIVERY"
    AUTHORIZED_GENUINE_ACCEPTED_TRANSFER = (
        "AUTHORIZED_GENUINE_ACCEPTED_TRANSFER"
    )
    AUTHORIZED_GENUINE_FAILED_TRANSFER = (
        "AUTHORIZED_GENUINE_FAILED_TRANSFER"
    )
    AUTHORIZED_TRAPPED_NEW_INVENTORY_TRANSFER = (
        "AUTHORIZED_TRAPPED_NEW_INVENTORY_TRANSFER"
    )
    AUTHORIZED_EXHAUSTED_FORCED_FLOW_TRANSFER = (
        "AUTHORIZED_EXHAUSTED_FORCED_FLOW_TRANSFER"
    )
    SOURCE_GEOMETRY_CONTRADICTS_HYPOTHESIS = (
        "SOURCE_GEOMETRY_CONTRADICTS_HYPOTHESIS"
    )
    INVENTORY_CONTRADICTS_HYPOTHESIS = "INVENTORY_CONTRADICTS_HYPOTHESIS"
    ATTACK_DELIVERY_CONTRADICTS_HYPOTHESIS = (
        "ATTACK_DELIVERY_CONTRADICTS_HYPOTHESIS"
    )
    CONTROL_DELIVERY_NOT_OWNED = "CONTROL_DELIVERY_NOT_OWNED"
    CONTROL_DELIVERY_CONTRADICTS_OWNERSHIP = (
        "CONTROL_DELIVERY_CONTRADICTS_OWNERSHIP"
    )
    COMMON_AUTHORIZATION_CONTRADICTS_HYPOTHESIS = (
        "COMMON_AUTHORIZATION_CONTRADICTS_HYPOTHESIS"
    )
    PRIOR_EXTERNAL_DRAW_NOT_CONSUMED = "PRIOR_EXTERNAL_DRAW_NOT_CONSUMED"
    DIRECTIONAL_DELIVERY_NOT_OWNED = "DIRECTIONAL_DELIVERY_NOT_OWNED"
    CROSS_MARKET_EVENT_UNRESOLVED = "CROSS_MARKET_EVENT_UNRESOLVED"
    ACCEPTED_DELIVERY_NOT_ESTABLISHED = "ACCEPTED_DELIVERY_NOT_ESTABLISHED"
    FAILED_TRANSFER_NOT_ESTABLISHED = "FAILED_TRANSFER_NOT_ESTABLISHED"


@dataclass(frozen=True, slots=True)
class SemanticAuctionAuthorityDecision:
    """Auditable, score-free authority decision for one structural event."""

    episode_id: str
    plan_id: str
    symbol: str
    family: str
    side: str
    decision_time_ns: int
    verdict: SemanticAuthorityVerdict
    ownership: AuctionDirectionOwnership
    inventory_relation: InventoryRelation
    attack_failure_mechanism: AttackFailureMechanism
    reason: SemanticAuthorityReason
    source_ownership: SourceOwnershipRole
    control_ownership: SourceOwnershipRole
    pre_event_structure_side: str | None
    pre_event_draw_side: str | None
    source_outward_side: str
    source_was_prior_draw_destination: bool
    event_leadership_role: EventLeadershipRole
    peer_participation: PeerParticipation
    accepted_repricing_phase: AcceptedRepricingPhase
    inventory_interpretation: InventoryInterpretation | None
    attack_delivery_role: FlowPriceDeliveryRole
    attack_delivery_interval: tuple[int, int]
    control_delivery_role: FlowPriceDeliveryRole
    control_delivery_interval: tuple[int, int]
    common_authorization_id: str | None
    common_responsibility: str | None
    missing_evidence: tuple[str, ...] = ()

    @property
    def authorized(self) -> bool:
        return self.verdict is SemanticAuthorityVerdict.AUTHORIZE

    def to_dict(self) -> dict[str, object]:
        return {
            "episode_id": self.episode_id,
            "plan_id": self.plan_id,
            "symbol": self.symbol,
            "family": self.family,
            "side": self.side,
            "decision_time_ns": self.decision_time_ns,
            "verdict": self.verdict.value,
            "authorized": self.authorized,
            "ownership": self.ownership.value,
            "inventory_relation": self.inventory_relation.value,
            "attack_failure_mechanism": self.attack_failure_mechanism.value,
            "reason": self.reason.value,
            "source_ownership": self.source_ownership.value,
            "control_ownership": self.control_ownership.value,
            "pre_event_structure_side": self.pre_event_structure_side,
            "pre_event_draw_side": self.pre_event_draw_side,
            "source_outward_side": self.source_outward_side,
            "source_was_prior_draw_destination": (
                self.source_was_prior_draw_destination
            ),
            "event_leadership_role": self.event_leadership_role.value,
            "peer_participation": self.peer_participation.value,
            "accepted_repricing_phase": self.accepted_repricing_phase.value,
            "inventory_interpretation": (
                None
                if self.inventory_interpretation is None
                else self.inventory_interpretation.value
            ),
            "attack_delivery_role": self.attack_delivery_role.value,
            "attack_delivery_interval": self.attack_delivery_interval,
            "control_delivery_role": self.control_delivery_role.value,
            "control_delivery_interval": self.control_delivery_interval,
            "common_authorization_id": self.common_authorization_id,
            "common_responsibility": self.common_responsibility,
            "missing_evidence": self.missing_evidence,
        }


_DIRECTIONAL_ROLES = {
    SourceOwnershipRole.LOCAL_SOURCE_OWNER,
    SourceOwnershipRole.COMMON_MARKET_OWNER_ONLY,
}
_LOCAL_LEADERSHIP = {
    EventLeadershipRole.INDEPENDENT_LEAD,
    EventLeadershipRole.UNANIMOUS_LEAD,
    EventLeadershipRole.QUORUM_LEAD,
    EventLeadershipRole.CO_LEAD,
}
_BROAD_PARTICIPATION = {
    PeerParticipation.UNANIMOUS,
    PeerParticipation.DOMINANT_QUORUM,
}
_FRESH_SPONSORSHIP = {
    InventoryInterpretation.FRESH_SPONSORSHIP_CROWDING,
    InventoryInterpretation.FRESH_SPONSORSHIP_COUNTER_INVENTORY,
}
_FORCED_DISCHARGE = {
    InventoryInterpretation.FORCED_DELEVERAGING_DISCHARGE,
    InventoryInterpretation.FORCED_DELEVERAGING_COUNTER_INVENTORY,
}


def _opposite(side: str) -> str:
    return "SHORT" if side == "LONG" else "LONG"


def _common_matches(
    authorization: CommonCandidateAuthorization | None,
    opportunity: StructuralOpportunity,
    family: CommonEpisodeFamily,
) -> bool:
    return bool(
        authorization is not None
        and authorization.symbol == opportunity.symbol
        and authorization.side == opportunity.side
        and authorization.family is family
        and authorization.candidate_time_ns == opportunity.decision_time_ns
    )


def _inventory_relation(
    opportunity: StructuralOpportunity,
    inventory: InventoryDecision | None,
) -> InventoryRelation:
    if inventory is None:
        return InventoryRelation.ABSENT
    if not inventory.known:
        return InventoryRelation.UNKNOWN

    expected_attack = (
        opportunity.side
        if opportunity.hypothesis is CampaignHypothesis.ACCEPTANCE
        else _opposite(opportunity.side)
    )
    expected_shock = "BUY" if expected_attack == "LONG" else "SELL"
    if inventory.shock_side != expected_shock:
        return InventoryRelation.CONTRADICTS_EVENT_ATTACK

    interpretation = inventory.interpretation
    if opportunity.hypothesis is CampaignHypothesis.ACCEPTANCE:
        if interpretation in _FORCED_DISCHARGE:
            return InventoryRelation.CONTRADICTS_ACCEPTED_DELIVERY
        if interpretation in _FRESH_SPONSORSHIP:
            return InventoryRelation.SUPPORTS_ACCEPTED_DELIVERY
        return InventoryRelation.NEUTRAL

    if interpretation is InventoryInterpretation.FORCED_DELEVERAGING_DISCHARGE:
        return InventoryRelation.SUPPORTS_FAILED_DELIVERY
    if interpretation in _FRESH_SPONSORSHIP:
        # A completed reclaim/control transfer makes the new attack inventory
        # trapped inventory; sponsorship is therefore compatible evidence,
        # never by itself an approval.
        return InventoryRelation.TRAPPED_SPONSORSHIP_COMPATIBLE_WITH_FAILURE
    return InventoryRelation.NEUTRAL


def _decision(
    opportunity: StructuralOpportunity,
    *,
    verdict: SemanticAuthorityVerdict,
    ownership: AuctionDirectionOwnership,
    inventory_relation: InventoryRelation,
    attack_failure_mechanism: AttackFailureMechanism,
    reason: SemanticAuthorityReason,
    source_ownership: SourceOwnershipDecision,
    control_ownership: SourceOwnershipDecision,
    cross_market_roles: CrossMarketAuctionRoles,
    pre_event: PreEventAuthority,
    attack_delivery: FlowPriceDeliveryObservation,
    control_delivery: FlowPriceDeliveryObservation,
    common_authorization: CommonCandidateAuthorization | None,
    inventory: InventoryDecision | None,
    missing_evidence: tuple[str, ...] = (),
) -> SemanticAuctionAuthorityDecision:
    return SemanticAuctionAuthorityDecision(
        episode_id=opportunity.episode_id,
        plan_id=opportunity.plan_id,
        symbol=opportunity.symbol,
        family=opportunity.family,
        side=opportunity.side,
        decision_time_ns=opportunity.decision_time_ns,
        verdict=verdict,
        ownership=ownership,
        inventory_relation=inventory_relation,
        attack_failure_mechanism=attack_failure_mechanism,
        reason=reason,
        source_ownership=source_ownership.role,
        control_ownership=control_ownership.role,
        pre_event_structure_side=pre_event.structure_side,
        pre_event_draw_side=pre_event.draw_side,
        source_outward_side=pre_event.source_outward_side,
        source_was_prior_draw_destination=(
            pre_event.source_was_prior_draw_destination
        ),
        event_leadership_role=cross_market_roles.event_leadership_role,
        peer_participation=cross_market_roles.peer_participation,
        accepted_repricing_phase=cross_market_roles.accepted_repricing_phase,
        inventory_interpretation=(
            None if inventory is None else inventory.interpretation
        ),
        attack_delivery_role=attack_delivery.role,
        attack_delivery_interval=(
            attack_delivery.interval_start_ns,
            attack_delivery.interval_end_ns,
        ),
        control_delivery_role=control_delivery.role,
        control_delivery_interval=(
            control_delivery.interval_start_ns,
            control_delivery.interval_end_ns,
        ),
        common_authorization_id=(
            None
            if common_authorization is None
            else common_authorization.authorization_id
        ),
        common_responsibility=(
            None if common_authorization is None else common_authorization.responsibility
        ),
        missing_evidence=missing_evidence,
    )


def decide_semantic_auction_authority(
    opportunity: StructuralOpportunity,
    *,
    pre_event: PreEventAuthority,
    source_ownership: SourceOwnershipDecision,
    control_ownership: SourceOwnershipDecision,
    attack_delivery: FlowPriceDeliveryObservation,
    control_delivery: FlowPriceDeliveryObservation,
    cross_market_roles: CrossMarketAuctionRoles,
    common_authorization: CommonCandidateAuthorization | None = None,
    inventory: InventoryDecision | None = None,
) -> SemanticAuctionAuthorityDecision:
    """Decide whether a completed structural auction owns its direction.

    Acceptance is authorized only by carried prior delivery or a genuine
    accepted transfer.  A failed auction additionally requires that the source
    consumed the prior external draw and that local or broad-common control
    genuinely transferred.  Unknown observations remain missing evidence.
    """

    decision_time_ns = opportunity.decision_time_ns
    if pre_event.observed_time_ns > decision_time_ns:
        raise ValueError("pre-event authority is later than the opportunity")
    if cross_market_roles.symbol != opportunity.symbol:
        raise ValueError("cross-market roles belong to another symbol")
    if cross_market_roles.side != opportunity.side:
        raise ValueError("cross-market roles use another direction")
    if (
        cross_market_roles.decision_time_ns
        != opportunity.hypothesis_confirmation_time_ns
    ):
        raise ValueError("cross-market roles use another event-confirmation clock")
    if inventory is not None:
        if inventory.symbol != opportunity.symbol:
            raise ValueError("inventory belongs to another symbol")
        if inventory.decision_ts_ns > decision_time_ns:
            raise ValueError("inventory is later than the opportunity")

    expected_attack_side = (
        opportunity.side
        if opportunity.hypothesis is CampaignHypothesis.ACCEPTANCE
        else _opposite(opportunity.side)
    )
    for name, delivery, expected_side in (
        ("attack", attack_delivery, expected_attack_side),
        ("control", control_delivery, opportunity.side),
    ):
        if delivery.symbol != opportunity.symbol:
            raise ValueError(f"{name} delivery belongs to another symbol")
        if delivery.side != expected_side:
            raise ValueError(f"{name} delivery uses another direction")
        if delivery.observed_time_ns > decision_time_ns:
            raise ValueError(f"{name} delivery is later than the opportunity")
    if (
        attack_delivery.interval_end_ns
        != opportunity.hypothesis_confirmation_time_ns
    ):
        raise ValueError("attack delivery must end at hypothesis confirmation")
    if control_delivery.interval_end_ns != opportunity.control_transfer_time_ns:
        raise ValueError("control delivery must end at control transfer")

    is_acceptance = opportunity.hypothesis is CampaignHypothesis.ACCEPTANCE
    expected_family = (
        "ACCEPTED_AUCTION_CONTINUATION"
        if is_acceptance
        else "FAILED_AUCTION_REVERSAL"
    )
    expected_outward = opportunity.side if is_acceptance else _opposite(opportunity.side)
    relation = _inventory_relation(opportunity, inventory)
    if is_acceptance:
        attack_failure_mechanism = AttackFailureMechanism.NOT_APPLICABLE
    elif (
        relation
        is InventoryRelation.TRAPPED_SPONSORSHIP_COMPATIBLE_WITH_FAILURE
        and attack_delivery.role
        is FlowPriceDeliveryRole.AGGRESSION_WITHOUT_PROGRESS
    ):
        attack_failure_mechanism = AttackFailureMechanism.TRAPPED_NEW_INVENTORY
    elif (
        inventory is not None
        and inventory.interpretation in _FORCED_DISCHARGE
        and attack_delivery.role
        in {
            FlowPriceDeliveryRole.AGGRESSION_WITHOUT_PROGRESS,
            FlowPriceDeliveryRole.OPPOSED,
        }
    ):
        attack_failure_mechanism = AttackFailureMechanism.EXHAUSTED_FORCED_FLOW
    elif attack_delivery.role is FlowPriceDeliveryRole.AGGRESSION_WITHOUT_PROGRESS:
        attack_failure_mechanism = (
            AttackFailureMechanism.AGGRESSION_WITHOUT_PROGRESS
        )
    elif attack_delivery.role is FlowPriceDeliveryRole.OPPOSED:
        attack_failure_mechanism = AttackFailureMechanism.OPPOSED_DELIVERY
    else:
        attack_failure_mechanism = AttackFailureMechanism.UNKNOWN

    if (
        opportunity.family != expected_family
        or pre_event.source_outward_side != expected_outward
    ):
        return _decision(
            opportunity,
            verdict=SemanticAuthorityVerdict.REJECT_CONTRADICTION,
            ownership=AuctionDirectionOwnership.NONE,
            inventory_relation=relation,
            attack_failure_mechanism=attack_failure_mechanism,
            reason=SemanticAuthorityReason.SOURCE_GEOMETRY_CONTRADICTS_HYPOTHESIS,
            source_ownership=source_ownership,
            control_ownership=control_ownership,
            cross_market_roles=cross_market_roles,
            pre_event=pre_event,
            attack_delivery=attack_delivery,
            control_delivery=control_delivery,
            common_authorization=common_authorization,
            inventory=inventory,
        )

    if relation in {
        InventoryRelation.CONTRADICTS_ACCEPTED_DELIVERY,
        InventoryRelation.CONTRADICTS_EVENT_ATTACK,
    }:
        return _decision(
            opportunity,
            verdict=SemanticAuthorityVerdict.REJECT_CONTRADICTION,
            ownership=AuctionDirectionOwnership.NONE,
            inventory_relation=relation,
            attack_failure_mechanism=attack_failure_mechanism,
            reason=SemanticAuthorityReason.INVENTORY_CONTRADICTS_HYPOTHESIS,
            source_ownership=source_ownership,
            control_ownership=control_ownership,
            cross_market_roles=cross_market_roles,
            pre_event=pre_event,
            attack_delivery=attack_delivery,
            control_delivery=control_delivery,
            common_authorization=common_authorization,
            inventory=inventory,
        )

    if common_authorization is not None:
        required_common_family = (
            CommonEpisodeFamily.CONTINUATION
            if is_acceptance
            else CommonEpisodeFamily.REVERSAL
        )
        if not _common_matches(
            common_authorization,
            opportunity,
            required_common_family,
        ):
            return _decision(
                opportunity,
                verdict=SemanticAuthorityVerdict.REJECT_CONTRADICTION,
                ownership=AuctionDirectionOwnership.NONE,
                inventory_relation=relation,
                attack_failure_mechanism=attack_failure_mechanism,
                reason=(
                    SemanticAuthorityReason.COMMON_AUTHORIZATION_CONTRADICTS_HYPOTHESIS
                ),
                source_ownership=source_ownership,
                control_ownership=control_ownership,
                cross_market_roles=cross_market_roles,
                pre_event=pre_event,
                attack_delivery=attack_delivery,
                control_delivery=control_delivery,
                common_authorization=common_authorization,
                inventory=inventory,
            )

    failed_delivery_roles = {
        FlowPriceDeliveryRole.AGGRESSION_WITHOUT_PROGRESS,
        FlowPriceDeliveryRole.OPPOSED,
    }
    delivered_roles = {
        FlowPriceDeliveryRole.LOCAL_PRICE_DISCOVERY,
        FlowPriceDeliveryRole.COMMON_REPRICING,
    }
    if is_acceptance and attack_delivery.role in failed_delivery_roles:
        return _decision(
            opportunity,
            verdict=SemanticAuthorityVerdict.REJECT_CONTRADICTION,
            ownership=AuctionDirectionOwnership.NONE,
            inventory_relation=relation,
            attack_failure_mechanism=attack_failure_mechanism,
            reason=(
                SemanticAuthorityReason.ATTACK_DELIVERY_CONTRADICTS_HYPOTHESIS
            ),
            source_ownership=source_ownership,
            control_ownership=control_ownership,
            cross_market_roles=cross_market_roles,
            pre_event=pre_event,
            attack_delivery=attack_delivery,
            control_delivery=control_delivery,
            common_authorization=common_authorization,
            inventory=inventory,
        )
    if is_acceptance and attack_delivery.role not in delivered_roles:
        return _decision(
            opportunity,
            verdict=SemanticAuthorityVerdict.INSUFFICIENT_CAUSAL_EVIDENCE,
            ownership=AuctionDirectionOwnership.NONE,
            inventory_relation=relation,
            attack_failure_mechanism=attack_failure_mechanism,
            reason=SemanticAuthorityReason.ACCEPTED_DELIVERY_NOT_ESTABLISHED,
            source_ownership=source_ownership,
            control_ownership=control_ownership,
            cross_market_roles=cross_market_roles,
            pre_event=pre_event,
            attack_delivery=attack_delivery,
            control_delivery=control_delivery,
            common_authorization=common_authorization,
            inventory=inventory,
            missing_evidence=("ATTACK_FLOW_PRICE_DELIVERY",),
        )
    if not is_acceptance and attack_delivery.role in delivered_roles:
        return _decision(
            opportunity,
            verdict=SemanticAuthorityVerdict.REJECT_CONTRADICTION,
            ownership=AuctionDirectionOwnership.NONE,
            inventory_relation=relation,
            attack_failure_mechanism=attack_failure_mechanism,
            reason=(
                SemanticAuthorityReason.ATTACK_DELIVERY_CONTRADICTS_HYPOTHESIS
            ),
            source_ownership=source_ownership,
            control_ownership=control_ownership,
            cross_market_roles=cross_market_roles,
            pre_event=pre_event,
            attack_delivery=attack_delivery,
            control_delivery=control_delivery,
            common_authorization=common_authorization,
            inventory=inventory,
        )

    control_local_delivery = (
        control_delivery.role is FlowPriceDeliveryRole.LOCAL_PRICE_DISCOVERY
        and control_ownership.role is SourceOwnershipRole.LOCAL_SOURCE_OWNER
    )
    control_common_delivery = (
        control_delivery.role is FlowPriceDeliveryRole.COMMON_REPRICING
        and control_ownership.role
        is SourceOwnershipRole.COMMON_MARKET_OWNER_ONLY
    )
    attack_local_delivery = bool(
        attack_delivery.role is FlowPriceDeliveryRole.LOCAL_PRICE_DISCOVERY
        and source_ownership.role is SourceOwnershipRole.LOCAL_SOURCE_OWNER
    )
    attack_common_delivery = bool(
        attack_delivery.role is FlowPriceDeliveryRole.COMMON_REPRICING
        and source_ownership.role
        is SourceOwnershipRole.COMMON_MARKET_OWNER_ONLY
    )
    if is_acceptance and not (attack_local_delivery or attack_common_delivery):
        return _decision(
            opportunity,
            verdict=SemanticAuthorityVerdict.REJECT_CONTRADICTION,
            ownership=AuctionDirectionOwnership.NONE,
            inventory_relation=relation,
            attack_failure_mechanism=attack_failure_mechanism,
            reason=SemanticAuthorityReason.ATTACK_DELIVERY_CONTRADICTS_HYPOTHESIS,
            source_ownership=source_ownership,
            control_ownership=control_ownership,
            cross_market_roles=cross_market_roles,
            pre_event=pre_event,
            attack_delivery=attack_delivery,
            control_delivery=control_delivery,
            common_authorization=common_authorization,
            inventory=inventory,
        )
    if (
        control_delivery.role in failed_delivery_roles
        or (
            control_delivery.role in delivered_roles
            and not (control_local_delivery or control_common_delivery)
        )
    ):
        return _decision(
            opportunity,
            verdict=SemanticAuthorityVerdict.REJECT_CONTRADICTION,
            ownership=AuctionDirectionOwnership.NONE,
            inventory_relation=relation,
            attack_failure_mechanism=attack_failure_mechanism,
            reason=(
                SemanticAuthorityReason.CONTROL_DELIVERY_NOT_OWNED
                if control_delivery.role in failed_delivery_roles
                else (
                    SemanticAuthorityReason.CONTROL_DELIVERY_CONTRADICTS_OWNERSHIP
                )
            ),
            source_ownership=source_ownership,
            control_ownership=control_ownership,
            cross_market_roles=cross_market_roles,
            pre_event=pre_event,
            attack_delivery=attack_delivery,
            control_delivery=control_delivery,
            common_authorization=common_authorization,
            inventory=inventory,
        )

    missing: list[str] = []
    if source_ownership.role is SourceOwnershipRole.UNKNOWN:
        missing.append("SOURCE_OWNERSHIP")
    if control_ownership.role is SourceOwnershipRole.UNKNOWN:
        missing.append("CONTROL_OWNERSHIP")
    if not cross_market_roles.synchronized_event_complete:
        missing.append("SYNCHRONIZED_CROSS_MARKET_EVENT")
    if cross_market_roles.event_leadership_role is EventLeadershipRole.UNKNOWN:
        missing.append("EVENT_LEADERSHIP")
    if cross_market_roles.peer_participation is PeerParticipation.UNKNOWN:
        missing.append("PEER_PARTICIPATION")
    if attack_delivery.role is FlowPriceDeliveryRole.UNKNOWN:
        missing.append("ATTACK_FLOW_PRICE_DELIVERY")
    if control_delivery.role is FlowPriceDeliveryRole.UNKNOWN:
        missing.append("CONTROL_FLOW_PRICE_DELIVERY")
    if inventory is None:
        missing.append("INVENTORY")
    elif not inventory.known:
        missing.append("INVENTORY_INTERPRETATION")

    required_common_family = (
        CommonEpisodeFamily.CONTINUATION
        if is_acceptance
        else CommonEpisodeFamily.REVERSAL
    )
    common_control_owned = bool(
        control_common_delivery
        and _common_matches(
            common_authorization,
            opportunity,
            required_common_family,
        )
    )
    directional_delivery = (
        source_ownership.role in _DIRECTIONAL_ROLES
        and (control_local_delivery or common_control_owned)
    )
    local_transfer = bool(
        directional_delivery
        and control_local_delivery
        and cross_market_roles.synchronized_event_complete
        and cross_market_roles.event_leadership_role in _LOCAL_LEADERSHIP
    )
    broad_transfer = bool(
        directional_delivery
        and common_control_owned
        and cross_market_roles.synchronized_event_complete
        and cross_market_roles.peer_participation in _BROAD_PARTICIPATION
    )

    if is_acceptance:
        opposite = _opposite(opportunity.side)
        carried_delivery = bool(
            directional_delivery
            and (attack_local_delivery or attack_common_delivery)
            and pre_event.structure_side == opportunity.side
            and pre_event.draw_side != opposite
        )
        common_accepted = bool(
            broad_transfer
            and attack_common_delivery
            and _common_matches(
                common_authorization,
                opportunity,
                CommonEpisodeFamily.CONTINUATION,
            )
        )
        local_accepted = bool(
            local_transfer
            and attack_local_delivery
        )
        if carried_delivery:
            ownership = AuctionDirectionOwnership.CARRIED_PRIOR_DELIVERY
            reason = SemanticAuthorityReason.AUTHORIZED_CARRIED_DELIVERY
        elif local_accepted:
            ownership = AuctionDirectionOwnership.GENUINE_LOCAL_ACCEPTED_TRANSFER
            reason = SemanticAuthorityReason.AUTHORIZED_GENUINE_ACCEPTED_TRANSFER
        elif common_accepted:
            ownership = AuctionDirectionOwnership.GENUINE_BROAD_ACCEPTED_TRANSFER
            reason = SemanticAuthorityReason.AUTHORIZED_GENUINE_ACCEPTED_TRANSFER
        else:
            return _decision(
                opportunity,
                verdict=SemanticAuthorityVerdict.INSUFFICIENT_CAUSAL_EVIDENCE,
                ownership=AuctionDirectionOwnership.NONE,
                inventory_relation=relation,
                attack_failure_mechanism=attack_failure_mechanism,
                reason=(
                    SemanticAuthorityReason.DIRECTIONAL_DELIVERY_NOT_OWNED
                    if not directional_delivery
                    else SemanticAuthorityReason.ACCEPTED_DELIVERY_NOT_ESTABLISHED
                ),
                source_ownership=source_ownership,
                control_ownership=control_ownership,
                cross_market_roles=cross_market_roles,
                pre_event=pre_event,
                attack_delivery=attack_delivery,
                control_delivery=control_delivery,
                common_authorization=common_authorization,
                inventory=inventory,
                missing_evidence=tuple(dict.fromkeys(missing)),
            )
        return _decision(
            opportunity,
            verdict=SemanticAuthorityVerdict.AUTHORIZE,
            ownership=ownership,
            inventory_relation=relation,
            attack_failure_mechanism=attack_failure_mechanism,
            reason=reason,
            source_ownership=source_ownership,
            control_ownership=control_ownership,
            cross_market_roles=cross_market_roles,
            pre_event=pre_event,
            attack_delivery=attack_delivery,
            control_delivery=control_delivery,
            common_authorization=common_authorization,
            inventory=inventory,
            missing_evidence=tuple(dict.fromkeys(missing)),
        )

    consumed_external_draw = bool(
        pre_event.source_was_prior_draw_destination
        and pre_event.source_outward_side == _opposite(opportunity.side)
    )
    if not consumed_external_draw:
        return _decision(
            opportunity,
            verdict=SemanticAuthorityVerdict.INSUFFICIENT_CAUSAL_EVIDENCE,
            ownership=AuctionDirectionOwnership.NONE,
            inventory_relation=relation,
            attack_failure_mechanism=attack_failure_mechanism,
            reason=SemanticAuthorityReason.PRIOR_EXTERNAL_DRAW_NOT_CONSUMED,
            source_ownership=source_ownership,
            control_ownership=control_ownership,
            cross_market_roles=cross_market_roles,
            pre_event=pre_event,
            attack_delivery=attack_delivery,
            control_delivery=control_delivery,
            common_authorization=common_authorization,
            inventory=inventory,
        )

    if attack_delivery.role not in failed_delivery_roles:
        return _decision(
            opportunity,
            verdict=SemanticAuthorityVerdict.INSUFFICIENT_CAUSAL_EVIDENCE,
            ownership=AuctionDirectionOwnership.NONE,
            inventory_relation=relation,
            attack_failure_mechanism=attack_failure_mechanism,
            reason=SemanticAuthorityReason.FAILED_TRANSFER_NOT_ESTABLISHED,
            source_ownership=source_ownership,
            control_ownership=control_ownership,
            cross_market_roles=cross_market_roles,
            pre_event=pre_event,
            attack_delivery=attack_delivery,
            control_delivery=control_delivery,
            common_authorization=common_authorization,
            inventory=inventory,
            missing_evidence=tuple(dict.fromkeys(missing)),
        )

    common_failed = bool(
        broad_transfer
        and _common_matches(
            common_authorization,
            opportunity,
            CommonEpisodeFamily.REVERSAL,
        )
        and common_authorization is not None
        and common_authorization.broad_price_failure_time_ns is not None
        and common_authorization.symbol_reclaim_time_ns is not None
        and common_authorization.broad_price_failure_time_ns <= decision_time_ns
        and common_authorization.symbol_reclaim_time_ns <= decision_time_ns
    )
    if local_transfer:
        if attack_failure_mechanism is AttackFailureMechanism.TRAPPED_NEW_INVENTORY:
            ownership = (
                AuctionDirectionOwnership.TRAPPED_NEW_INVENTORY_LOCAL_TRANSFER
            )
        elif (
            attack_failure_mechanism
            is AttackFailureMechanism.EXHAUSTED_FORCED_FLOW
        ):
            ownership = (
                AuctionDirectionOwnership.EXHAUSTED_FORCED_FLOW_LOCAL_TRANSFER
            )
        else:
            ownership = AuctionDirectionOwnership.GENUINE_LOCAL_FAILED_TRANSFER
    elif common_failed:
        if attack_failure_mechanism is AttackFailureMechanism.TRAPPED_NEW_INVENTORY:
            ownership = (
                AuctionDirectionOwnership.TRAPPED_NEW_INVENTORY_BROAD_COMMON_TRANSFER
            )
        elif (
            attack_failure_mechanism
            is AttackFailureMechanism.EXHAUSTED_FORCED_FLOW
        ):
            ownership = (
                AuctionDirectionOwnership.EXHAUSTED_FORCED_FLOW_BROAD_COMMON_TRANSFER
            )
        else:
            ownership = (
                AuctionDirectionOwnership.GENUINE_BROAD_COMMON_FAILED_TRANSFER
            )
    else:
        return _decision(
            opportunity,
            verdict=SemanticAuthorityVerdict.INSUFFICIENT_CAUSAL_EVIDENCE,
            ownership=AuctionDirectionOwnership.NONE,
            inventory_relation=relation,
            attack_failure_mechanism=attack_failure_mechanism,
            reason=(
                SemanticAuthorityReason.DIRECTIONAL_DELIVERY_NOT_OWNED
                if not directional_delivery
                else SemanticAuthorityReason.FAILED_TRANSFER_NOT_ESTABLISHED
            ),
            source_ownership=source_ownership,
            control_ownership=control_ownership,
            cross_market_roles=cross_market_roles,
            pre_event=pre_event,
            attack_delivery=attack_delivery,
            control_delivery=control_delivery,
            common_authorization=common_authorization,
            inventory=inventory,
            missing_evidence=tuple(dict.fromkeys(missing)),
        )
    reason = (
        SemanticAuthorityReason.AUTHORIZED_TRAPPED_NEW_INVENTORY_TRANSFER
        if attack_failure_mechanism
        is AttackFailureMechanism.TRAPPED_NEW_INVENTORY
        else SemanticAuthorityReason.AUTHORIZED_EXHAUSTED_FORCED_FLOW_TRANSFER
        if attack_failure_mechanism
        is AttackFailureMechanism.EXHAUSTED_FORCED_FLOW
        else SemanticAuthorityReason.AUTHORIZED_GENUINE_FAILED_TRANSFER
    )
    return _decision(
        opportunity,
        verdict=SemanticAuthorityVerdict.AUTHORIZE,
        ownership=ownership,
        inventory_relation=relation,
        attack_failure_mechanism=attack_failure_mechanism,
        reason=reason,
        source_ownership=source_ownership,
        control_ownership=control_ownership,
        cross_market_roles=cross_market_roles,
        pre_event=pre_event,
        attack_delivery=attack_delivery,
        control_delivery=control_delivery,
        common_authorization=common_authorization,
        inventory=inventory,
        missing_evidence=tuple(dict.fromkeys(missing)),
    )


__all__ = [
    "AttackFailureMechanism",
    "AuctionDirectionOwnership",
    "InventoryRelation",
    "SemanticAuctionAuthorityDecision",
    "SemanticAuthorityReason",
    "SemanticAuthorityVerdict",
    "decide_semantic_auction_authority",
]
