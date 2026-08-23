"""Causal structural stream for source-bound liquidity campaigns.

This module deliberately stops before ownership inference and order planning.
It turns completed five-minute bars into persistent balance, source, objective,
and attack-genealogy facts.  Pivots become observable only after their right
hand confirmation bars have closed; no lifecycle transition is driven by time.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import median
from typing import Any

from smc_ict_4.episode_policy_live.domain import Bar, Pivot, stable_id
from smc_ict_4.episode_policy_live.market_state import BarAggregator, PivotTracker

from .attack_ledger import (
    AttackLedger,
    AttackOutcome,
    CampaignPhase,
    SourceKey,
    SourceSide,
    SourceSpec,
    StructuralEvent,
)
from .liquidity_graph import (
    Lifecycle,
    LiquidityGraph,
    LiquidityNode,
    LiquiditySide,
    NodeRole,
    ParentIdentity,
    SourceIdentity,
)


NS_PER_MINUTE = 60_000_000_000
DAY_NS = 1_440 * NS_PER_MINUTE


class StreamEventKind(str, Enum):
    BALANCE_OBSERVED = "BALANCE_OBSERVED"
    SOURCE_OBSERVED = "SOURCE_OBSERVED"
    OBJECTIVE_OBSERVED = "OBJECTIVE_OBSERVED"
    OBJECTIVE_CONSUMED = "OBJECTIVE_CONSUMED"
    SOURCE_TOUCHED = "SOURCE_TOUCHED"
    SOURCE_DEPARTED = "SOURCE_DEPARTED"
    RESPONSE_COMPLETED = "RESPONSE_COMPLETED"


@dataclass(frozen=True, slots=True)
class StreamEvent:
    time_ns: int
    kind: StreamEventKind
    identity: SourceIdentity
    detail: str = ""


@dataclass(frozen=True, slots=True)
class StructuralStreamUpdate:
    time_ns: int
    new_sources: tuple[LiquidityNode, ...]
    new_objectives: tuple[LiquidityNode, ...]
    events: tuple[StreamEvent, ...]
    ledger_events: tuple[StructuralEvent, ...]

    @classmethod
    def empty(cls, time_ns: int) -> "StructuralStreamUpdate":
        return cls(time_ns, (), (), (), ())


@dataclass(slots=True)
class _ResponseState:
    attack_ordinal: int
    departed_time_ns: int | None = None


@dataclass(slots=True)
class _EqualPool:
    side: LiquiditySide
    parent: ParentIdentity
    anchor_pivot_id: str
    prices: list[float]
    last_event_time_ns: int
    source_generation: int = 0


class StructuralLiquidityStream:
    """Consume completed 5m bars and maintain causal structural state."""

    def __init__(self, symbol: str, tick_size: float) -> None:
        if not symbol:
            raise ValueError("symbol cannot be empty")
        if tick_size <= 0.0:
            raise ValueError("tick_size must be positive")
        self.symbol = symbol
        self.tick_size = float(tick_size)
        self.graph = LiquidityGraph(symbol)
        self.ledger = AttackLedger(symbol)

        self._agg_15 = BarAggregator(symbol, 5, 15)
        self._agg_60 = BarAggregator(symbol, 15, 60)
        self._pivot_5 = PivotTracker(symbol, 5, 2)
        self._pivot_15 = PivotTracker(symbol, 15, 2)
        self._pivot_60 = PivotTracker(symbol, 60, 2)
        self._five: list[Bar] = []
        self._fifteen: list[Bar] = []
        self._sixty: list[Bar] = []
        self._day_key: int | None = None
        self._day_bars: list[Bar] = []
        self._day_generation = 0
        self._sixty_generation = 0
        self._last_60_pivot: Pivot | None = None
        self._equal_pools: dict[tuple[ParentIdentity, LiquiditySide], list[_EqualPool]] = {}
        self._responses: dict[SourceKey, _ResponseState] = {}
        # Historical nodes remain immutable evidence.  Only these live
        # identities belong on the per-bar hot path; scanning every old pivot
        # would make a continuous replay quadratic in history length.
        self._live_balances: set[SourceIdentity] = set()
        self._live_sources: set[SourceIdentity] = set()
        self._fresh_objectives: set[SourceIdentity] = set()
        self._last_bar: Bar | None = None

    @property
    def graph_snapshot(self) -> dict[str, Any]:
        return self.graph.snapshot()

    @property
    def ledger_snapshot(self) -> dict[str, Any]:
        return self.ledger.export_state()

    def snapshot(self) -> dict[str, Any]:
        """Expose stable graph and ledger state for replay comparison."""
        return {"graph": self.graph_snapshot, "ledger": self.ledger_snapshot}

    def push(self, bar: Bar) -> StructuralStreamUpdate:
        if bar.symbol != self.symbol or bar.interval_minutes != 5:
            raise ValueError("bar does not match structural stream")
        if self._last_bar is not None:
            if bar.close_time_ns == self._last_bar.close_time_ns and bar == self._last_bar:
                return StructuralStreamUpdate.empty(bar.close_time_ns)
            if bar.close_time_ns <= self._last_bar.close_time_ns:
                raise RuntimeError("five-minute bar is out of order or mutated")

        self._last_bar = bar
        self._five.append(bar)
        new_sources: list[LiquidityNode] = []
        new_objectives: list[LiquidityNode] = []
        events: list[StreamEvent] = []
        ledger_events: list[StructuralEvent] = []

        self._update_prior_day(bar, new_sources, new_objectives, events, ledger_events)

        pivots_5 = self._pivot_5.push(bar)
        fifteen = self._agg_15.push(bar)
        pivots_15: list[Pivot] = []
        pivots_60: list[Pivot] = []
        if fifteen is not None:
            self._fifteen.append(fifteen)
            pivots_15 = self._pivot_15.push(fifteen)
            sixty = self._agg_60.push(fifteen)
            if sixty is not None:
                self._sixty.append(sixty)
                pivots_60 = self._pivot_60.push(sixty)

        # A confirmed 60m pair is a real balance-generation event.  Observe it
        # before binding pivots confirmed by this same completed input bar.
        for pivot in pivots_60:
            self._observe_60_pivot(pivot, new_sources, new_objectives, events, ledger_events)

        for pivot in (*pivots_5, *pivots_15):
            objective = self._observe_objective(pivot)
            if objective is not None:
                new_objectives.append(objective)
                events.append(StreamEvent(bar.close_time_ns, StreamEventKind.OBJECTIVE_OBSERVED, objective.identity, f"{pivot.timeframe_minutes}m"))
            if pivot.timeframe_minutes == 15:
                source = self._observe_equal_pool(pivot, events, ledger_events)
                if source is not None:
                    new_sources.append(source)

        # Objective/source observations at this close cannot be touched by the
        # bar which supplied their final confirmation evidence.
        self._consume_objective_touches(
            bar,
            events,
            newly_observed={node.identity for node in new_objectives},
        )
        self._complete_confirmed_responses(pivots_5, bar.close_time_ns, events, ledger_events)
        self._observe_source_touches_and_departures(bar, events, ledger_events)

        return StructuralStreamUpdate(
            time_ns=bar.close_time_ns,
            new_sources=tuple(new_sources),
            new_objectives=tuple(new_objectives),
            events=tuple(events),
            ledger_events=tuple(ledger_events),
        )

    def _update_prior_day(
        self,
        bar: Bar,
        sources: list[LiquidityNode],
        objectives: list[LiquidityNode],
        events: list[StreamEvent],
        ledger_events: list[StructuralEvent],
    ) -> None:
        key = bar.open_time_ns // DAY_NS
        if self._day_key is None:
            self._day_key = key
        if key == self._day_key:
            self._day_bars.append(bar)
            return
        if key < self._day_key:
            raise RuntimeError("five-minute day moved backwards")

        prior_key = self._day_key
        prior = self._day_bars
        self._day_key = key
        self._day_bars = [bar]
        complete = (
            key == prior_key + 1
            and len(prior) == 288
            and prior[0].open_time_ns == prior_key * DAY_NS
            and prior[-1].open_time_ns == (prior_key + 1) * DAY_NS - 5 * NS_PER_MINUTE
        )
        if not complete:
            return
        self._day_generation += 1
        self._observe_balance(
            balance_id=f"{self.symbol}:PRIOR_DAY_BALANCE",
            generation=self._day_generation,
            lower=min(item.low for item in prior),
            upper=max(item.high for item in prior),
            observed_time_ns=bar.open_time_ns,
            scale_rank=1_440,
            sources=sources,
            objectives=objectives,
            events=events,
            ledger_events=ledger_events,
        )

    def _observe_60_pivot(
        self,
        pivot: Pivot,
        sources: list[LiquidityNode],
        objectives: list[LiquidityNode],
        events: list[StreamEvent],
        ledger_events: list[StructuralEvent],
    ) -> None:
        prior = self._last_60_pivot
        if prior is None:
            self._last_60_pivot = pivot
            return
        if prior.side == pivot.side:
            if (pivot.side == "HIGH" and pivot.price > prior.price) or (pivot.side == "LOW" and pivot.price < prior.price):
                self._last_60_pivot = pivot
            return
        self._last_60_pivot = pivot
        lower, upper = sorted((prior.price, pivot.price))
        if upper - lower < self.tick_size:
            return
        self._sixty_generation += 1
        self._observe_balance(
            balance_id=f"{self.symbol}:CONFIRMED_60M_BALANCE",
            generation=self._sixty_generation,
            lower=lower,
            upper=upper,
            observed_time_ns=pivot.observed_time_ns,
            scale_rank=60,
            sources=sources,
            objectives=objectives,
            events=events,
            ledger_events=ledger_events,
        )

    def _observe_balance(
        self,
        *,
        balance_id: str,
        generation: int,
        lower: float,
        upper: float,
        observed_time_ns: int,
        scale_rank: int,
        sources: list[LiquidityNode],
        objectives: list[LiquidityNode],
        events: list[StreamEvent],
        ledger_events: list[StructuralEvent],
    ) -> None:
        if upper <= lower:
            return
        balance = LiquidityNode(
            identity=SourceIdentity(balance_id, generation),
            symbol=self.symbol,
            role=NodeRole.BALANCE,
            side=LiquiditySide.HIGH,
            scale_rank=scale_rank,
            lower=lower,
            upper=upper,
            observed_time_ns=observed_time_ns,
        )
        self.graph.observe(balance)
        self._live_balances.add(balance.identity)
        self._prune_live_indexes()
        ledger_events.extend(self.ledger.supersede_parent(balance_id, generation, observed_time_ns))
        events.append(StreamEvent(observed_time_ns, StreamEventKind.BALANCE_OBSERVED, balance.identity, f"{lower}:{upper}"))

        width = min(max(self.tick_size, (upper - lower) * 0.001), (upper - lower) * 0.25)
        parent = ParentIdentity(balance_id, generation)
        for side, band_lower, band_upper in (
            (LiquiditySide.HIGH, upper - width, upper),
            (LiquiditySide.LOW, lower, lower + width),
        ):
            source = LiquidityNode(
                identity=SourceIdentity(f"{balance_id}:{side.value}_EDGE", generation),
                symbol=self.symbol,
                role=NodeRole.SOURCE,
                side=side,
                scale_rank=scale_rank,
                lower=band_lower,
                upper=band_upper,
                observed_time_ns=observed_time_ns,
                parent=parent,
            )
            self.graph.observe(source)
            self._live_sources.add(source.identity)
            source_key = SourceKey(source.source_id, source.generation)
            ledger_events.extend(self.ledger.register_source(SourceSpec(
                key=source_key,
                side=SourceSide(source.side.value),
                tick_size=self.tick_size,
                observed_time_ns=observed_time_ns,
                parent_id=balance_id,
                parent_generation=generation,
            )))
            sources.append(source)
            events.append(StreamEvent(observed_time_ns, StreamEventKind.SOURCE_OBSERVED, source.identity, "BALANCE_EDGE"))

            objective = LiquidityNode(
                identity=SourceIdentity(f"{balance_id}:{side.value}_EDGE_OBJECTIVE", generation),
                symbol=self.symbol,
                role=NodeRole.OBJECTIVE,
                side=side,
                scale_rank=scale_rank,
                lower=band_lower,
                upper=band_upper,
                observed_time_ns=observed_time_ns,
                parent=parent,
                paired_parent_edge=True,
            )
            self.graph.observe(objective)
            self._fresh_objectives.add(objective.identity)
            objectives.append(objective)
            events.append(StreamEvent(
                observed_time_ns,
                StreamEventKind.OBJECTIVE_OBSERVED,
                objective.identity,
                "BALANCE_EDGE",
            ))

    def _live_enclosing_parent(self, price: float, child_scale: int) -> LiquidityNode | None:
        balances = [
            node
            for identity in sorted(self._live_balances)
            for node in (self.graph.node(identity),)
            if node.live
            and node.scale_rank > child_scale
            and node.lower <= price <= node.upper
        ]
        if not balances:
            return None
        return min(balances, key=lambda item: (item.upper - item.lower, item.scale_rank, -item.observed_time_ns, item.identity))

    def _observe_objective(self, pivot: Pivot) -> LiquidityNode | None:
        parent_node = self._live_enclosing_parent(pivot.price, pivot.timeframe_minutes)
        if parent_node is None:
            return None
        half = self._objective_half_width(pivot.timeframe_minutes)
        lower = max(parent_node.lower, pivot.price - half)
        upper = min(parent_node.upper, pivot.price + half)
        if upper <= lower:
            if pivot.price <= parent_node.lower:
                lower, upper = parent_node.lower, min(parent_node.upper, parent_node.lower + self.tick_size)
            else:
                lower, upper = max(parent_node.lower, parent_node.upper - self.tick_size), parent_node.upper
        node = LiquidityNode(
            identity=SourceIdentity(f"OBJECTIVE:{pivot.pivot_id}", 1),
            symbol=self.symbol,
            role=NodeRole.OBJECTIVE,
            side=LiquiditySide(pivot.side),
            scale_rank=pivot.timeframe_minutes,
            lower=lower,
            upper=upper,
            observed_time_ns=pivot.observed_time_ns,
            parent=ParentIdentity(parent_node.source_id, parent_node.generation),
        )
        self.graph.observe(node)
        self._fresh_objectives.add(node.identity)
        return node

    def _objective_half_width(self, timeframe: int) -> float:
        bars = self._fifteen if timeframe == 15 else self._five
        ranges = [item.range for item in bars[-20:] if item.range > 0.0]
        structural = (median(ranges) * 0.01) if ranges else 0.0
        return max(self.tick_size * 0.5, structural)

    def _observe_equal_pool(
        self,
        pivot: Pivot,
        events: list[StreamEvent],
        ledger_events: list[StructuralEvent],
    ) -> LiquidityNode | None:
        parent_node = self._live_enclosing_parent(pivot.price, 15)
        if parent_node is None:
            return None
        parent = ParentIdentity(parent_node.source_id, parent_node.generation)
        side = LiquiditySide(pivot.side)
        key = (parent, side)
        pools = self._equal_pools.setdefault(key, [])
        ranges = [item.range for item in self._fifteen[-20:] if item.range > 0.0]
        tolerance = max(self.tick_size, (median(ranges) * 0.05) if ranges else self.tick_size)
        pool = next((item for item in pools if abs(pivot.price - median(item.prices)) <= tolerance), None)
        if pool is None:
            pools.append(_EqualPool(side, parent, pivot.pivot_id, [pivot.price], pivot.event_time_ns))
            return None
        pool.prices.append(pivot.price)
        pool.last_event_time_ns = pivot.event_time_ns
        pool.source_generation += 1
        source_id = f"{self.symbol}:15M_EQUAL_POOL:{side.value}:{parent.component_id}:{parent.generation}:{pool.anchor_pivot_id}"
        prior_identity = (
            SourceIdentity(source_id, pool.source_generation - 1)
            if pool.source_generation > 1
            else None
        )
        if prior_identity is not None and prior_identity in self.graph:
            ledger_events.extend(self.ledger.source_invalidated(
                SourceKey(prior_identity.source_id, prior_identity.generation),
                time_ns=pivot.observed_time_ns,
            ))
        lower = max(parent_node.lower, min(pool.prices) - tolerance * 0.5)
        upper = min(parent_node.upper, max(pool.prices) + tolerance * 0.5)
        if upper <= lower:
            return None
        source = LiquidityNode(
            identity=SourceIdentity(source_id, pool.source_generation),
            symbol=self.symbol,
            role=NodeRole.SOURCE,
            side=side,
            scale_rank=15,
            lower=lower,
            upper=upper,
            observed_time_ns=pivot.observed_time_ns,
            parent=parent,
        )
        self.graph.observe(source)
        self._live_sources.add(source.identity)
        self._prune_live_indexes()
        ledger_events.extend(self.ledger.register_source(SourceSpec(
            key=SourceKey(source.source_id, source.generation),
            side=SourceSide(side.value),
            tick_size=self.tick_size,
            observed_time_ns=pivot.observed_time_ns,
            parent_id=parent.component_id,
            parent_generation=parent.generation,
        )))
        events.append(StreamEvent(pivot.observed_time_ns, StreamEventKind.SOURCE_OBSERVED, source.identity, "15M_EQUAL_POOL"))
        return source

    def _consume_objective_touches(
        self,
        bar: Bar,
        events: list[StreamEvent],
        *,
        newly_observed: set[SourceIdentity],
    ) -> None:
        for identity in sorted(tuple(self._fresh_objectives)):
            node = self.graph.node(identity)
            if node.lifecycle is not Lifecycle.FRESH:
                self._fresh_objectives.discard(identity)
                continue
            if node.identity in newly_observed:
                continue
            if node.observed_time_ns >= bar.close_time_ns:
                continue
            if bar.high >= node.lower and bar.low <= node.upper:
                self.graph.consume_touch(node.identity, time_ns=bar.close_time_ns, low=bar.low, high=bar.high)
                self._fresh_objectives.discard(node.identity)
                events.append(StreamEvent(bar.close_time_ns, StreamEventKind.OBJECTIVE_CONSUMED, node.identity, "ACTUAL_TOUCH"))

    def _complete_confirmed_responses(
        self,
        pivots: list[Pivot],
        time_ns: int,
        events: list[StreamEvent],
        ledger_events: list[StructuralEvent],
    ) -> None:
        for pivot in pivots:
            opposing_source_side = SourceSide.HIGH if pivot.side == "LOW" else SourceSide.LOW
            for key, state in list(self._responses.items()):
                if state.departed_time_ns is None:
                    continue
                campaign = self.ledger.campaign(key)
                if (
                    campaign is None
                    or campaign.phase is CampaignPhase.TERMINAL
                    or not campaign.attacks
                    or campaign.attacks[-1].ordinal != state.attack_ordinal
                    or campaign.attacks[-1].outcome is AttackOutcome.RESPONSE_COMPLETED
                ):
                    continue
                source_node = self.graph.node(SourceIdentity(key.source_id, key.generation))
                if source_node.lifecycle.terminal:
                    continue
                spec_side = SourceSide.HIGH if source_node.side is LiquiditySide.HIGH else SourceSide.LOW
                if spec_side is not opposing_source_side or pivot.event_time_ns <= state.departed_time_ns:
                    continue
                ledger_events.extend(self.ledger.observe_response(
                    key,
                    time_ns=time_ns,
                    response_extreme=pivot.price,
                    completed=True,
                    frozen_control=pivot.price,
                ))
                events.append(StreamEvent(time_ns, StreamEventKind.RESPONSE_COMPLETED, SourceIdentity(key.source_id, key.generation), pivot.pivot_id))

    def _observe_source_touches_and_departures(
        self,
        bar: Bar,
        events: list[StreamEvent],
        ledger_events: list[StructuralEvent],
    ) -> None:
        for identity in sorted(tuple(self._live_sources)):
            node = self.graph.node(identity)
            if node.lifecycle.terminal:
                self._live_sources.discard(identity)
                continue
            if node.observed_time_ns >= bar.close_time_ns:
                continue
            key = SourceKey(node.source_id, node.generation)
            touched = bar.high >= node.lower and bar.low <= node.upper
            if touched:
                before = self.ledger.campaign(key)
                extreme = bar.high if node.side is LiquiditySide.HIGH else bar.low
                additions = self.ledger.record_touch(
                    key,
                    time_ns=bar.close_time_ns,
                    extreme=extreme,
                    physical_attack_id=stable_id(
                        self.symbol,
                        node.source_id,
                        node.generation,
                        bar.open_time_ns,
                        node.side.value,
                        prefix="ATTACK:",
                    ),
                )
                ledger_events.extend(additions)
                after = self.ledger.campaign(key)
                if before is None:
                    self.graph.activate(node.identity, time_ns=bar.close_time_ns, reason="FIRST_APPROACH_TOUCH")
                if after is not None:
                    prior_state = self._responses.get(key)
                    if prior_state is None or prior_state.attack_ordinal != after.attacks[-1].ordinal:
                        self._responses[key] = _ResponseState(after.attacks[-1].ordinal)
                events.append(StreamEvent(bar.close_time_ns, StreamEventKind.SOURCE_TOUCHED, node.identity, "FIRST_OR_EXTENSION_OR_REATTACK"))

            campaign = self.ledger.campaign(key)
            if campaign is None or not campaign.attacks:
                continue
            state = self._responses.setdefault(key, _ResponseState(campaign.attacks[-1].ordinal))
            inward_departed = bar.close < node.lower if node.side is LiquiditySide.HIGH else bar.close > node.upper
            if state.departed_time_ns is None and inward_departed:
                state.departed_time_ns = bar.close_time_ns
                events.append(StreamEvent(bar.close_time_ns, StreamEventKind.SOURCE_DEPARTED, node.identity, "CLOSE_INSIDE_PARENT"))

    def _prune_live_indexes(self) -> None:
        self._live_balances = {
            identity for identity in self._live_balances if self.graph.node(identity).live
        }
        self._live_sources = {
            identity for identity in self._live_sources if self.graph.node(identity).live
        }
        self._fresh_objectives = {
            identity
            for identity in self._fresh_objectives
            if self.graph.node(identity).lifecycle is Lifecycle.FRESH
        }


__all__ = [
    "StreamEvent",
    "StreamEventKind",
    "StructuralLiquidityStream",
    "StructuralStreamUpdate",
]
