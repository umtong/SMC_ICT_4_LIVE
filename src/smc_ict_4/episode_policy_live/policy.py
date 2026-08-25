"""Unified streaming auction policy for replay and paper.

The active decision path is not the former two-family policy.  A pre-existing
public structure owns one parent auction campaign, or a sealed prior value
distribution owns one value-auction episode.  Those owners interpret price and
volume, commit their own entry/invalidation/destination geometry, and only then
route the completed opportunity through symmetric four-market causal
ownership.  Local residual delivery keeps the native family; common delivery
is handled only when the derivatives-inventory responsibility is observable.

Older watch, journey and local-continuation methods remain in this module for
persisted-state compatibility.  The synchronized one-minute coordinator does
not call them as alpha and they are not treated as a standard to improve.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from math import log
from statistics import median

from .attack_ledger import (
    AttackLedger,
    AttackOutcome,
    CampaignPhase,
    EventKind,
    OwnerSide,
    SourceKey,
    SourceSide,
    SourceSpec,
)
from .auction_journey import (
    CausalJourneyRegistry,
    EventTimeAuctionJourney,
    JourneyEvidence,
    StructureInteraction,
)
from .cross_market_roles import (
    EventPrice,
    SourceOwnershipRole,
    analyze_cross_market_roles,
    classify_source_ownership,
)
from .campaign_ownership import (
    observe_interval_ownership,
    source_campaign_root_id,
)
from .common_episode_ledger import (
    CommonCandidateAuthorization,
    CommonEpisodeError,
    CommonEpisodeFamily,
    CommonEpisodeLedger,
    CommonEpisodeState,
)
from .common_price_campaign import (
    CommonPriceCampaignBook,
    CommonPriceOpportunity,
    CommonSourceJoin,
)
from .control_router import (
    ControlEpisodeRouter,
    causal_root_id,
)
from .directional_context import (
    PreEventAuthority,
    boundary_role,
    build_active_liquidity_context,
    build_directional_context,
    build_directional_update,
)
from .domain import (
    ENTRY_LIFECYCLE_IMMEDIATE_RESPONSE,
    Bar,
    EntryZone,
    LiquidityBoundary,
    Pivot,
    TradePlan,
    stable_id,
)
from .factor_continuation import (
    CausalFlowAnalyzer,
    CommonFactorState,
    LocalAuctionContinuationSetup,
    five_minute_engulfing_ob,
)
from .inventory_ownership import (
    InventoryDecision,
    InventoryInterpretation,
    InventoryRegime,
    InventoryTimeline,
    OwnershipBranch,
)
from .market_state import NS_PER_MINUTE, SymbolMarketState
from .structural_liquidity import (
    FeasibleTrendChannelBook,
    StructuralNode,
    StructureRole,
    destination_first_geometry,
    event_local_locations,
    structural_stop,
)
from .structural_campaign import (
    CampaignHypothesis,
    CampaignObservation,
    CampaignSeed,
    CampaignSnapshot,
    EntryRefinement,
    FlowBaseline,
    HypothesisGeometry,
    ParentCampaignOwner,
    StructuralOpportunity,
)
from .value_distribution import (
    ValueDistributionAuctionBook,
    ValueDistributionCandidate,
)

MAX_CAUSAL_ORDER_TIME_NS = (1 << 63) - 1
POLICY_DECISION_SCHEMA_VERSION = 2
POLICY_FINGERPRINT = (
    "unified-causal-auction-control-route-v5"
)


def _decision_tree(value: object) -> object:
    """Use the same list-based tree in memory and in durable JSON events."""

    if isinstance(value, Mapping):
        return {str(key): _decision_tree(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_decision_tree(item) for item in value]
    return value


@dataclass(slots=True)
class EpisodeWatch:
    episode_id: str
    family: str
    source: LiquidityBoundary
    side: str
    state: str
    interaction_serial: int
    interaction_time_ns: int
    event_extreme: float
    last_update_serial: int
    last_update_time_ns: int
    # Retained for constructor/state compatibility.  Journey lifetime is
    # structural and never decremented by a clock.
    bars_remaining: int = 0
    pullback_extreme: float | None = None
    entry_zone: EntryZone | None = None
    evidence: dict[str, float | str | int] = field(default_factory=dict)
    # Compatibility fields now describe the current counterfactual observation;
    # they are not accumulated across bars.
    supportive_control: float = 0.0
    opposing_control: float = 0.0
    ownership_balance: float = 0.0
    contradiction_count: int = 0
    proof_extreme: float | None = None
    committed_destination: LiquidityBoundary | None = None
    objective_commit_time_ns: int | None = None


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    # Legacy fields stay accepted by deployed configuration files but do not
    # own decisions in the active parent-campaign/value-auction law.
    min_history_5m: int = 72
    failed_confirmation_bars: int = 3
    accepted_lifetime_bars: int = 8
    initiative_lifetime_bars: int = 8
    order_lifetime_minutes: int = 45
    min_activity_ratio: float = 0.75
    min_control_score: float = 0.15
    max_source_distance_atr: float = 1.5


@dataclass(frozen=True, slots=True)
class RouterSource:
    boundary: LiquidityBoundary
    generation: int
    authority: PreEventAuthority | None = None


@dataclass(frozen=True, slots=True)
class RouterPrebarContext:
    """Facts which existed before the current minute began."""

    sequence: int
    structure_serial: int
    sources: tuple[RouterSource, ...]
    flow_baseline: FlowBaseline | None


class SymbolEpisodePolicy:
    STATE_VERSION = 1
    RUNTIME_STATE_VERSION = 2

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        config: PolicyConfig | None = None,
        inventory_timeline: InventoryTimeline | None = None,
    ) -> None:
        self.symbol = symbol
        self.tick_size = float(tick_size)
        self.config = config or PolicyConfig()
        self.market = SymbolMarketState(symbol, self.tick_size)
        self.journey = EventTimeAuctionJourney(symbol, self.tick_size)
        self.journey_registry = CausalJourneyRegistry(self.tick_size)
        self.attack_ledger = AttackLedger(symbol)
        self.inventory_timeline = inventory_timeline
        self.factor_flow = CausalFlowAnalyzer(self.tick_size)
        self._common_factor_state: CommonFactorState | None = None
        self._continuation_local_side: str | None = None
        self._continuation_direction_pivot_id: str | None = None
        self._continuation_direction_event_time_ns: int | None = None
        self._continuation_broken_pivot_ids: set[str] = set()
        self._continuation_direction_bar_count = 0
        self._continuation_setups: dict[str, LocalAuctionContinuationSetup] = {}
        self._continuation_seen_source_ids: set[str] = set()
        self._trend_channel_books = {
            timeframe: FeasibleTrendChannelBook(
                symbol, timeframe, self.tick_size,
            )
            for timeframe in (15, 60)
        }
        self._structural_bar_counts = {15: 0, 60: 0}
        self._structural_pivot_ids: set[str] = set()
        self._watches: dict[str, EpisodeWatch] = {}
        self._proposals: dict[str, TradePlan] = {}
        self._used_episodes: set[str] = set()
        self._claimed_plans: dict[str, str] = {}
        self._claimed_plan_metadata: dict[str, dict[str, str | int | float]] = {}
        self._invalidated_claimed_plans: dict[str, dict[str, str | int]] = {}
        self._terminalized_episodes: dict[str, str] = {}
        self._started_episodes: dict[str, dict[str, object]] = {}
        self._terminal_decisions: dict[str, dict[str, object]] = {}
        self._pending_started_episode_ids: set[str] = set()
        self._pending_terminal_episode_ids: set[str] = set()
        # Transient causal state: completed-bar replay rebuilds physical source
        # attacks and their response/reattack genealogy before the durable
        # account-claim overlay is restored.
        self._campaign_sources: dict[
            SourceKey, tuple[LiquidityBoundary, str]
        ] = {}
        self._source_keys_by_boundary_id: dict[str, SourceKey] = {}
        # Active alpha state.  The legacy watches above are intentionally not
        # consulted by the synchronized minute path.
        self._structural_campaigns: dict[str, CampaignSnapshot] = {}
        self._structural_campaign_roots: dict[str, str] = {}
        self._router_directional_authority: dict[str, PreEventAuthority] = {}
        self._router_opened_sources: set[tuple[str, int]] = set()
        self._value_distribution = ValueDistributionAuctionBook(symbol)
        self._last_plan_time_ns = -1
        self._diagnostic_counts: dict[str, int] = {}
        self._rejections: list[dict[str, float | str | int]] = []

    @property
    def diagnostics(self) -> dict[str, object]:
        return {
            "counts": dict(sorted(self._diagnostic_counts.items())),
            "recent_rejections": list(self._rejections[-100:]),
            "active_episodes": len(self._watches),
            "active_proposals": len(self._proposals),
            "used_episodes": len(self._used_episodes),
            "invalidated_claimed_plans": dict(self._invalidated_claimed_plans),
            "terminalized_episodes": dict(self._terminalized_episodes),
            "started_episode_count": len(self._started_episodes),
            "terminal_decision_count": len(self._terminal_decisions),
            "source_campaigns": len(self._campaign_sources),
            "active_local_continuations": len(self._continuation_setups),
            "continuation_local_side": self._continuation_local_side,
            "active_structural_campaigns": sum(
                not state.terminal for state in self._structural_campaigns.values()
            ),
            "active_value_profile": (
                None
                if self._value_distribution.active_profile is None
                else self._value_distribution.active_profile.profile_id
            ),
        }

    @property
    def invalidated_claimed_plans(self) -> dict[str, dict[str, str | int]]:
        """Durable policy validity signals for live pending-order cancellation.

        A claimed plan may be an exchange-pending order or a filled position;
        the execution layer owns that distinction.  It must cancel only a still
        pending order and must never use this signal to time-exit a fill.
        """

        return {
            plan_id: dict(values)
            for plan_id, values in self._invalidated_claimed_plans.items()
        }

    def claimed_plan_validity(self, plan_id: str) -> tuple[bool, str | None]:
        invalidation = self._invalidated_claimed_plans.get(plan_id)
        return (
            (True, None)
            if invalidation is None
            else (False, str(invalidation["reason"]))
        )

    def _record(
        self,
        reason: str,
        watch: EpisodeWatch | None,
        bar: Bar,
        **values: float | str,
    ) -> None:
        self._diagnostic_counts[reason] = self._diagnostic_counts.get(reason, 0) + 1
        payload: dict[str, float | str | int] = {
            "reason": reason,
            "symbol": self.symbol,
            "time_ns": bar.close_time_ns,
        }
        if watch is not None:
            payload.update(
                episode_id=watch.episode_id,
                family=watch.family,
                side=watch.side,
                ownership_balance=watch.ownership_balance,
            )
            watch.evidence["last_rejection_reason"] = reason
        payload.update(values)
        self._rejections.append(payload)
        if len(self._rejections) > 200:
            del self._rejections[:-100]

    @staticmethod
    def _decision_id(episode_id: str) -> str:
        return stable_id(episode_id, "TERMINAL", prefix="DEC:")

    def _episode_start_payload(
        self,
        watch: EpisodeWatch,
        *,
        started_time_ns: int,
    ) -> dict[str, object]:
        source_lower, source_upper = watch.source.band_at(watch.interaction_serial)
        chart_start = min(
            int(watch.source.observed_time_ns),
            int(watch.interaction_time_ns),
        )
        return {
            "schema_version": POLICY_DECISION_SCHEMA_VERSION,
            "policy_fingerprint": POLICY_FINGERPRINT,
            "episode_id": watch.episode_id,
            "symbol": self.symbol,
            "family": watch.family,
            "side": watch.side,
            "started_time_ns": int(started_time_ns),
            "interaction_time_ns": int(watch.interaction_time_ns),
            "interaction_serial": int(watch.interaction_serial),
            "source_boundary_id": watch.source.boundary_id,
            "source_kind": watch.source.kind,
            "source_side": watch.source.side,
            "source_timeframe_minutes": int(watch.source.timeframe_minutes),
            "source_observed_time_ns": int(watch.source.observed_time_ns),
            "interaction_source_lower": float(
                watch.evidence.get("interaction_source_lower", source_lower),
            ),
            "interaction_source_upper": float(
                watch.evidence.get("interaction_source_upper", source_upper),
            ),
            "chart_symbol": self.symbol,
            "chart_interval_minutes": 1,
            "chart_start_time_ns": chart_start,
            "chart_bounds_semantics": (
                "bars.open_time_ns>=chart_start_time_ns and "
                "bars.close_time_ns<=chart_end_time_ns"
            ),
            "initial_evidence": dict(watch.evidence),
        }

    @staticmethod
    def _merge_identical_decision(
        target: dict[str, dict[str, object]],
        episode_id: str,
        payload: dict[str, object],
        *,
        label: str,
    ) -> None:
        normalized = _decision_tree(payload)
        if not isinstance(normalized, dict):
            raise TypeError("decision payload normalization did not produce a mapping")
        existing = target.get(episode_id)
        if existing is not None and existing != normalized:
            raise RuntimeError(f"conflicting {label} for episode {episode_id}")
        target[episode_id] = normalized

    def _queue_episode_started(
        self,
        watch: EpisodeWatch,
        *,
        started_time_ns: int,
    ) -> None:
        payload = self._episode_start_payload(
            watch,
            started_time_ns=started_time_ns,
        )
        self._merge_identical_decision(
            self._started_episodes,
            watch.episode_id,
            payload,
            label="episode start",
        )
        self._pending_started_episode_ids.add(watch.episode_id)

    def _queue_episode_started_from_plan(self, plan: TradePlan) -> None:
        """Compatibility boundary for restored/focused synthetic proposals."""

        interaction_time = int(
            plan.evidence.get("interaction_time_ns", plan.decision_time_ns),
        )
        observed_time = int(
            plan.evidence.get(
                "source_observed_time_ns",
                plan.entry_zone.observed_time_ns,
            ),
        )
        payload: dict[str, object] = {
            "schema_version": POLICY_DECISION_SCHEMA_VERSION,
            "policy_fingerprint": POLICY_FINGERPRINT,
            "episode_id": plan.episode_id,
            "symbol": self.symbol,
            "family": plan.family,
            "side": plan.side,
            "started_time_ns": plan.decision_time_ns,
            "interaction_time_ns": interaction_time,
            "interaction_serial": int(plan.evidence.get("interaction_serial", -1)),
            "source_boundary_id": plan.source_boundary_id,
            "source_kind": str(plan.evidence.get("source_kind", "UNKNOWN")),
            "source_side": str(plan.evidence.get("source_side", "UNKNOWN")),
            "source_timeframe_minutes": int(
                plan.evidence.get("source_timeframe_minutes", 0),
            ),
            "source_observed_time_ns": observed_time,
            "interaction_source_lower": float(
                plan.evidence.get("interaction_source_lower", plan.entry_zone.lower),
            ),
            "interaction_source_upper": float(
                plan.evidence.get("interaction_source_upper", plan.entry_zone.upper),
            ),
            "chart_symbol": self.symbol,
            "chart_interval_minutes": 1,
            "chart_start_time_ns": min(observed_time, interaction_time),
            "chart_bounds_semantics": (
                "bars.open_time_ns>=chart_start_time_ns and "
                "bars.close_time_ns<=chart_end_time_ns"
            ),
            "initial_evidence": dict(plan.evidence),
        }
        self._merge_identical_decision(
            self._started_episodes,
            plan.episode_id,
            payload,
            label="episode start",
        )
        self._pending_started_episode_ids.add(plan.episode_id)

    def _queue_terminal_decision(
        self,
        *,
        outcome: str,
        stage: str,
        reason: str,
        terminal_time_ns: int,
        watch: EpisodeWatch | None = None,
        plan: TradePlan | None = None,
        details: Mapping[str, object] | None = None,
        mark_terminalized: bool = True,
    ) -> None:
        if outcome not in {"SELECTED", "NO_TRADE"}:
            raise ValueError(f"invalid terminal decision outcome: {outcome}")
        if stage not in {"POLICY", "ARBITRATION", "EXECUTION_ADMISSION"}:
            raise ValueError(f"invalid terminal decision stage: {stage}")
        if not reason:
            raise ValueError("terminal decision reason cannot be empty")
        episode_id = watch.episode_id if watch is not None else plan.episode_id if plan else ""
        if not episode_id:
            raise ValueError("terminal decision requires an episode")
        start = self._started_episodes.get(episode_id)
        if start is None:
            if watch is not None:
                self._queue_episode_started(
                    watch,
                    started_time_ns=watch.last_update_time_ns,
                )
            elif plan is not None:
                self._queue_episode_started_from_plan(plan)
            else:
                raise RuntimeError(f"terminal decision has no episode start: {episode_id}")
            start = self._started_episodes[episode_id]
        evidence: dict[str, object] = {}
        if watch is not None:
            evidence.update(watch.evidence)
        if plan is not None:
            evidence.update(plan.evidence)
        terminal: dict[str, object] = {
            **{key: value for key, value in start.items() if key != "initial_evidence"},
            "decision_id": self._decision_id(episode_id),
            "outcome": outcome,
            "terminal_stage": stage,
            "terminal_reason": reason,
            "terminal_time_ns": int(terminal_time_ns),
            "family": watch.family if watch is not None else plan.family if plan else start["family"],
            "side": watch.side if watch is not None else plan.side if plan else start["side"],
            "plan_id": None if plan is None else plan.plan_id,
            "plan": None if plan is None else plan.to_dict(),
            "entry": None if plan is None else plan.entry,
            "stop": None if plan is None else plan.stop,
            "target": None if plan is None else plan.target,
            "gross_rr": None if plan is None else plan.gross_rr,
            "destination_boundary_id": (
                None if plan is None else plan.destination_boundary_id
            ),
            "journey_terminal_state": evidence.get("journey_terminal_state"),
            "journey_completed_states": evidence.get("journey_completed_states"),
            "acceptance_retest_time_ns": evidence.get("acceptance_retest_time_ns"),
            "acceptance_response_time_ns": evidence.get("acceptance_response_time_ns"),
            "chart_end_time_ns": int(terminal_time_ns),
            "evidence": evidence,
            "details": dict(details or {}),
        }
        self._merge_identical_decision(
            self._terminal_decisions,
            episode_id,
            terminal,
            label="terminal decision",
        )
        self._pending_terminal_episode_ids.add(episode_id)
        if outcome == "NO_TRADE" and mark_terminalized:
            existing_reason = self._terminalized_episodes.get(episode_id)
            if existing_reason is not None and existing_reason != reason:
                raise RuntimeError(
                    f"episode {episode_id} already terminalized as {existing_reason}",
                )
            self._terminalized_episodes[episode_id] = reason

    def _record_terminal(
        self,
        reason: str,
        watch: EpisodeWatch,
        bar: Bar,
        *,
        stage: str = "POLICY",
        plan: TradePlan | None = None,
        **values: float | str | int,
    ) -> None:
        self._record(reason, watch, bar, **values)
        self._queue_terminal_decision(
            outcome="NO_TRADE",
            stage=stage,
            reason=reason,
            terminal_time_ns=bar.close_time_ns,
            watch=watch,
            plan=plan,
            details=values,
        )

    def drain_decision_events(self) -> list[dict[str, object]]:
        """Return deterministic exactly-once event intents for ``StateStore``.

        The live adapter must append these with the supplied ``event_key``
        before checkpointing policy state.  Retrying a drained batch is safe
        because ``StateStore.append_event`` validates semantic-key identity.
        """

        output: list[dict[str, object]] = []
        for episode_id in self._pending_started_episode_ids:
            payload = self._started_episodes[episode_id]
            output.append(
                {
                    "time_ns": int(payload["started_time_ns"]),
                    "event_type": "POLICY_EPISODE_STARTED",
                    "event_key": f"POLICY_EPISODE_STARTED:{episode_id}",
                    "payload": dict(payload),
                },
            )
        for episode_id in self._pending_terminal_episode_ids:
            payload = self._terminal_decisions[episode_id]
            output.append(
                {
                    "time_ns": int(payload["terminal_time_ns"]),
                    "event_type": "POLICY_EPISODE_TERMINAL",
                    "event_key": f"POLICY_EPISODE_TERMINAL:{episode_id}",
                    "payload": dict(payload),
                },
            )
        output.sort(
            key=lambda item: (
                int(item["time_ns"]),
                0 if item["event_type"] == "POLICY_EPISODE_STARTED" else 1,
                str(item["event_key"]),
            ),
        )
        self._pending_started_episode_ids.clear()
        self._pending_terminal_episode_ids.clear()
        return output

    def ingest_one_minute(self, bar: Bar) -> Bar | None:
        # The active law owns micro response in ParentCampaignOwner and the
        # shared price campaign.  Legacy journey/factor state is deliberately
        # not advanced on the production path.
        five, _ = self.market.push_one_minute(bar)
        if five is not None:
            self._sync_structural_books()
        for book in self._trend_channel_books.values():
            book.observe_price(bar)
        return five

    def ingest_five_minute(self, bar: Bar) -> Bar:
        raise RuntimeError(
            "direct five-minute ingestion is not an active policy path; "
            "use the synchronized four-market one-minute coordinator",
        )

    def _router_pre_event_authority(
        self,
        *,
        source: LiquidityBoundary,
        bar: Bar,
        serial: int,
        bars_by_symbol: Mapping[str, Sequence[Bar]],
    ) -> PreEventAuthority:
        """Freeze structure and live liquidity draw before source contact."""

        prior = tuple(
            item
            for item in bars_by_symbol.get(self.symbol, ())
            if item.close_time_ns <= bar.open_time_ns
        )
        price = prior[-1].close if prior else bar.open
        atr_price = self.market.atr(list(prior[-20:])) if prior else None
        if atr_price is not None and atr_price <= 0.0:
            atr_price = None
        active_boundaries = tuple(
            item
            for item in self.market.boundary_book.active(bar.open_time_ns)
            if not any(
                token in item.kind
                for token in (
                    "UPTREND_LINE",
                    "DOWNTREND_LINE",
                    "DIAGONAL_LIQUIDITY",
                )
            )
        )
        liquidity = build_active_liquidity_context(
            boundaries=active_boundaries,
            price=price,
            decision_time_ns=bar.open_time_ns,
            serial=serial,
            atr_price=atr_price,
        )
        balance = liquidity.direction_source_balance
        draw_side = (
            "LONG"
            if balance is not None and balance > 0.0
            else "SHORT"
            if balance is not None and balance < 0.0
            else None
        )
        structure_time = self._continuation_direction_event_time_ns
        structure_side = (
            self._continuation_local_side
            if structure_time is not None and structure_time < bar.open_time_ns
            else None
        )
        outward = "LONG" if source.side == "HIGH" else "SHORT"
        return PreEventAuthority(
            observed_time_ns=bar.open_time_ns,
            structure_side=structure_side,
            structure_event_time_ns=(
                structure_time if structure_side is not None else None
            ),
            draw_side=draw_side,
            draw_balance=balance,
            source_semantic_kind=boundary_role(source).semantic_kind,
            source_outward_side=outward,
            source_was_prior_draw_destination=draw_side == outward,
        )

    def prepare_router_minute(
        self,
        bar: Bar,
        *,
        bars_by_symbol: Mapping[str, Sequence[Bar]],
    ) -> RouterPrebarContext:
        """Freeze source facts before ``bar`` can update any registry."""

        if bar.symbol != self.symbol or bar.interval_minutes != 1:
            raise ValueError("router preparation requires this symbol's one-minute bar")
        # The incoming minute belongs to the next forming five-minute
        # coordinate.  Freezing that coordinate prevents four stale dynamic
        # projections followed by a jump only on the fifth minute.
        serial = self.market.serial_5m + 1
        candidates: list[RouterSource] = []
        for source in self.market.boundary_book.active(bar.open_time_ns):
            if source.observed_time_ns > bar.open_time_ns:
                continue
            if any(
                token in source.kind
                for token in (
                    "UPTREND_LINE",
                    "DOWNTREND_LINE",
                    "DIAGONAL_LIQUIDITY",
                )
            ):
                continue
            if not boundary_role(source).direction_source:
                continue
            candidates.append(RouterSource(source, 1))
        for node in self._projected_structural_nodes(
            bar.open_time_ns + 1,
            serial,
        ):
            if node.role is not StructureRole.SOURCE:
                continue
            if node.observed_time_ns > bar.open_time_ns or not node.is_fresh(
                bar.open_time_ns,
            ):
                continue
            candidates.append(
                RouterSource(
                    self._boundary_from_structural_node(node, serial),
                    node.version,
                )
            )

        touched: list[RouterSource] = []
        for item in candidates:
            lower, upper = item.boundary.band_at(serial)
            if bar.low <= upper and bar.high >= lower:
                touched.append(item)

        # Tick-connected aliases of the same same-side price fact have one
        # parent.  Selection uses only structure known before this bar.
        canonical: list[RouterSource] = []
        for side in ("HIGH", "LOW"):
            ordered = sorted(
                (item for item in touched if item.boundary.side == side),
                key=lambda item: (
                    item.boundary.band_at(serial)[0],
                    item.boundary.band_at(serial)[1],
                    item.boundary.boundary_id,
                ),
            )
            components: list[list[RouterSource]] = []
            component_upper: list[float] = []
            for item in ordered:
                lower, upper = item.boundary.band_at(serial)
                if not components or lower > component_upper[-1] + self.tick_size:
                    components.append([item])
                    component_upper.append(upper)
                else:
                    components[-1].append(item)
                    component_upper[-1] = max(component_upper[-1], upper)
            for component in components:
                canonical.append(
                    min(
                        component,
                        key=lambda item: (
                            -item.boundary.timeframe_minutes,
                            -int(self._is_versioned_structural_kind(item.boundary.kind)),
                            -item.boundary.strength,
                            -item.boundary.observed_time_ns,
                            item.boundary.band_at(serial)[1]
                            - item.boundary.band_at(serial)[0],
                            item.boundary.boundary_id,
                        ),
                    )
                )
        prior = tuple(self.market.one_minute)[-60:]
        usable_prior = tuple(item for item in prior if item.quote_volume > 0.0)
        baseline = (
            FlowBaseline.from_prior_bars(usable_prior)
            if len(prior) == 60 and len(usable_prior) == 60
            else None
        )
        authoritative: tuple[RouterSource, ...] = ()
        if canonical:
            shared_authority = self._router_pre_event_authority(
                source=canonical[0].boundary,
                bar=bar,
                serial=serial,
                bars_by_symbol=bars_by_symbol,
            )
            authoritative = tuple(
                replace(
                    item,
                    authority=replace(
                        shared_authority,
                        source_semantic_kind=boundary_role(
                            item.boundary,
                        ).semantic_kind,
                        source_outward_side=(
                            "LONG" if item.boundary.side == "HIGH" else "SHORT"
                        ),
                        source_was_prior_draw_destination=(
                            shared_authority.draw_side
                            == (
                                "LONG"
                                if item.boundary.side == "HIGH"
                                else "SHORT"
                            )
                        ),
                    ),
                )
                for item in canonical
            )
        return RouterPrebarContext(
            sequence=len(self.market.one_minute),
            structure_serial=serial,
            sources=authoritative,
            flow_baseline=baseline,
        )

    def _router_route_obstacles(
        self,
        *,
        visible_time_ns: int,
        serial: int,
    ) -> tuple[StructuralNode, ...]:
        return tuple(
            node
            for node in self._projected_structural_nodes(
                visible_time_ns + 1,
                serial,
            )
            if node.role is StructureRole.ROUTE_OBSTACLE
            and node.observed_time_ns <= visible_time_ns
            and node.is_fresh(visible_time_ns)
        )

    def _router_route_clear(
        self,
        *,
        source: LiquidityBoundary,
        side: str,
        target: float,
        visible_time_ns: int,
        serial: int,
    ) -> bool:
        direction = 1.0 if side == "LONG" else -1.0
        source_lower, source_upper = source.band_at(serial)
        reference = source_upper if side == "LONG" else source_lower
        for obstacle in self._router_route_obstacles(
            visible_time_ns=visible_time_ns,
            serial=serial,
        ):
            obstacle_lower, obstacle_upper = obstacle.band_at(serial)
            obstacle_price = (
                obstacle_lower if side == "LONG" else obstacle_upper
            )
            if (
                direction * (obstacle_price - reference) > self.tick_size
                and direction * (target - obstacle_price) > 0.0
            ):
                return False
        return True

    def _router_destination_geometry(
        self,
        *,
        source: LiquidityBoundary,
        side: str,
        family: str,
        decision_bar: Bar,
        serial: int,
    ) -> HypothesisGeometry | None:
        """Choose the owning family's destination before any RR calculation."""

        direction = 1.0 if side == "LONG" else -1.0
        wanted = "HIGH" if side == "LONG" else "LOW"
        source_lower, source_upper = source.band_at(serial)
        reference = source_upper if side == "LONG" else source_lower
        visible_time = decision_bar.close_time_ns
        destinations: dict[str, LiquidityBoundary] = {}

        if family == "ACCEPTANCE":
            # Continuation exits at the first still-live outward obstacle.
            # A nearby confirmed liquidity objective cannot be skipped merely
            # to advertise a farther higher-timeframe reward.
            for item in self.market.objective_book.active_at(
                visible_time,
                source_boundary_id=source.boundary_id,
            ):
                if item.side == wanted:
                    destinations[item.boundary_id] = item
            for item in self.market.boundary_book.boundaries.values():
                if (
                    item.boundary_id != source.boundary_id
                    and item.side == wanted
                    and item.observed_time_ns <= visible_time
                    and item.is_fresh(visible_time)
                ):
                    destinations[item.boundary_id] = item
        elif family == "REVERSAL":
            # Rejection/trap returns to the first internal opposing liquidity;
            # the horizontal registry is its own family-specific objective set.
            for item in self.market.objective_book.active_at(
                visible_time,
                source_boundary_id=source.boundary_id,
            ):
                if item.side == wanted:
                    destinations[item.boundary_id] = item
        else:
            raise ValueError(f"unknown structural geometry family: {family}")

        feasible = [
            item
            for item in destinations.values()
            if direction * (item.price_at(serial) - reference) > self.tick_size
            and not (
                side == "LONG" and decision_bar.high >= item.price_at(serial)
                or side == "SHORT" and decision_bar.low <= item.price_at(serial)
            )
        ]
        if not feasible:
            return None
        destination = min(
            feasible,
            key=lambda item: (
                direction * (item.price_at(serial) - reference),
                -item.timeframe_minutes,
                -item.strength,
                item.boundary_id,
            ),
        )
        target = destination.price_at(serial)
        if not self._router_route_clear(
            source=source,
            side=side,
            target=target,
            visible_time_ns=visible_time,
            serial=serial,
        ):
            return None
        invalidation = (
            source_lower - self.tick_size
            if side == "LONG"
            else source_upper + self.tick_size
        )
        return HypothesisGeometry(
            destination=destination,
            invalidation_price=invalidation,
            target_price=target,
        )

    def _prior_adverse_wick_noise(self, side: str, before_time_ns: int) -> float:
        """Return causal ordinary wick noise from the prior two trading hours."""

        recent = [
            item
            for item in self.market.one_minute
            if item.close_time_ns < before_time_ns
        ][-120:]
        if not recent:
            return 2.0 * self.tick_size
        if side == "LONG":
            wicks = [min(item.open, item.close) - item.low for item in recent]
        else:
            wicks = [item.high - max(item.open, item.close) for item in recent]
        usable = [value for value in wicks if value >= 0.0]
        value = median(usable) if usable else 0.0
        if value <= 0.0:
            value = median(item.high - item.low for item in recent) / 2.0
        return max(2.0 * self.tick_size, value)

    def _router_refinement(
        self,
        state: CampaignSnapshot,
        bar: Bar,
        serial: int,
    ) -> EntryRefinement | None:
        side = state.active_side
        if side is None:
            return None
        source_lower, source_upper = state.source.band_at(serial)
        locations = event_local_locations(
            tuple(self.market.one_minute),
            side=side,
            event_start_time_ns=state.interaction_open_time_ns,
            decision_time_ns=bar.close_time_ns,
            source_lower=source_lower,
            source_upper=source_upper,
            tick_size=self.tick_size,
        )
        order_blocks = [
            item
            for item in locations
            if item.kind == "ORDER_BLOCK"
            and max(item.lower, source_lower) < min(item.upper, source_upper)
        ]

        def exact_source_retest() -> EntryRefinement | None:
            confirmation = state.confirmation_time_ns
            if confirmation is None or bar.close_time_ns <= confirmation:
                return None
            frozen_lower, frozen_upper = state.source.band_at(
                serial,
            )
            source_invalidation = (
                frozen_lower - self.tick_size
                if side == "LONG"
                else frozen_upper + self.tick_size
            )
            stop = structural_stop(
                side=side,
                micro_stop=source_invalidation,
                event_extreme=(
                    state.episode_low if side == "LONG" else state.episode_high
                ),
                tick_size=self.tick_size,
                source_invalidation=source_invalidation,
                adverse_noise=self._prior_adverse_wick_noise(
                    side,
                    bar.close_time_ns,
                ),
            )
            confirmation_bar = next(
                (
                    item
                    for item in reversed(self.market.one_minute)
                    if item.close_time_ns == confirmation
                ),
                None,
            )
            return EntryRefinement(
                zone=EntryZone(
                    kind="STRUCTURAL_SOURCE_FIRST_RETEST",
                    lower=frozen_lower,
                    upper=frozen_upper,
                    observed_time_ns=confirmation,
                    source_bar_open_time_ns=(
                        confirmation - NS_PER_MINUTE
                        if confirmation_bar is None
                        else confirmation_bar.open_time_ns
                    ),
                ),
                side=side,
                structural_stop=stop,
                invalidation_id=stable_id(
                    state.campaign_id,
                    confirmation,
                    "STRUCTURAL_SOURCE_FIRST_RETEST",
                    prefix="LOC:",
                ),
            )

        if not order_blocks:
            return exact_source_retest()
        owner = min(
            order_blocks,
            key=lambda item: (
                -item.observed_time_ns,
                -item.source_time_ns,
                item.location_id,
            ),
        )
        overlaps = [
            item
            for item in locations
            if item.kind == "FAIR_VALUE_GAP"
            and max(item.lower, owner.lower, source_lower)
            < min(item.upper, owner.upper, source_upper)
        ]
        if overlaps:
            fvg = min(
                overlaps,
                key=lambda item: (
                    -item.observed_time_ns,
                    -item.source_time_ns,
                    item.location_id,
                ),
            )
            lower = max(owner.lower, fvg.lower, source_lower)
            upper = min(owner.upper, fvg.upper, source_upper)
            kind = "SOURCE_ORDER_BLOCK_FVG"
            observed = max(owner.observed_time_ns, fvg.observed_time_ns)
            invalidation = (
                min(owner.invalidation, fvg.invalidation)
                if side == "LONG"
                else max(owner.invalidation, fvg.invalidation)
            )
            location_id = stable_id(owner.location_id, fvg.location_id, prefix="LOC:")
        else:
            lower = max(owner.lower, source_lower)
            upper = min(owner.upper, source_upper)
            kind = "SOURCE_ORDER_BLOCK"
            observed = owner.observed_time_ns
            invalidation = owner.invalidation
            location_id = owner.location_id
        if lower >= upper:
            return exact_source_retest()
        source_invalidation = (
            source_lower - self.tick_size
            if side == "LONG"
            else source_upper + self.tick_size
        )
        stop = structural_stop(
            side=side,
            micro_stop=invalidation,
            event_extreme=(state.episode_low if side == "LONG" else state.episode_high),
            tick_size=self.tick_size,
            source_invalidation=source_invalidation,
            location_invalidation=invalidation,
            adverse_noise=self._prior_adverse_wick_noise(
                side,
                bar.close_time_ns,
            ),
        )
        return EntryRefinement(
            zone=EntryZone(
                kind=kind,
                lower=lower,
                upper=upper,
                observed_time_ns=observed,
                source_bar_open_time_ns=owner.source_time_ns,
            ),
            side=side,
            structural_stop=stop,
            invalidation_id=location_id,
        )

    def _router_attack_measurement(
        self,
        *,
        interval_open_time_ns: int,
        decision_time_ns: int,
    ) -> tuple[float, float] | None:
        """Measure the first completed source-attack bucket causally."""

        event = tuple(
            item
            for item in self.market.one_minute
            if item.open_time_ns >= interval_open_time_ns
            and item.close_time_ns <= decision_time_ns
        )
        if not event:
            return None
        attack_close = (
            (interval_open_time_ns // (5 * NS_PER_MINUTE)) + 1
        ) * (5 * NS_PER_MINUTE)
        attack = tuple(
            item for item in event if item.close_time_ns <= attack_close
        ) or event[:1]
        return (
            attack[-1].close - attack[0].open,
            sum(item.signed_quote_flow for item in attack),
        )

    def _router_inventory(
        self,
        *,
        shock_side: str,
        interval_open_time_ns: int,
        decision_time_ns: int,
        price_move: float,
        signed_taker_flow: float,
    ):
        if self.inventory_timeline is None:
            return None
        return self.inventory_timeline.evaluate(
            shock_side="BUY" if shock_side == "LONG" else "SELL",
            episode_start_ns=interval_open_time_ns,
            decision_ts_ns=decision_time_ns,
            price_move=price_move,
            signed_taker_flow=signed_taker_flow,
        )

    @staticmethod
    def _interval_open_from_source_time(
        bars: Sequence[Bar],
        source_time_ns: int,
    ) -> int | None:
        match = next(
            (item for item in bars if item.close_time_ns == source_time_ns),
            None,
        )
        return None if match is None else match.open_time_ns

    @staticmethod
    def _directional_authorization(
        candidate: StructuralOpportunity | ValueDistributionCandidate,
        authority: PreEventAuthority | None,
        common_authorization: CommonCandidateAuthorization | None,
        common_broad_failure_time_ns: int | None,
        common_symbol_reclaim_time_ns: int | None,
    ) -> tuple[bool, dict[str, object]]:
        """Resolve direction categorically; completed events own neutral state."""

        if not isinstance(candidate, StructuralOpportunity) or authority is None:
            return True, {"direction_authority_reason": "COMPLETED_EVENT_OWNS"}
        side = candidate.side
        opposite = "SHORT" if side == "LONG" else "LONG"
        structure = authority.structure_side
        draw = authority.draw_side
        inventory_transfer = common_authorization is not None
        broad_price_transfer = (
            common_broad_failure_time_ns is not None
            and common_symbol_reclaim_time_ns is not None
            and common_broad_failure_time_ns <= candidate.decision_time_ns
            and common_symbol_reclaim_time_ns <= candidate.decision_time_ns
        )
        if candidate.hypothesis is CampaignHypothesis.ACCEPTANCE:
            if structure == side:
                reason = "PERSISTENT_STRUCTURE_ALIGNED"
            elif draw == side:
                reason = "ACTIVE_LIQUIDITY_DRAW_ALIGNED"
            elif inventory_transfer:
                reason = "COMMON_CONTROL_TRANSFER"
            elif structure == opposite and draw == opposite:
                reason = "REJECTED_BOTH_PRIOR_AUTHORITIES_OPPOSE_CONTINUATION"
                allowed = False
                return allowed, {
                    **authority.to_dict(),
                    "direction_authority_reason": reason,
                    "direction_authority_allowed": allowed,
                }
            else:
                reason = "COMPLETED_EVENT_OWNS_NEUTRAL_OR_CONFLICTED_STATE"
        else:
            consumed_old_draw = (
                authority.source_was_prior_draw_destination
                and authority.source_outward_side == opposite
            )
            if structure == side:
                reason = "PERSISTENT_STRUCTURE_ALIGNED"
            elif consumed_old_draw:
                reason = "EXTERNAL_DRAW_CONSUMED_AND_RECLAIMED"
            elif broad_price_transfer:
                reason = "BROAD_COMMON_FAILURE_TRANSFER"
            elif structure == opposite and draw == opposite:
                reason = "REJECTED_ORDINARY_COUNTERTREND_RECLAIM"
                allowed = False
                return allowed, {
                    **authority.to_dict(),
                    "direction_authority_reason": reason,
                    "direction_authority_allowed": allowed,
                }
            else:
                reason = "COMPLETED_EVENT_OWNS_NEUTRAL_OR_CONFLICTED_STATE"
        return True, {
            **authority.to_dict(),
            "direction_authority_reason": reason,
            "direction_authority_allowed": True,
            "common_broad_price_failure_time_ns": (
                common_broad_failure_time_ns
            ),
            "common_symbol_reclaim_time_ns": common_symbol_reclaim_time_ns,
        }

    def _route_completed_opportunity(
        self,
        candidate: StructuralOpportunity | ValueDistributionCandidate,
        *,
        interval_open_time_ns: int,
        campaign_root: str,
        bars_by_symbol: Mapping[str, Sequence[Bar]],
        interaction_time_ns: int,
        first_return_time_ns: int,
        common_episodes: CommonEpisodeLedger | None = None,
    ) -> TradePlan | None:
        ownership = observe_interval_ownership(
            observed_bars_by_symbol=bars_by_symbol,
            side=candidate.side,
            interval_open_time_ns=interval_open_time_ns,
            interval_close_time_ns=candidate.decision_time_ns,
            observed_time_ns=candidate.decision_time_ns,
            campaign_root_id=campaign_root,
        )
        control_completion_time_ns = (
            candidate.control_transfer_time_ns
            if isinstance(candidate, StructuralOpportunity)
            else candidate.decision_time_ns
        )
        if first_return_time_ns >= control_completion_time_ns:
            return None
        control_histories = {
            symbol: tuple(
                bar
                for bar in history
                if bar.close_time_ns <= control_completion_time_ns
            )
            for symbol, history in bars_by_symbol.items()
        }
        control_ownership = observe_interval_ownership(
            observed_bars_by_symbol=control_histories,
            side=candidate.side,
            interval_open_time_ns=first_return_time_ns,
            interval_close_time_ns=control_completion_time_ns,
            observed_time_ns=control_completion_time_ns,
            campaign_root_id=campaign_root,
        )
        selected = control_ownership.for_symbol(self.symbol)

        inventory: InventoryDecision | None = None
        attack_evidence: dict[str, float | str | int | bool] = {}
        if isinstance(candidate, StructuralOpportunity):
            shock_side = (
                candidate.side
                if candidate.hypothesis is CampaignHypothesis.ACCEPTANCE
                else "SHORT" if candidate.side == "LONG" else "LONG"
            )
            measurement = self._router_attack_measurement(
                interval_open_time_ns=interval_open_time_ns,
                decision_time_ns=candidate.decision_time_ns,
            )
            if measurement is not None:
                price_move, signed_flow = measurement
                sign = 1.0 if shock_side == "LONG" else -1.0
                attack_evidence = {
                    "source_attack_side": shock_side,
                    "source_attack_price_move": price_move,
                    "source_attack_signed_taker_flow": signed_flow,
                    "source_attack_price_flow_aligned": (
                        sign * price_move > 0.0 and sign * signed_flow > 0.0
                    ),
                }
                inventory = self._router_inventory(
                    shock_side=shock_side,
                    interval_open_time_ns=interval_open_time_ns,
                    decision_time_ns=candidate.decision_time_ns,
                    price_move=price_move,
                    signed_taker_flow=signed_flow,
                )
        common_attack = None
        authorization: CommonCandidateAuthorization | None = None
        common_broad_failure_time_ns: int | None = None
        common_symbol_reclaim_time_ns: int | None = None
        if common_episodes is not None:
            try:
                common_attack = common_episodes.attack(campaign_root)
            except CommonEpisodeError:
                common_attack = None
        if common_attack is not None and common_episodes is not None:
            reclaim_pairs, broad_clock, _ = common_episodes.price_failure_state(
                campaign_root,
            )
            reclaim_clocks = dict(reclaim_pairs)
            symbol_clock = reclaim_clocks.get(self.symbol)
            if (
                broad_clock is not None
                and broad_clock <= candidate.decision_time_ns
                and symbol_clock is not None
                and symbol_clock <= candidate.decision_time_ns
            ):
                common_broad_failure_time_ns = broad_clock
                common_symbol_reclaim_time_ns = symbol_clock
        if (
            selected.role is SourceOwnershipRole.COMMON_MARKET_OWNER_ONLY
            and common_attack is not None
            and isinstance(candidate, StructuralOpportunity)
        ):
            branch = (
                CommonEpisodeFamily.CONTINUATION
                if candidate.hypothesis is CampaignHypothesis.ACCEPTANCE
                else CommonEpisodeFamily.REVERSAL
            )
            authorization = common_episodes.authorize_candidate(
                campaign_root,
                symbol=self.symbol,
                family=branch,
                side=candidate.side,
                candidate_time_ns=candidate.decision_time_ns,
                source_campaign_root_id=campaign_root,
            )
        direction_allowed, direction_evidence = self._directional_authorization(
            candidate,
            self._router_directional_authority.get(campaign_root),
            authorization,
            common_broad_failure_time_ns,
            common_symbol_reclaim_time_ns,
        )
        if not direction_allowed:
            reason = str(direction_evidence["direction_authority_reason"])
            key = f"DIRECTIONAL_AUTHORITY:{reason}"
            self._diagnostic_counts[key] = self._diagnostic_counts.get(key, 0) + 1
            return None
        plan = ControlEpisodeRouter.route(
            candidate,
            ownership,
            inventory=inventory,
            control_ownership=control_ownership,
            common_authorization=authorization,
            interaction_time_ns=interaction_time_ns,
            first_return_time_ns=first_return_time_ns,
        )
        if plan is None:
            return plan
        plan = replace(
            plan,
            evidence={
                **dict(plan.evidence),
                **direction_evidence,
                **attack_evidence,
            },
        )
        if common_attack is None:
            return plan
        # Even a locally owned residual response can originate inside a broad
        # physical attack.  It keeps its native family, while this association
        # lets an eventual account claim consume every sibling source root.
        if plan.evidence.get("route_owner") == "LOCAL_SOURCE_CAMPAIGN":
            plan = replace(
                plan,
                evidence={
                    **dict(plan.evidence),
                    "mapped_common_root_id": common_attack.root_id,
                    "mapped_common_sibling_root_id": common_attack.sibling_root_id,
                    "mapped_common_attack_side": common_attack.attack_side,
                    "mapped_common_attack_time_ns": common_attack.attack_time_ns,
                    "mapped_common_participant_source_roots": (
                        common_attack.participant_source_roots
                    ),
                },
            )
        return plan

    def evaluate_router_minute(
        self,
        bar: Bar,
        context: RouterPrebarContext,
        *,
        decision_bar: Bar | None,
        bars_by_symbol: Mapping[str, Sequence[Bar]],
        emit_plan: bool,
        common_episodes: CommonEpisodeLedger | None = None,
    ) -> list[TradePlan]:
        """Advance every active owner; ``emit_plan`` only gates submission."""

        serial = context.structure_serial
        is_decision_close = decision_bar is not None
        geometry_bar = decision_bar or bar
        opportunities: list[
            tuple[
                StructuralOpportunity | ValueDistributionCandidate,
                int,
                str,
                int,
                int,
            ]
        ] = []
        newly_opened: set[str] = set()
        newly_opened_static_source_ids: set[str] = set()

        if context.flow_baseline is not None:
            for item in context.sources:
                source_key = (item.boundary.boundary_id, item.generation)
                if source_key in self._router_opened_sources:
                    continue
                root = source_campaign_root_id(
                    source_identity=item.boundary.boundary_id,
                    source_generation=item.generation,
                    interaction_time_ns=bar.open_time_ns,
                )
                outward = "LONG" if item.boundary.side == "HIGH" else "SHORT"
                reversal = "SHORT" if outward == "LONG" else "LONG"
                acceptance_geometry = self._router_destination_geometry(
                    source=item.boundary,
                    side=outward,
                    family="ACCEPTANCE",
                    decision_bar=geometry_bar,
                    serial=context.structure_serial,
                )
                reversal_geometry = self._router_destination_geometry(
                    source=item.boundary,
                    side=reversal,
                    family="REVERSAL",
                    decision_bar=geometry_bar,
                    serial=context.structure_serial,
                )
                observation = CampaignObservation(
                    sequence=context.sequence,
                    structure_serial=context.structure_serial,
                    bar=bar,
                    is_decision_close=is_decision_close,
                    decision_bar=decision_bar,
                    acceptance_geometry=acceptance_geometry,
                    reversal_geometry=reversal_geometry,
                    acceptance_route_clear=acceptance_geometry is not None,
                    reversal_route_clear=reversal_geometry is not None,
                )
                transition = ParentCampaignOwner.open(
                    CampaignSeed(
                        source=item.boundary,
                        interaction=observation,
                        flow_baseline=context.flow_baseline,
                        tick_size=self.tick_size,
                    )
                )
                self._router_opened_sources.add(source_key)
                self._structural_campaigns[root] = transition.current
                self._structural_campaign_roots[transition.current.campaign_id] = root
                if item.authority is None:
                    raise RuntimeError("router source lost its pre-event authority")
                self._router_directional_authority[root] = item.authority
                newly_opened.add(root)
                if item.boundary.boundary_id in self.market.boundary_book.boundaries:
                    newly_opened_static_source_ids.add(item.boundary.boundary_id)
                if transition.opportunity is not None:
                    opportunities.append(
                        (
                            transition.opportunity,
                            bar.open_time_ns,
                            root,
                            transition.current.interaction_time_ns,
                            transition.opportunity.first_return_time_ns,
                        )
                    )

        for root, prior in tuple(self._structural_campaigns.items()):
            if root in newly_opened or prior.terminal:
                continue
            acceptance_side = prior.outward_side
            reversal_side = prior.reversal_side
            acceptance_geometry = self._router_destination_geometry(
                source=prior.source,
                side=acceptance_side,
                family="ACCEPTANCE",
                decision_bar=geometry_bar,
                serial=serial,
            )
            reversal_geometry = self._router_destination_geometry(
                source=prior.source,
                side=reversal_side,
                family="REVERSAL",
                decision_bar=geometry_bar,
                serial=serial,
            )
            acceptance_target = (
                prior.committed_geometry.committed_target
                if prior.hypothesis is CampaignHypothesis.ACCEPTANCE
                and prior.committed_geometry is not None
                else (
                    None
                    if acceptance_geometry is None
                    else acceptance_geometry.committed_target
                )
            )
            reversal_target = (
                prior.committed_geometry.committed_target
                if prior.hypothesis
                in {CampaignHypothesis.REJECTION, CampaignHypothesis.TRAP}
                and prior.committed_geometry is not None
                else (
                    None
                    if reversal_geometry is None
                    else reversal_geometry.committed_target
                )
            )
            observation = CampaignObservation(
                sequence=context.sequence,
                structure_serial=serial,
                bar=bar,
                is_decision_close=is_decision_close,
                decision_bar=decision_bar,
                refinement=self._router_refinement(prior, bar, serial),
                acceptance_geometry=acceptance_geometry,
                reversal_geometry=reversal_geometry,
                acceptance_route_clear=(
                    acceptance_target is not None
                    and self._router_route_clear(
                        source=prior.source,
                        side=acceptance_side,
                        target=acceptance_target,
                        visible_time_ns=geometry_bar.close_time_ns,
                        serial=serial,
                    )
                ),
                reversal_route_clear=(
                    reversal_target is not None
                    and self._router_route_clear(
                        source=prior.source,
                        side=reversal_side,
                        target=reversal_target,
                        visible_time_ns=geometry_bar.close_time_ns,
                        serial=serial,
                    )
                ),
            )
            transition = ParentCampaignOwner.advance(prior, observation)
            self._structural_campaigns[root] = transition.current
            if transition.opportunity is not None:
                opportunities.append(
                    (
                        transition.opportunity,
                        prior.interaction_open_time_ns,
                        root,
                        prior.interaction_time_ns,
                        transition.opportunity.first_return_time_ns,
                    )
                )

        value_candidates = self._value_distribution.on_bar(bar, spot_return=None)
        for candidate in value_candidates:
            if isinstance(candidate.evidence, dict):
                candidate.evidence["spot_confirmation"] = "UNAVAILABLE_FLOW_ONLY"
            source_time = int(
                candidate.evidence.get(
                    "departure_time_ns",
                    candidate.evidence.get(
                        "contact_time_ns",
                        candidate.entry_zone.source_bar_open_time_ns,
                    ),
                )
            )
            interval_open = self._interval_open_from_source_time(
                tuple(self.market.one_minute),
                source_time,
            )
            if interval_open is None:
                interval_open = candidate.entry_zone.source_bar_open_time_ns
            root = source_campaign_root_id(
                source_identity=candidate.source_object_id,
                source_generation=0,
                interaction_time_ns=interval_open,
            )
            opportunities.append(
                (
                    candidate,
                    interval_open,
                    root,
                    source_time,
                    int(
                        candidate.evidence.get(
                            "retest_time_ns",
                            candidate.entry_zone.observed_time_ns,
                        )
                    ),
                )
            )

        plans: list[TradePlan] = []
        for candidate, interval_open, root, interaction, first_return in opportunities:
            plan = self._route_completed_opportunity(
                candidate,
                interval_open_time_ns=interval_open,
                campaign_root=root,
                bars_by_symbol=bars_by_symbol,
                interaction_time_ns=interaction,
                first_return_time_ns=first_return,
                common_episodes=common_episodes,
            )
            if plan is not None and emit_plan:
                plans.append(plan)
        for root, state in tuple(self._structural_campaigns.items()):
            if not state.terminal:
                continue
            self._structural_campaigns.pop(root, None)
            self._structural_campaign_roots.pop(state.campaign_id, None)
            self._router_directional_authority.pop(root, None)
        # Source discovery was frozen before ingest and every campaign has now
        # seen this physical touch.  A canonical static source is spent by its
        # first contact, but only after that contact has opened its one parent.
        for boundary_id in newly_opened_static_source_ids:
            source = self.market.boundary_book.boundaries.get(boundary_id)
            if source is not None and source.consumed_time_ns is None:
                self.market.boundary_book.boundaries[boundary_id] = replace(
                    source,
                    consumed_time_ns=bar.close_time_ns,
                )
        self.market.boundary_book.mark_consumed(bar, context.structure_serial)
        return self._refresh_router_proposals(plans, bar) if emit_plan else []

    def _refresh_router_proposals(
        self,
        plans: Sequence[TradePlan],
        bar: Bar,
    ) -> list[TradePlan]:
        """Maintain family-owned plans without global objective replacement."""

        for episode_id, proposal in tuple(self._proposals.items()):
            reason = ""
            if episode_id in self._used_episodes:
                reason = "PROPOSAL_ALREADY_CLAIMED"
            elif (
                proposal.entry_lifecycle == "IMMEDIATE_RESPONSE"
                and bar.close_time_ns > proposal.decision_time_ns
            ):
                reason = "IMMEDIATE_RESPONSE_DECISION_EXPIRED"
            elif bar.close_time_ns > proposal.decision_time_ns:
                if (
                    proposal.side == "LONG" and bar.low <= proposal.stop
                    or proposal.side == "SHORT" and bar.high >= proposal.stop
                ):
                    reason = "FAMILY_INVALIDATION_REACHED_BEFORE_ENTRY"
                elif (
                    proposal.side == "LONG" and bar.high >= proposal.target
                    or proposal.side == "SHORT" and bar.low <= proposal.target
                ):
                    reason = "FAMILY_DESTINATION_SPENT_BEFORE_ENTRY"
                elif (
                    proposal.entry_lifecycle == "RESTING_FIRST_RETURN"
                    and (
                        proposal.side == "LONG" and bar.low <= proposal.entry
                        or proposal.side == "SHORT" and bar.high >= proposal.entry
                    )
                ):
                    reason = "FIRST_RETURN_ALREADY_PASSED"
            if not reason:
                continue
            self._record(reason, None, bar, plan_id=proposal.plan_id)
            self._queue_terminal_decision(
                outcome="NO_TRADE",
                stage="POLICY",
                reason=reason,
                terminal_time_ns=bar.close_time_ns,
                plan=proposal,
            )
            self._proposals.pop(episode_id, None)

        for plan in ControlEpisodeRouter.arbitrate(plans):
            if (
                plan.episode_id not in self._used_episodes
                and plan.episode_id not in self._terminalized_episodes
            ):
                existing = self._proposals.get(plan.episode_id)
                if existing is None:
                    self._proposals[plan.episode_id] = plan
                else:
                    self._proposals[plan.episode_id] = ControlEpisodeRouter.arbitrate(
                        (existing, plan)
                    )[0]
        return list(ControlEpisodeRouter.arbitrate(tuple(self._proposals.values())))

    def suppress_claimed_root(
        self,
        root_id: str,
        selected_plan_id: str,
        *,
        time_ns: int,
    ) -> None:
        """Consume every unselected sibling of a claimed physical root."""

        for episode_id, proposal in tuple(self._proposals.items()):
            if causal_root_id(proposal) != root_id or proposal.plan_id == selected_plan_id:
                continue
            self._queue_terminal_decision(
                outcome="NO_TRADE",
                stage="ARBITRATION",
                reason="CAUSAL_ROOT_CLAIMED_BY_SIBLING",
                terminal_time_ns=time_ns,
                plan=proposal,
                details={"selected_plan_id": selected_plan_id},
            )
            self._proposals.pop(episode_id, None)
            self._terminalized_episodes[episode_id] = "CAUSAL_ROOT_CLAIMED_BY_SIBLING"

    def _sync_structural_books(self) -> None:
        """Feed causal bars/pivots into RE1's versioned feasible geometry."""

        histories = {
            15: self.market.fifteen_minute,
            60: self.market.sixty_minute,
        }
        for timeframe, history in histories.items():
            book = self._trend_channel_books[timeframe]
            start = self._structural_bar_counts[timeframe]
            for item in history[start:]:
                book.observe_bar(item)
            self._structural_bar_counts[timeframe] = len(history)
            pivots = [
                pivot
                for side in ("LOW", "HIGH")
                for pivot in self.market.boundary_book.pivots_by_tf_side.get(
                    (timeframe, side), ()
                )
            ]
            for pivot in sorted(
                pivots, key=lambda item: (item.observed_time_ns, item.pivot_id),
            ):
                if pivot.pivot_id in self._structural_pivot_ids:
                    continue
                book.observe_pivot(pivot)
                self._structural_pivot_ids.add(pivot.pivot_id)
        self._sync_continuation_direction()

    def set_common_factor_state(self, state: CommonFactorState | None) -> None:
        self._common_factor_state = state

    def _sync_continuation_direction(self) -> None:
        """Track completed 15m BOS; the pivot never predicts its own break."""

        history = self.market.fifteen_minute
        for bar in history[self._continuation_direction_bar_count :]:
            breaks: list[tuple[str, Pivot]] = []
            for side in ("HIGH", "LOW"):
                for pivot in self.market.boundary_book.pivots_by_tf_side.get(
                    (15, side),
                    (),
                ):
                    if (
                        pivot.pivot_id in self._continuation_broken_pivot_ids
                        or pivot.observed_time_ns >= bar.close_time_ns
                    ):
                        continue
                    direction = (
                        "LONG"
                        if side == "HIGH" and bar.close > pivot.price
                        else "SHORT"
                        if side == "LOW" and bar.close < pivot.price
                        else None
                    )
                    if direction is not None:
                        self._continuation_broken_pivot_ids.add(pivot.pivot_id)
                        breaks.append((direction, pivot))
            if breaks:
                direction, pivot = max(
                    breaks,
                    key=lambda item: (
                        item[1].event_time_ns,
                        item[1].observed_time_ns,
                        item[1].pivot_id,
                    ),
                )
                self._continuation_local_side = direction
                self._continuation_direction_pivot_id = pivot.pivot_id
                self._continuation_direction_event_time_ns = bar.close_time_ns
        self._continuation_direction_bar_count = len(history)

    def _projected_structural_nodes(
        self, decision_time_ns: int, serial: int,
    ) -> list[StructuralNode]:
        output: list[StructuralNode] = []
        for timeframe, book in self._trend_channel_books.items():
            if not book.bars:
                continue
            local_serial = len(book.bars) - 1
            # FeasibleTrendChannelBook slopes are expressed per local HTF bar.
            # Convert the already-feasible current projection onto the global
            # five-minute decision clock exactly once.
            # ``serial_5m`` is replay-local, never an epoch bucket.  Resolve
            # the last constituent 5m bar by its open timestamp, which is
            # invariant to exact-edge vs ``edge-1`` close conventions.
            final_five_open = (
                book.bars[-1].open_time_ns
                + (timeframe - 5) * NS_PER_MINUTE
            )
            global_anchor = next(
                (
                    index
                    for index in range(len(self.market.five_minute) - 1, -1, -1)
                    if self.market.five_minute[index].open_time_ns == final_five_open
                ),
                None,
            )
            if global_anchor is None:
                reason = "STRUCTURAL_CLOCK_ANCHOR_UNAVAILABLE"
                self._diagnostic_counts[reason] = (
                    self._diagnostic_counts.get(reason, 0) + 1
                )
                continue
            slope_scale = 5.0 / float(timeframe)
            for node in book.projected_nodes(decision_time_ns, local_serial):
                lower, upper = node.band_at(local_serial)
                slope_5m = node.slope_per_bar * slope_scale
                shift = slope_5m * (serial - global_anchor)
                output.append(
                    StructuralNode(
                        node_id=node.node_id,
                        symbol=node.symbol,
                        side=node.side,
                        kind=node.kind,
                        role=node.role,
                        timeframe_minutes=node.timeframe_minutes,
                        observed_time_ns=node.observed_time_ns,
                        lower=lower + shift,
                        upper=upper + shift,
                        anchor_serial=serial,
                        slope_per_bar=slope_5m,
                        version=node.version,
                        invalidation=(
                            None if node.invalidation is None
                            else node.invalidation + shift
                        ),
                        consumed_time_ns=node.consumed_time_ns,
                        superseded_time_ns=node.superseded_time_ns,
                    )
                )
        return output

    @staticmethod
    def _boundary_from_structural_node(
        node: StructuralNode, serial: int,
    ) -> LiquidityBoundary:
        lower, upper = node.band_at(serial)
        return LiquidityBoundary(
            boundary_id=node.node_id,
            symbol=node.symbol,
            side=node.side,
            kind=node.kind,
            timeframe_minutes=node.timeframe_minutes,
            observed_time_ns=node.observed_time_ns,
            lower=lower,
            upper=upper,
            price=(lower + upper) / 2.0,
            strength=3.0,
            dynamic_slope_per_bar=node.slope_per_bar,
            anchor_serial=serial,
            consumed_time_ns=node.consumed_time_ns,
        )

    def _finish_continuation_setup(self, episode_id: str, reason: str) -> None:
        self._continuation_setups.pop(episode_id, None)
        self._diagnostic_counts[reason] = self._diagnostic_counts.get(reason, 0) + 1

    def _observe_local_continuation_five(self, bar: Bar) -> None:
        """Let local BOS own a flow-validated 5m first-return location.

        This is the later RE1 responsibility split.  The common-market factor
        is not an AND gate which suppresses otherwise complete local auctions;
        it can only veto formation or first return when it is actively opposite.
        """

        if len(self.market.five_minute) < 2:
            return
        raw = five_minute_engulfing_ob(
            self.market.five_minute[-2],
            bar,
            tick_size=self.tick_size,
        )
        if raw is None:
            return
        source, invalidation, ratio = raw
        if source.boundary_id in self._continuation_seen_source_ids:
            return
        self._continuation_seen_source_ids.add(source.boundary_id)
        side = "LONG" if source.side == "LOW" else "SHORT"
        factor = self._common_factor_state
        if factor is not None and factor.side != side:
            self._diagnostic_counts["LOCAL_CONTINUATION_FORMATION_COMMON_VETO"] = (
                self._diagnostic_counts.get(
                    "LOCAL_CONTINUATION_FORMATION_COMMON_VETO",
                    0,
                )
                + 1
            )
            return
        if (
            self._continuation_local_side != side
            or self._continuation_direction_pivot_id is None
            or self._continuation_direction_event_time_ns is None
        ):
            self._diagnostic_counts["LOCAL_CONTINUATION_WITHOUT_ALIGNED_15M_BOS"] = (
                self._diagnostic_counts.get(
                    "LOCAL_CONTINUATION_WITHOUT_ALIGNED_15M_BOS",
                    0,
                )
                + 1
            )
            return
        observations = self.factor_flow.between(
            self.market.five_minute[-2].close_time_ns,
            bar.close_time_ns,
        )
        if not observations:
            return
        direction = 1.0 if side == "LONG" else -1.0
        cumulative_flow = direction * sum(
            item.signed_taker_quote for item in observations
        )
        progress = direction * (observations[-1].close - observations[0].open)
        coherent = any(
            item.active
            and item.directed
            and item.material_progress
            and direction * item.signed_taker_quote > 0.0
            and direction * item.body > 0.0
            for item in observations
        )
        if cumulative_flow <= 0.0 or progress <= 0.0 or not coherent:
            self._diagnostic_counts["LOCAL_CONTINUATION_WITHOUT_FORMATION_FLOW"] = (
                self._diagnostic_counts.get(
                    "LOCAL_CONTINUATION_WITHOUT_FORMATION_FLOW",
                    0,
                )
                + 1
            )
            return
        reference = bar.high if side == "LONG" else bar.low
        destinations = self.market.objective_book.destination_candidates_at(
            side=side,
            reference_price=reference,
            decision_time_ns=bar.close_time_ns,
            source_boundary_id=source.boundary_id,
        )
        if not destinations:
            self._diagnostic_counts["LOCAL_CONTINUATION_WITHOUT_OBJECTIVE"] = (
                self._diagnostic_counts.get(
                    "LOCAL_CONTINUATION_WITHOUT_OBJECTIVE",
                    0,
                )
                + 1
            )
            return
        destination = destinations[0]
        episode_id = stable_id(
            self.symbol,
            self._continuation_direction_event_time_ns,
            source.boundary_id,
            self._continuation_direction_pivot_id,
            prefix="LOCAL_CONTINUATION_EP:",
        )
        if (
            episode_id in self._continuation_setups
            or episode_id in self._used_episodes
            or episode_id in self._terminalized_episodes
        ):
            return
        self._continuation_setups[episode_id] = LocalAuctionContinuationSetup(
            episode_id=episode_id,
            symbol=self.symbol,
            side=side,
            source=source,
            source_invalidation=invalidation,
            source_strength_ratio=ratio,
            local_direction_pivot_id=self._continuation_direction_pivot_id,
            local_direction_event_time_ns=(
                self._continuation_direction_event_time_ns
            ),
            destination=destination,
            objective_commit_time_ns=bar.close_time_ns,
            formation_factor_side=None if factor is None else factor.side,
            formation_factor_event_time_ns=(
                None if factor is None else factor.event_time_ns
            ),
            formation_factor_sequence=(
                None if factor is None else factor.sequence
            ),
            formation_factor_agreeing_symbols=(
                () if factor is None else factor.agreeing_symbols
            ),
        )
        self._diagnostic_counts["LOCAL_CONTINUATION_ARMED"] = (
            self._diagnostic_counts.get("LOCAL_CONTINUATION_ARMED", 0) + 1
        )

    def _local_continuation_route_nodes(
        self,
        setup: LocalAuctionContinuationSetup,
        *,
        decision_time_ns: int,
        serial: int,
    ) -> tuple[StructuralNode, list[StructuralNode]]:
        destination = setup.destination
        if destination is None:
            raise RuntimeError("local continuation has no committed destination")
        source = StructuralNode(
            node_id=setup.source.boundary_id,
            symbol=self.symbol,
            side=setup.source.side,
            kind=setup.source.kind,
            role=StructureRole.SOURCE,
            timeframe_minutes=5,
            observed_time_ns=setup.source.observed_time_ns,
            lower=setup.source.lower,
            upper=setup.source.upper,
            anchor_serial=serial,
            invalidation=setup.source_invalidation,
        )
        target_price = self._objective_execution_price(
            setup.side,
            destination.price,
        )
        nodes = [
            StructuralNode(
                node_id=destination.boundary_id,
                symbol=self.symbol,
                side=destination.side,
                kind=destination.kind,
                role=StructureRole.DESTINATION,
                timeframe_minutes=destination.timeframe_minutes,
                observed_time_ns=destination.observed_time_ns,
                lower=target_price,
                upper=target_price,
                anchor_serial=serial,
                consumed_time_ns=destination.consumed_time_ns,
            ),
        ]
        nodes.extend(
            node.as_role(StructureRole.ROUTE_OBSTACLE)
            for node in self._projected_structural_nodes(
                decision_time_ns,
                serial,
            )
        )
        return source, nodes

    def _build_local_continuation_plan(
        self,
        setup: LocalAuctionContinuationSetup,
        bar: Bar,
        serial: int,
        response_mechanism: str,
    ) -> TradePlan | None:
        destination = setup.destination
        if destination is None:
            return None
        entry = bar.close
        stop = setup.source_invalidation
        source, nodes = self._local_continuation_route_nodes(
            setup,
            decision_time_ns=bar.close_time_ns,
            serial=serial,
        )
        route = destination_first_geometry(
            side=setup.side,
            source=source,
            nodes=nodes,
            entry=entry,
            stop=stop,
            decision_time_ns=bar.close_time_ns,
            serial=serial,
            minimum_gross_rr=1.0,
        )
        if not route.accepted or route.destination is None or route.target is None:
            self._diagnostic_counts[f"LOCAL_CONTINUATION_{route.reason}"] = (
                self._diagnostic_counts.get(
                    f"LOCAL_CONTINUATION_{route.reason}",
                    0,
                )
                + 1
            )
            return None
        episode_id = setup.episode_id
        plan_id = stable_id(
            episode_id,
            entry,
            stop,
            route.target,
            bar.close_time_ns,
            prefix="PLAN:",
        )
        evidence: dict[str, float | str | int] = {
            "source_kind": setup.source.kind,
            "source_side": setup.source.side,
            "source_observed_time_ns": setup.source.observed_time_ns,
            "source_timeframe_minutes": 5,
            "destination_kind": route.destination.kind,
            "destination_observed_time_ns": route.destination.observed_time_ns,
            "interaction_time_ns": int(
                setup.first_touch_time_ns or setup.source.observed_time_ns
            ),
            "first_touch_time_ns": int(setup.first_touch_time_ns or 0),
            "interaction_source_lower": setup.source.lower,
            "interaction_source_upper": setup.source.upper,
            "source_strength_ratio": setup.source_strength_ratio,
            "local_direction_pivot_id": setup.local_direction_pivot_id,
            "local_direction_event_time_ns": setup.local_direction_event_time_ns,
            "direction_ownership_role": "LOCAL_15M_BOS_PLUS_FORMATION_FLOW",
            "directional_posterior_support_state": "SUPPORTED",
            "directional_posterior_support_rank": 1,
            "directional_family_transition_state": "LOCAL_AUCTION_CONTINUED",
            "directional_family_transition_rank": 1,
            "cross_market_ownership_mode": "LOCAL_SOURCE",
            "source_ownership_role": SourceOwnershipRole.LOCAL_SOURCE_OWNER.value,
            "common_factor_at_formation": (
                setup.formation_factor_side or "NEUTRAL"
            ),
            "objective_commit_time_ns": setup.objective_commit_time_ns,
            "objective_revision_count": setup.objective_revision_count,
            "objective_rearm_after_ns": int(setup.objective_rearm_after_ns or 0),
            "committed_destination_boundary_id": destination.boundary_id,
            "committed_destination_price": destination.price,
            "route_rr": float(route.gross_rr or 0.0),
            "entry_event": "ACCEPTANCE_FIRST_RESPONSE_CLOSE",
            "entry_execution_instruction": "IMMEDIATE_MARKETABLE_FIRST_RESPONSE",
            "response_flow_mechanism": response_mechanism,
            "completion_target_origin": (
                "PRE_TOUCH_COMMITTED_NEAREST_SIGNIFICANT_1M_5M_15M_OBJECTIVE"
            ),
            "complete_episode_invalidation": stop,
        }
        if setup.formation_factor_event_time_ns is not None:
            evidence["formation_factor_event_time_ns"] = (
                setup.formation_factor_event_time_ns
            )
        if setup.formation_factor_sequence is not None:
            evidence["formation_factor_sequence"] = setup.formation_factor_sequence
        if setup.formation_factor_agreeing_symbols:
            evidence["formation_factor_agreeing_symbols"] = ",".join(
                setup.formation_factor_agreeing_symbols,
            )
        return TradePlan(
            episode_id=episode_id,
            plan_id=plan_id,
            symbol=self.symbol,
            family="LOCAL_AUCTION_CONTINUATION",
            side=setup.side,
            decision_time_ns=bar.close_time_ns,
            entry=entry,
            stop=stop,
            target=route.target,
            expires_time_ns=MAX_CAUSAL_ORDER_TIME_NS,
            source_boundary_id=setup.source.boundary_id,
            destination_boundary_id=route.destination.node_id,
            entry_zone=EntryZone(
                "FLOW_VALIDATED_5M_ORDER_BLOCK_FIRST_RETURN",
                setup.source.lower,
                setup.source.upper,
                setup.source.observed_time_ns,
                setup.source.observed_time_ns,
            ),
            evidence=evidence,
        )

    def _local_continuation_response_mechanism(
        self,
        setup: LocalAuctionContinuationSetup,
        bar: Bar,
    ) -> str | None:
        """Confirm control transfer on the one allowed response minute."""

        observation = self.factor_flow.last_observation
        if observation is None or observation.time_ns != bar.close_time_ns:
            return None
        if not observation.active or not observation.directed:
            return None
        direction = 1.0 if setup.side == "LONG" else -1.0
        intended_body = direction * observation.body > 0.0
        signed_flow = direction * observation.signed_taker_quote
        if signed_flow > 0.0 and intended_body and observation.material_progress:
            return "FIRST_RESPONSE_ALIGNED_INITIATIVE"
        if signed_flow < 0.0 and intended_body:
            return "FIRST_RESPONSE_ADVERSE_FLOW_ABSORBED"
        return None

    def _advance_local_continuation_setups(
        self,
        bar: Bar,
        serial: int,
    ) -> list[TradePlan]:
        output: list[TradePlan] = []
        for episode_id, setup in list(self._continuation_setups.items()):
            if self._continuation_local_side != setup.side:
                self._finish_continuation_setup(
                    episode_id,
                    "LOCAL_CONTINUATION_DIRECTION_CHANGED_BEFORE_ENTRY",
                )
                continue
            if bar.close_time_ns <= setup.source.observed_time_ns:
                continue
            stop_touched = (
                bar.low <= setup.source_invalidation
                if setup.side == "LONG"
                else bar.high >= setup.source_invalidation
            )
            if stop_touched:
                self._finish_continuation_setup(
                    episode_id,
                    "LOCAL_CONTINUATION_SOURCE_INVALIDATED_BEFORE_ENTRY",
                )
                continue
            reference_entry = (
                setup.source.upper if setup.side == "LONG" else setup.source.lower
            )
            current_target = (
                None
                if setup.destination is None
                else self.market.objective_book.objectives.get(
                    setup.destination.boundary_id,
                )
            )
            if setup.destination is not None and current_target is None:
                raise RuntimeError("committed continuation objective disappeared")
            if (
                current_target is not None
                and current_target.consumed_time_ns is not None
                and current_target.consumed_time_ns <= bar.close_time_ns
            ):
                if setup.first_touch_time_ns is not None:
                    self._finish_continuation_setup(
                        episode_id,
                        "LOCAL_CONTINUATION_DESTINATION_SPENT_AFTER_FIRST_TOUCH",
                    )
                    continue
                # No entry event exists yet.  Completion of the provisional
                # route does not revive an older farther target; it leaves the
                # still-untouched source without a destination until price
                # creates a genuinely later opposing objective.
                setup.destination = None
                setup.objective_rearm_after_ns = current_target.consumed_time_ns
                setup.objective_commit_time_ns = current_target.consumed_time_ns
                self._diagnostic_counts[
                    "LOCAL_CONTINUATION_DESTINATION_SPENT_AWAITING_FRESH_OBJECTIVE"
                ] = (
                    self._diagnostic_counts.get(
                        "LOCAL_CONTINUATION_DESTINATION_SPENT_AWAITING_FRESH_OBJECTIVE",
                        0,
                    )
                    + 1
                )
                current_target = None

            if current_target is not None:
                target = self._objective_execution_price(
                    side=setup.side,
                    pivot_price=current_target.price,
                )
                closer_objectives = self._new_closer_objectives(
                    side=setup.side,
                    entry=reference_entry,
                    target=target,
                    destination_boundary_id=current_target.boundary_id,
                    source_boundary_id=setup.source.boundary_id,
                    decision_time_ns=bar.close_time_ns,
                    route_commit_time_ns=setup.objective_commit_time_ns,
                )
                if closer_objectives and setup.first_touch_time_ns is not None:
                    self._finish_continuation_setup(
                        episode_id,
                        "LOCAL_CONTINUATION_ROUTE_CHANGED_AFTER_FIRST_TOUCH",
                    )
                    continue
                if closer_objectives:
                    closer = closer_objectives[0]
                    if (
                        closer.consumed_time_ns is not None
                        and closer.consumed_time_ns <= bar.close_time_ns
                    ):
                        setup.destination = None
                        setup.objective_rearm_after_ns = closer.consumed_time_ns
                        setup.objective_commit_time_ns = closer.consumed_time_ns
                        self._diagnostic_counts[
                            "LOCAL_CONTINUATION_CLOSER_SPENT_AWAITING_FRESH_OBJECTIVE"
                        ] = (
                            self._diagnostic_counts.get(
                                "LOCAL_CONTINUATION_CLOSER_SPENT_AWAITING_FRESH_OBJECTIVE",
                                0,
                            )
                            + 1
                        )
                    else:
                        setup.destination = closer
                        setup.objective_commit_time_ns = closer.observed_time_ns
                        setup.objective_revision_count += 1
                        self._diagnostic_counts[
                            "LOCAL_CONTINUATION_OBJECTIVE_RECOMMITTED_CLOSER"
                        ] = (
                            self._diagnostic_counts.get(
                                "LOCAL_CONTINUATION_OBJECTIVE_RECOMMITTED_CLOSER",
                                0,
                            )
                            + 1
                        )

            if setup.destination is None and setup.first_touch_time_ns is None:
                rearm_after = int(setup.objective_rearm_after_ns or 0)
                fresh = [
                    objective
                    for objective in self.market.objective_book.destination_candidates_at(
                        side=setup.side,
                        reference_price=reference_entry,
                        decision_time_ns=bar.close_time_ns,
                        source_boundary_id=setup.source.boundary_id,
                    )
                    if objective.observed_time_ns > rearm_after
                ]
                if fresh:
                    setup.destination = fresh[0]
                    setup.objective_commit_time_ns = fresh[0].observed_time_ns
                    setup.objective_revision_count += 1
                    self._diagnostic_counts[
                        "LOCAL_CONTINUATION_OBJECTIVE_RECONSTITUTED_BEFORE_TOUCH"
                    ] = (
                        self._diagnostic_counts.get(
                            "LOCAL_CONTINUATION_OBJECTIVE_RECONSTITUTED_BEFORE_TOUCH",
                            0,
                        )
                        + 1
                    )
            if setup.destination is not None:
                executable_target = self._objective_execution_price(
                    setup.side,
                    setup.destination.price,
                )
                executable_target_touched = (
                    bar.high >= executable_target
                    if setup.side == "LONG"
                    else bar.low <= executable_target
                )
                if executable_target_touched:
                    if setup.first_touch_time_ns is not None:
                        self._finish_continuation_setup(
                            episode_id,
                            "LOCAL_CONTINUATION_EXECUTABLE_DESTINATION_SPENT_AFTER_FIRST_TOUCH",
                        )
                        continue
                    # The executable TP is one tick inside the pivot, so this
                    # bar can spend the provisional route without consuming
                    # the raw pivot identity.  Apply the same no-fallback rule
                    # and require a genuinely later objective before touch.
                    setup.destination = None
                    setup.objective_rearm_after_ns = bar.close_time_ns
                    setup.objective_commit_time_ns = bar.close_time_ns
                    self._diagnostic_counts[
                        "LOCAL_CONTINUATION_EXECUTABLE_DESTINATION_SPENT_AWAITING_FRESH_OBJECTIVE"
                    ] = (
                        self._diagnostic_counts.get(
                            "LOCAL_CONTINUATION_EXECUTABLE_DESTINATION_SPENT_AWAITING_FRESH_OBJECTIVE",
                            0,
                        )
                        + 1
                    )
            if setup.first_touch_time_ns is None:
                if not (
                    bar.low <= setup.source.upper
                    and bar.high >= setup.source.lower
                ):
                    continue
                if setup.destination is None:
                    self._finish_continuation_setup(
                        episode_id,
                        "LOCAL_CONTINUATION_NO_FRESH_OBJECTIVE_AT_FIRST_TOUCH",
                    )
                    continue
                factor = self._common_factor_state
                if factor is not None and factor.side != setup.side:
                    self._finish_continuation_setup(
                        episode_id,
                        "LOCAL_CONTINUATION_FIRST_TOUCH_COMMON_FACTOR_VETO",
                    )
                    continue
                setup.first_touch_time_ns = bar.close_time_ns
                setup.touch_high = bar.high
                setup.touch_low = bar.low
                continue
            assert setup.touch_high is not None and setup.touch_low is not None
            factor = self._common_factor_state
            if factor is not None and factor.side != setup.side:
                self._finish_continuation_setup(
                    episode_id,
                    "LOCAL_CONTINUATION_RESPONSE_COMMON_FACTOR_VETO",
                )
                continue
            confirms = (
                bar.close > setup.touch_high
                if setup.side == "LONG"
                else bar.close < setup.touch_low
            )
            if not confirms:
                self._finish_continuation_setup(
                    episode_id,
                    "LOCAL_CONTINUATION_FIRST_RESPONSE_FAILED",
                )
                continue
            response_mechanism = self._local_continuation_response_mechanism(
                setup,
                bar,
            )
            if response_mechanism is None:
                self._finish_continuation_setup(
                    episode_id,
                    "LOCAL_CONTINUATION_FIRST_RESPONSE_WITHOUT_FLOW_TRANSFER",
                )
                continue
            plan = self._build_local_continuation_plan(
                setup,
                bar,
                serial,
                response_mechanism,
            )
            self._finish_continuation_setup(
                episode_id,
                "LOCAL_CONTINUATION_PLANNED"
                if plan is not None
                else "LOCAL_CONTINUATION_NO_TRADE_GEOMETRY",
            )
            if plan is not None:
                output.append(plan)
        return output

    def evaluate_minute(
        self,
        bar: Bar,
        *,
        bars_by_symbol: Mapping[str, Sequence[Bar]] | None = None,
    ) -> list[TradePlan]:
        raise RuntimeError(
            "legacy per-symbol evaluation is disabled; use "
            "LiquidityEpisodeCoordinator.push_bar()",
        )

    def evaluate_five_minute(
        self,
        bar: Bar,
        common_breadth: float,
        bars_by_symbol: Mapping[str, Sequence[Bar]] | None = None,
        *,
        interaction_bar: Bar | None = None,
    ) -> list[TradePlan]:
        raise RuntimeError(
            "legacy five-minute evaluation is disabled; use "
            "LiquidityEpisodeCoordinator.push_bar()",
        )

    def _refresh_proposals(self, plans: Sequence[TradePlan], bar: Bar) -> list[TradePlan]:
        # No clock expiry: an unfilled first return ends only when price passes
        # it, the complete episode invalidates, or the destination is consumed.
        existing_structural_ids = self._existing_projected_structure_ids(
            bar.close_time_ns, self.market.serial_5m,
        )
        self._invalidate_superseded_claimed_structures(
            bar, existing_structural_ids,
        )
        self._invalidate_claimed_objective_routes(bar)
        for episode_id, proposal in list(self._proposals.items()):
            reason = ""
            if episode_id in self._used_episodes:
                reason = "PROPOSAL_ALREADY_CLAIMED"
            elif (
                self._is_versioned_structural_kind(
                    str(proposal.evidence.get("source_kind", ""))
                )
                and proposal.source_boundary_id not in existing_structural_ids
            ):
                reason = "STRUCTURAL_SOURCE_VERSION_SUPERSEDED"
            elif (
                self._is_versioned_structural_kind(
                    str(proposal.evidence.get("destination_kind", ""))
                )
                and proposal.destination_boundary_id not in existing_structural_ids
            ):
                reason = "STRUCTURAL_DESTINATION_VERSION_SUPERSEDED"
            elif self._has_new_closer_objective(
                side=proposal.side,
                entry=proposal.entry,
                target=proposal.target,
                destination_boundary_id=proposal.destination_boundary_id,
                source_boundary_id=proposal.source_boundary_id,
                decision_time_ns=bar.close_time_ns,
                route_commit_time_ns=int(
                    proposal.evidence.get(
                        "objective_commit_time_ns",
                        proposal.decision_time_ns,
                    ),
                ),
            ):
                reason = "ROUTE_CHANGED_BY_NEW_CLOSER_OBJECTIVE"
            elif (
                str(proposal.evidence.get("entry_event", ""))
                == "ACCEPTANCE_FIRST_RESPONSE_CLOSE"
                and bar.close_time_ns > proposal.decision_time_ns
            ):
                # A completed first response is a one-close decision, never a
                # pending signal.  Same-close execution rejection may still
                # cascade to another proposal; no loser survives into a later
                # minute as a stale IOC.
                reason = "IMMEDIATE_RESPONSE_DECISION_EXPIRED"
            elif bar.close_time_ns > proposal.decision_time_ns:
                if proposal.side == "LONG" and bar.low <= proposal.stop or proposal.side == "SHORT" and bar.high >= proposal.stop:
                    reason = "COMPLETE_EPISODE_INVALIDATED_BEFORE_ENTRY"
                elif proposal.side == "LONG" and bar.high >= proposal.target or proposal.side == "SHORT" and bar.low <= proposal.target:
                    reason = "DESTINATION_SPENT_BEFORE_ENTRY"
                elif proposal.side == "LONG" and bar.low <= proposal.entry or proposal.side == "SHORT" and bar.high >= proposal.entry:
                    reason = "FIRST_RETURN_ALREADY_PASSED"
            if reason:
                watch = self._watches.get(episode_id)
                if watch is None:
                    self._record(reason, None, bar, plan_id=proposal.plan_id)
                    self._queue_terminal_decision(
                        outcome="NO_TRADE",
                        stage="POLICY",
                        reason=reason,
                        terminal_time_ns=bar.close_time_ns,
                        plan=proposal,
                        details={"plan_id": proposal.plan_id},
                    )
                else:
                    self._record_terminal(
                        reason,
                        watch,
                        bar,
                        plan=proposal,
                        plan_id=proposal.plan_id,
                    )
                self._proposals.pop(episode_id, None)
                self._watches.pop(episode_id, None)
        for plan in plans:
            if (
                plan.episode_id not in self._used_episodes
                and plan.episode_id not in self._terminalized_episodes
            ):
                self._proposals[plan.episode_id] = plan
        if not self._proposals:
            return []
        return [min(self._proposals.values(), key=self._arbitration_key)]

    @staticmethod
    def _is_versioned_structural_kind(kind: str) -> bool:
        upper = kind.upper()
        return "LINE" in upper or "CHANNEL" in upper

    def _existing_projected_structure_ids(
        self, decision_time_ns: int, serial: int,
    ) -> set[str]:
        """Return observable versions, including an already-touched edge.

        ``projected_nodes`` removes genuinely superseded versions itself.  A
        consumed edge remains only as the identity of its already-started
        episode; fresh source discovery cannot reattack retired geometry.
        """

        active = {
            node.node_id
            for node in self._projected_structural_nodes(decision_time_ns, serial)
        }
        for book in self._trend_channel_books.values():
            active.update(book.known_node_ids(decision_time_ns))
        return active

    def _invalidate_superseded_claimed_structures(
        self, bar: Bar, existing_structural_ids: set[str],
    ) -> None:
        for episode_id, metadata in self._claimed_plan_metadata.items():
            plan_id = str(metadata["plan_id"])
            if plan_id in self._invalidated_claimed_plans:
                continue
            source_kind = str(metadata.get("source_kind", ""))
            source_id = str(metadata.get("source_boundary_id", ""))
            destination_kind = str(metadata.get("destination_kind", ""))
            destination_id = str(metadata.get("destination_boundary_id", ""))
            if (
                self._is_versioned_structural_kind(source_kind)
                and source_id not in existing_structural_ids
            ):
                reason = "STRUCTURAL_SOURCE_VERSION_SUPERSEDED"
            elif (
                self._is_versioned_structural_kind(destination_kind)
                and destination_id not in existing_structural_ids
            ):
                reason = "STRUCTURAL_DESTINATION_VERSION_SUPERSEDED"
            else:
                continue
            self._invalidated_claimed_plans[plan_id] = {
                "episode_id": episode_id,
                "reason": reason,
                "time_ns": bar.close_time_ns,
                "superseding_episode_id": "STRUCTURAL_VERSION_LEDGER",
            }
            self._diagnostic_counts[reason] = self._diagnostic_counts.get(reason, 0) + 1

    def _new_closer_objectives(
        self,
        *,
        side: str,
        entry: float,
        target: float,
        destination_boundary_id: str,
        source_boundary_id: str,
        decision_time_ns: int,
        route_commit_time_ns: int,
    ) -> list[LiquidityBoundary]:
        wanted = "HIGH" if side == "LONG" else "LOW"
        direction = 1.0 if side == "LONG" else -1.0
        # Retain consumed history: a closer objective which appeared and was
        # spent cannot be forgotten merely because it is no longer active.
        candidates = [
            objective
            for objective_id, objective in self.market.objective_book.objectives.items()
            if objective.side == wanted
            and route_commit_time_ns < objective.observed_time_ns < decision_time_ns
            and self.market.objective_book.source_boundary_by_objective.get(
                objective_id,
            ) != source_boundary_id
            and direction * (
                self._objective_execution_price(side, objective.price) - entry
            ) > self.tick_size
        ]
        if not candidates:
            return []
        planned_distance = abs(target - entry)
        closer = [
            objective
            for objective in candidates
            if objective.boundary_id != destination_boundary_id
            and abs(self._objective_execution_price(side, objective.price) - entry)
            < planned_distance - 0.5 * self.tick_size
        ]
        return sorted(
            closer,
            key=lambda objective: (
                abs(self._objective_execution_price(side, objective.price) - entry),
                -objective.timeframe_minutes,
                -objective.strength,
                objective.boundary_id,
            ),
        )

    def _has_new_closer_objective(
        self,
        *,
        side: str,
        entry: float,
        target: float,
        destination_boundary_id: str,
        source_boundary_id: str,
        decision_time_ns: int,
        route_commit_time_ns: int,
    ) -> bool:
        return bool(
            self._new_closer_objectives(
                side=side,
                entry=entry,
                target=target,
                destination_boundary_id=destination_boundary_id,
                source_boundary_id=source_boundary_id,
                decision_time_ns=decision_time_ns,
                route_commit_time_ns=route_commit_time_ns,
            )
        )

    def _objective_execution_price(self, side: str, pivot_price: float) -> float:
        """Place TP one tick inside the actual horizontal liquidity price."""

        if side == "LONG":
            return pivot_price - self.tick_size
        if side == "SHORT":
            return pivot_price + self.tick_size
        raise ValueError("side must be LONG or SHORT")

    def _invalidate_claimed_objective_routes(self, bar: Bar) -> None:
        """Signal cancellation when an unfilled route gains a closer objective.

        Claimed plans may already be filled.  As with every claimed-plan
        invalidation in this class, the execution layer applies this signal to
        a pending entry only and never moves or exits a filled position.
        """

        for episode_id, metadata in self._claimed_plan_metadata.items():
            plan_id = str(metadata["plan_id"])
            if plan_id in self._invalidated_claimed_plans:
                continue
            entry = metadata.get("entry")
            target = metadata.get("target")
            if not isinstance(entry, (float, int)) or not isinstance(
                target,
                (float, int),
            ):
                # Backward-compatible restored metadata predating the
                # objective registry cannot be safely reinterpreted.
                continue
            if not self._has_new_closer_objective(
                side=str(metadata["side"]),
                entry=float(entry),
                target=float(target),
                destination_boundary_id=str(
                    metadata.get("destination_boundary_id", ""),
                ),
                source_boundary_id=str(metadata.get("source_boundary_id", "")),
                decision_time_ns=bar.close_time_ns,
                route_commit_time_ns=int(
                    metadata.get(
                        "objective_commit_time_ns",
                        metadata.get("interaction_time_ns", 0),
                    ),
                ),
            ):
                continue
            reason = "ROUTE_CHANGED_BY_NEW_CLOSER_OBJECTIVE"
            self._invalidated_claimed_plans[plan_id] = {
                "episode_id": episode_id,
                "reason": reason,
                "time_ns": bar.close_time_ns,
                "superseding_episode_id": "OBJECTIVE_FIRST_ABSORBING_ROUTE",
            }
            self._diagnostic_counts[reason] = (
                self._diagnostic_counts.get(reason, 0) + 1
            )

    @staticmethod
    def _arbitration_key(
        plan: TradePlan,
    ) -> tuple[float, float, float, float, float, int, str]:
        # Exact source ownership precedes direction context and magnitude; RR
        # remains route feasibility rather than a quality score.
        return (
            -float(
                1.0
                if plan.evidence.get("source_ownership_role")
                == SourceOwnershipRole.LOCAL_SOURCE_OWNER.value
                else 0.0
            ),
            -float(plan.evidence.get("directional_posterior_support_rank", -1.0)),
            -float(plan.evidence.get("directional_family_transition_rank", -1.0)),
            -float(
                plan.evidence.get(
                    "event_residual_ownership_units",
                    0.0,
                ),
            ),
            -float(plan.evidence.get("inventory_coherence_rank", 0.0)),
            -int(plan.evidence.get("source_observed_time_ns", 0)),
            plan.plan_id,
        )

    def validate_claim(self, plan: TradePlan) -> None:
        """Validate an account claim without changing policy state."""

        claimed_plan_id = self._claimed_plans.get(plan.episode_id)
        if claimed_plan_id is not None:
            if claimed_plan_id != plan.plan_id:
                raise ValueError("episode was already claimed by a different plan")
            return
        if plan.episode_id in self._terminalized_episodes:
            raise ValueError("cannot claim a terminally rejected proposal")
        proposal = self._proposals.get(plan.episode_id)
        if proposal is None or proposal.plan_id != plan.plan_id:
            raise ValueError("cannot claim an unknown or superseded proposal")

    def claim(self, plan: TradePlan, *, time_ns: int | None = None) -> None:
        """Mark an episode used only after the shared account accepts it."""

        self.validate_claim(plan)
        claimed_plan_id = self._claimed_plans.get(plan.episode_id)
        if claimed_plan_id == plan.plan_id:
            return
        watch = self._watches.get(plan.episode_id)
        self._queue_terminal_decision(
            outcome="SELECTED",
            stage="EXECUTION_ADMISSION",
            reason="ENTRY_ORDER_ACCEPTED",
            terminal_time_ns=plan.decision_time_ns if time_ns is None else time_ns,
            watch=watch,
            plan=plan,
        )
        self._used_episodes.add(plan.episode_id)
        self._claimed_plans[plan.episode_id] = plan.plan_id
        self._claimed_plan_metadata[plan.episode_id] = {
            "plan_id": plan.plan_id,
            "side": plan.side,
            "family": plan.family,
            "interaction_time_ns": int(
                plan.evidence.get("interaction_time_ns", plan.decision_time_ns)
            ),
            "source_timeframe_minutes": int(
                plan.evidence.get("source_timeframe_minutes", 0)
            ),
            "source_boundary_id": plan.source_boundary_id,
            "source_kind": str(plan.evidence.get("source_kind", "UNKNOWN")),
            "destination_boundary_id": plan.destination_boundary_id,
            "destination_kind": str(
                plan.evidence.get("destination_kind", "UNKNOWN")
            ),
            "entry": plan.entry,
            "stop": plan.stop,
            "target": plan.target,
            "objective_commit_time_ns": int(
                plan.evidence.get(
                    "objective_commit_time_ns",
                    plan.decision_time_ns,
                ),
            ),
        }
        self._last_plan_time_ns = plan.decision_time_ns
        self._proposals.pop(plan.episode_id, None)
        self._watches.pop(plan.episode_id, None)
        self._diagnostic_counts["proposal_claimed"] = (
            self._diagnostic_counts.get("proposal_claimed", 0) + 1
        )

    def reject_proposal(
        self,
        plan: TradePlan,
        reason: str,
        *,
        time_ns: int | None = None,
    ) -> None:
        """Terminally remove an execution-infeasible proposal without claim."""

        if not reason:
            raise ValueError("proposal rejection reason cannot be empty")
        existing = self._terminalized_episodes.get(plan.episode_id)
        if existing is not None:
            if existing != reason:
                raise ValueError("episode was terminalized with a different reason")
            return
        proposal = self._proposals.get(plan.episode_id)
        if proposal is None or proposal.plan_id != plan.plan_id:
            raise ValueError("cannot reject an unknown or superseded proposal")
        watch = self._watches.get(plan.episode_id)
        self._queue_terminal_decision(
            outcome="NO_TRADE",
            stage="EXECUTION_ADMISSION",
            reason=reason,
            terminal_time_ns=plan.decision_time_ns if time_ns is None else time_ns,
            watch=watch,
            plan=plan,
        )
        self._proposals.pop(plan.episode_id, None)
        self._watches.pop(plan.episode_id, None)
        diagnostic = f"EXECUTION_REJECTED:{reason}"
        self._diagnostic_counts[diagnostic] = (
            self._diagnostic_counts.get(diagnostic, 0) + 1
        )

    def export_state(self) -> dict[str, object]:
        return {
            "version": self.STATE_VERSION,
            "symbol": self.symbol,
            "used_episode_ids": sorted(self._used_episodes),
            "claimed_plan_ids": dict(sorted(self._claimed_plans.items())),
            "claimed_plan_metadata": {
                key: dict(value)
                for key, value in sorted(self._claimed_plan_metadata.items())
            },
            "invalidated_claimed_plans": {
                key: dict(value)
                for key, value in sorted(self._invalidated_claimed_plans.items())
            },
            "terminalized_episodes": dict(sorted(self._terminalized_episodes.items())),
            "started_episodes": {
                key: dict(value)
                for key, value in sorted(self._started_episodes.items())
            },
            "terminal_decisions": {
                key: dict(value)
                for key, value in sorted(self._terminal_decisions.items())
            },
            "pending_started_episode_ids": sorted(
                self._pending_started_episode_ids,
            ),
            "pending_terminal_episode_ids": sorted(
                self._pending_terminal_episode_ids,
            ),
            "last_plan_time_ns": self._last_plan_time_ns,
        }

    def export_runtime_state(self) -> dict[str, object]:
        """Return only state which causal bar/event replay cannot rebuild."""

        if self._pending_started_episode_ids or self._pending_terminal_episode_ids:
            raise RuntimeError(
                "policy decisions must be durably drained before runtime snapshot",
            )
        return {
            "version": self.RUNTIME_STATE_VERSION,
            "symbol": self.symbol,
            "used_episode_ids": sorted(self._used_episodes),
            "claimed_plan_ids": dict(sorted(self._claimed_plans.items())),
            "claimed_plan_metadata": {
                key: dict(value)
                for key, value in sorted(self._claimed_plan_metadata.items())
            },
            "invalidated_claimed_plans": {
                key: dict(value)
                for key, value in sorted(self._invalidated_claimed_plans.items())
            },
            "terminalized_episodes": dict(sorted(self._terminalized_episodes.items())),
            "last_plan_time_ns": self._last_plan_time_ns,
        }

    def _decision_ledger_overlay_state(
        self,
        starts: Mapping[str, Mapping[str, object]],
        terminals: Mapping[str, Mapping[str, object]],
    ) -> dict[str, object]:
        """Compose a legacy-shaped validated overlay without queueing events."""

        state = self.export_state()
        merged_starts = {
            str(key): dict(value)
            for key, value in self._started_episodes.items()
        }
        merged_terminals = {
            str(key): dict(value)
            for key, value in self._terminal_decisions.items()
        }
        for episode_id, values in starts.items():
            payload = dict(values)
            existing = merged_starts.get(episode_id)
            if existing is not None and existing != payload:
                raise ValueError(
                    f"conflicting durable episode start for episode {episode_id}",
                )
            merged_starts[episode_id] = payload
        for episode_id, values in terminals.items():
            payload = dict(values)
            existing = merged_terminals.get(episode_id)
            if existing is not None and existing != payload:
                raise ValueError(
                    f"conflicting durable terminal decision for episode {episode_id}",
                )
            merged_terminals[episode_id] = payload
        state["started_episodes"] = merged_starts
        state["terminal_decisions"] = merged_terminals
        used = set(state["used_episode_ids"])
        claimed = dict(state["claimed_plan_ids"])
        metadata = {
            str(key): dict(value)
            for key, value in state["claimed_plan_metadata"].items()
        }
        terminalized = dict(state["terminalized_episodes"])
        last_plan_time_ns = int(state["last_plan_time_ns"])
        for episode_id, terminal in merged_terminals.items():
            outcome = terminal.get("outcome")
            reason = terminal.get("terminal_reason")
            if outcome == "NO_TRADE":
                if reason == "SAME_CASCADE_NON_OWNER":
                    used.add(episode_id)
                elif isinstance(reason, str) and reason:
                    terminalized[episode_id] = reason
                continue
            if outcome != "SELECTED":
                continue
            raw_plan = terminal.get("plan")
            if not isinstance(raw_plan, Mapping):
                raise ValueError(
                    f"selected durable decision has no plan for episode {episode_id}",
                )
            plan = TradePlan.from_dict(raw_plan)
            if plan.episode_id != episode_id or terminal.get("plan_id") != plan.plan_id:
                raise ValueError(f"selected durable plan identity mismatch for {episode_id}")
            used.add(episode_id)
            claimed[episode_id] = plan.plan_id
            metadata[episode_id] = {
                "plan_id": plan.plan_id,
                "side": plan.side,
                "family": plan.family,
                "interaction_time_ns": int(
                    plan.evidence.get("interaction_time_ns", plan.decision_time_ns),
                ),
                "source_timeframe_minutes": int(
                    plan.evidence.get("source_timeframe_minutes", 0),
                ),
                "source_boundary_id": plan.source_boundary_id,
                "source_kind": str(plan.evidence.get("source_kind", "UNKNOWN")),
                "destination_boundary_id": plan.destination_boundary_id,
                "destination_kind": str(
                    plan.evidence.get("destination_kind", "UNKNOWN"),
                ),
                "entry": plan.entry,
                "stop": plan.stop,
                "target": plan.target,
                "objective_commit_time_ns": int(
                    plan.evidence.get(
                        "objective_commit_time_ns",
                        plan.decision_time_ns,
                    ),
                ),
            }
            last_plan_time_ns = max(last_plan_time_ns, plan.decision_time_ns)
        state["used_episode_ids"] = sorted(used)
        state["claimed_plan_ids"] = dict(sorted(claimed.items()))
        state["claimed_plan_metadata"] = {
            key: metadata[key] for key in sorted(metadata)
        }
        state["terminalized_episodes"] = dict(sorted(terminalized.items()))
        state["last_plan_time_ns"] = last_plan_time_ns
        return state

    def restore_state(self, payload: Mapping[str, object]) -> None:
        if not isinstance(payload, Mapping):
            raise ValueError("policy state must be a mapping")
        version = payload.get("version")
        if version not in {self.STATE_VERSION, self.RUNTIME_STATE_VERSION}:
            raise ValueError(f"unsupported policy state version: {payload.get('version')!r}")
        if payload.get("symbol") != self.symbol:
            raise ValueError(f"policy state symbol mismatch: {payload.get('symbol')!r}")
        raw_used = payload.get("used_episode_ids", [])
        raw_claimed = payload.get("claimed_plan_ids", {})
        raw_metadata = payload.get("claimed_plan_metadata", {})
        raw_invalidated = payload.get("invalidated_claimed_plans", {})
        raw_terminalized = payload.get("terminalized_episodes", {})
        raw_started = payload.get("started_episodes", {})
        raw_decisions = payload.get("terminal_decisions", {})
        raw_pending_started = payload.get("pending_started_episode_ids", [])
        raw_pending_terminal = payload.get("pending_terminal_episode_ids", [])
        if version == self.RUNTIME_STATE_VERSION and any(
            key in payload
            for key in (
                "started_episodes",
                "terminal_decisions",
                "pending_started_episode_ids",
                "pending_terminal_episode_ids",
            )
        ):
            raise ValueError("compact policy runtime state contains decision payloads")
        raw_last = payload.get("last_plan_time_ns", -1)
        if not isinstance(raw_used, list) or any(
            not isinstance(value, str) or not value for value in raw_used
        ):
            raise ValueError("used_episode_ids must be a list of non-empty strings")
        if len(set(raw_used)) != len(raw_used):
            raise ValueError("used_episode_ids contains duplicates")
        if not isinstance(raw_claimed, Mapping) or any(
            not isinstance(key, str) or not key or not isinstance(value, str) or not value
            for key, value in raw_claimed.items()
        ):
            raise ValueError("claimed_plan_ids must map non-empty episode IDs to plan IDs")
        if isinstance(raw_last, bool) or not isinstance(raw_last, int):
            raise ValueError("last_plan_time_ns must be an integer")
        if (
            not isinstance(raw_metadata, Mapping)
            or not isinstance(raw_invalidated, Mapping)
            or not isinstance(raw_terminalized, Mapping)
            or not isinstance(raw_started, Mapping)
            or not isinstance(raw_decisions, Mapping)
        ):
            raise ValueError("policy identity and decision state must be mappings")
        if (
            not isinstance(raw_pending_started, list)
            or not isinstance(raw_pending_terminal, list)
            or any(not isinstance(value, str) or not value for value in raw_pending_started)
            or any(not isinstance(value, str) or not value for value in raw_pending_terminal)
            or len(set(raw_pending_started)) != len(raw_pending_started)
            or len(set(raw_pending_terminal)) != len(raw_pending_terminal)
        ):
            raise ValueError("pending policy decision IDs are malformed")
        if any(
            not isinstance(episode_id, str)
            or not episode_id
            or not isinstance(reason, str)
            or not reason
            for episode_id, reason in raw_terminalized.items()
        ):
            raise ValueError("terminalized_episodes is malformed")
        for episode_id, values in raw_metadata.items():
            if (
                not isinstance(episode_id, str)
                or not isinstance(values, Mapping)
                or not isinstance(values.get("plan_id"), str)
                or values.get("side") not in {"LONG", "SHORT"}
                or not isinstance(values.get("interaction_time_ns"), int)
            ):
                raise ValueError("claimed_plan_metadata is malformed")
        for plan_id, values in raw_invalidated.items():
            if (
                not isinstance(plan_id, str)
                or not isinstance(values, Mapping)
                or not isinstance(values.get("reason"), str)
                or not isinstance(values.get("time_ns"), int)
            ):
                raise ValueError("invalidated_claimed_plans is malformed")
        restored_used = set(raw_used)
        restored_claimed = {str(key): str(value) for key, value in raw_claimed.items()}
        restored_metadata = {
            str(key): {str(name): value for name, value in values.items()}
            for key, values in raw_metadata.items()
            if isinstance(values, Mapping)
        }
        restored_invalidated = {
            str(key): {str(name): value for name, value in values.items()}
            for key, values in raw_invalidated.items()
            if isinstance(values, Mapping)
        }
        restored_terminalized = {
            str(key): str(value) for key, value in raw_terminalized.items()
        }
        restored_started: dict[str, dict[str, object]] = {}
        for episode_id, values in raw_started.items():
            if (
                not isinstance(episode_id, str)
                or not episode_id
                or not isinstance(values, Mapping)
                or values.get("episode_id") != episode_id
                or values.get("symbol") != self.symbol
                or not isinstance(values.get("started_time_ns"), int)
                or values.get("policy_fingerprint") != POLICY_FINGERPRINT
            ):
                raise ValueError("started_episodes is malformed or incompatible")
            restored_started[episode_id] = dict(values)
        restored_decisions: dict[str, dict[str, object]] = {}
        for episode_id, values in raw_decisions.items():
            if (
                not isinstance(episode_id, str)
                or not episode_id
                or not isinstance(values, Mapping)
                or values.get("episode_id") != episode_id
                or values.get("symbol") != self.symbol
                or values.get("outcome") not in {"SELECTED", "NO_TRADE"}
                or not isinstance(values.get("terminal_reason"), str)
                or not isinstance(values.get("terminal_time_ns"), int)
                or values.get("decision_id") != self._decision_id(episode_id)
                or values.get("policy_fingerprint") != POLICY_FINGERPRINT
            ):
                raise ValueError("terminal_decisions is malformed or incompatible")
            restored_decisions[episode_id] = dict(values)
        pending_started = set(raw_pending_started)
        pending_terminal = set(raw_pending_terminal)
        if not set(restored_decisions).issubset(restored_started):
            raise ValueError("every terminal decision must reference a started episode")
        if not pending_started.issubset(restored_started):
            raise ValueError("pending starts must reference started episodes")
        if not pending_terminal.issubset(restored_decisions):
            raise ValueError("pending terminals must reference terminal decisions")
        if not set(restored_claimed).issubset(restored_used):
            raise ValueError("every claimed episode must also be present in used_episode_ids")
        if not set(restored_metadata).issubset(restored_claimed):
            raise ValueError("claimed metadata must reference a claimed episode")
        for episode_id, metadata in restored_metadata.items():
            if metadata.get("plan_id") != restored_claimed[episode_id]:
                raise ValueError("conflicting claimed plan identity in metadata")
        claimed_plan_ids = set(restored_claimed.values()) | set(self._claimed_plans.values())
        if not set(restored_invalidated).issubset(claimed_plan_ids):
            raise ValueError("claimed plan invalidation must reference a claimed plan")
        if set(restored_terminalized) & restored_used:
            raise ValueError("terminalized episodes cannot also be used or claimed")
        if set(restored_terminalized) & self._used_episodes:
            raise ValueError("terminalized episode conflicts with existing used identity")
        if restored_used & set(self._terminalized_episodes):
            raise ValueError("used episode conflicts with existing terminal identity")
        for episode_id, plan_id in restored_claimed.items():
            existing = self._claimed_plans.get(episode_id)
            if existing is not None and existing != plan_id:
                raise ValueError(f"conflicting claimed plan for episode {episode_id}")
        for episode_id, metadata in restored_metadata.items():
            existing = self._claimed_plan_metadata.get(episode_id)
            if existing is not None and existing != metadata:
                raise ValueError(f"conflicting claimed metadata for episode {episode_id}")
        for plan_id, invalidation in restored_invalidated.items():
            existing = self._invalidated_claimed_plans.get(plan_id)
            if existing is not None and existing != invalidation:
                raise ValueError(f"conflicting claimed invalidation for plan {plan_id}")
        for episode_id, reason in restored_terminalized.items():
            existing = self._terminalized_episodes.get(episode_id)
            if existing is not None and existing != reason:
                raise ValueError(f"conflicting terminal reason for episode {episode_id}")
        for episode_id, values in restored_started.items():
            existing = self._started_episodes.get(episode_id)
            if existing is not None and existing != values:
                raise ValueError(f"conflicting episode start for episode {episode_id}")
        for episode_id, values in restored_decisions.items():
            existing = self._terminal_decisions.get(episode_id)
            if existing is not None and existing != values:
                raise ValueError(f"conflicting terminal decision for episode {episode_id}")
        self._used_episodes.update(restored_used)
        self._claimed_plans.update(restored_claimed)
        self._claimed_plan_metadata.update(restored_metadata)
        self._invalidated_claimed_plans.update(restored_invalidated)
        self._terminalized_episodes.update(restored_terminalized)
        self._started_episodes.update(restored_started)
        self._terminal_decisions.update(restored_decisions)
        self._pending_started_episode_ids.update(pending_started)
        self._pending_terminal_episode_ids.update(pending_terminal)
        self._last_plan_time_ns = max(self._last_plan_time_ns, raw_last)
        for episode_id in restored_used | set(self._terminalized_episodes):
            self._proposals.pop(episode_id, None)
            self._watches.pop(episode_id, None)

    @staticmethod
    def _cascade_key(plan: TradePlan) -> tuple[int, str, str] | None:
        interaction = int(plan.evidence.get("interaction_time_ns", plan.decision_time_ns))
        cascade = plan.evidence.get("cascade_id")
        if not isinstance(cascade, str) or not cascade:
            return None
        return (interaction // (5 * NS_PER_MINUTE), plan.side, cascade)

    def suppress_claimed_cascade(
        self,
        cascade_key: tuple[int, str, str],
        claimed_episode_id: str,
        *,
        time_ns: int | None = None,
    ) -> None:
        for episode_id, proposal in list(self._proposals.items()):
            if (
                episode_id != claimed_episode_id
                and self._cascade_key(proposal) == cascade_key
            ):
                watch = self._watches.get(episode_id)
                self._queue_terminal_decision(
                    outcome="NO_TRADE",
                    stage="ARBITRATION",
                    reason="SAME_CASCADE_NON_OWNER",
                    terminal_time_ns=(
                        proposal.decision_time_ns if time_ns is None else time_ns
                    ),
                    watch=watch,
                    plan=proposal,
                    details={"claimed_episode_id": claimed_episode_id},
                    mark_terminalized=False,
                )
                self._proposals.pop(episode_id, None)
                self._watches.pop(episode_id, None)
                self._used_episodes.add(episode_id)
                self._diagnostic_counts["SAME_CASCADE_NON_OWNER"] = (
                    self._diagnostic_counts.get("SAME_CASCADE_NON_OWNER", 0) + 1
                )

    def _higher_timeframe_context(self, side: str) -> dict[str, float | str | int]:
        direction = 1.0 if side == "LONG" else -1.0
        components: list[float] = []
        for bars in (self.market.fifteen_minute, self.market.sixty_minute):
            if len(bars) >= 2:
                anchor = bars[max(0, len(bars) - 4)].close
                components.append(
                    direction * (bars[-1].close - anchor)
                    / max(self.market.atr(bars), self.tick_size)
                )
        score = sum(components) / len(components) if components else 0.0
        aligned = sum(value > 0.0 for value in components)
        opposed = sum(value < 0.0 for value in components)
        return {
            "higher_timeframe_score": score,
            "higher_timeframe_regime": (
                "ALIGNED" if aligned > opposed else "OPPOSED" if opposed > aligned else "MIXED"
            ),
            "higher_timeframe_aligned": aligned,
            "higher_timeframe_opposing": opposed,
        }

    def _bar_evidence(
        self, bar: Bar, side: str, atr: float, common_breadth: float,
    ) -> dict[str, float]:
        """Compatibility diagnostic; decisions do not accumulate this score."""

        direction = 1.0 if side == "LONG" else -1.0
        local = direction * bar.body / max(atr, self.tick_size)
        common = direction * common_breadth
        residual = local - common
        return {
            "local_control_score": local,
            "common_market_component": common,
            "residual_control_score": residual,
            "control_score": residual,
            "common_breadth_signed": common,
        }

    def _seed_ownership(
        self, watch: EpisodeWatch, bar: Bar, atr: float, breadth: float,
    ) -> None:
        evidence = self._bar_evidence(bar, watch.side, atr, breadth)
        residual = evidence["residual_control_score"]
        watch.supportive_control = max(residual, 0.0)
        watch.opposing_control = max(-residual, 0.0)
        watch.ownership_balance = residual
        watch.evidence.update(
            initial_local_control=evidence["local_control_score"],
            initial_common_component=evidence["common_market_component"],
            initial_residual_control=residual,
        )

    def _register_attack_source(
        self,
        source: LiquidityBoundary,
        semantic_kind: str,
        key: SourceKey,
    ) -> None:
        existing = self._source_keys_by_boundary_id.get(source.boundary_id)
        if existing is not None and existing != key:
            raise RuntimeError("one structural source cannot change generation")
        if key in self._campaign_sources:
            return
        self.attack_ledger.register_source(
            SourceSpec(
                key=key,
                side=SourceSide(source.side),
                tick_size=self.tick_size,
                observed_time_ns=source.observed_time_ns,
                # Boundary and projected-node IDs already encode their causal
                # parent/version identity.  Supersession is therefore observed
                # explicitly when that exact projected ID disappears.
                parent_id=source.boundary_id,
                parent_generation=key.generation,
            ),
        )
        self._source_keys_by_boundary_id[source.boundary_id] = key
        self._campaign_sources[key] = (source, semantic_kind)

    def _record_source_attack(
        self,
        source: LiquidityBoundary,
        semantic_kind: str,
        key: SourceKey,
        bar: Bar,
    ) -> int | None:
        self._register_attack_source(source, semantic_kind, key)
        events = self.attack_ledger.record_touch(
            key,
            time_ns=bar.close_time_ns,
            extreme=bar.high if source.side == "HIGH" else bar.low,
            physical_attack_id=stable_id(
                self.symbol,
                source.boundary_id,
                key.generation,
                bar.open_time_ns,
                bar.interval_minutes,
                source.side,
                prefix="ATTACK:",
            ),
        )
        if not any(
            event.kind in {EventKind.CAMPAIGN_STARTED, EventKind.REATTACK_APPENDED}
            for event in events
        ):
            return None
        campaign = self.attack_ledger.campaign(key)
        if campaign is None or not campaign.attacks:
            raise RuntimeError("new source attack has no campaign state")
        return campaign.attacks[-1].ordinal

    def _retire_source_campaign(
        self,
        key: SourceKey,
        source: LiquidityBoundary,
        bar: Bar,
    ) -> None:
        self.attack_ledger.source_invalidated(key, time_ns=bar.close_time_ns)
        reason = "SOURCE_GENERATION_SUPERSEDED"
        for episode_id, watch in list(self._watches.items()):
            if watch.source.boundary_id != source.boundary_id:
                continue
            plan = self._proposals.get(episode_id)
            self._record_terminal(
                reason,
                watch,
                bar,
                plan=plan,
                source_boundary_id=source.boundary_id,
                source_generation=key.generation,
            )
            self._watches.pop(episode_id, None)
            self._proposals.pop(episode_id, None)
        for episode_id, metadata in self._claimed_plan_metadata.items():
            if metadata.get("source_boundary_id") != source.boundary_id:
                continue
            plan_id = str(metadata["plan_id"])
            if plan_id in self._invalidated_claimed_plans:
                continue
            self._invalidated_claimed_plans[plan_id] = {
                "episode_id": episode_id,
                "reason": reason,
                "time_ns": bar.close_time_ns,
                "superseding_episode_id": f"SOURCE:{source.boundary_id}",
            }
            self._diagnostic_counts[reason] = (
                self._diagnostic_counts.get(reason, 0) + 1
            )
        self._campaign_sources.pop(key, None)
        if self._source_keys_by_boundary_id.get(source.boundary_id) == key:
            self._source_keys_by_boundary_id.pop(source.boundary_id, None)

    def _campaign_source_candidates(
        self,
        *,
        bar: Bar,
        existing_source_ids: set[str],
        tradeable_projected_ids: set[str],
    ) -> list[tuple[LiquidityBoundary, str, SourceKey]]:
        output: list[tuple[LiquidityBoundary, str, SourceKey]] = []
        for key, (source, semantic_kind) in list(self._campaign_sources.items()):
            campaign = self.attack_ledger.campaign(key)
            if campaign is None or campaign.phase is CampaignPhase.TERMINAL:
                self._campaign_sources.pop(key, None)
                if self._source_keys_by_boundary_id.get(source.boundary_id) == key:
                    self._source_keys_by_boundary_id.pop(source.boundary_id, None)
                continue
            if source.boundary_id not in existing_source_ids:
                self._retire_source_campaign(key, source, bar)
                continue
            if (
                self._is_versioned_structural_kind(source.kind)
                and source.boundary_id not in tradeable_projected_ids
            ):
                # A first-touched line/channel keeps its identity until the
                # already-started auction resolves, but the retired geometry
                # cannot originate a later reattack.
                continue
            output.append((source, semantic_kind, key))
        return output

    def _create_boundary_watches(
        self, bar: Bar, serial: int, atr: float, breadth: float,
    ) -> None:
        del atr, breadth
        projected = self._projected_structural_nodes(bar.close_time_ns, serial)
        tradeable_projected_ids = {
            node.node_id
            for node in projected
            if node.is_fresh(bar.close_time_ns)
        }
        existing_projected_ids = self._existing_projected_structure_ids(
            bar.close_time_ns,
            serial,
        )
        existing_source_ids = (
            set(self.market.boundary_book.boundaries) | existing_projected_ids
        )
        sources: dict[
            SourceKey, tuple[LiquidityBoundary, str, SourceKey]
        ] = {}
        for source in self.market.boundary_book.active(bar.close_time_ns):
            if any(
                token in source.kind
                for token in ("UPTREND_LINE", "DOWNTREND_LINE", "DIAGONAL_LIQUIDITY")
            ):
                # market_state's last-two-pivot projection remains diagnostic;
                # only FeasibleTrendChannelBook versions can trade.
                continue
            role = boundary_role(source)
            if role.direction_source:
                key = self._source_keys_by_boundary_id.get(
                    source.boundary_id,
                    SourceKey(source.boundary_id, 1),
                )
                sources[key] = (source, role.semantic_kind, key)
        for node in projected:
            if not node.is_fresh(bar.close_time_ns):
                continue
            source = self._boundary_from_structural_node(node, serial)
            key = self._source_keys_by_boundary_id.get(
                node.node_id,
                SourceKey(node.node_id, node.version),
            )
            sources[key] = (source, "FEASIBLE_TREND_CHANNEL_STRUCTURE", key)
        for source, semantic_kind, key in self._campaign_source_candidates(
            bar=bar,
            existing_source_ids=existing_source_ids,
            tradeable_projected_ids=tradeable_projected_ids,
        ):
            sources[key] = (source, semantic_kind, key)

        touched: list[
            tuple[LiquidityBoundary, str, SourceKey, float, float]
        ] = []
        for source, semantic_kind, key in sources.values():
            # The entire interaction candle must be later than the structure's
            # causal observation.  ``<= open`` accepts both exact-edge feeds
            # (prior close == next open) and Binance-style close == next open-1
            # without retroactively treating the confirmation candle's wick as
            # a source touch.
            if source.observed_time_ns > bar.open_time_ns:
                self._record(
                    "SOURCE_NOT_OBSERVABLE_AT_INTERACTION_OPEN",
                    None,
                    bar,
                    source_boundary_id=source.boundary_id,
                    source_observed_time_ns=source.observed_time_ns,
                )
                continue
            lower, upper = source.band_at(serial)
            if not (bar.low <= upper and bar.high >= lower):
                continue
            touched.append((source, semantic_kind, key, lower, upper))

        # Simultaneously overlapping public facts describe one interaction.
        # Canonical ownership is structural/timeframe/observation based and
        # therefore invariant to BoundaryBook insertion order.
        def ownership_key(
            item: tuple[LiquidityBoundary, str, SourceKey, float, float],
        ) -> tuple[float | int | str, ...]:
            return (
                -item[0].timeframe_minutes,
                -int(self._is_versioned_structural_kind(item[0].kind)),
                -item[0].strength,
                -item[0].observed_time_ns,
                item[4] - item[3],
                item[0].boundary_id,
            )

        # Build tick-connected interval components before selecting ownership;
        # otherwise a suppressed bridge B could leave A and C as two owners.
        # HIGH and LOW pools are competing directional facts, never aliases of
        # one another even when their price bands overlap.
        components: list[
            list[tuple[LiquidityBoundary, str, SourceKey, float, float]]
        ] = []
        for source_side in ("HIGH", "LOW"):
            side_components: list[
                list[tuple[LiquidityBoundary, str, SourceKey, float, float]]
            ] = []
            side_upper: list[float] = []
            for item in sorted(
                (value for value in touched if value[0].side == source_side),
                key=lambda value: (value[3], value[4], value[0].boundary_id),
            ):
                if (
                    not side_components
                    or item[3] > side_upper[-1] + self.tick_size
                ):
                    side_components.append([item])
                    side_upper.append(item[4])
                else:
                    side_components[-1].append(item)
                    side_upper[-1] = max(side_upper[-1], item[4])
            components.extend(side_components)

        for component in components:
            owner = min(component, key=ownership_key)
            source, semantic_kind, key, _lower, _upper = owner
            for suppressed, _semantic, _key, _lo, _hi in component:
                if suppressed.boundary_id == source.boundary_id:
                    continue
                self._record(
                    "OVERLAPPING_SOURCE_CANONICALIZED",
                    None,
                    bar,
                    source_boundary_id=suppressed.boundary_id,
                    owning_source_boundary_id=source.boundary_id,
                )
            attack_ordinal = self._record_source_attack(
                source, semantic_kind, key, bar,
            )
            if attack_ordinal is None:
                continue
            prior = next(
                (
                    watch
                    for watch in self._watches.values()
                    if watch.source.boundary_id == source.boundary_id
                    and watch.state != "INVALID"
                ),
                None,
            )
            if prior is not None:
                prior_plan = self._proposals.get(prior.episode_id)
                if prior.state != "PROPOSED" or prior_plan is None:
                    raise RuntimeError("fresh reattack collided with an unfinished response")
                self._record_terminal(
                    "FRESH_REATTACK_SUPERSEDED_PENDING_SOURCE_ATTACK",
                    prior,
                    bar,
                    plan=prior_plan,
                    new_attack_ordinal=attack_ordinal,
                )
                self._watches.pop(prior.episode_id, None)
                self._proposals.pop(prior.episode_id, None)
            self._start_interaction(
                source,
                bar,
                serial,
                semantic_kind,
                attack_key=key,
                attack_ordinal=attack_ordinal,
            )

    def _interaction_liquidity_evidence(
        self,
        *,
        side: str,
        bar: Bar,
        serial: int,
    ) -> dict[str, float | str | int]:
        # ``open+1`` admits a structure observed exactly at the interaction
        # open while the explicit observed<=open filter excludes anything
        # learned inside the candle.
        causal_time = bar.open_time_ns + 1
        boundaries_by_id = {
            item.boundary_id: item
            for item in self.market.boundary_book.boundaries.values()
            if item.observed_time_ns <= bar.open_time_ns
        }
        boundaries_by_id.update(
            {
                node.node_id: self._boundary_from_structural_node(node, serial)
                for node in self._projected_structural_nodes(causal_time, serial)
                if node.observed_time_ns <= bar.open_time_ns
                and node.is_fresh(causal_time)
            },
        )
        # A campaign retains its own source identity for causal response and a
        # physically fresh reattack.  It is not reinserted into the general
        # active-liquidity map after consumption, where it would be counted a
        # second time as its own route obstacle.
        context = build_active_liquidity_context(
            boundaries=boundaries_by_id.values(),
            price=bar.open,
            decision_time_ns=causal_time,
            serial=serial,
            atr_price=max(self.market.atr(self.market.five_minute), self.tick_size),
        )
        balance = context.direction_source_balance
        aligned = (
            None
            if balance is None
            else float(balance) * (1.0 if side == "LONG" else -1.0)
        )
        obstacle = (
            context.nearest_long_obstacle
            if side == "LONG"
            else context.nearest_short_obstacle
        )
        return {
            "interaction_direction_source_pull": (
                "UNKNOWN" if aligned is None else aligned
            ),
            "interaction_two_sided_source_pull": (
                "UNKNOWN"
                if context.two_sided_source_pull is None
                else context.two_sided_source_pull
            ),
            "interaction_nearest_route_obstacle": (
                "UNKNOWN" if obstacle is None else obstacle.boundary_id
            ),
        }

    def _start_interaction(
        self,
        source: LiquidityBoundary,
        bar: Bar,
        serial: int,
        semantic_kind: str,
        *,
        attack_key: SourceKey | None = None,
        attack_ordinal: int | None = None,
    ) -> None:
        if attack_key is None or attack_ordinal is None:
            attack_key = self._source_keys_by_boundary_id.get(
                source.boundary_id,
                SourceKey(source.boundary_id, 1),
            )
            attack_ordinal = self._record_source_attack(
                source, semantic_kind, attack_key, bar,
            )
            if attack_ordinal is None:
                return
        episode_id = stable_id(
            self.symbol,
            source.boundary_id,
            attack_key.generation,
            attack_ordinal,
            bar.open_time_ns,
            "AUCTION",
            prefix="EP:",
        )
        if (
            episode_id in self._watches
            or episode_id in self._used_episodes
            or episode_id in self._terminalized_episodes
        ):
            return
        lower, upper = source.band_at(serial)
        if bar.close > upper:
            side = "LONG"
            family = "ACCEPTED_AUCTION_CONTINUATION"
        elif bar.close < lower:
            side = "SHORT"
            family = "ACCEPTED_AUCTION_CONTINUATION"
        else:
            side = "SHORT" if source.side == "HIGH" else "LONG"
            family = "FAILED_AUCTION_REVERSAL"
        interaction_evidence = self._interaction_liquidity_evidence(
            side=side, bar=bar, serial=serial,
        )
        watch = EpisodeWatch(
            episode_id=episode_id,
            family=family,
            source=source,
            side=side,
            state="SOURCE_INTERACTION",
            interaction_serial=serial,
            interaction_time_ns=bar.open_time_ns,
            event_extreme=bar.low if side == "LONG" else bar.high,
            last_update_serial=serial,
            last_update_time_ns=bar.close_time_ns,
            evidence={
                "interaction_close": bar.close,
                "source_semantic_kind": semantic_kind,
                "campaign_source_id": attack_key.source_id,
                "source_generation": attack_key.generation,
                "attack_ordinal": attack_ordinal,
                "interaction_source_lower": lower,
                "interaction_source_upper": upper,
                **interaction_evidence,
            },
            proof_extreme=bar.high if side == "LONG" else bar.low,
        )
        self._watches[episode_id] = watch
        self._queue_episode_started(watch, started_time_ns=bar.close_time_ns)

    # Private compatibility helpers used by older restore/contract tests.
    def _start_failed(
        self, source: LiquidityBoundary, side: str, bar: Bar, serial: int,
        extreme: float, atr: float, breadth: float,
    ) -> None:
        semantic_kind = boundary_role(source).semantic_kind
        attack_key = self._source_keys_by_boundary_id.get(
            source.boundary_id,
            SourceKey(source.boundary_id, 1),
        )
        attack_ordinal = self._record_source_attack(
            source, semantic_kind, attack_key, bar,
        )
        if attack_ordinal is None:
            return
        episode_id = stable_id(
            self.symbol,
            source.boundary_id,
            attack_key.generation,
            attack_ordinal,
            bar.open_time_ns,
            "FAILED",
            prefix="EP:",
        )
        if (
            episode_id in self._watches
            or episode_id in self._used_episodes
            or episode_id in self._terminalized_episodes
        ):
            return
        watch = EpisodeWatch(
            episode_id, "FAILED_AUCTION_REVERSAL", source, side, "RECLAIMED",
            serial, bar.open_time_ns, extreme, serial, bar.close_time_ns,
        )
        lower, upper = source.band_at(serial)
        watch.evidence.update(
            campaign_source_id=attack_key.source_id,
            source_generation=attack_key.generation,
            attack_ordinal=attack_ordinal,
            interaction_source_lower=lower,
            interaction_source_upper=upper,
        )
        self._seed_ownership(watch, bar, atr, breadth)
        self._watches[episode_id] = watch
        self._queue_episode_started(watch, started_time_ns=bar.close_time_ns)

    def _start_accepted(
        self, source: LiquidityBoundary, side: str, bar: Bar, serial: int,
        extreme: float, atr: float, breadth: float,
    ) -> None:
        self._start_interaction(source, bar, serial, boundary_role(source).semantic_kind)

    def _interaction(self, watch: EpisodeWatch) -> StructureInteraction:
        lower, upper = watch.source.band_at(watch.interaction_serial)
        return StructureInteraction(
            watch.source.boundary_id,
            self.symbol,
            watch.source.side,  # type: ignore[arg-type]
            lower,
            upper,
            watch.interaction_time_ns,
        )

    @staticmethod
    def _watch_attack_key(watch: EpisodeWatch) -> SourceKey | None:
        source_id = watch.evidence.get("campaign_source_id")
        generation = watch.evidence.get("source_generation")
        if (
            not isinstance(source_id, str)
            or not source_id
            or isinstance(generation, bool)
            or not isinstance(generation, int)
        ):
            return None
        return SourceKey(source_id, generation)

    def _complete_attack_response(
        self,
        watch: EpisodeWatch,
        journey: JourneyEvidence,
        bar: Bar,
    ) -> None:
        key = self._watch_attack_key(watch)
        attack_ordinal = watch.evidence.get("attack_ordinal")
        if key is None or not isinstance(attack_ordinal, int):
            return
        campaign = self.attack_ledger.campaign(key)
        if campaign is None or not campaign.attacks:
            raise RuntimeError("episode lost its source campaign")
        current = campaign.attacks[-1]
        if current.ordinal != attack_ordinal:
            raise RuntimeError("episode response no longer owns the current source attack")
        if current.outcome is AttackOutcome.RESPONSE_COMPLETED:
            return
        episode = self._episode_tape(watch, bar.close_time_ns)
        if not episode:
            raise RuntimeError("completed source response has no causal tape")
        response = [
            item for item in episode
            if item.close_time_ns > current.start_time_ns
        ]
        if not response:
            raise RuntimeError("completed source response has no post-attack bar")
        response_extreme = (
            min(item.low for item in response)
            if watch.source.side == "HIGH"
            else max(item.high for item in response)
        )
        frozen_control = next(
            (
                float(value)
                for value in (
                    journey.response_required_extreme,
                    journey.response_close,
                    bar.close,
                )
                if isinstance(value, (float, int))
            ),
            bar.close,
        )
        self.attack_ledger.observe_response(
            key,
            time_ns=bar.close_time_ns,
            response_extreme=response_extreme,
            completed=True,
            frozen_control=frozen_control,
        )

    def _claim_attack_owner(self, watch: EpisodeWatch, bar: Bar) -> None:
        key = self._watch_attack_key(watch)
        attack_ordinal = watch.evidence.get("attack_ordinal")
        if key is None or not isinstance(attack_ordinal, int):
            return
        campaign = self.attack_ledger.campaign(key)
        if campaign is None or not campaign.attacks:
            raise RuntimeError("completed episode lost its source campaign")
        if campaign.attacks[-1].ordinal != attack_ordinal:
            raise RuntimeError("completed episode does not own the latest source attack")
        self.attack_ledger.claim(
            key,
            time_ns=bar.close_time_ns,
            owner=OwnerSide(watch.side),
        )

    @staticmethod
    def _side_for_journey(source_side: str, family: str) -> str:
        if family == "ACCEPTED_AUCTION_CONTINUATION":
            return "LONG" if source_side == "HIGH" else "SHORT"
        return "SHORT" if source_side == "HIGH" else "LONG"

    @staticmethod
    def _journey_flow_response_evidence(
        journey: JourneyEvidence,
    ) -> dict[str, float | str | int]:
        """Flatten causal flow/response observations for durable evidence.

        Missing public-kline inputs stay absent rather than becoming neutral
        flow.  Absorption fields are outcome proxies: klines cannot observe L2
        replenishment or passive queue identity.
        """

        output: dict[str, float | str | int] = {
            "journey_flow_response_semantics": "PUBLIC_KLINE_OUTCOME_PROXY_NOT_L2",
        }
        optional = {
            "journey_baseline_pressure": getattr(journey, "baseline_pressure", None),
            "journey_control_flow_coherence": getattr(
                journey, "control_flow_coherence", None,
            ),
            # Compatibility alias retaining the pre-existing calculation.
            "journey_control_flow_share": getattr(journey, "control_flow_share", None),
            "journey_control_pressure": getattr(journey, "control_pressure", None),
            "journey_control_pressure_surprise": getattr(
                journey, "control_pressure_surprise", None,
            ),
            "journey_control_price_response": getattr(
                journey, "control_price_response", None,
            ),
            "journey_control_impact_per_pressure": getattr(
                journey, "control_impact_per_pressure", None,
            ),
        }
        output.update({key: value for key, value in optional.items() if value is not None})
        for index, block in enumerate(getattr(journey, "blocks", ()), start=1):
            prefix = f"journey_activity_third_{index}"
            output.update(
                {
                    f"{prefix}_price_response": block.price_response,
                    f"{prefix}_realized_capacity": block.realized_capacity,
                },
            )
            block_optional = {
                f"{prefix}_flow_coherence": block.flow_coherence,
                f"{prefix}_flow_share": block.flow_share,
                f"{prefix}_pressure": block.pressure,
                f"{prefix}_impact_per_pressure": block.impact_per_pressure,
                f"{prefix}_absorption_outcome_proxy": block.absorption_outcome_proxy,
            }
            output.update(
                {
                    key: value
                    for key, value in block_optional.items()
                    if value is not None
                },
            )
        return output

    def _episode_tape(self, watch: EpisodeWatch, observed_time_ns: int):
        return list(
            self.journey.bars_between(
                watch.interaction_time_ns,
                observed_time_ns,
            )
        )

    def _event_ownership(
        self,
        watch: EpisodeWatch,
        decision_bar: Bar,
        bars_by_symbol: Mapping[str, Sequence[Bar]] | None,
    ) -> dict[str, float | str | int]:
        direction = 1.0 if watch.side == "LONG" else -1.0

        def move(values: Sequence[Bar]) -> tuple[float | None, float | None]:
            segment = [
                item for item in values
                if watch.interaction_time_ns < item.close_time_ns <= decision_bar.close_time_ns
            ]
            if not segment or segment[0].open <= 0.0:
                return None, None
            raw = log(segment[-1].close / segment[0].open)
            prior = [
                item for item in values
                if item.close_time_ns < watch.interaction_time_ns
            ]
            if not prior or prior[-1].close <= 0.0:
                return raw, None
            atr = SymbolMarketState.atr(list(prior), length=20)
            volatility_fraction = atr / prior[-1].close
            units = raw / volatility_fraction if volatility_fraction > 0.0 else None
            return raw, units

        own_bars = (
            bars_by_symbol[self.symbol]
            if bars_by_symbol is not None and self.symbol in bars_by_symbol
            else tuple(self.market.one_minute)
        )
        own_raw, own_units = move(own_bars)
        if own_raw is None:
            return {
                "ownership_known": 0,
                "ownership_reason": "LOCAL_EVENT_MOVE_UNAVAILABLE",
            }
        peer_moves = {
            symbol: value
            for symbol, bars in bars_by_symbol.items()
            if symbol != self.symbol
            and (value := move(bars))[0] is not None
        } if bars_by_symbol is not None else {}
        peers = [float(value[0]) for value in peer_moves.values() if value[0] is not None]
        peer_units = [
            direction * float(value[1])
            for value in peer_moves.values()
            if value[1] is not None
        ]
        local = direction * own_raw
        ownership = classify_source_ownership(
            local_units=(None if own_units is None else direction * own_units),
            peer_units=peer_units,
        )
        output: dict[str, float | str | int] = {
            "ownership_known": int(ownership.role is not SourceOwnershipRole.UNKNOWN),
            "event_local_progress": local,
            "ownership_reason": "VOLATILITY_NORMALIZED_LOCAL_MINUS_COMMON",
            "source_ownership_role": ownership.role.value,
            "peer_context_known": int(bool(peers)),
        }
        if ownership.local_units is not None:
            output["event_local_progress_units"] = ownership.local_units
        if ownership.common_units is not None:
            output["event_common_progress_units"] = ownership.common_units
        if ownership.residual_units is not None:
            output["event_residual_ownership_units"] = ownership.residual_units
        if peers:
            common = direction * float(median(peers))
            residual = local - common
            output.update(
                event_common_progress=common,
                event_residual_ownership=residual,
                event_ownership_role=(
                    "LOCAL_LEADER"
                    if ownership.role is SourceOwnershipRole.LOCAL_SOURCE_OWNER
                    else "COMMON_FOLLOWER"
                    if local > 0.0 and common > 0.0
                    else "DIVIDED"
                ),
            )
        else:
            output["event_ownership_role"] = "LOCAL_ONLY"
        if bars_by_symbol is None:
            bars_by_symbol = {self.symbol: own_bars}
        elif self.symbol not in bars_by_symbol:
            bars_by_symbol = {**bars_by_symbol, self.symbol: own_bars}
        event_segments = {
            symbol: [
                item for item in values
                if watch.interaction_time_ns < item.close_time_ns <= decision_bar.close_time_ns
            ]
            for symbol, values in bars_by_symbol.items()
        }
        common_starts = {
            segment[0].close_time_ns for segment in event_segments.values() if segment
        }
        if (
            len(event_segments) >= 3
            and len(common_starts) == 1
            and all(len(segment) >= 2 for segment in event_segments.values())
        ):
            del common_starts
            paths = {
                symbol: tuple(
                    [EventPrice(watch.interaction_time_ns, segment[0].open)]
                    + [
                        EventPrice(item.close_time_ns, item.close)
                        for item in segment
                        if item.close_time_ns > watch.interaction_time_ns
                    ]
                )
                for symbol, segment in event_segments.items()
            }
            prior_closes = {
                name: max(
                    (
                        item.close_time_ns for item in bars_by_symbol[name]
                        if item.close_time_ns < watch.interaction_time_ns
                    ),
                    default=-1,
                )
                for name in event_segments
            }
            directional_contexts = None
            if len(set(prior_closes.values())) == 1 and min(prior_closes.values()) >= 0:
                prior_time_ns = next(iter(prior_closes.values()))
                try:
                    directional_contexts = {
                        name: build_directional_context(
                            symbol=name,
                            side=watch.side,
                            decision_time_ns=prior_time_ns,
                            bars_by_symbol=bars_by_symbol,
                            interval_minutes=1,
                        )
                        for name in event_segments
                    }
                except ValueError:
                    directional_contexts = None
            roles = analyze_cross_market_roles(
                symbols=tuple(sorted(event_segments)),
                symbol=self.symbol,
                side=watch.side,
                sweep_time_ns=watch.interaction_time_ns,
                decision_time_ns=decision_bar.close_time_ns,
                event_paths=paths,
                directional_contexts=directional_contexts,
            )
            if roles.synchronized_event_complete:
                mode = (
                    "INDEPENDENT_LOCAL_TRANSFER"
                    if roles.independently_leads_event
                    else "COMMON_CASCADE"
                    if roles.peer_participation.value in {"UNANIMOUS", "DOMINANT_QUORUM"}
                    else "DIVIDED_MARKET_TRANSFER"
                )
                output.update(
                    cross_market_ownership_mode=mode,
                    cross_market_event_direction_rank=roles.event_direction_rank or 0,
                    cross_market_signed_event_return=roles.signed_event_returns[self.symbol],
                    cross_market_sweep_time_ns=roles.sweep_time_ns,
                    cross_market_event_leadership_role=roles.event_leadership_role.value,
                    cross_market_peer_participation=roles.peer_participation.value,
                    cross_market_aligned_peer_count=roles.aligned_peer_count or 0,
                    cross_market_local_path_efficiency=(
                        roles.local_event_path_efficiency
                        if roles.local_event_path_efficiency is not None else "UNKNOWN"
                    ),
                    cross_market_accepted_repricing_phase=roles.accepted_repricing_phase.value,
                    cross_market_trailing_auction_role=roles.trailing_auction_role.value,
                )
            else:
                output.update(
                    cross_market_ownership_mode="UNKNOWN",
                    cross_market_event_leadership_role="UNKNOWN",
                    cross_market_peer_participation="UNKNOWN",
                )
        else:
            output.update(
                cross_market_ownership_mode="UNKNOWN",
                cross_market_event_leadership_role="UNKNOWN",
                cross_market_peer_participation="UNKNOWN",
            )
        prior_time_ns = max(
            (
                item.close_time_ns for item in bars_by_symbol[self.symbol]
                if item.close_time_ns < watch.interaction_time_ns
            ),
            default=-1,
        )
        update = None
        if prior_time_ns >= 0:
            try:
                update = build_directional_update(
                    symbol=self.symbol,
                    side=watch.side,
                    prior_time_ns=prior_time_ns,
                    decision_time_ns=decision_bar.close_time_ns,
                    bars_by_symbol=bars_by_symbol,
                    interval_minutes=1,
                )
            except ValueError:
                update = None
        if update is not None:
            output.update(
                directional_trend_alignment=(
                    "UNKNOWN"
                    if update.posterior.trend_alignment is None
                    else update.posterior.trend_alignment
                ),
                directional_symbol_residual=(
                    "UNKNOWN"
                    if update.posterior.symbol_residual is None
                    else update.posterior.symbol_residual
                ),
                directional_common_component=(
                    "UNKNOWN"
                    if update.posterior.common_component is None
                    else update.posterior.common_component
                ),
                directional_prior_alignment=(
                    "UNKNOWN"
                    if update.prior.trend_alignment is None
                    else update.prior.trend_alignment
                ),
                directional_alignment_update=(
                    "UNKNOWN"
                    if update.trend_alignment_update is None
                    else update.trend_alignment_update
                ),
            )
        output.update(self._directional_ownership_category(watch, update))
        return output

    @staticmethod
    def _directional_ownership_category(
        watch: EpisodeWatch, update: object | None,
    ) -> dict[str, float | str | int]:
        prior = getattr(getattr(update, "prior", None), "trend_alignment", None)
        posterior = getattr(getattr(update, "posterior", None), "trend_alignment", None)
        change = getattr(update, "trend_alignment_update", None)
        if not isinstance(posterior, (float, int)):
            return {
                "directional_ownership_category": "UNKNOWN",
                "directional_posterior_support_state": "UNKNOWN",
                "directional_posterior_support_rank": -1,
                "directional_family_transition_state": "UNKNOWN",
                "directional_family_transition_rank": -1,
            }
        posterior_supported = float(posterior) > 0.0
        support_state = "SUPPORTED" if posterior_supported else "OPPOSED"
        support_rank = 1 if posterior_supported else 0
        if watch.family == "ACCEPTED_AUCTION_CONTINUATION":
            if not posterior_supported:
                category, transition_rank = "ACCEPTED_POSTERIOR_OPPOSED", 0
            elif isinstance(prior, (float, int)) and float(prior) > 0.0:
                if isinstance(change, (float, int)) and float(change) > 0.0:
                    category, transition_rank = "CARRIED_PRIOR_FRESH_OUTWARD_UPDATE", 2
                else:
                    category, transition_rank = "CARRIED_PRIOR_ALIGNED_CONTINUATION", 1
            else:
                category, transition_rank = "FRESH_OUTWARD_DIRECTIONAL_TRANSFER", 2
        elif not posterior_supported:
            category, transition_rank = "POST_SWEEP_DIRECTIONAL_TRANSFER_ABSENT", 0
        elif isinstance(prior, (float, int)) and float(prior) < 0.0:
            category, transition_rank = "GENUINE_POST_SWEEP_DIRECTIONAL_REVERSAL", 2
        elif isinstance(change, (float, int)) and float(change) > 0.0:
            category, transition_rank = "POST_SWEEP_DIRECTIONAL_TRANSFER", 2
        else:
            category, transition_rank = "POST_SWEEP_DIRECTIONAL_SUPPORT", 1
        return {
            "directional_ownership_category": category,
            "directional_posterior_support_state": support_state,
            "directional_posterior_support_rank": support_rank,
            "directional_family_transition_state": category,
            "directional_family_transition_rank": transition_rank,
        }

    def _inventory_evidence(
        self, watch: EpisodeWatch, decision_bar: Bar,
    ) -> dict[str, float | str | int]:
        if self.inventory_timeline is None:
            return {
                "inventory_interpretation": "UNKNOWN",
                "inventory_reason": "NO_INVENTORY_TIMELINE",
                "inventory_coherence_rank": 0,
            }
        episode = self._episode_tape(watch, decision_bar.close_time_ns)
        if not episode:
            return {
                "inventory_interpretation": "UNKNOWN",
                "inventory_reason": "NO_CAUSAL_TAPE",
                "inventory_coherence_rank": 0,
            }
        if any(item.signed_flow is None for item in episode):
            return {
                "inventory_interpretation": "UNKNOWN",
                "inventory_reason": "PRICE_OR_FLOW_MISSING",
                "inventory_regime": "UNKNOWN",
                "inventory_coherence_rank": 0,
            }
        decision = self.inventory_timeline.evaluate(
            shock_side="BUY" if watch.side == "LONG" else "SELL",
            episode_start_ns=watch.interaction_time_ns,
            decision_ts_ns=decision_bar.close_time_ns,
            price_move=episode[-1].close - episode[0].open,
            signed_taker_flow=sum(
                float(item.signed_flow) for item in episode
                if item.signed_flow is not None
            ),
        )
        coherent = (
            watch.family == "ACCEPTED_AUCTION_CONTINUATION"
            and decision.known
            and decision.interpretation
            is InventoryInterpretation.FRESH_SPONSORSHIP_CROWDING
        ) or (
            watch.family == "FAILED_AUCTION_REVERSAL"
            and decision.known
            and decision.interpretation
            is InventoryInterpretation.FORCED_DELEVERAGING_DISCHARGE
        )
        contradictory = decision.known and not coherent and (
            decision.interpretation is not InventoryInterpretation.UNCHANGED_INVENTORY
        )
        return {
            "inventory_interpretation": decision.interpretation.value,
            "inventory_reason": decision.reason,
            "inventory_regime": decision.regime.value,
            "inventory_coherence_rank": 1 if coherent else -1 if contradictory else 0,
            "inventory_oi_change_fraction": (
                "UNKNOWN" if decision.oi_change_fraction is None else decision.oi_change_fraction
            ),
        }

    def _advance_watches(
        self,
        bar: Bar,
        serial: int,
        atr: float,
        common_breadth: float,
        bars_by_symbol: Mapping[str, Sequence[Bar]] | None = None,
    ) -> list[TradePlan]:
        del atr, common_breadth
        output: list[TradePlan] = []
        existing_structural_ids = self._existing_projected_structure_ids(
            bar.close_time_ns, serial,
        )
        for episode_id, watch in list(self._watches.items()):
            # Processing a newer accepted episode can terminally supersede and
            # remove an older watch while this snapshot is still being
            # traversed.  Never evaluate that stale object again: doing so can
            # create a second, contradictory terminal reason for one episode.
            if self._watches.get(episode_id) is not watch:
                continue
            if bar.close_time_ns <= watch.last_update_time_ns or watch.state == "PROPOSED":
                continue
            if (
                self._is_versioned_structural_kind(watch.source.kind)
                and watch.source.boundary_id not in existing_structural_ids
            ):
                self._record_terminal(
                    "STRUCTURAL_SOURCE_VERSION_SUPERSEDED", watch, bar,
                )
                self._watches.pop(episode_id, None)
                self._proposals.pop(episode_id, None)
                continue
            journey = self.journey.evaluate(
                self._interaction(watch), bar.close_time_ns,
            )
            watch.last_update_serial = serial
            watch.last_update_time_ns = bar.close_time_ns
            watch.state = journey.terminal_state
            watch.evidence.update(
                journey_terminal_state=journey.terminal_state,
                journey_completed_states="|".join(journey.completed_states),
                journey_phase_basis=journey.phase_basis,
                journey_control_transfer=int(journey.control_transfer),
                journey_activity_known=int(journey.activity_input_known),
                journey_flow_known=int(journey.flow_input_known),
                **self._journey_flow_response_evidence(journey),
            )
            if not journey.completed or journey.family is None:
                terminal_acceptance = journey.terminal_state in {
                    "ACCEPTANCE_FIRST_RESPONSE_FAILED",
                    "ACCEPTANCE_TARGET_SPENT_ON_FIRST_RESPONSE",
                    "ACCEPTANCE_STOP_TOUCHED_ON_FIRST_RESPONSE",
                }
                if terminal_acceptance:
                    self._complete_attack_response(watch, journey, bar)
                    self._record_terminal(journey.terminal_state, watch, bar)
                    self._watches.pop(episode_id, None)
                    continue
                if journey.terminal_state == "HISTORY_UNAVAILABLE":
                    self._record_terminal(
                        "HISTORY_UNAVAILABLE_TERMINAL",
                        watch,
                        bar,
                    )
                    self._watches.pop(episode_id, None)
                    continue
                self._record(
                    "AUCTION_SEQUENCE_INCOMPLETE", watch, bar,
                    terminal_state=journey.terminal_state,
                    completed_states="|".join(journey.completed_states),
                )
                continue
            self._complete_attack_response(watch, journey, bar)
            interaction = self._interaction(watch)
            existing_owner = self.journey_registry.existing_owner(interaction)
            if existing_owner is not None and existing_owner.owner_id != episode_id:
                self._record_terminal(
                    "OVERLAPPING_STRUCTURE_INTERACTION_ALREADY_OWNED",
                    watch,
                    bar,
                    owning_episode_id=existing_owner.owner_id,
                )
                self._watches.pop(episode_id, None)
                continue
            self.journey_registry.claim(interaction, journey, episode_id)
            watch.family = journey.family
            watch.side = self._side_for_journey(watch.source.side, watch.family)
            if journey.family == "DEFENDED_AUCTION_CONTINUATION":
                self._record_terminal(
                    "DEFENDED_SEQUENCE_DIAGNOSTIC_ONLY",
                    watch,
                    bar,
                )
                self._watches.pop(episode_id, None)
                continue
            episode = self._episode_tape(watch, bar.close_time_ns)
            if not episode:
                self._record_terminal("NO_CAUSAL_TAPE", watch, bar)
                self._watches.pop(episode_id, None)
                continue
            watch.event_extreme = (
                min(item.low for item in episode)
                if watch.side == "LONG"
                else max(item.high for item in episode)
            )
            watch.proof_extreme = (
                max(item.high for item in episode)
                if watch.side == "LONG"
                else min(item.low for item in episode)
            )
            watch.evidence["acceptance_origin"] = episode[0].open

            if watch.family == "ACCEPTED_AUCTION_CONTINUATION":
                # auction_journey owns the exact RE1 first-response event.  A
                # 5m fallback which notices that event later cannot revive it.
                if journey.response_time_ns != bar.close_time_ns:
                    self._record_terminal(
                        "ACCEPTANCE_RESPONSE_ENTRY_STALE", watch, bar,
                        response_time_ns=journey.response_time_ns or 0,
                    )
                    self._watches.pop(episode_id, None)
                    continue
                retest = next(
                    (
                        item for item in episode
                        if item.close_time_ns == journey.retest_time_ns
                    ),
                    None,
                )
                if retest is None:
                    self._record_terminal(
                        "ACCEPTANCE_RETEST_EVIDENCE_MISSING",
                        watch,
                        bar,
                    )
                    self._watches.pop(episode_id, None)
                    continue
                watch.pullback_extreme = (
                    retest.low if watch.side == "LONG" else retest.high
                )
                watch.evidence.update(
                    acceptance_first_response="CONFIRMED_NOW",
                    acceptance_retest_time_ns=journey.retest_time_ns or 0,
                    acceptance_response_time_ns=journey.response_time_ns or 0,
                    acceptance_response_required_extreme=(
                        journey.response_required_extreme
                        if journey.response_required_extreme is not None else 0.0
                    ),
                )

            ownership = self._event_ownership(watch, bar, bars_by_symbol)
            local_progress = ownership.get("event_local_progress")
            if not isinstance(local_progress, (float, int)):
                self._record_terminal(
                    "COUNTERFACTUAL_DIRECTION_UNKNOWN",
                    watch,
                    bar,
                )
                self._watches.pop(episode_id, None)
                continue
            watch.evidence.update(ownership)
            local_progress = float(local_progress)
            watch.ownership_balance = local_progress
            watch.supportive_control = max(local_progress, 0.0)
            watch.opposing_control = max(-local_progress, 0.0)
            ownership_role = str(
                ownership.get("source_ownership_role", SourceOwnershipRole.UNKNOWN.value),
            )
            if ownership_role != SourceOwnershipRole.LOCAL_SOURCE_OWNER.value:
                reason = (
                    "COMMON_MARKET_MOVE_WITHOUT_LOCAL_SOURCE_OWNERSHIP"
                    if ownership_role
                    == SourceOwnershipRole.COMMON_MARKET_OWNER_ONLY.value
                    else "ABSOLUTE_DIRECTIONAL_DELIVERY_ABSENT"
                    if ownership_role
                    == SourceOwnershipRole.NO_DIRECTIONAL_DELIVERY.value
                    else "COUNTERFACTUAL_DIRECTION_UNKNOWN"
                )
                self._record_terminal(
                    reason,
                    watch,
                    bar,
                    selection_state="NULL",
                    event_local_progress=local_progress,
                    event_common_progress=float(
                        ownership.get("event_common_progress", 0.0),
                    ),
                    event_residual_ownership=float(
                        ownership.get("event_residual_ownership", 0.0),
                    ),
                    event_ownership_role=str(
                        ownership.get("event_ownership_role", "DIVIDED"),
                    ),
                )
                self._watches.pop(episode_id, None)
                continue
            directional_support = str(
                ownership.get("directional_posterior_support_state", "UNKNOWN"),
            )
            if directional_support != "SUPPORTED":
                # A completed liquidity event may propose a direction, but it
                # does not own the account while the multi-horizon price/flow
                # state still points the other way.  Zero is the semantic
                # boundary; this introduces no fitted strength threshold.
                self._record_terminal(
                    (
                        "POST_EVENT_DIRECTION_REMAINS_OPPOSED"
                        if directional_support == "OPPOSED"
                        else "POST_EVENT_DIRECTION_UNAVAILABLE"
                    ),
                    watch,
                    bar,
                    selection_state="NULL",
                    directional_posterior_support_state=directional_support,
                    directional_family_transition_state=str(
                        ownership.get(
                            "directional_family_transition_state",
                            "UNKNOWN",
                        ),
                    ),
                )
                self._watches.pop(episode_id, None)
                continue
            self._claim_attack_owner(watch, bar)
            watch.evidence.update(self._inventory_evidence(watch, bar))
            if not self._commit_destination(watch, journey, bar):
                self._watches.pop(episode_id, None)
                continue
            zone = self._origin_zone(watch, bar)
            watch.entry_zone = zone
            plan = self._build_plan(
                watch,
                bar,
                serial,
                max(self.market.atr(self.market.five_minute), self.tick_size),
                ownership,
                zone,
                journey,
            )
            if plan is None:
                # Completion owns one immutable destination/route decision.
                # A later consumed obstacle may not reveal a farther substitute.
                self._watches.pop(episode_id, None)
                continue
            if watch.family == "ACCEPTED_AUCTION_CONTINUATION":
                self._supersede_older_same_side(watch, bar)
            watch.state = "PROPOSED"
            output.append(plan)
        return output

    def _supersede_older_same_side(self, owner: EpisodeWatch, bar: Bar) -> None:
        for episode_id, older in list(self._watches.items()):
            if (
                episode_id == owner.episode_id
                or older.side != owner.side
                or older.interaction_time_ns >= owner.interaction_time_ns
            ):
                continue
            self._record_terminal(
                "NEWER_SAME_SIDE_LEVEL_SUPERSEDED_PENDING",
                older,
                bar,
                plan=self._proposals.get(episode_id),
                newer_episode_id=owner.episode_id,
            )
            self._watches.pop(episode_id, None)
            self._proposals.pop(episode_id, None)
        self._invalidate_claimed_before(
            owner,
            bar,
            same_side=True,
            reason="NEWER_SAME_SIDE_ACCEPTED_LEVEL_SUPERSEDED_PENDING",
        )
        if owner.source.timeframe_minutes >= 15:
            self._invalidate_claimed_before(
                owner,
                bar,
                same_side=False,
                reason="OPPOSITE_15M_ACCEPTED_LEG_SUPERSEDED_PENDING",
            )

    def _invalidate_claimed_before(
        self,
        owner: EpisodeWatch,
        bar: Bar,
        *,
        same_side: bool,
        reason: str,
    ) -> None:
        for episode_id, metadata in self._claimed_plan_metadata.items():
            plan_id = str(metadata["plan_id"])
            if plan_id in self._invalidated_claimed_plans:
                continue
            side_matches = metadata.get("side") == owner.side
            if side_matches != same_side:
                continue
            interaction = int(metadata.get("interaction_time_ns", -1))
            if interaction >= owner.interaction_time_ns:
                continue
            self._invalidated_claimed_plans[plan_id] = {
                "episode_id": episode_id,
                "reason": reason,
                "time_ns": bar.close_time_ns,
                "superseding_episode_id": owner.episode_id,
            }
            self._diagnostic_counts[reason] = self._diagnostic_counts.get(reason, 0) + 1

    @staticmethod
    def _intersection(
        *bands: tuple[float, float],
    ) -> tuple[float, float] | None:
        lower = max(item[0] for item in bands)
        upper = min(item[1] for item in bands)
        return (lower, upper) if upper > lower else None

    def _origin_zone(
        self, watch: EpisodeWatch, decision_bar: Bar, atr: float | None = None,
    ) -> EntryZone:
        del atr
        source_lower, source_upper = watch.source.band_at(self.market.serial_5m)
        frozen_lower = watch.evidence.get("interaction_source_lower")
        frozen_upper = watch.evidence.get("interaction_source_upper")
        location_source_lower = (
            float(frozen_lower)
            if isinstance(frozen_lower, (float, int))
            else watch.source.band_at(watch.interaction_serial)[0]
        )
        location_source_upper = (
            float(frozen_upper)
            if isinstance(frozen_upper, (float, int))
            else watch.source.band_at(watch.interaction_serial)[1]
        )
        locations = event_local_locations(
            list(self.market.one_minute),
            side=watch.side,
            event_start_time_ns=watch.interaction_time_ns,
            decision_time_ns=decision_bar.close_time_ns,
            source_lower=location_source_lower,
            source_upper=location_source_upper,
            tick_size=self.tick_size,
        )
        obs = [item for item in locations if item.kind == "ORDER_BLOCK"]
        fvgs = [item for item in locations if item.kind == "FAIR_VALUE_GAP"]
        choices: list[tuple[int, int, str, float, float, int, int, float]] = []
        source_band = (location_source_lower, location_source_upper)
        for ob in obs:
            for fvg in fvgs:
                overlap = self._intersection(
                    source_band, (ob.lower, ob.upper), (fvg.lower, fvg.upper),
                )
                if overlap is not None:
                    choices.append(
                        (0, -max(ob.observed_time_ns, fvg.observed_time_ns),
                         "SOURCE_ORDER_BLOCK_FVG", overlap[0], overlap[1],
                         max(ob.observed_time_ns, fvg.observed_time_ns),
                         max(ob.source_time_ns, fvg.source_time_ns),
                         min(ob.invalidation, fvg.invalidation)
                         if watch.side == "LONG" else max(ob.invalidation, fvg.invalidation))
                    )
        for ob in obs:
            overlap = self._intersection(source_band, (ob.lower, ob.upper))
            if overlap is not None:
                choices.append(
                    (1, -ob.observed_time_ns, "SOURCE_ORDER_BLOCK", overlap[0], overlap[1],
                     ob.observed_time_ns, ob.source_time_ns, ob.invalidation)
                )
        # FVG without an overlapping event-local OB is intentionally absent.
        if fvgs and not any(item[2] == "SOURCE_ORDER_BLOCK_FVG" for item in choices):
            self._record("STANDALONE_FVG_NOT_EXECUTABLE", watch, decision_bar)
        choices.append(
            (2, -watch.source.observed_time_ns, "TRANSFERRED_SOURCE", source_lower,
             source_upper, watch.source.observed_time_ns, watch.interaction_time_ns,
             source_lower - self.tick_size if watch.side == "LONG"
             else source_upper + self.tick_size)
        )
        _, _, kind, lower, upper, observed, source_time, invalidation = min(choices)
        watch.evidence["location_components"] = kind
        watch.evidence["location_invalidation"] = invalidation
        return EntryZone(kind, lower, upper, observed, source_time)

    def _source_node(self, watch: EpisodeWatch, serial: int) -> StructuralNode:
        lower, upper = watch.source.band_at(serial)
        return StructuralNode(
            node_id=watch.source.boundary_id,
            symbol=self.symbol,
            side=watch.source.side,
            kind=watch.source.kind,
            role=StructureRole.SOURCE,
            timeframe_minutes=watch.source.timeframe_minutes,
            observed_time_ns=watch.source.observed_time_ns,
            lower=lower,
            upper=upper,
            anchor_serial=serial,
            slope_per_bar=watch.source.dynamic_slope_per_bar,
            invalidation=(
                lower - self.tick_size if watch.source.side == "LOW"
                else upper + self.tick_size
            ),
            consumed_time_ns=watch.source.consumed_time_ns,
        )

    def _commit_destination(
        self,
        watch: EpisodeWatch,
        journey: JourneyEvidence,
        decision_bar: Bar,
    ) -> bool:
        """Freeze the first objective when auction direction settles.

        RE1 committed accepted-auction objectives at the held first return;
        structural synthesis recommitted only after a genuine failed-auction
        role flip.  Looking the book up *as of* that checkpoint prevents an
        objective spent during later confirmation from revealing a farther TP.
        """

        checkpoint = (
            journey.retest_time_ns
            if watch.family == "ACCEPTED_AUCTION_CONTINUATION"
            else journey.reclaim_time_ns
        )
        if checkpoint is None:
            self._record_terminal(
                "OBJECTIVE_COMMIT_CHECKPOINT_UNAVAILABLE",
                watch,
                decision_bar,
            )
            return False
        checkpoint_bar = next(
            (
                item
                for item in self.journey.bars_between(
                    watch.interaction_time_ns,
                    decision_bar.close_time_ns,
                )
                if item.close_time_ns == checkpoint
            ),
            None,
        )
        if checkpoint_bar is None:
            self._record_terminal(
                "OBJECTIVE_COMMIT_BAR_UNAVAILABLE",
                watch,
                decision_bar,
            )
            return False
        candidates = self.market.objective_book.destination_candidates_at(
            side=watch.side,
            reference_price=checkpoint_bar.close,
            decision_time_ns=checkpoint,
            source_boundary_id=watch.source.boundary_id,
        )
        if not candidates:
            self._record_terminal(
                "NO_FRESH_OPPOSING_DESTINATION_AT_SETTLEMENT",
                watch,
                decision_bar,
                objective_commit_time_ns=checkpoint,
            )
            return False
        destination = candidates[0]
        watch.committed_destination = destination
        watch.objective_commit_time_ns = checkpoint
        watch.evidence.update(
            objective_commit_time_ns=checkpoint,
            objective_commit_reference_price=checkpoint_bar.close,
            committed_destination_boundary_id=destination.boundary_id,
            committed_destination_price=destination.price,
            committed_destination_kind=destination.kind,
            committed_destination_observed_time_ns=destination.observed_time_ns,
            objective_commit_role=(
                "ACCEPTED_HELD_FIRST_RETURN"
                if watch.family == "ACCEPTED_AUCTION_CONTINUATION"
                else "FAILED_RECLAIM_OR_ROLE_FLIP"
            ),
        )
        return True

    def _current_committed_destination(
        self,
        watch: EpisodeWatch,
        decision_bar: Bar,
        entry: float,
    ) -> LiquidityBoundary | None:
        committed = watch.committed_destination
        if committed is None or watch.objective_commit_time_ns is None:
            self._record_terminal(
                "OBJECTIVE_NOT_COMMITTED_AT_SETTLEMENT",
                watch,
                decision_bar,
            )
            return None
        current = self.market.objective_book.objectives.get(
            committed.boundary_id,
        )
        if current is None:
            self._record_terminal(
                "COMMITTED_DESTINATION_IDENTITY_LOST",
                watch,
                decision_bar,
            )
            return None
        if (
            current.consumed_time_ns is not None
            and current.consumed_time_ns <= decision_bar.close_time_ns
        ):
            self._record_terminal(
                "COMMITTED_DESTINATION_SPENT_BEFORE_ENTRY",
                watch,
                decision_bar,
                committed_destination_boundary_id=current.boundary_id,
            )
            return None
        target = self._objective_execution_price(watch.side, current.price)
        direction = 1.0 if watch.side == "LONG" else -1.0
        if direction * (target - entry) <= self.tick_size:
            self._record_terminal(
                "COMMITTED_DESTINATION_PASSED_BEFORE_ENTRY",
                watch,
                decision_bar,
                committed_destination_boundary_id=current.boundary_id,
            )
            return None
        if self._has_new_closer_objective(
            side=watch.side,
            entry=entry,
            target=target,
            destination_boundary_id=current.boundary_id,
            source_boundary_id=watch.source.boundary_id,
            decision_time_ns=decision_bar.close_time_ns,
            route_commit_time_ns=watch.objective_commit_time_ns,
        ):
            self._record_terminal(
                "NEW_CLOSER_OBJECTIVE_ENDED_COMMITTED_ROUTE",
                watch,
                decision_bar,
                committed_destination_boundary_id=current.boundary_id,
            )
            return None
        return current

    def _route_nodes(
        self,
        watch: EpisodeWatch,
        decision_time_ns: int,
        serial: int,
        entry: float | None = None,
    ) -> list[StructuralNode]:
        wanted = "HIGH" if watch.side == "LONG" else "LOW"
        output: list[StructuralNode] = []
        if entry is not None and watch.committed_destination is not None:
            objectives = [
                self.market.objective_book.objectives[
                    watch.committed_destination.boundary_id
                ],
            ]
        elif entry is None:
            objectives = self.market.objective_book.active(
                decision_time_ns,
                source_boundary_id=watch.source.boundary_id,
            )
        else:
            objectives = self.market.objective_book.destination_candidates(
                side=watch.side,
                entry=entry,
                decision_time_ns=decision_time_ns,
                source_boundary_id=watch.source.boundary_id,
            )
        if entry is None:
            objectives = sorted(
                (item for item in objectives if item.side == wanted),
                key=lambda item: (
                    -item.timeframe_minutes,
                    -item.strength,
                    item.boundary_id,
                ),
            )
        for boundary in objectives:
            execution_price = self._objective_execution_price(
                watch.side,
                boundary.price,
            )
            output.append(
                StructuralNode(
                    node_id=boundary.boundary_id,
                    symbol=self.symbol,
                    side=boundary.side,
                    kind=boundary.kind,
                    role=StructureRole.DESTINATION,
                    timeframe_minutes=boundary.timeframe_minutes,
                    observed_time_ns=boundary.observed_time_ns,
                    # Objective identity and first-touch consumption stay at
                    # the actual pivot; the executable TP sits one instrument
                    # tick inside it so a fill does not require trading exactly
                    # at the liquidity line.
                    lower=execution_price,
                    upper=execution_price,
                    anchor_serial=serial,
                    consumed_time_ns=boundary.consumed_time_ns,
                )
            )
        for projected in self._projected_structural_nodes(decision_time_ns, serial):
            if projected.node_id == watch.source.boundary_id:
                continue
            lower, upper = projected.band_at(serial)
            output.append(
                StructuralNode(
                    node_id=projected.node_id,
                    symbol=projected.symbol,
                    side=projected.side,
                    kind=projected.kind,
                    # Lines and channels interpret the route.  They are not an
                    # implicit full-position rotation target.
                    role=StructureRole.ROUTE_OBSTACLE,
                    timeframe_minutes=projected.timeframe_minutes,
                    observed_time_ns=projected.observed_time_ns,
                    lower=lower,
                    upper=upper,
                    anchor_serial=serial,
                    slope_per_bar=projected.slope_per_bar,
                    version=projected.version,
                    invalidation=projected.invalidation,
                    # First interaction retires diagonal geometry from future
                    # route decisions.  The campaign/watch retains source
                    # identity separately until its auction journey resolves.
                    consumed_time_ns=projected.consumed_time_ns,
                    superseded_time_ns=projected.superseded_time_ns,
                )
            )
        return output

    def _pre_entry_geometry_violation(
        self,
        watch: EpisodeWatch,
        *,
        decision_time_ns: int,
        stop: float,
        target: float,
        target_live_after_ns: int,
    ) -> str | None:
        """Reject geometry consumed anywhere in the completed event tape.

        ``EventTimeAuctionJourney`` freezes accepted ownership at the exact
        first-response bar.  Route geometry is only known afterwards, so the
        policy must replay that immutable geometry over the whole already-
        observed interaction, not merely over the response candle.  Target is
        tested before stop on each bar, preserving RE1's conservative terminal
        ordering when both prices occur in one candle.
        """

        for event_bar in self.journey.bars_between(
            watch.interaction_time_ns,
            decision_time_ns,
        ):
            target_touched = event_bar.close_time_ns > target_live_after_ns and (
                event_bar.high >= target
                if watch.side == "LONG"
                else event_bar.low <= target
            )
            if target_touched:
                return "DESTINATION_SPENT_BEFORE_ENTRY"
            stop_touched = (
                event_bar.low <= stop
                if watch.side == "LONG"
                else event_bar.high >= stop
            )
            if stop_touched:
                return "COMPLETE_EPISODE_INVALIDATED_BEFORE_ENTRY"
        return None

    def _build_plan(
        self,
        watch: EpisodeWatch,
        decision_bar: Bar,
        serial: int,
        atr: float,
        evidence: Mapping[str, float | str | int],
        zone: EntryZone,
        completed_journey: JourneyEvidence | None = None,
    ) -> TradePlan | None:
        del atr
        immediate_acceptance_response = (
            watch.family == "ACCEPTED_AUCTION_CONTINUATION"
            and completed_journey is not None
            and completed_journey.response_time_ns == decision_bar.close_time_ns
            and completed_journey.response_close is not None
        )
        if immediate_acceptance_response:
            # RE1 reaction semantics: the first held retest is already the
            # return, and the first later micro response close is the entry.
            # Waiting for the source again would silently trade a second return.
            entry = float(completed_journey.response_close)
        else:
            # Failed/reclaimed auctions wait at the first price encountered on
            # a future defended return.  The completion candle necessarily
            # crossed/reclaimed the source before the plan existed, so only
            # displacement at its close is required here.  Later completed
            # bars own pass/fill invalidation in ``_refresh_proposals``.
            entry = zone.upper if watch.side == "LONG" else zone.lower
            future_return = (
                entry < decision_bar.close - self.tick_size
                if watch.side == "LONG"
                else entry > decision_bar.close + self.tick_size
            )
            if not future_return:
                self._record_terminal(
                    "FIRST_RETURN_ALREADY_PASSED", watch, decision_bar, entry=entry,
                )
                return None
        source = self._source_node(watch, serial)
        location_invalidation = watch.evidence.get("location_invalidation")
        location_stop = (
            float(location_invalidation)
            if isinstance(location_invalidation, (float, int))
            else zone.lower - self.tick_size if watch.side == "LONG"
            else zone.upper + self.tick_size
        )
        source_invalidation = source.invalidation
        acceptance_origin = watch.evidence.get("acceptance_origin")
        stop = structural_stop(
            side=watch.side,
            micro_stop=location_stop,
            event_extreme=watch.event_extreme,
            tick_size=self.tick_size,
            source_invalidation=source_invalidation,
            location_invalidation=location_stop,
            acceptance_origin=(
                float(acceptance_origin)
                if watch.family == "ACCEPTED_AUCTION_CONTINUATION"
                and ("LINE" in watch.source.kind or "CHANNEL" in watch.source.kind)
                and isinstance(acceptance_origin, (float, int))
                else None
            ),
        )
        if not (stop < entry if watch.side == "LONG" else stop > entry):
            self._record_terminal(
                "INVALID_COMPLETE_EPISODE_STOP",
                watch,
                decision_bar,
            )
            return None
        committed_destination = self._current_committed_destination(
            watch,
            decision_bar,
            entry,
        )
        if committed_destination is None:
            return None
        watch.committed_destination = committed_destination
        route = destination_first_geometry(
            side=watch.side,
            source=source,
            nodes=self._route_nodes(
                watch,
                decision_bar.close_time_ns,
                serial,
                entry,
            ),
            entry=entry,
            stop=stop,
            decision_time_ns=decision_bar.close_time_ns,
            serial=serial,
            minimum_gross_rr=1.0,
        )
        if not route.accepted or route.destination is None or route.target is None:
            reason = {
                "NO_FRESH_DESTINATION": "NO_FRESH_OPPOSING_DESTINATION",
                "FIRST_DESTINATION_BELOW_MINIMUM_R": "DESTINATION_BELOW_ONE_R",
                "ROUTE_OBSTACLE_BEFORE_DESTINATION": "ROUTE_OBSTACLE_BEFORE_DESTINATION",
                "INVALID_STRUCTURAL_STOP": "INVALID_COMPLETE_EPISODE_STOP",
            }.get(route.reason, route.reason)
            self._record_terminal(reason, watch, decision_bar)
            return None
        geometry_violation = self._pre_entry_geometry_violation(
            watch,
            decision_time_ns=decision_bar.close_time_ns,
            stop=stop,
            target=route.target,
            target_live_after_ns=max(
                committed_destination.observed_time_ns,
                int(watch.objective_commit_time_ns or 0),
            ),
        )
        if geometry_violation is not None:
            self._record_terminal(geometry_violation, watch, decision_bar)
            return None
        # Re-evaluate the already-completed journey with immutable geometry.
        # A destination or full-episode stop touched before decision cannot be
        # revived as a pending first return.
        verified = self.journey.evaluate(
            self._interaction(watch),
            decision_bar.close_time_ns,
            stop=stop,
            # Target freshness is replayed above only after that objective was
            # observable.  Passing it here would inspect pre-confirmation
            # wicks and manufacture a spent destination.
            target=None,
        ) if self.journey.bars else completed_journey
        if verified is not None and not verified.completed:
            reason = (
                "DESTINATION_SPENT_BEFORE_ENTRY"
                if verified.target_fresh is False
                else "COMPLETE_EPISODE_INVALIDATED_BEFORE_ENTRY"
                if verified.stop_intact is False
                else "COMPLETED_JOURNEY_GEOMETRY_REEVALUATION_FAILED"
            )
            self._record_terminal(reason, watch, decision_bar)
            return None
        risk_fraction = abs(entry - stop) / max(abs(entry), self.tick_size)
        local_progress = float(
            evidence.get("event_local_progress", watch.ownership_balance),
        )
        delivery_per_risk = local_progress / max(risk_fraction, 1e-12)
        residual_units = float(
            evidence.get("event_residual_ownership_units", 0.0),
        )
        htf = self._higher_timeframe_context(watch.side)
        proof_price = watch.proof_extreme if watch.proof_extreme is not None else decision_bar.close
        merged: dict[str, float | str | int] = {
            **watch.evidence,
            **dict(evidence),
            "source_kind": watch.source.kind,
            "destination_kind": route.destination.kind,
            "source_observed_time_ns": watch.source.observed_time_ns,
            "source_timeframe_minutes": watch.source.timeframe_minutes,
            "destination_observed_time_ns": route.destination.observed_time_ns,
            "event_extreme": watch.event_extreme,
            "interaction_time_ns": watch.interaction_time_ns,
            "absolute_delivery_per_risk": delivery_per_risk,
            "counterfactual_ownership_units": residual_units,
            "counterfactual_ownership_per_risk": (
                residual_units / max(risk_fraction, 1e-12)
            ),
            "route_rr": float(route.gross_rr or 0.0),
            "delivery_proof_price": proof_price,
            "delivery_proof_role": "SEQUENCE_COMPLETION_EVIDENCE_ONLY",
            "entry_event": (
                "ACCEPTANCE_FIRST_RESPONSE_CLOSE"
                if immediate_acceptance_response else "FAILED_AUCTION_FUTURE_FIRST_RETURN"
            ),
            "entry_execution_instruction": (
                "IMMEDIATE_MARKETABLE_FIRST_RESPONSE"
                if immediate_acceptance_response else "RESTING_FUTURE_FIRST_RETURN_LIMIT"
            ),
            "completion_target_origin": (
                "SETTLEMENT_COMMITTED_FIRST_LIVE_OPPOSING_"
                "HORIZONTAL_1M_SPAN6_OR_5M_15M_SPAN2_OBJECTIVE"
            ),
            "complete_episode_invalidation": stop,
            "higher_timeframe_regime": str(htf["higher_timeframe_regime"]),
        }
        plan_id = stable_id(
            watch.episode_id, entry, stop, route.target, decision_bar.close_time_ns,
            prefix="PLAN:",
        )
        return TradePlan(
            episode_id=watch.episode_id,
            plan_id=plan_id,
            symbol=self.symbol,
            family=watch.family,
            side=watch.side,
            decision_time_ns=decision_bar.close_time_ns,
            entry=entry,
            stop=stop,
            target=route.target,
            expires_time_ns=MAX_CAUSAL_ORDER_TIME_NS,
            source_boundary_id=watch.source.boundary_id,
            destination_boundary_id=route.destination.node_id,
            entry_zone=zone,
            evidence=merged,
        )


class LiquidityEpisodeCoordinator:
    """Synchronize four markets and arbitrate one actual account opportunity."""

    def __init__(
        self,
        policies: Mapping[str, SymbolEpisodePolicy],
        *,
        decision_symbols: Sequence[str] | None = None,
    ) -> None:
        self.policies = dict(policies)
        if set(self.policies) != {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}:
            raise ValueError("coordinator requires the four configured markets")
        selected = set(self.policies) if decision_symbols is None else set(decision_symbols)
        if not selected or not selected <= set(self.policies):
            raise ValueError("decision_symbols must be a non-empty subset of the four markets")
        # All four markets are always ingested.  Restricting decision symbols
        # is only a focused-research lens; peer paths still supply causal
        # common-market ownership context to every selected symbol.
        self.decision_symbols = frozenset(selected)
        self._pending_by_close: dict[int, dict[str, Bar]] = {}
        self._common_episodes = CommonEpisodeLedger()
        self._common_price_campaigns = CommonPriceCampaignBook(
            tick_sizes={
                symbol: policy.tick_size for symbol, policy in self.policies.items()
            },
        )
        # Frozen attack bars are the price/flow measurement paired with every
        # later official inventory observation.  They are not an execution or
        # expiry clock.
        self._common_attack_bars: dict[str, dict[str, Bar]] = {}

    @staticmethod
    def _common_inventory_decision(
        policy: SymbolEpisodePolicy,
        attack_bar: Bar,
        attack_side: str,
        decision_time_ns: int,
    ) -> InventoryDecision:
        shock_side = "BUY" if attack_side == "LONG" else "SELL"
        if policy.inventory_timeline is None:
            return InventoryDecision(
                symbol=attack_bar.symbol,
                regime=InventoryRegime.UNKNOWN,
                ownership=OwnershipBranch.UNKNOWN,
                interpretation=InventoryInterpretation.UNKNOWN,
                reason="NO_INVENTORY_TIMELINE",
                shock_side=shock_side,
                episode_start_ns=attack_bar.open_time_ns,
                decision_ts_ns=decision_time_ns,
                prior_observed_ts_ns=None,
                current_observed_ts_ns=None,
                oi_change_fraction=None,
                all_account_change_log=None,
                price_flow_aligned=None,
            )
        return policy.inventory_timeline.evaluate(
            shock_side=shock_side,
            episode_start_ns=attack_bar.open_time_ns,
            decision_ts_ns=decision_time_ns,
            price_move=attack_bar.close - attack_bar.open,
            signed_taker_flow=attack_bar.signed_quote_flow,
        )

    def _register_common_attacks(
        self,
        bars: Mapping[str, Bar],
        contexts: Mapping[str, RouterPrebarContext],
    ) -> None:
        """Join synchronized outside breaks into one still-live attack wave."""

        by_side: dict[str, dict[str, dict[str, CommonSourceJoin]]] = {
            "LONG": {},
            "SHORT": {},
        }
        for symbol in sorted(bars):
            context = contexts[symbol]
            attack_bar = bars[symbol]
            if context.flow_baseline is None:
                continue
            for item in context.sources:
                lower, upper = item.boundary.band_at(context.structure_serial)
                if (
                    item.boundary.side == "HIGH"
                    and attack_bar.close > upper
                    and attack_bar.body > 0.0
                    and attack_bar.signed_quote_flow > 0.0
                ):
                    side = "LONG"
                elif (
                    item.boundary.side == "LOW"
                    and attack_bar.close < lower
                    and attack_bar.body < 0.0
                    and attack_bar.signed_quote_flow < 0.0
                ):
                    side = "SHORT"
                else:
                    continue
                native_root = source_campaign_root_id(
                    source_identity=item.boundary.boundary_id,
                    source_generation=item.generation,
                    interaction_time_ns=attack_bar.open_time_ns,
                )
                by_side[side].setdefault(symbol, {})[native_root] = CommonSourceJoin(
                    symbol=symbol,
                    source_campaign_root_id=native_root,
                    source_boundary_id=item.boundary.boundary_id,
                    source_lower=lower,
                    source_upper=upper,
                    source_observed_time_ns=item.boundary.observed_time_ns,
                    join_time_ns=attack_bar.close_time_ns,
                )

        for side in ("LONG", "SHORT"):
            joins_by_symbol = by_side[side]
            if len(joins_by_symbol) < 3:
                continue
            participants = tuple(symbol for symbol in sorted(joins_by_symbol))
            inventory = {
                symbol: self._common_inventory_decision(
                    self.policies[symbol],
                    bars[symbol],
                    side,
                    bars[symbol].close_time_ns,
                )
                for symbol in participants
            }
            source_roots = {
                symbol: tuple(sorted(joins_by_symbol[symbol]))
                for symbol in participants
            }
            root_id = self._extensible_common_root(side)
            current_joins = {
                symbol: tuple(joins_by_symbol[symbol].values())
                for symbol in participants
            }
            if (
                root_id is not None
                and self._common_price_campaigns.fresh_attack_completes_prior_delivery(
                    root_id,
                    bars=bars,
                    source_joins=current_joins,
                )
            ):
                root_id = None
            if root_id is None:
                attack = self._common_episodes.register_attack(
                    attack_time_ns=next(iter(bars.values())).close_time_ns,
                    attack_side=side,
                    source_campaign_roots=source_roots,
                    attack_inventory=inventory,
                )
                root_id = attack.root_id
                canonical = {
                    symbol: min(
                        joins_by_symbol[symbol].values(),
                        key=lambda join: (
                            (
                                -join.source_upper
                                if side == "LONG"
                                else join.source_lower
                            ),
                            (
                                -join.source_lower
                                if side == "LONG"
                                else join.source_upper
                            ),
                            join.source_boundary_id,
                            join.source_campaign_root_id,
                        ),
                    )
                    for symbol in participants
                }
                registered = self._common_price_campaigns.register_attack(
                    attack_side=side,
                    bars=bars,
                    source_joins=canonical,
                    root_id=root_id,
                )
                if registered != root_id:
                    raise RuntimeError("price owner rejected a registered common attack")
            else:
                attack = self._common_episodes.extend_attack(
                    root_id,
                    source_campaign_roots=source_roots,
                    join_inventory=inventory,
                )

            frozen = self._common_attack_bars.setdefault(root_id, {})
            for symbol in participants:
                frozen.setdefault(symbol, bars[symbol])
                for join in joins_by_symbol[symbol].values():
                    self._common_price_campaigns.add_source_join(
                        root_id,
                        source_join=join,
                        bar=bars[symbol],
                    )

    def _extensible_common_root(
        self,
        side: str,
    ) -> str | None:
        """Return the latest open, unreclaimed root for this attack side."""

        candidates: list[tuple[int, str]] = []
        for root_id in self._common_price_campaigns.roots:
            if self._common_episodes.state(root_id) is not CommonEpisodeState.OPEN:
                continue
            snapshot = self._common_price_campaigns.snapshot(root_id)
            if (
                snapshot.attack_side == side
                and not snapshot.fully_reclaimed
                and not self._common_price_campaigns.continuation_delivered(
                    root_id,
                )
            ):
                candidates.append((snapshot.attack_time_ns, root_id))
        return None if not candidates else max(candidates)[1]

    def _common_price_plan(
        self,
        opportunity: CommonPriceOpportunity,
    ) -> TradePlan | None:
        """Bind shared price completion to its frozen inventory responsibility."""

        if opportunity.symbol not in self.decision_symbols:
            return None
        origin_root = str(
            opportunity.evidence["origin_source_campaign_root_id"],
        )
        authorization = self._common_episodes.authorize_candidate(
            opportunity.root_id,
            symbol=opportunity.symbol,
            family=CommonEpisodeFamily.CONTINUATION,
            side=opportunity.side,
            candidate_time_ns=opportunity.confirmation_time_ns,
            source_campaign_root_id=origin_root,
        )
        if authorization is None:
            return None
        source_id = str(opportunity.evidence["origin_source_boundary_id"])
        proposal_episode_id = stable_id(
            opportunity.root_id,
            opportunity.symbol,
            opportunity.opportunity_id,
            prefix="common-proposal-",
        )
        destination_id = stable_id(
            opportunity.root_id,
            opportunity.symbol,
            "FROZEN_ATTACK_EXTREME",
            prefix="common-objective-",
        )
        evidence: dict[str, object] = {
            **dict(opportunity.evidence),
            **dict(authorization.evidence),
            "native_episode_id": proposal_episode_id,
            "native_plan_id": opportunity.opportunity_id,
            "native_family": CommonEpisodeFamily.CONTINUATION.value,
            "native_scenario": "COMMON_ATTACK_PAUSE_PIVOT_TRANSFER",
            "causal_root_id": opportunity.root_id,
            "parent_campaign_id": opportunity.root_id,
            "source_ownership_role": SourceOwnershipRole.COMMON_MARKET_OWNER_ONLY.value,
            "route_owner": "COMMON_CASCADE",
            "route_responsibility": authorization.responsibility,
            "common_cascade_id": opportunity.root_id,
            "entry_lifecycle": ENTRY_LIFECYCLE_IMMEDIATE_RESPONSE,
            "entry_event": "COMMON_PAUSE_PIVOT_TRANSFER_CLOSE",
            "objective_lifecycle": "FAMILY_IMMUTABLE",
            "objective_commit_time_ns": opportunity.pause_time_ns,
            "interaction_time_ns": opportunity.attack_time_ns,
            "first_return_time_ns": opportunity.confirmation_time_ns,
            "physical_completion_time_ns": opportunity.confirmation_time_ns,
            "source_kind": "COMMON_STRUCTURAL_OUTSIDE_ATTACK",
            "destination_kind": "FROZEN_ATTACK_EXTREME",
        }
        return TradePlan(
            episode_id=proposal_episode_id,
            plan_id=stable_id(
                opportunity.root_id,
                opportunity.opportunity_id,
                authorization.authorization_id,
                prefix="common-control-plan-",
            ),
            symbol=opportunity.symbol,
            family=CommonEpisodeFamily.CONTINUATION.value,
            side=opportunity.side,
            decision_time_ns=opportunity.confirmation_time_ns,
            entry=opportunity.entry,
            stop=opportunity.stop,
            target=opportunity.target,
            expires_time_ns=MAX_CAUSAL_ORDER_TIME_NS,
            source_boundary_id=source_id,
            destination_boundary_id=destination_id,
            entry_zone=EntryZone(
                kind="COMMON_PAUSE_PIVOT_TRANSFER_CLOSE",
                lower=opportunity.entry_zone_lower,
                upper=opportunity.entry_zone_upper,
                observed_time_ns=opportunity.confirmation_time_ns,
                source_bar_open_time_ns=(
                    opportunity.confirmation_time_ns - NS_PER_MINUTE
                ),
            ),
            evidence=evidence,
        )

    def _observe_common_price(
        self,
        bars: Mapping[str, Bar],
    ) -> list[TradePlan]:
        """Advance every live shared price root and expose completed plans."""

        plans: list[TradePlan] = []
        decision_time_ns = next(iter(bars.values())).close_time_ns
        for root_id in self._common_price_campaigns.roots:
            if self._common_episodes.state(root_id) is not CommonEpisodeState.OPEN:
                continue
            snapshot = self._common_price_campaigns.snapshot(root_id)
            if snapshot.attack_time_ns >= decision_time_ns:
                continue
            opportunities = self._common_price_campaigns.observe(root_id, bars)
            updated = self._common_price_campaigns.snapshot(root_id)
            self._common_episodes.observe_price_failure(
                root_id,
                reclaim_time_ns=dict(updated.reclaim_time_ns),
                broad_failure_time_ns=updated.fully_reclaimed_time_ns,
                broad_failure_participants=updated.fully_reclaimed_participants,
            )
            for opportunity in opportunities:
                plan = self._common_price_plan(opportunity)
                if plan is not None:
                    plans.append(plan)
        return plans

    def _update_common_inventory(self, decision_time_ns: int) -> None:
        """Attach each newly observable official row to its frozen attack."""

        for root_id in self._common_episodes.roots:
            if self._common_episodes.state(root_id) is not CommonEpisodeState.OPEN:
                continue
            attack = self._common_episodes.attack(root_id)
            if decision_time_ns <= attack.attack_time_ns:
                continue
            frozen = self._common_attack_bars[root_id]
            decisions = {
                symbol: self._common_inventory_decision(
                    self.policies[symbol],
                    frozen[symbol],
                    attack.attack_side,
                    decision_time_ns,
                )
                for symbol in attack.participants
            }
            self._common_episodes.update_inventory(root_id, decisions)

    def claim(self, plan: TradePlan, *, time_ns: int | None = None) -> None:
        policy = self.policies.get(plan.symbol)
        if policy is None:
            raise ValueError(f"unknown plan symbol: {plan.symbol}")
        root_id = causal_root_id(plan)
        shared_root = plan.evidence.get(
            "common_root_id",
            plan.evidence.get("mapped_common_root_id"),
        )
        attack = None
        authorization_id: str | None = None
        policy.validate_claim(plan)
        if policy._claimed_plans.get(plan.episode_id) == plan.plan_id:
            return
        if isinstance(shared_root, str) and shared_root:
            attack = self._common_episodes.attack(shared_root)
            raw_authorization = plan.evidence.get("common_authorization_id")
            if isinstance(raw_authorization, str) and raw_authorization:
                authorization_id = raw_authorization
                if self._common_episodes.state(shared_root) is not CommonEpisodeState.OPEN:
                    raise CommonEpisodeError(
                        "authorized common proposal belongs to a terminal root",
                    )
                else:
                    self._common_episodes.validate_claim(authorization_id)

        # Both owners are validated before either mutates.  The coordinator is
        # single-threaded, so the following transitions cannot race between
        # validation and commit.
        policy.claim(plan, time_ns=time_ns)
        claim_time = plan.decision_time_ns if time_ns is None else time_ns
        related_roots = {root_id}
        if attack is not None:
            related_roots.add(attack.root_id)
            related_roots.update(
                source_root
                for _, source_root in attack.participant_source_roots
            )
            if self._common_episodes.state(attack.root_id) is CommonEpisodeState.OPEN:
                if authorization_id is not None:
                    self._common_episodes.claim(authorization_id)
                else:
                    self._common_episodes.invalidate(
                        attack.root_id,
                        reason="LOCAL_NATIVE_OWNER_CLAIMED_SHARED_ATTACK",
                    )
            self._common_attack_bars.pop(attack.root_id, None)
        for peer in self.policies.values():
            for related_root in related_roots:
                peer.suppress_claimed_root(
                    related_root,
                    plan.plan_id,
                    time_ns=claim_time,
                )

    def reject_proposal(
        self,
        plan: TradePlan,
        reason: str,
        *,
        time_ns: int | None = None,
    ) -> list[TradePlan]:
        """Reject one infeasible winner and immediately expose the next one."""

        policy = self.policies.get(plan.symbol)
        if policy is None:
            raise ValueError(f"unknown plan symbol: {plan.symbol}")
        policy.reject_proposal(plan, reason, time_ns=time_ns)
        remaining = [
            proposal
            for peer in self.policies.values()
            for proposal in peer._proposals.values()
            if proposal.episode_id not in peer._terminalized_episodes
        ]
        return self._arbitrate_current(remaining)

    def drain_decision_events(self) -> list[dict[str, object]]:
        """Collect deterministic policy event intents for durable append.

        Callers append every returned item with
        ``StateStore.append_event(**item)`` before saving a strategy snapshot.
        """

        output = [
            event
            for symbol in sorted(self.policies)
            for event in self.policies[symbol].drain_decision_events()
        ]
        output.sort(
            key=lambda item: (
                int(item["time_ns"]),
                0 if item["event_type"] == "POLICY_EPISODE_STARTED" else 1,
                str(item["event_key"]),
            ),
        )
        return output

    def export_state(self) -> dict[str, object]:
        return {
            "version": 3,
            "policies": {
                symbol: self.policies[symbol].export_state()
                for symbol in sorted(self.policies)
            },
            **self._common_state_payload(),
        }

    def export_runtime_state(self) -> dict[str, object]:
        return {
            "version": 3,
            "policies": {
                symbol: self.policies[symbol].export_runtime_state()
                for symbol in sorted(self.policies)
            },
            **self._common_state_payload(),
        }

    def _common_state_payload(self) -> dict[str, object]:
        return {
            "common_episode_ledger": self._common_episodes.export_state(),
            "common_price_campaigns": self._common_price_campaigns.export_state(),
            "common_attack_bars": {
                root_id: {
                    symbol: bar.to_dict()
                    for symbol, bar in sorted(frozen.items())
                }
                for root_id, frozen in sorted(self._common_attack_bars.items())
            },
        }

    def restore_decision_events(
        self,
        events: Sequence[Mapping[str, object]],
    ) -> None:
        """Rebuild the full decision ledger from durable semantic events."""

        starts: dict[str, dict[str, Mapping[str, object]]] = {
            symbol: {} for symbol in self.policies
        }
        terminals: dict[str, dict[str, Mapping[str, object]]] = {
            symbol: {} for symbol in self.policies
        }
        for event in events:
            event_type = event.get("event_type")
            if event_type not in {"POLICY_EPISODE_STARTED", "POLICY_EPISODE_TERMINAL"}:
                raise ValueError(f"unsupported policy decision event: {event_type!r}")
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                raise ValueError("policy decision event payload must be a mapping")
            symbol = payload.get("symbol")
            episode_id = payload.get("episode_id")
            if symbol not in self.policies or not isinstance(episode_id, str) or not episode_id:
                raise ValueError("policy decision event has invalid symbol or episode ID")
            expected_key = f"{event_type}:{episode_id}"
            event_key = event.get("event_key")
            if event_key is not None and event_key != expected_key:
                raise ValueError("policy decision semantic event key mismatch")
            destination = starts if event_type == "POLICY_EPISODE_STARTED" else terminals
            existing = destination[str(symbol)].get(episode_id)
            if existing is not None and dict(existing) != dict(payload):
                raise ValueError(f"conflicting policy decision event for {episode_id}")
            destination[str(symbol)][episode_id] = payload

        overlays = {
            symbol: self.policies[symbol]._decision_ledger_overlay_state(
                starts[symbol], terminals[symbol],
            )
            for symbol in sorted(self.policies)
        }
        # Validate the full four-symbol overlay before mutating live state.
        for symbol in sorted(self.policies):
            validator = SymbolEpisodePolicy(
                symbol, self.policies[symbol].tick_size, self.policies[symbol].config,
            )
            validator.restore_state(overlays[symbol])
        for symbol in sorted(self.policies):
            self.policies[symbol].restore_state(overlays[symbol])

    def restore_state(self, payload: Mapping[str, object]) -> None:
        if not isinstance(payload, Mapping):
            raise ValueError("coordinator state must be a mapping")
        version = payload.get("version")
        if version != 3:
            raise ValueError(f"unsupported coordinator state version: {payload.get('version')!r}")
        raw = payload.get("policies")
        if not isinstance(raw, Mapping) or set(raw) != set(self.policies):
            raise ValueError("coordinator state must contain exactly the four configured policies")
        restored_common_episodes: CommonEpisodeLedger | None = None
        restored_common_price: CommonPriceCampaignBook | None = None
        restored_attack_bars: dict[str, dict[str, Bar]] | None = None
        if version == 3:
            raw_episodes = payload.get("common_episode_ledger")
            raw_price = payload.get("common_price_campaigns")
            raw_attack_bars = payload.get("common_attack_bars")
            if (
                not isinstance(raw_episodes, Mapping)
                or not isinstance(raw_price, Mapping)
                or not isinstance(raw_attack_bars, Mapping)
            ):
                raise ValueError("coordinator common state is incomplete")
            restored_common_price = CommonPriceCampaignBook.restore_state(raw_price)
            restored_common_episodes = CommonEpisodeLedger.restore_state(raw_episodes)
            if set(restored_common_episodes.roots) != set(restored_common_price.roots):
                raise ValueError("common ledger and price roots differ")
            for root_id in restored_common_episodes.roots:
                price_snapshot = restored_common_price.snapshot(root_id)
                ledger_price_state = restored_common_episodes.price_failure_state(
                    root_id,
                )
                price_state = (
                    price_snapshot.reclaim_time_ns,
                    price_snapshot.fully_reclaimed_time_ns,
                    price_snapshot.fully_reclaimed_participants,
                )
                if ledger_price_state != price_state:
                    raise ValueError(
                        "common ledger and price reclaim states differ"
                    )
            restored_attack_bars = {}
            for root_id, raw_frozen in raw_attack_bars.items():
                if not isinstance(root_id, str) or not isinstance(raw_frozen, Mapping):
                    raise ValueError("common frozen attack bars are malformed")
                frozen = {
                    str(symbol): Bar.from_dict(raw_bar)
                    for symbol, raw_bar in raw_frozen.items()
                    if isinstance(raw_bar, Mapping)
                }
                if len(frozen) != len(raw_frozen):
                    raise ValueError("common frozen attack bar payload is malformed")
                attack = restored_common_episodes.attack(root_id)
                if set(frozen) != set(attack.participants) or any(
                    bar.symbol != symbol for symbol, bar in frozen.items()
                ):
                    raise ValueError("common frozen attack participants differ")
                if restored_common_episodes.state(root_id) is not CommonEpisodeState.OPEN:
                    raise ValueError("terminal common root retained frozen attack bars")
                restored_attack_bars[root_id] = frozen
            open_roots = {
                root_id
                for root_id in restored_common_episodes.roots
                if restored_common_episodes.state(root_id) is CommonEpisodeState.OPEN
            }
            if set(restored_attack_bars) != open_roots:
                raise ValueError("open common roots lack frozen attack bars")

        # Validate the whole payload before mutating any live policy.
        for symbol in sorted(self.policies):
            state = raw[symbol]
            if not isinstance(state, Mapping):
                raise ValueError(f"policy state for {symbol} must be a mapping")
            validator = SymbolEpisodePolicy(
                symbol, self.policies[symbol].tick_size, self.policies[symbol].config,
            )
            validator.restore_state(state)
            for episode_id, plan_id in validator._claimed_plans.items():
                existing = self.policies[symbol]._claimed_plans.get(episode_id)
                if existing is not None and existing != plan_id:
                    raise ValueError(f"conflicting claimed plan for episode {episode_id}")
        for symbol in sorted(self.policies):
            state = raw[symbol]
            assert isinstance(state, Mapping)
            self.policies[symbol].restore_state(state)
        if restored_common_episodes is not None:
            assert restored_common_price is not None
            assert restored_attack_bars is not None
            self._common_episodes = restored_common_episodes
            self._common_price_campaigns = restored_common_price
            self._common_attack_bars = restored_attack_bars

    def _one_minute_map(self) -> dict[str, Sequence[Bar]]:
        return {
            symbol: tuple(policy.market.one_minute)
            for symbol, policy in self.policies.items()
        }

    @classmethod
    def _arbitrate(cls, candidates: Sequence[TradePlan]) -> list[TradePlan]:
        return cls.arbitrate(candidates)

    def _arbitrate_current(
        self,
        candidates: Sequence[TradePlan],
    ) -> list[TradePlan]:
        """Arbitrate new and still-live causal-root proposals together."""

        pool = {
            plan.plan_id: plan
            for plan in (
                *candidates,
                *(
                    proposal
                    for policy in self.policies.values()
                    for proposal in policy._proposals.values()
                    if proposal.episode_id not in policy._terminalized_episodes
                ),
            )
        }
        return self.arbitrate(tuple(pool.values()))

    @classmethod
    def arbitrate(cls, candidates: Sequence[TradePlan]) -> list[TradePlan]:
        """Return the first physical causal-root owner for the one account."""

        owner = ControlEpisodeRouter.account_owner(candidates)
        return [] if owner is None else [owner]

    def push_five_minute_group(self, bars: Mapping[str, Bar]) -> list[TradePlan]:
        raise RuntimeError(
            "the unified policy requires synchronized completed one-minute "
            "bars through push_bar(); five-minute-only input cannot preserve "
            "physical interaction and first-return causality"
        )

    def push_bar(self, bar: Bar) -> list[TradePlan]:
        bucket = self._pending_by_close.setdefault(bar.close_time_ns, {})
        if bar.symbol in bucket:
            prior = bucket[bar.symbol]
            if prior != bar:
                raise RuntimeError(f"bar mutation for {bar.symbol} at {bar.close_time_ns}")
            return []
        bucket[bar.symbol] = bar
        if set(bucket) != set(self.policies):
            return []
        synchronized = self._pending_by_close.pop(bar.close_time_ns)
        prior_one_minute = self._one_minute_map()
        prebar = {
            symbol: self.policies[symbol].prepare_router_minute(
                synchronized[symbol],
                bars_by_symbol=prior_one_minute,
            )
            for symbol in sorted(synchronized)
        }
        self._register_common_attacks(synchronized, prebar)
        completed: dict[str, Bar] = {}
        for symbol in sorted(synchronized):
            five = self.policies[symbol].ingest_one_minute(synchronized[symbol])
            if five is not None:
                completed[symbol] = five
        if completed and set(completed) != set(self.policies):
            raise RuntimeError("four-market 5-minute clocks diverged")
        self._update_common_inventory(bar.close_time_ns)
        common_plans = self._observe_common_price(synchronized)
        one_minute = self._one_minute_map()
        candidates: list[TradePlan] = []
        for symbol in sorted(self.policies):
            candidates.extend(
                self.policies[symbol].evaluate_router_minute(
                    synchronized[symbol],
                    prebar[symbol],
                    decision_bar=completed.get(symbol),
                    bars_by_symbol=one_minute,
                    emit_plan=symbol in self.decision_symbols,
                    common_episodes=self._common_episodes,
                )
            )
        for plan in common_plans:
            candidates.extend(
                self.policies[plan.symbol]._refresh_router_proposals(
                    (plan,),
                    synchronized[plan.symbol],
                )
            )
        return self._arbitrate_current(candidates)


__all__ = [
    "EpisodeWatch",
    "LiquidityEpisodeCoordinator",
    "PolicyConfig",
    "SymbolEpisodePolicy",
]
