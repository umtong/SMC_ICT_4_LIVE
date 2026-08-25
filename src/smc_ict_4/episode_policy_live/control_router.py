"""Deterministic ownership router for the unified episode policy.

This module is deliberately smaller than a strategy.  Structural campaigns
and value-distribution episodes decide whether an opportunity exists and own
its physical entry, invalidation and destination.  The router only assigns
that opportunity to its causal owner, applies the common-cascade inventory
responsibility, and converts it to the execution-neutral :class:`TradePlan`.

Ownership is never used as a magnitude score.  A locally owned opportunity
keeps its native family.  A common-market opportunity is routed only when the
coordinator's shared common-episode ledger explicitly authorizes that exact
native structural campaign and completed candidate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence, TypeAlias

from .campaign_ownership import IntervalOwnershipSnapshot, SymbolIntervalOwnership
from .common_episode_ledger import (
    CommonCandidateAuthorization,
    CommonEpisodeFamily,
)
from .cross_market_roles import SourceOwnershipRole
from .domain import (
    ENTRY_LIFECYCLE_IMMEDIATE_RESPONSE,
    ENTRY_LIFECYCLE_RESTING_FIRST_RETURN,
    PolicyError,
    TradePlan,
    stable_id,
)
from .inventory_ownership import InventoryDecision, InventoryInterpretation
from .structural_campaign import CampaignHypothesis, StructuralOpportunity
from .value_distribution import ValueDistributionCandidate


MAX_TIME_NS = (1 << 63) - 1
OBJECTIVE_LIFECYCLE_FAMILY_IMMUTABLE = "FAMILY_IMMUTABLE"
ROUTE_OWNER_LOCAL = "LOCAL_SOURCE_CAMPAIGN"
ROUTE_OWNER_COMMON = "COMMON_CASCADE"

RoutableOpportunity: TypeAlias = StructuralOpportunity | ValueDistributionCandidate


def _entry_lifecycle(candidate: RoutableOpportunity) -> str:
    """Preserve the native opportunity's physical entry contract.

    A structural opportunity owns a previously observed OB/FVG/retest zone;
    routing occurs after that geometry has been committed, so its parent order
    remains a passive first-return order.  Value-distribution candidates are
    emitted by their completed response/retest bar and retain the immediate
    response contract.
    """

    if isinstance(candidate, StructuralOpportunity):
        return ENTRY_LIFECYCLE_RESTING_FIRST_RETURN
    return ENTRY_LIFECYCLE_IMMEDIATE_RESPONSE

def _ownership_evidence(
    snapshot: IntervalOwnershipSnapshot,
    selected: SymbolIntervalOwnership,
) -> dict[str, Any]:
    """Return the measurements used for ownership, including peer context."""

    siblings = tuple(
        {
            "symbol": item.symbol,
            "side": item.side,
            "role": item.role.value,
            "raw_log_return": item.raw_log_return,
            "signed_log_return": item.signed_log_return,
            "local_delivery_units": item.local_delivery_units,
            "peer_common_units": item.peer_common_units,
            "residual_local_units": item.residual_local_units,
            "atr_sample_open_time_ns": item.atr.sample_open_time_ns,
            "atr_sample_close_time_ns": item.atr.sample_close_time_ns,
            "atr_sample_count": item.atr.sample_count,
            "atr_price": item.atr.atr_price,
            "atr_fraction": item.atr.atr_fraction,
        }
        for item in snapshot.ownership
    )
    return {
        "ownership_interval_open_time_ns": snapshot.interval_open_time_ns,
        "ownership_interval_close_time_ns": snapshot.interval_close_time_ns,
        "ownership_observed_time_ns": snapshot.observed_time_ns,
        "ownership_local_delivery_units": selected.local_delivery_units,
        "ownership_peer_common_units": selected.peer_common_units,
        "ownership_residual_local_units": selected.residual_local_units,
        "ownership_raw_log_return": selected.raw_log_return,
        "ownership_signed_log_return": selected.signed_log_return,
        "ownership_atr_sample_open_time_ns": selected.atr.sample_open_time_ns,
        "ownership_atr_sample_close_time_ns": selected.atr.sample_close_time_ns,
        "ownership_atr_sample_count": selected.atr.sample_count,
        "ownership_atr_price": selected.atr.atr_price,
        "ownership_atr_fraction": selected.atr.atr_fraction,
        "ownership_sibling_evidence": siblings,
    }


def _control_ownership_evidence(
    snapshot: IntervalOwnershipSnapshot,
    selected: SymbolIntervalOwnership,
) -> dict[str, Any]:
    """Keep source/event ownership distinct from the later control leg."""

    return {
        key.replace("ownership_", "control_ownership_", 1): value
        for key, value in _ownership_evidence(snapshot, selected).items()
    }


def _inventory_evidence(inventory: InventoryDecision | None) -> dict[str, Any]:
    if inventory is None:
        return {
            "inventory_interpretation": InventoryInterpretation.UNKNOWN.value,
            "inventory_evidence": None,
        }
    return {
        "inventory_interpretation": inventory.interpretation.value,
        "inventory_regime": inventory.regime.value,
        "inventory_ownership": inventory.ownership.value,
        "inventory_reason": inventory.reason,
        "inventory_shock_side": inventory.shock_side,
        "inventory_episode_start_ns": inventory.episode_start_ns,
        "inventory_decision_ts_ns": inventory.decision_ts_ns,
        "inventory_prior_observed_ts_ns": inventory.prior_observed_ts_ns,
        "inventory_current_observed_ts_ns": inventory.current_observed_ts_ns,
        "inventory_oi_change_fraction": inventory.oi_change_fraction,
        "inventory_all_account_change_log": inventory.all_account_change_log,
        "inventory_price_flow_aligned": inventory.price_flow_aligned,
        "inventory_evidence": {
            "interpretation": inventory.interpretation.value,
            "regime": inventory.regime.value,
            "ownership": inventory.ownership.value,
            "reason": inventory.reason,
            "shock_side": inventory.shock_side,
            "episode_start_ns": inventory.episode_start_ns,
            "decision_ts_ns": inventory.decision_ts_ns,
            "prior_observed_ts_ns": inventory.prior_observed_ts_ns,
            "current_observed_ts_ns": inventory.current_observed_ts_ns,
            "oi_change_fraction": inventory.oi_change_fraction,
            "all_account_change_log": inventory.all_account_change_log,
            "price_flow_aligned": inventory.price_flow_aligned,
        },
    }


def _candidate_fields(candidate: RoutableOpportunity) -> dict[str, Any]:
    if isinstance(candidate, StructuralOpportunity):
        return {
            "episode_id": candidate.episode_id,
            "native_plan_id": candidate.plan_id,
            "symbol": candidate.symbol,
            "family": str(candidate.family),
            "scenario": candidate.hypothesis.value,
            "side": str(candidate.side),
            "decision_time_ns": candidate.decision_time_ns,
            "entry": candidate.entry,
            "stop": candidate.stop,
            "target": candidate.target,
            "source_boundary_id": candidate.source_boundary_id,
            "destination_boundary_id": candidate.destination_boundary_id,
            "entry_zone": candidate.entry_zone,
            "evidence": dict(candidate.as_trade_plan_fields()["evidence"]),
        }
    return {
        "episode_id": candidate.episode_id,
        "native_plan_id": candidate.candidate_id,
        "symbol": candidate.symbol,
        "family": candidate.family,
        "scenario": candidate.scenario,
        "side": candidate.side,
        "decision_time_ns": candidate.decision_time_ns,
        "entry": candidate.entry,
        "stop": candidate.stop,
        "target": candidate.target,
        "source_boundary_id": candidate.source_object_id,
        "destination_boundary_id": candidate.objective_object_id,
        "entry_zone": candidate.entry_zone,
        "evidence": dict(candidate.evidence),
    }


def _common_family(candidate: RoutableOpportunity) -> CommonEpisodeFamily | None:
    """Return the ledger branch represented by a native structural candidate.

    Value-distribution candidates have no registered broad structural source,
    so peer-correlated delivery cannot relabel them as common cascades.
    """

    if isinstance(candidate, StructuralOpportunity):
        if (
            candidate.hypothesis is CampaignHypothesis.ACCEPTANCE
            and candidate.family == "ACCEPTED_AUCTION_CONTINUATION"
        ):
            return CommonEpisodeFamily.CONTINUATION
        if (
            candidate.hypothesis
            in {CampaignHypothesis.REJECTION, CampaignHypothesis.TRAP}
            and candidate.family == "FAILED_AUCTION_REVERSAL"
        ):
            return CommonEpisodeFamily.REVERSAL
        raise PolicyError("structural opportunity family contradicts its hypothesis")
    return None


def _physical_times(
    candidate: RoutableOpportunity,
    ownership: IntervalOwnershipSnapshot,
    *,
    interaction_time_ns: int | None,
    first_return_time_ns: int | None,
) -> tuple[int, int]:
    fields = _candidate_fields(candidate)
    evidence = fields["evidence"]
    assert isinstance(evidence, Mapping)
    if interaction_time_ns is None:
        interaction_time_ns = int(
            evidence.get(
                "interaction_time_ns",
                evidence.get(
                    "contact_time_ns",
                    evidence.get("departure_time_ns", ownership.interval_close_time_ns),
                ),
            )
        )
    if first_return_time_ns is None:
        first_return_time_ns = int(
            evidence.get(
                "first_return_time_ns",
                evidence.get("retest_time_ns", candidate.entry_zone.observed_time_ns),
            )
        )
    if interaction_time_ns < 0 or first_return_time_ns < interaction_time_ns:
        raise PolicyError("first return cannot precede its physical interaction")
    if first_return_time_ns > candidate.decision_time_ns:
        raise PolicyError("first return cannot occur after the route decision")
    return interaction_time_ns, first_return_time_ns


class ControlEpisodeRouter:
    """Assign complete opportunities to local or common causal owners."""

    @staticmethod
    def route(
        candidate: RoutableOpportunity,
        ownership: IntervalOwnershipSnapshot,
        inventory: InventoryDecision | None = None,
        *,
        control_ownership: IntervalOwnershipSnapshot | None = None,
        common_authorization: CommonCandidateAuthorization | None = None,
        interaction_time_ns: int | None = None,
        first_return_time_ns: int | None = None,
    ) -> TradePlan | None:
        fields = _candidate_fields(candidate)
        symbol = str(fields["symbol"])
        side = str(fields["side"])
        decision_time_ns = int(fields["decision_time_ns"])
        if ownership.side != side:
            raise PolicyError("source ownership side differs from opportunity side")
        if ownership.observed_time_ns > decision_time_ns:
            raise PolicyError("source ownership was not observable at the route decision")
        control = ownership if control_ownership is None else control_ownership
        if control.side != side:
            raise PolicyError("control ownership side differs from opportunity side")
        if control.campaign_root_id != ownership.campaign_root_id:
            raise PolicyError("source and control ownership belong to different campaigns")
        if control.observed_time_ns > decision_time_ns:
            raise PolicyError("control ownership was not observable at the route decision")
        source_selected = ownership.for_symbol(symbol)
        selected = control.for_symbol(symbol)
        if selected.side != side:
            raise PolicyError("symbol control ownership side differs from opportunity side")
        if inventory is not None:
            if inventory.symbol != symbol:
                raise PolicyError("inventory decision belongs to another symbol")
            if inventory.decision_ts_ns > decision_time_ns:
                raise PolicyError("inventory was not observable at the route decision")

        role = selected.role
        if role in {
            SourceOwnershipRole.UNKNOWN,
            SourceOwnershipRole.NO_DIRECTIONAL_DELIVERY,
        }:
            return None

        parent_campaign_id = str(fields["episode_id"])
        if role is SourceOwnershipRole.LOCAL_SOURCE_OWNER:
            routed_family = str(fields["family"])
            route_owner = ROUTE_OWNER_LOCAL
            causal_root_id = ownership.campaign_root_id
            responsibility = "LOCAL_CONTROL_NATIVE_FAMILY"
        elif role is SourceOwnershipRole.COMMON_MARKET_OWNER_ONLY:
            branch = _common_family(candidate)
            if branch is None or common_authorization is None:
                return None
            if common_authorization.source_campaign_root_id != ownership.campaign_root_id:
                raise PolicyError(
                    "common authorization belongs to another native campaign root"
                )
            if common_authorization.symbol != symbol:
                raise PolicyError("common authorization belongs to another symbol")
            if common_authorization.side != side:
                raise PolicyError("common authorization side differs from candidate")
            if common_authorization.family is not branch:
                raise PolicyError("common authorization family differs from candidate")
            if common_authorization.candidate_time_ns != decision_time_ns:
                raise PolicyError("common authorization time differs from candidate")
            routed_family = branch.value
            responsibility = common_authorization.responsibility
            route_owner = ROUTE_OWNER_COMMON
            causal_root_id = common_authorization.root_id
        else:  # pragma: no cover - enum exhaustiveness guard
            raise PolicyError(f"unsupported source ownership role: {role}")

        interaction, first_return = _physical_times(
            candidate,
            ownership,
            interaction_time_ns=interaction_time_ns,
            first_return_time_ns=first_return_time_ns,
        )
        native_evidence = fields["evidence"]
        assert isinstance(native_evidence, Mapping)
        entry_lifecycle = _entry_lifecycle(candidate)
        evidence: dict[str, Any] = {
            **dict(native_evidence),
            **_ownership_evidence(ownership, source_selected),
            **_control_ownership_evidence(control, selected),
            **_inventory_evidence(inventory),
            **(
                dict(common_authorization.evidence)
                if common_authorization is not None
                and route_owner == ROUTE_OWNER_COMMON
                else {}
            ),
            "native_episode_id": parent_campaign_id,
            "native_plan_id": str(fields["native_plan_id"]),
            "native_family": str(fields["family"]),
            "native_scenario": str(fields["scenario"]),
            "causal_root_id": causal_root_id,
            "parent_campaign_id": parent_campaign_id,
            "ownership_snapshot_id": ownership.ownership_snapshot_id,
            "control_ownership_snapshot_id": control.ownership_snapshot_id,
            "source_ownership_role": source_selected.role.value,
            "control_ownership_role": role.value,
            "route_owner": route_owner,
            "route_responsibility": responsibility,
            "common_cascade_id": (
                causal_root_id
                if route_owner == ROUTE_OWNER_COMMON
                else None
            ),
            "entry_lifecycle": entry_lifecycle,
            "objective_lifecycle": OBJECTIVE_LIFECYCLE_FAMILY_IMMUTABLE,
            "interaction_time_ns": interaction,
            "first_return_time_ns": first_return,
            "physical_completion_time_ns": decision_time_ns,
        }
        plan_id = stable_id(
            causal_root_id,
            parent_campaign_id,
            routed_family,
            fields["native_plan_id"],
            prefix="control-plan-",
        )
        proposal_episode_id = (
            parent_campaign_id
            if route_owner == ROUTE_OWNER_COMMON
            else causal_root_id
        )
        return TradePlan(
            # A shared root owns cross-symbol consumption, not the durable
            # identity of each sibling proposal.  Keeping the native child ID
            # avoids semantic event-key collisions between BTC/ETH/SOL/XRP.
            episode_id=proposal_episode_id,
            plan_id=plan_id,
            symbol=symbol,
            family=routed_family,
            side=side,
            decision_time_ns=decision_time_ns,
            entry=float(fields["entry"]),
            stop=float(fields["stop"]),
            target=float(fields["target"]),
            expires_time_ns=MAX_TIME_NS,
            source_boundary_id=str(fields["source_boundary_id"]),
            destination_boundary_id=str(fields["destination_boundary_id"]),
            entry_zone=fields["entry_zone"],
            evidence=evidence,
        )

    @staticmethod
    def arbitrate(candidates: Sequence[TradePlan]) -> tuple[TradePlan, ...]:
        """Keep one hypothesis per causal root and return deterministic order.

        Physical precedence is the only precedence: immediate response before
        a resting parent, then the first completed opportunity, then its source
        interaction.  Stable plan identity is used only for an exact physical
        tie.  Reward/risk, ownership magnitude and family names are absent from
        the ordering key by design.
        """

        by_plan_id: dict[str, TradePlan] = {}
        for plan in candidates:
            previous = by_plan_id.get(plan.plan_id)
            if previous is not None and previous != plan:
                raise PolicyError(f"plan id collision: {plan.plan_id}")
            by_plan_id[plan.plan_id] = plan

        def physical_key(plan: TradePlan) -> tuple[int, int, int]:
            immediate = (
                0
                if plan.entry_lifecycle == ENTRY_LIFECYCLE_IMMEDIATE_RESPONSE
                else 1
            )
            completion = int(
                plan.evidence.get("physical_completion_time_ns", plan.decision_time_ns)
            )
            interaction = int(
                plan.evidence.get("interaction_time_ns", plan.decision_time_ns)
            )
            return immediate, completion, interaction

        def sibling_key(plan: TradePlan) -> tuple[int, int, int, int, float, str]:
            """Choose the cleanest executable expression of one shared event."""

            mechanism = 0
            liquidity = 0.0
            if plan.evidence.get("route_owner") == ROUTE_OWNER_COMMON:
                interpretation = str(
                    plan.evidence.get("latest_inventory_interpretation", "UNKNOWN")
                )
                regime = str(plan.evidence.get("latest_inventory_regime", "UNKNOWN"))
                # New counter-inventory is trapped by the confirmed transfer;
                # a clean position reset is next.  Renewed same-direction
                # crowding is still authorized by the episode but is the least
                # attractive vehicle when a cleaner sibling exists.
                if interpretation == "FRESH_SPONSORSHIP_COUNTER_INVENTORY":
                    mechanism = 0
                elif regime == "POSITION_RESET":
                    mechanism = 1
                elif interpretation == "FRESH_SPONSORSHIP_CROWDING":
                    mechanism = 3
                else:
                    mechanism = 2
                liquidity = -float(
                    plan.evidence.get("confirmation_quote_volume", 0.0)
                )
            return (*physical_key(plan), mechanism, liquidity, plan.plan_id)

        by_root: dict[str, TradePlan] = {}
        for plan in by_plan_id.values():
            root = causal_root_id(plan)
            previous = by_root.get(root)
            if previous is None or sibling_key(plan) < sibling_key(previous):
                by_root[root] = plan
        return tuple(
            sorted(
                by_root.values(),
                key=lambda plan: (*physical_key(plan), plan.plan_id),
            )
        )

    @classmethod
    def account_owner(cls, candidates: Sequence[TradePlan]) -> TradePlan | None:
        """Return the first physical owner for the one-account slot."""

        ordered = cls.arbitrate(candidates)
        return ordered[0] if ordered else None


def causal_root_id(plan: TradePlan) -> str:
    value = plan.evidence.get("causal_root_id", plan.episode_id)
    if not isinstance(value, str) or not value:
        raise PolicyError("plan has no causal root identity")
    return value


def remove_consumed_root_siblings(
    candidates: Iterable[TradePlan],
    consumed: TradePlan | str,
) -> tuple[TradePlan, ...]:
    """Remove every sibling hypothesis after one physical root is consumed."""

    root = causal_root_id(consumed) if isinstance(consumed, TradePlan) else consumed
    if not root:
        raise PolicyError("consumed causal root cannot be empty")
    return tuple(plan for plan in candidates if causal_root_id(plan) != root)


@dataclass(slots=True)
class SiblingRootConsumption:
    """Small execution-side helper for cross-symbol/root sibling consumption."""

    _consumed_by_plan: dict[str, str] = field(default_factory=dict)

    def consume(self, plan: TradePlan) -> None:
        root = causal_root_id(plan)
        existing = self._consumed_by_plan.get(root)
        if existing is not None and existing != plan.plan_id:
            raise PolicyError(
                f"causal root {root} was already consumed by plan {existing}"
            )
        self._consumed_by_plan[root] = plan.plan_id

    def consumed(self, plan_or_root: TradePlan | str) -> bool:
        root = (
            causal_root_id(plan_or_root)
            if isinstance(plan_or_root, TradePlan)
            else plan_or_root
        )
        return root in self._consumed_by_plan

    def available(self, candidates: Iterable[TradePlan]) -> tuple[TradePlan, ...]:
        return tuple(
            plan
            for plan in candidates
            if causal_root_id(plan) not in self._consumed_by_plan
        )

    def remove_siblings(
        self,
        candidates: Iterable[TradePlan],
        consumed: TradePlan,
    ) -> tuple[TradePlan, ...]:
        self.consume(consumed)
        return remove_consumed_root_siblings(candidates, consumed)

    def snapshot(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._consumed_by_plan.items()))

    def restore(self, values: Mapping[str, str] | Iterable[tuple[str, str]]) -> None:
        restored = dict(values)
        if any(not root or not plan_id for root, plan_id in restored.items()):
            raise PolicyError("consumed root state contains an empty identity")
        for root, plan_id in restored.items():
            existing = self._consumed_by_plan.get(root)
            if existing is not None and existing != plan_id:
                raise PolicyError(f"conflicting consumed plan for causal root {root}")
        self._consumed_by_plan.update(restored)


__all__ = [
    "ControlEpisodeRouter",
    "MAX_TIME_NS",
    "OBJECTIVE_LIFECYCLE_FAMILY_IMMUTABLE",
    "ROUTE_OWNER_COMMON",
    "ROUTE_OWNER_LOCAL",
    "RoutableOpportunity",
    "SiblingRootConsumption",
    "causal_root_id",
    "remove_consumed_root_siblings",
]
