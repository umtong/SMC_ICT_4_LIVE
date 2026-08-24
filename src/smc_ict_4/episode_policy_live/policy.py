"""One streaming source-to-destination auction policy for replay and paper.

This is a production synthesis of mechanisms which already existed in the
research branches.  It is deliberately not described as a new ``Missing
Piece``:

* candidate 4t supplies local-minus-common, counterfactual ownership;
* structural-auction-control v5 supplies the event-time auction journey and
  one owner for one structure interaction;
* directional-liquidity-policy v2 supplies direction/liquidity context;
* EasyChart RE1 supplies accepted-break first response, complete-episode
  invalidation, its causal pivot-only 1m/5m/15m objective book, FVG-with-OB
  responsibility and latest-accepted-level ownership; and
* candidate 3b supplies delivery proof, retained here as completion evidence
  rather than a target cap or a separate entry family.

The executable law is one causal chain::

    pre-existing structural source
    -> completed auction ownership transfer
    -> one future first-return location
    -> complete-episode stop and first live horizontal day-trade objective

An FVG cannot originate an episode.  Control observations are not accumulated
into an age-dependent quality score.  A completed delivery proof cannot replace
or cap the first live structural destination.
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
from .cross_market_roles import EventPrice, analyze_cross_market_roles
from .directional_context import (
    boundary_role,
    build_active_liquidity_context,
    build_directional_context,
    build_directional_update,
)
from .domain import Bar, EntryZone, LiquidityBoundary, TradePlan, stable_id
from .inventory_ownership import InventoryInterpretation, InventoryTimeline
from .market_state import NS_PER_MINUTE, SymbolMarketState
from .structural_liquidity import (
    FeasibleTrendChannelBook,
    StructuralNode,
    StructureRole,
    destination_first_geometry,
    event_local_locations,
    structural_stop,
)

MAX_CAUSAL_ORDER_TIME_NS = (1 << 63) - 1
POLICY_DECISION_SCHEMA_VERSION = 1
POLICY_FINGERPRINT = "liquidity-episode-source-transfer-v3-local-delivery-reattack"


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


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    # Legacy fields stay accepted by deployed configuration files.  Only
    # min_history_5m is an admission precondition; the remaining thresholds and
    # lifetimes do not own decisions in the integrated law.
    min_history_5m: int = 72
    failed_confirmation_bars: int = 3
    accepted_lifetime_bars: int = 8
    initiative_lifetime_bars: int = 8
    order_lifetime_minutes: int = 45
    min_activity_ratio: float = 0.75
    min_control_score: float = 0.15
    max_source_distance_atr: float = 1.5


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
        existing = target.get(episode_id)
        if existing is not None and existing != payload:
            raise RuntimeError(f"conflicting {label} for episode {episode_id}")
        target[episode_id] = payload

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
        # EventTimeAuctionJourney owns the micro response; SymbolMarketState
        # continues to own completed 5m/15m/60m public structure.
        self.journey.observe(bar)
        five, _ = self.market.push_one_minute(bar)
        if five is not None:
            self._sync_structural_books()
        return five

    def ingest_five_minute(self, bar: Bar) -> Bar:
        self.market.push_five_minute(bar)
        self._sync_structural_books()
        return bar

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

    def evaluate_minute(
        self,
        bar: Bar,
        *,
        bars_by_symbol: Mapping[str, Sequence[Bar]] | None = None,
    ) -> list[TradePlan]:
        """Advance an existing 5m structural thesis on each synchronized minute.

        This is not a separate one-minute strategy.  The minute tape may only
        interact with already-observed public structure, and is used so the RE1
        first response is not discovered up to four minutes late.
        """

        if bar.interval_minutes != 1:
            raise ValueError("minute evaluation requires a completed one-minute bar")
        if len(self.market.five_minute) < self.config.min_history_5m:
            return []
        serial = self.market.serial_5m
        atr = max(self.market.atr(self.market.five_minute), self.tick_size)
        self._create_boundary_watches(bar, serial, atr, 0.0)
        plans = self._advance_watches(bar, serial, atr, 0.0, bars_by_symbol)
        return self._refresh_proposals(plans, bar)

    def evaluate_five_minute(
        self,
        bar: Bar,
        common_breadth: float,
        bars_by_symbol: Mapping[str, Sequence[Bar]] | None = None,
        *,
        interaction_bar: Bar | None = None,
    ) -> list[TradePlan]:
        if bar.interval_minutes != 5:
            raise ValueError("policy decisions require a completed 5-minute bar")
        decision_bar = interaction_bar or bar
        if interaction_bar is not None and (
            interaction_bar.symbol != bar.symbol
            or interaction_bar.interval_minutes != 1
            or interaction_bar.close_time_ns != bar.close_time_ns
        ):
            raise ValueError("fifth minute must close on the completed five-minute bar")
        serial = self.market.serial_5m
        atr = max(self.market.atr(self.market.five_minute), self.tick_size)
        if len(self.market.five_minute) >= self.config.min_history_5m:
            self._create_boundary_watches(
                decision_bar, serial, atr, common_breadth,
            )
        # A touched decision-bar level can establish the current interaction but
        # cannot remain available as its own future destination.
        self.market.boundary_book.mark_consumed(bar, serial)
        plans = self._advance_watches(
            decision_bar, serial, atr, common_breadth, bars_by_symbol,
        )
        return self._refresh_proposals(plans, decision_bar)

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
            ):
                reason = "ROUTE_CHANGED_BY_NEW_CLOSER_OBJECTIVE"
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
        consumed edge is still returned and can own a later fresh reattack, so
        it must not be confused with structural supersession.
        """

        return {
            node.node_id
            for node in self._projected_structural_nodes(decision_time_ns, serial)
        }

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

    def _has_new_closer_objective(
        self,
        *,
        side: str,
        entry: float,
        target: float,
        destination_boundary_id: str,
        source_boundary_id: str,
        decision_time_ns: int,
    ) -> bool:
        candidates = self.market.objective_book.destination_candidates(
            side=side,
            entry=entry,
            decision_time_ns=decision_time_ns,
            source_boundary_id=source_boundary_id,
        )
        if not candidates:
            return False
        nearest = candidates[0]
        if nearest.boundary_id == destination_boundary_id:
            return False
        planned_distance = abs(target - entry)
        nearest_distance = abs(
            self._objective_execution_price(side, nearest.price) - entry,
        )
        return nearest_distance < planned_distance - 0.5 * self.tick_size

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
    ) -> tuple[float, float, float, float, int, str]:
        # Semantic ownership precedes raw magnitude; RR remains feasibility.
        return (
            -float(plan.evidence.get("directional_posterior_support_rank", -1.0)),
            -float(plan.evidence.get("directional_family_transition_rank", -1.0)),
            -float(
                plan.evidence.get(
                    "absolute_delivery_per_risk",
                    plan.evidence.get("counterfactual_ownership_per_risk", 0.0),
                ),
            ),
            -float(plan.evidence.get("inventory_coherence_rank", 0.0)),
            -int(plan.evidence.get("source_observed_time_ns", 0)),
            plan.plan_id,
        )

    def claim(self, plan: TradePlan, *, time_ns: int | None = None) -> None:
        """Mark an episode used only after the shared account accepts it."""

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
            output.append((source, semantic_kind, key))
        return output

    def _create_boundary_watches(
        self, bar: Bar, serial: int, atr: float, breadth: float,
    ) -> None:
        del atr, breadth
        projected = self._projected_structural_nodes(bar.close_time_ns, serial)
        existing_projected_ids = {node.node_id for node in projected}
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
        # A completed response keeps its source campaign alive for a genuinely
        # fresh reattack.  The source therefore remains directional context even
        # after BoundaryBook records the original liquidity touch as consumed.
        for key, (source, _semantic_kind) in self._campaign_sources.items():
            campaign = self.attack_ledger.campaign(key)
            if (
                campaign is not None
                and campaign.phase is not CampaignPhase.TERMINAL
                and source.observed_time_ns <= bar.open_time_ns
            ):
                boundaries_by_id[source.boundary_id] = replace(
                    source,
                    consumed_time_ns=None,
                )
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

        def move(values: Sequence[Bar]) -> float | None:
            segment = [
                item for item in values
                if watch.interaction_time_ns < item.close_time_ns <= decision_bar.close_time_ns
            ]
            if not segment or segment[0].open <= 0.0:
                return None
            return log(segment[-1].close / segment[0].open)

        own_bars = (
            bars_by_symbol[self.symbol]
            if bars_by_symbol is not None and self.symbol in bars_by_symbol
            else tuple(self.market.one_minute)
        )
        own_raw = move(own_bars)
        if own_raw is None:
            return {
                "ownership_known": 0,
                "ownership_reason": "LOCAL_EVENT_MOVE_UNAVAILABLE",
            }
        peers = [
            value for symbol, bars in bars_by_symbol.items()
            if symbol != self.symbol and (value := move(bars)) is not None
        ] if bars_by_symbol is not None else []
        local = direction * own_raw
        output: dict[str, float | str | int] = {
            "ownership_known": 1,
            "event_local_progress": local,
            "ownership_reason": "ABSOLUTE_LOCAL_DELIVERY",
            "peer_context_known": int(bool(peers)),
        }
        if peers:
            common = direction * float(median(peers))
            residual = local - common
            output.update(
                ownership_reason="ABSOLUTE_LOCAL_DELIVERY_WITH_PEER_CONTEXT",
                event_common_progress=common,
                event_residual_ownership=residual,
                event_ownership_role=(
                    "LOCAL_LEADER"
                    if local > 0.0 and residual > 0.0
                    else "COMMON_FOLLOWER"
                    if local > 0.0 and common > 0.0
                    else "DIVIDED"
                ),
            )
        else:
            output["event_ownership_role"] = "LOCAL_ONLY"
        # Common-market and residual moves classify the delivery path; they do
        # not decide whether this symbol actually delivered in the intended
        # direction.  Admission is owned by ``event_local_progress`` below.
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
            self._claim_attack_owner(watch, bar)
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
            if local_progress <= 0.0:
                self._record_terminal(
                    "ABSOLUTE_DIRECTIONAL_DELIVERY_ABSENT",
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
            watch.evidence.update(self._inventory_evidence(watch, bar))
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

    def _route_nodes(
        self,
        watch: EpisodeWatch,
        decision_time_ns: int,
        serial: int,
        entry: float | None = None,
    ) -> list[StructuralNode]:
        wanted = "HIGH" if watch.side == "LONG" else "LOW"
        output: list[StructuralNode] = []
        objectives = (
            self.market.objective_book.active(
                decision_time_ns,
                source_boundary_id=watch.source.boundary_id,
            )
            if entry is None
            else self.market.objective_book.destination_candidates(
                side=watch.side,
                entry=entry,
                decision_time_ns=decision_time_ns,
                source_boundary_id=watch.source.boundary_id,
            )
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
        live_campaign_ids = {
            source.boundary_id
            for key, (source, _semantic_kind) in self._campaign_sources.items()
            for campaign in (self.attack_ledger.campaign(key),)
            if campaign is not None and campaign.phase is not CampaignPhase.TERMINAL
        }
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
                    # A completed-response campaign remains a live route fact
                    # until its structural version is actually superseded.
                    consumed_time_ns=(
                        None
                        if projected.node_id in live_campaign_ids
                        else projected.consumed_time_ns
                    ),
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
            target_touched = (
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
            target=route.target,
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
            # Compatibility name for persisted consumers.  Its value now
            # represents absolute local delivery, never peer residual spread.
            "counterfactual_ownership_per_risk": delivery_per_risk,
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
                "FIRST_LIVE_OPPOSING_HORIZONTAL_1M_SPAN6_OR_5M_15M_SPAN2_OBJECTIVE"
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

    def __init__(self, policies: Mapping[str, SymbolEpisodePolicy]) -> None:
        self.policies = dict(policies)
        if set(self.policies) != {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}:
            raise ValueError("coordinator requires the four configured markets")
        self._pending_by_close: dict[int, dict[str, Bar]] = {}

    def claim(self, plan: TradePlan, *, time_ns: int | None = None) -> None:
        policy = self.policies.get(plan.symbol)
        if policy is None:
            raise ValueError(f"unknown plan symbol: {plan.symbol}")
        cascade_key = policy._cascade_key(plan)
        policy.claim(plan, time_ns=time_ns)
        if cascade_key is not None:
            for peer in self.policies.values():
                peer.suppress_claimed_cascade(
                    cascade_key,
                    plan.episode_id,
                    time_ns=time_ns,
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
        return self.arbitrate(remaining)

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
            "version": 1,
            "policies": {
                symbol: self.policies[symbol].export_state()
                for symbol in sorted(self.policies)
            },
        }

    def export_runtime_state(self) -> dict[str, object]:
        return {
            "version": 2,
            "policies": {
                symbol: self.policies[symbol].export_runtime_state()
                for symbol in sorted(self.policies)
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
        if payload.get("version") not in {1, 2}:
            raise ValueError(f"unsupported coordinator state version: {payload.get('version')!r}")
        raw = payload.get("policies")
        if not isinstance(raw, Mapping) or set(raw) != set(self.policies):
            raise ValueError("coordinator state must contain exactly the four configured policies")
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

    def _peer_breadth(self, completed: Mapping[str, Bar]) -> dict[str, float]:
        signs: dict[str, float] = {}
        for symbol, five in completed.items():
            history = self.policies[symbol].market.five_minute
            if len(history) >= 2 and history[-2].close > 0.0:
                signs[symbol] = 1.0 if log(five.close / history[-2].close) > 0.0 else -1.0
        return {
            symbol: (
                sum(value for peer, value in signs.items() if peer != symbol)
                / max(sum(peer != symbol for peer in signs), 1)
            )
            for symbol in completed
        }

    def _one_minute_map(self) -> dict[str, Sequence[Bar]]:
        return {
            symbol: tuple(policy.market.one_minute)
            for symbol, policy in self.policies.items()
        }

    @staticmethod
    def _mark_common_cascades(candidates: Sequence[TradePlan]) -> None:
        groups: dict[tuple[int, str], list[TradePlan]] = {}
        for plan in candidates:
            if plan.evidence.get("cross_market_ownership_mode") != "COMMON_CASCADE":
                continue
            interaction = int(plan.evidence.get("interaction_time_ns", plan.decision_time_ns))
            groups.setdefault((interaction // (5 * NS_PER_MINUTE), plan.side), []).append(plan)
        for (bucket, side), group in groups.items():
            if len({item.symbol for item in group}) < 2:
                continue
            cascade_id = stable_id(bucket, side, "COMMON_MARKET_CASCADE", prefix="CASCADE:")
            for plan in group:
                if isinstance(plan.evidence, dict):
                    plan.evidence["cascade_id"] = cascade_id

    @classmethod
    def _arbitrate(cls, candidates: Sequence[TradePlan]) -> list[TradePlan]:
        return cls.arbitrate(candidates)

    @classmethod
    def arbitrate(cls, candidates: Sequence[TradePlan]) -> list[TradePlan]:
        """Return one eligible account owner, or no owner.

        A completed event with explicitly non-positive absolute local progress
        is a NULL proposal.  Missing evidence remains compatible with focused
        synthetic coordinators; production plans always carry the field.
        """

        eligible = [
            plan
            for plan in candidates
            if not isinstance(
                plan.evidence.get("event_local_progress"),
                (float, int),
            )
            or float(plan.evidence["event_local_progress"]) > 0.0
        ]
        if not eligible:
            return []
        cls._mark_common_cascades(eligible)
        return [min(eligible, key=SymbolEpisodePolicy._arbitration_key)]

    def push_five_minute_group(self, bars: Mapping[str, Bar]) -> list[TradePlan]:
        if set(bars) != set(self.policies):
            raise ValueError("a synchronized five-minute group must contain all four markets")
        if len({item.close_time_ns for item in bars.values()}) != 1:
            raise ValueError("five-minute group clocks differ")
        completed = {
            symbol: self.policies[symbol].ingest_five_minute(bars[symbol])
            for symbol in sorted(bars)
        }
        breadth = self._peer_breadth(completed)
        candidates: list[TradePlan] = []
        for symbol in sorted(completed):
            candidates.extend(
                self.policies[symbol].evaluate_five_minute(
                    completed[symbol], breadth[symbol], self._one_minute_map(),
                )
            )
        return self.arbitrate(candidates)

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
        completed: dict[str, Bar] = {}
        for symbol in sorted(synchronized):
            five = self.policies[symbol].ingest_one_minute(synchronized[symbol])
            if five is not None:
                completed[symbol] = five
        if completed and set(completed) != set(self.policies):
            raise RuntimeError("four-market 5-minute clocks diverged")
        one_minute = self._one_minute_map()
        candidates: list[TradePlan] = []
        if completed:
            breadth = self._peer_breadth(completed)
            for symbol in sorted(completed):
                candidates.extend(
                    self.policies[symbol].evaluate_five_minute(
                        completed[symbol],
                        breadth[symbol],
                        one_minute,
                        interaction_bar=synchronized[symbol],
                    )
                )
        else:
            for symbol in sorted(synchronized):
                candidates.extend(
                    self.policies[symbol].evaluate_minute(
                        synchronized[symbol], bars_by_symbol=one_minute,
                    )
                )
        return self.arbitrate(candidates)


__all__ = [
    "EpisodeWatch",
    "LiquidityEpisodeCoordinator",
    "PolicyConfig",
    "SymbolEpisodePolicy",
]
