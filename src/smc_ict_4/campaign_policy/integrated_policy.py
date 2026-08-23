"""Integrated causal campaign policy for the four-symbol global account.

The policy is intentionally an orchestration layer.  Structural discovery,
attack genealogy, owner inference, and route construction retain their own
state and contracts; this module orders their observations on each completed
five-minute market clock and exposes only neutral order intents to execution.

Quantity and leverage do not belong here.  The execution adapter sizes the
immutable entry/stop geometry to three percent of current account NAV.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping

from smc_ict_4.episode_policy_live.domain import DEFAULT_CONTRACTS, Bar, stable_id
from smc_ict_4.episode_policy_live.neutral_policy import (
    ExecutionFeedback,
    IntentValidity,
    MARKET_SYMBOLS,
    MarketFrame,
    OrderIntent,
    PolicyOutput,
)

from .attack_ledger import CampaignPhase, EventKind, OwnerSide, SourceKey, SourceSide
from .latent_owner import (
    LatentOwnerFilter,
    OwnerDirection,
    OwnerIdentity,
    OwnerPhase,
    PosteriorView,
)
from .liquidity_graph import Lifecycle, LiquidityNode, SourceIdentity, TargetRoute
from .owner_observation import (
    CompletedMarketBar,
    OwnerObservationBuilder,
    SourceGeometry,
)
from .route_topology import (
    CompletedRouteBar,
    RouteEntrySignal,
    RouteOpportunity,
    SourceBand,
    SourceRouteTopology,
)
from .structural_stream import (
    StreamEventKind,
    StructuralLiquidityStream,
    StructuralStreamUpdate,
)


@dataclass(slots=True)
class CampaignRuntime:
    """Inference and route hypotheses owned by one exact source generation."""

    symbol: str
    source: LiquidityNode
    owner_filter: LatentOwnerFilter = field(default_factory=LatentOwnerFilter)
    routes: dict[tuple[int, OwnerDirection], SourceRouteTopology] = field(default_factory=dict)
    registered_attacks: set[int] = field(default_factory=set)
    terminal: bool = False

    @property
    def key(self) -> SourceKey:
        return SourceKey(self.source.source_id, self.source.generation)


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    """Replaceable decision boundary over an already structural opportunity.

    Route topology has already established release/defence and gross RR.  The
    temporary decision calculation therefore uses the mutually exclusive
    owner-vs-not-owner mixture once.  It deliberately does not multiply an
    additional phase probability into the same structural evidence.  Keeping
    this boundary explicit lets a later terminal/fill mixture replace it
    without changing market structure or execution APIs.
    """

    opportunity: RouteOpportunity
    symbol: str
    owner_probability: float
    expected_r: float
    phase_probability: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class _IntentBinding:
    symbol: str
    source_key: SourceKey
    attack_ordinal: int
    direction: OwnerDirection
    target_identity: SourceIdentity


class IntegratedCampaignPolicy:
    """One causal policy and one pending/position slot across four markets."""

    def __init__(self) -> None:
        self.streams = {
            symbol: StructuralLiquidityStream(
                symbol,
                float(DEFAULT_CONTRACTS[symbol].tick_size),
            )
            for symbol in MARKET_SYMBOLS
        }
        self.observations = OwnerObservationBuilder()
        self._runtimes: dict[tuple[str, SourceKey], CampaignRuntime] = {}
        self._active_runtime_keys: set[tuple[str, SourceKey]] = set()
        self._slot_intent_id: str | None = None
        self._slot_state: str | None = None
        self._cancel_requested = False
        self._bindings: dict[str, _IntentBinding] = {}
        self._contexts: dict[str, dict[str, Any]] = {}
        self._cancellation_diagnostics: list[dict[str, Any]] = []
        self._last_replay_opportunities: tuple[dict[str, Any], ...] = ()
        self._last_close_time_ns: int | None = None

    @property
    def global_slot_state(self) -> str | None:
        return self._slot_state

    @property
    def active_campaign_count(self) -> int:
        return len(self._active_runtime_keys)

    def on_market_frame(self, frame: MarketFrame) -> PolicyOutput:
        if frame.interval_minutes != 5:
            raise ValueError("integrated campaign policy requires completed 5m frames")
        if self._last_close_time_ns is not None and frame.close_time_ns <= self._last_close_time_ns:
            raise ValueError("completed market frames must be strictly increasing")
        self._last_close_time_ns = frame.close_time_ns

        updates = {
            symbol: self.streams[symbol].push(frame.bar(symbol))
            for symbol in MARKET_SYMBOLS
        }

        # A terminal fact observed at this close owns the clock.  It is applied
        # before a same-clock attack registration, inference update, or route.
        self._apply_terminal_events(updates)
        self._register_attack_events(updates)

        geometries = self._current_geometries()
        observations = self.observations.observe(
            {
                symbol: self._completed_market_bar(frame.bar(symbol), frame.close_time_ns)
                for symbol in MARKET_SYMBOLS
            },
            tuple(geometries.values()),
        )
        posteriors = self._update_owner_filters(observations)

        opportunities = self._advance_routes(frame, posteriors)
        evaluations = tuple(
            self._evaluate_opportunity(symbol, opportunity, posteriors[(symbol, opportunity.source_key)])
            for symbol, opportunity in opportunities
        )

        validity = self._pending_validity(frame)
        selected: CandidateEvaluation | None = None
        if self._slot_intent_id is None and not validity:
            actionable = tuple(item for item in evaluations if item.expected_r > 0.0)
            selected = max(actionable, key=self._candidate_order_key, default=None)

        self._last_replay_opportunities = self._diagnostic_records(
            evaluations,
            selected,
            slot_occupied=self._slot_intent_id is not None,
        )
        if selected is None:
            return PolicyOutput(validity=validity)

        intent = self._intent_from_evaluation(selected, frame.close_time_ns)
        self._slot_intent_id = intent.intent_id
        self._slot_state = "PENDING"
        self._cancel_requested = False
        return PolicyOutput(intents=(intent,), validity=validity)

    def on_execution_feedback(self, feedback: ExecutionFeedback) -> None:
        status = feedback.status.upper()
        if feedback.intent_id not in self._bindings:
            raise ValueError(f"feedback references unknown intent: {feedback.intent_id}")

        terminal = {
            "CANCELED",
            "CANCELLED",
            "REJECTED",
            "EXPIRED",
            "STOP_FILLED",
            "TARGET_FILLED",
            "CLOSED",
        }
        if status == "FILLED":
            if self._slot_intent_id != feedback.intent_id:
                raise ValueError("fill does not belong to the occupied global slot")
            self._slot_state = "POSITION"
            self._cancel_requested = False
        elif status == "SUBMITTED":
            if self._slot_intent_id != feedback.intent_id:
                raise ValueError("submission does not belong to the occupied global slot")
            self._slot_state = "PENDING"
        elif status in terminal:
            if self._slot_intent_id == feedback.intent_id:
                self._slot_intent_id = None
                self._slot_state = None
                self._cancel_requested = False

    def intent_replay_context(self, intent_id: str) -> Mapping[str, Any]:
        return dict(self._contexts.get(intent_id, {}))

    def drain_replay_opportunities(self) -> tuple[Mapping[str, Any], ...]:
        records = self._last_replay_opportunities
        self._last_replay_opportunities = ()
        return records

    def cancellation_diagnostics(self) -> tuple[Mapping[str, Any], ...]:
        """Research trace of structural pending-order invalidations."""

        return tuple(dict(item) for item in self._cancellation_diagnostics)

    def _apply_terminal_events(
        self,
        updates: Mapping[str, StructuralStreamUpdate],
    ) -> None:
        for symbol in MARKET_SYMBOLS:
            update = updates[symbol]
            for event in update.ledger_events:
                if event.kind is not EventKind.CAMPAIGN_TERMINATED:
                    continue
                runtime = self._runtimes.get((symbol, event.key))
                if runtime is not None:
                    self._terminate_runtime(runtime, event.time_ns, event.detail)

            consumed = {
                event.identity
                for event in update.events
                if event.kind is StreamEventKind.OBJECTIVE_CONSUMED
            }
            if not consumed:
                continue
            for runtime_key in tuple(self._active_runtime_keys):
                runtime_symbol, _ = runtime_key
                runtime = self._runtimes[runtime_key]
                if runtime_symbol != symbol or runtime.terminal:
                    continue
                campaign = self.streams[symbol].ledger.campaign(runtime.key)
                if campaign is None or not campaign.attacks:
                    continue
                latest_ordinal = campaign.attacks[-1].ordinal
                for (ordinal, direction), route in runtime.routes.items():
                    if ordinal != latest_ordinal:
                        continue
                    if (
                        route.target_identity is not None
                        and route.target_identity in consumed
                        and not route.terminal
                    ):
                        route.terminate(decision=update.time_ns, reason="TARGET_CONSUMED")
                # Target lifecycle belongs to the exact frozen route.  The
                # source-generation owner remains a valid hypothesis for a
                # later physical re-attack with a newly frozen objective.

    def _terminate_runtime(self, runtime: CampaignRuntime, time_ns: int, reason: str) -> None:
        if runtime.terminal:
            return
        for route in runtime.routes.values():
            route.terminate(decision=time_ns, reason=reason)
        for identity in tuple(runtime.owner_filter.posterior().identity_probability):
            runtime.owner_filter.mark_structurally_invalidated(identity, time_ns, reason)
        runtime.terminal = True
        self._active_runtime_keys.discard((runtime.symbol, runtime.key))

    def _register_attack_events(
        self,
        updates: Mapping[str, StructuralStreamUpdate],
    ) -> None:
        for symbol in MARKET_SYMBOLS:
            stream = self.streams[symbol]
            for event in updates[symbol].ledger_events:
                if event.kind not in {EventKind.CAMPAIGN_STARTED, EventKind.REATTACK_APPENDED}:
                    continue
                if event.attack_ordinal is None:
                    continue
                node = stream.graph.node(SourceIdentity(event.key.source_id, event.key.generation))
                runtime_key = (symbol, event.key)
                runtime = self._runtimes.get(runtime_key)
                if runtime is None:
                    runtime = CampaignRuntime(symbol=symbol, source=node)
                    self._runtimes[runtime_key] = runtime
                    self._active_runtime_keys.add(runtime_key)
                if runtime.terminal or event.attack_ordinal in runtime.registered_attacks:
                    continue
                runtime.owner_filter.register_competing_attack(
                    event.key.source_id,
                    event.key.generation,
                    event.time_ns,
                )
                runtime.registered_attacks.add(event.attack_ordinal)
                self._create_attack_routes(runtime, event.attack_ordinal, event.time_ns)

    def _create_attack_routes(
        self,
        runtime: CampaignRuntime,
        attack_ordinal: int,
        decision_time_ns: int,
    ) -> None:
        source = runtime.source
        source_band = SourceBand(
            runtime.key,
            SourceSide(source.side.value),
            source.lower,
            source.upper,
            float(DEFAULT_CONTRACTS[runtime.symbol].tick_size),
        )
        for direction in OwnerDirection:
            runtime.routes[(attack_ordinal, direction)] = SourceRouteTopology(
                source_band,
                attack_ordinal=attack_ordinal,
            )

    def _current_geometries(self) -> dict[OwnerIdentity, SourceGeometry]:
        result: dict[OwnerIdentity, SourceGeometry] = {}
        for runtime_key in sorted(self._active_runtime_keys):
            runtime = self._runtimes[runtime_key]
            if runtime.terminal:
                continue
            campaign = self.streams[runtime.symbol].ledger.campaign(runtime.key)
            if campaign is None or campaign.phase is CampaignPhase.TERMINAL or not campaign.attacks:
                continue
            ordinal = campaign.attacks[-1].ordinal
            attack_reference = campaign.attacks[-1].extreme
            for direction in OwnerDirection:
                route = runtime.routes.get((ordinal, direction))
                if route is None or route.terminal:
                    continue
                identity = OwnerIdentity(runtime.key.source_id, runtime.key.generation, direction)
                result[identity] = SourceGeometry(
                    identity=identity,
                    symbol=runtime.symbol,
                    direction=direction,
                    source_lower=runtime.source.lower,
                    source_upper=runtime.source.upper,
                    target_price=route.target,
                    attack_reference_price=attack_reference,
                )
        return result

    def _update_owner_filters(
        self,
        observations: Mapping[OwnerIdentity, Any],
    ) -> dict[tuple[str, SourceKey], PosteriorView]:
        result: dict[tuple[str, SourceKey], PosteriorView] = {}
        for runtime_key in tuple(self._active_runtime_keys):
            runtime = self._runtimes[runtime_key]
            if runtime.terminal:
                continue
            identities = runtime.owner_filter.posterior().identity_probability
            local = {identity: observations[identity] for identity in identities if identity in observations}
            if local:
                result[runtime_key] = runtime.owner_filter.update_competing(local)
            else:
                result[runtime_key] = runtime.owner_filter.posterior()
        return result

    def _advance_routes(
        self,
        frame: MarketFrame,
        posteriors: Mapping[tuple[str, SourceKey], PosteriorView],
    ) -> tuple[tuple[str, RouteOpportunity], ...]:
        found: list[tuple[str, RouteOpportunity]] = []
        for runtime_key in sorted(self._active_runtime_keys):
            runtime = self._runtimes[runtime_key]
            if runtime.terminal or runtime_key not in posteriors:
                continue
            campaign = self.streams[runtime.symbol].ledger.campaign(runtime.key)
            if campaign is None:
                continue
            raw = frame.bar(runtime.symbol)
            bar = CompletedRouteBar(
                raw.open_time_ns,
                raw.close_time_ns,
                raw.open,
                raw.high,
                raw.low,
                raw.close,
            )
            for (ordinal, direction), route in sorted(
                runtime.routes.items(),
                key=lambda item: (item[0][0], item[0][1].value),
            ):
                outputs = route.on_bar(
                    bar,
                    campaign=campaign,
                    owner=OwnerSide(direction.value),
                )
                for output in outputs:
                    if not isinstance(output, RouteEntrySignal):
                        continue
                    rejection = (
                        runtime.source.side.value == "HIGH"
                        and direction is OwnerDirection.SHORT
                    ) or (
                        runtime.source.side.value == "LOW"
                        and direction is OwnerDirection.LONG
                    )
                    target = self.streams[runtime.symbol].graph.select_target(
                        runtime.source.identity,
                        route=(
                            TargetRoute.REJECTION
                            if rejection
                            else TargetRoute.ACCEPTANCE
                        ),
                        decision_time_ns=output.decision,
                        reference_price=output.entry,
                    )
                    if target is None:
                        route.reject_target(
                            decision=output.decision,
                            reason="NO_FRESH_FIRST_OBJECTIVE",
                        )
                        continue
                    opportunity = route.bind_target(
                        output,
                        target_identity=target.target,
                        target=target.target_price,
                    )
                    if opportunity is not None:
                        found.append((runtime.symbol, opportunity))
        return tuple(found)

    @staticmethod
    def _evaluate_opportunity(
        symbol: str,
        opportunity: RouteOpportunity,
        posterior: PosteriorView,
    ) -> CandidateEvaluation:
        direction = OwnerDirection(opportunity.owner_side.value)
        identity = OwnerIdentity(
            opportunity.source_key.source_id,
            opportunity.source_key.generation,
            direction,
        )
        owner_probability = float(posterior.identity_probability.get(identity, 0.0))
        expected_r = owner_probability * opportunity.gross_rr - (1.0 - owner_probability)
        phase = posterior.phase_probability.get(identity, {})
        return CandidateEvaluation(
            opportunity=opportunity,
            symbol=symbol,
            owner_probability=owner_probability,
            expected_r=expected_r,
            phase_probability={item.value: float(value) for item, value in phase.items()},
        )

    @staticmethod
    def _candidate_order_key(item: CandidateEvaluation) -> tuple[float, str, str, int, str]:
        opportunity = item.opportunity
        return (
            item.expected_r,
            # Reversed only for numeric utility by max(); the remaining fields
            # are stable deterministic tie data, not preference/alpha scores.
            item.symbol,
            opportunity.source_key.source_id,
            opportunity.source_key.generation,
            opportunity.owner_side.value,
        )

    def _intent_from_evaluation(
        self,
        evaluation: CandidateEvaluation,
        decision_time_ns: int,
    ) -> OrderIntent:
        opportunity = evaluation.opportunity
        direction = OwnerDirection(opportunity.owner_side.value)
        intent_id = stable_id(
            evaluation.symbol,
            opportunity.source_key.source_id,
            opportunity.source_key.generation,
            opportunity.attack_ordinal,
            direction.value,
            decision_time_ns,
            opportunity.entry,
            opportunity.stop,
            opportunity.target,
            prefix="CAMPAIGN_INTENT:",
        )
        binding = _IntentBinding(
            symbol=evaluation.symbol,
            source_key=opportunity.source_key,
            attack_ordinal=opportunity.attack_ordinal,
            direction=direction,
            target_identity=opportunity.target_identity,
        )
        self._bindings[intent_id] = binding
        self._contexts[intent_id] = {
            "source_id": (
                f"{opportunity.source_key.source_id}:{opportunity.source_key.generation}:"
                f"{opportunity.attack_ordinal}"
            ),
            "owner": direction.value,
            "route": opportunity.mode.value,
            "evidence": self._evidence(evaluation),
        }
        return OrderIntent(
            intent_id=intent_id,
            symbol=evaluation.symbol,
            side=direction.value,
            decision_time_ns=decision_time_ns,
            entry=opportunity.entry,
            stop=opportunity.stop,
            target=opportunity.target,
            valid_until_ns=None,
        )

    def _pending_validity(self, frame: MarketFrame) -> tuple[IntentValidity, ...]:
        if (
            self._slot_intent_id is None
            or self._slot_state != "PENDING"
            or self._cancel_requested
        ):
            return ()
        binding = self._bindings[self._slot_intent_id]
        runtime = self._runtimes.get((binding.symbol, binding.source_key))
        reason: str | None = None
        if runtime is None or runtime.terminal:
            reason = "source_generation_terminal"
        else:
            source = self.streams[binding.symbol].graph.node(runtime.source.identity)
            target = self.streams[binding.symbol].graph.node(binding.target_identity)
            route = runtime.routes.get((binding.attack_ordinal, binding.direction))
            if source.lifecycle.terminal:
                reason = "source_generation_terminal"
            elif target.lifecycle is not Lifecycle.FRESH:
                reason = "target_consumed_or_retired"
            elif route is None or route.terminal:
                reason = "route_structurally_terminal"
            else:
                intent = self._intent_for_id(self._slot_intent_id)
                bar = frame.bar(binding.symbol)
                if intent.side == "LONG" and bar.low <= intent.stop:
                    reason = "structural_invalidation_touched"
                elif intent.side == "SHORT" and bar.high >= intent.stop:
                    reason = "structural_invalidation_touched"
        if reason is None:
            return ()
        self._cancel_requested = True
        self._cancellation_diagnostics.append(
            {
                "intent_id": self._slot_intent_id,
                "event_time_ns": frame.close_time_ns,
                "reason": reason,
                **dict(self._contexts[self._slot_intent_id]),
            }
        )
        return (IntentValidity(self._slot_intent_id, False, reason),)

    def _intent_for_id(self, intent_id: str) -> OrderIntent:
        context = self._contexts[intent_id]
        evidence = context["evidence"]
        return OrderIntent(
            intent_id=intent_id,
            symbol=str(evidence["symbol"]),
            side=str(context["owner"]),
            decision_time_ns=int(evidence["decision_time_ns"]),
            entry=float(evidence["entry"]),
            stop=float(evidence["stop"]),
            target=float(evidence["target"]),
        )

    def _diagnostic_records(
        self,
        evaluations: tuple[CandidateEvaluation, ...],
        selected: CandidateEvaluation | None,
        *,
        slot_occupied: bool,
    ) -> tuple[dict[str, Any], ...]:
        records: list[dict[str, Any]] = []
        for item in evaluations:
            is_selected = item is selected
            if is_selected:
                reason = "selected_max_positive_expected_r"
            elif slot_occupied:
                reason = "global_slot_occupied"
            elif item.expected_r <= 0.0:
                reason = "non_positive_expected_r"
            else:
                reason = "single_account_other_route_selected"
            opportunity = item.opportunity
            records.append(
                {
                    "decision_time_ns": opportunity.decision,
                    "symbol": item.symbol,
                    "side": opportunity.owner_side.value,
                    "source_id": (
                        f"{opportunity.source_key.source_id}:"
                        f"{opportunity.source_key.generation}:"
                        f"{opportunity.attack_ordinal}"
                    ),
                    "owner": opportunity.owner_side.value,
                    "route": opportunity.mode.value,
                    "reason": reason,
                    "entry": opportunity.entry,
                    "stop": opportunity.stop,
                    "target": opportunity.target,
                    "selected": is_selected,
                    "evidence": self._evidence(item),
                }
            )
        return tuple(records)

    @staticmethod
    def _evidence(item: CandidateEvaluation) -> dict[str, Any]:
        opportunity = item.opportunity
        return {
            "symbol": item.symbol,
            "decision_time_ns": opportunity.decision,
            "entry": opportunity.entry,
            "stop": opportunity.stop,
            "target": opportunity.target,
            "gross_rr": opportunity.gross_rr,
            "owner_probability": item.owner_probability,
            "expected_r": item.expected_r,
            "phase_probability": dict(item.phase_probability),
            "target_identity": (
                f"{opportunity.target_identity.source_id}:"
                f"{opportunity.target_identity.generation}"
            ),
            "zone_lower": opportunity.zone.lower,
            "zone_upper": opportunity.zone.upper,
            "zone_origin_time_ns": opportunity.zone.origin_time_ns,
            "fvg_lower": opportunity.zone.fvg_lower,
            "fvg_upper": opportunity.zone.fvg_upper,
            "entry_inside_zone": (
                opportunity.zone.lower <= opportunity.entry <= opportunity.zone.upper
            ),
        }

    @staticmethod
    def _completed_market_bar(bar: Bar, close_time_ns: int) -> CompletedMarketBar:
        # Binance kline close timestamps are commonly interval-end minus one
        # millisecond.  Owner observations require the logical five-minute
        # clock, so derive its open from the authoritative synchronized close.
        return CompletedMarketBar(
            symbol=bar.symbol,
            open_time_ns=bar.open_time_ns,
            close_time_ns=close_time_ns,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            quote_volume=bar.quote_volume,
            taker_buy_quote_volume=bar.taker_buy_quote_volume,
        )


def create_integrated_policy() -> IntegratedCampaignPolicy:
    """Factory used by the fast replay and execution-neutral adapters."""

    return IntegratedCampaignPolicy()


__all__ = [
    "CampaignRuntime",
    "CandidateEvaluation",
    "IntegratedCampaignPolicy",
    "create_integrated_policy",
]
