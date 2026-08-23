"""Persistent, generation-bound liquidity component graph.

The graph is deliberately policy-neutral.  It preserves the structural facts
which several research policies need in common:

* a source is identified by ``(source_id, generation)``;
* a child belongs to one exact generation of its parent component;
* replacing or retiring a parent retires its complete descendant subtree;
* objectives remain available until a structural event consumes or invalidates
  them -- elapsed wall-clock time is never a lifecycle event.

Target selection is causal and deterministic.  At the route-entry signal it
selects the nearest pre-existing fresh objective in the delivery direction.
A lower-scale obstacle is still an obstacle: it cannot be skipped to improve
RR by choosing a farther paired edge or scale-matched destination.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math
from typing import Any, Iterable, Mapping


class LiquidityGraphError(ValueError):
    """The observation or lifecycle transition violates graph invariants."""


class Lifecycle(str, Enum):
    FRESH = "FRESH"
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    INVALIDATED = "INVALIDATED"
    SUPERSEDED = "SUPERSEDED"

    @property
    def terminal(self) -> bool:
        return self in {Lifecycle.CONSUMED, Lifecycle.INVALIDATED, Lifecycle.SUPERSEDED}


class NodeRole(str, Enum):
    BALANCE = "BALANCE"
    SOURCE = "SOURCE"
    OBJECTIVE = "OBJECTIVE"


class LiquiditySide(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"

    @property
    def opposite(self) -> LiquiditySide:
        return LiquiditySide.LOW if self is LiquiditySide.HIGH else LiquiditySide.HIGH


class TargetRoute(str, Enum):
    REJECTION = "REJECTION"
    ACCEPTANCE = "ACCEPTANCE"


class TargetRelation(str, Enum):
    PAIRED_PARENT_OPPOSITE_EDGE = "PAIRED_PARENT_OPPOSITE_EDGE"
    SAME_OR_HIGHER_SCALE_OUTWARD = "SAME_OR_HIGHER_SCALE_OUTWARD"
    FIRST_OUTWARD_OBJECTIVE = "FIRST_OUTWARD_OBJECTIVE"


@dataclass(frozen=True, slots=True, order=True)
class SourceIdentity:
    """Exact identity of a source or component revision."""

    source_id: str
    generation: int

    def __post_init__(self) -> None:
        if not self.source_id:
            raise LiquidityGraphError("source_id cannot be empty")
        if self.generation < 1:
            raise LiquidityGraphError("generation must be positive")


@dataclass(frozen=True, slots=True, order=True)
class ParentIdentity:
    """Exact parent balance/component revision to which a child is bound."""

    component_id: str
    generation: int

    def __post_init__(self) -> None:
        if not self.component_id:
            raise LiquidityGraphError("parent component_id cannot be empty")
        if self.generation < 1:
            raise LiquidityGraphError("parent generation must be positive")

    @property
    def key(self) -> SourceIdentity:
        return SourceIdentity(self.component_id, self.generation)


def _finite(name: str, value: float) -> float:
    converted = float(value)
    if not math.isfinite(converted):
        raise LiquidityGraphError(f"{name} must be finite")
    return converted


@dataclass(frozen=True, slots=True)
class LiquidityNode:
    identity: SourceIdentity
    symbol: str
    role: NodeRole
    side: LiquiditySide
    scale_rank: int
    lower: float
    upper: float
    observed_time_ns: int
    parent: ParentIdentity | None = None
    paired_parent_edge: bool = False
    lifecycle: Lifecycle = Lifecycle.FRESH
    lifecycle_time_ns: int | None = None
    lifecycle_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise LiquidityGraphError("symbol cannot be empty")
        if self.scale_rank < 1:
            raise LiquidityGraphError("scale_rank must be positive")
        object.__setattr__(self, "lower", _finite("lower", self.lower))
        object.__setattr__(self, "upper", _finite("upper", self.upper))
        if self.lower >= self.upper:
            raise LiquidityGraphError("liquidity band must have positive width")
        if self.observed_time_ns < 0:
            raise LiquidityGraphError("observed_time_ns cannot be negative")
        if self.parent is not None and self.parent.key == self.identity:
            raise LiquidityGraphError("a node cannot parent itself")
        if self.role is not NodeRole.BALANCE and self.parent is None:
            raise LiquidityGraphError("source and objective nodes require an exact parent generation")
        if self.paired_parent_edge and self.role is not NodeRole.OBJECTIVE:
            raise LiquidityGraphError("only an objective can be a paired parent edge")
        if self.lifecycle is Lifecycle.FRESH:
            if self.lifecycle_time_ns is not None or self.lifecycle_reason is not None:
                raise LiquidityGraphError("a fresh node cannot have terminal lifecycle metadata")
        else:
            if self.lifecycle_time_ns is None or self.lifecycle_time_ns < self.observed_time_ns:
                raise LiquidityGraphError("non-fresh lifecycle requires a causal lifecycle time")
            if not self.lifecycle_reason:
                raise LiquidityGraphError("non-fresh lifecycle requires a reason")

    @property
    def source_id(self) -> str:
        return self.identity.source_id

    @property
    def generation(self) -> int:
        return self.identity.generation

    @property
    def live(self) -> bool:
        return not self.lifecycle.terminal


@dataclass(frozen=True, slots=True)
class TargetSelection:
    source: SourceIdentity
    target: SourceIdentity
    route: TargetRoute
    relation: TargetRelation
    decision_time_ns: int
    target_price: float


class LiquidityGraph:
    """A deterministic persistent graph with structural, non-TTL lifecycles."""

    SNAPSHOT_VERSION = 2

    def __init__(self, symbol: str) -> None:
        if not symbol:
            raise LiquidityGraphError("symbol cannot be empty")
        self.symbol = symbol
        self._nodes: dict[SourceIdentity, LiquidityNode] = {}
        self._children: dict[SourceIdentity, set[SourceIdentity]] = {}
        self._identities_by_source_id: dict[str, set[SourceIdentity]] = {}

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, identity: SourceIdentity) -> bool:
        return identity in self._nodes

    def node(self, identity: SourceIdentity) -> LiquidityNode:
        try:
            return self._nodes[identity]
        except KeyError as exc:
            raise LiquidityGraphError(f"unknown source identity: {identity}") from exc

    def nodes(self, *, lifecycle: Lifecycle | None = None) -> tuple[LiquidityNode, ...]:
        values = self._nodes.values()
        if lifecycle is not None:
            values = (node for node in values if node.lifecycle is lifecycle)
        return tuple(sorted(values, key=lambda node: node.identity))

    def children(self, identity: SourceIdentity, *, recursive: bool = False) -> tuple[LiquidityNode, ...]:
        self.node(identity)
        keys = self._descendant_keys(identity) if recursive else set(self._children.get(identity, ()))
        return tuple(self._nodes[key] for key in sorted(keys))

    def _descendant_keys(self, identity: SourceIdentity) -> set[SourceIdentity]:
        descendants: set[SourceIdentity] = set()
        pending = list(self._children.get(identity, ()))
        while pending:
            child = pending.pop()
            if child in descendants:
                continue
            descendants.add(child)
            pending.extend(self._children.get(child, ()))
        return descendants

    @staticmethod
    def _terminal_replacement(
        node: LiquidityNode,
        lifecycle: Lifecycle,
        time_ns: int,
        reason: str,
    ) -> LiquidityNode:
        if not lifecycle.terminal:
            raise LiquidityGraphError("retirement lifecycle must be terminal")
        last_event_time = node.lifecycle_time_ns or node.observed_time_ns
        if time_ns < max(node.observed_time_ns, last_event_time):
            raise LiquidityGraphError("lifecycle event cannot precede observation")
        if not reason:
            raise LiquidityGraphError("lifecycle reason cannot be empty")
        if node.lifecycle.terminal:
            return node
        return replace(
            node,
            lifecycle=lifecycle,
            lifecycle_time_ns=time_ns,
            lifecycle_reason=reason,
        )

    def observe(self, node: LiquidityNode) -> LiquidityNode:
        """Add one observed revision and atomically supersede older live revisions.

        Observation never competes for a singleton campaign slot: a new source
        remains in the graph even while unrelated sources are active.
        """

        if node.symbol != self.symbol:
            raise LiquidityGraphError("node symbol does not match graph")
        if node.lifecycle is not Lifecycle.FRESH:
            raise LiquidityGraphError("new observations must enter FRESH")
        existing_exact = self._nodes.get(node.identity)
        if existing_exact is not None:
            if existing_exact == node:
                return existing_exact
            raise LiquidityGraphError(f"conflicting duplicate observation: {node.identity}")

        same_id = [
            self._nodes[key]
            for key in self._identities_by_source_id.get(node.source_id, ())
        ]
        if same_id and node.generation <= max(item.generation for item in same_id):
            raise LiquidityGraphError("a new source generation must increase monotonically")

        parent_key: SourceIdentity | None = None
        if node.parent is not None:
            parent_key = node.parent.key
            parent = self._nodes.get(parent_key)
            if parent is None:
                raise LiquidityGraphError(f"exact parent generation is not observed: {node.parent}")
            if not parent.live:
                raise LiquidityGraphError("cannot bind a child to a retired parent generation")
            if parent.symbol != node.symbol:
                raise LiquidityGraphError("parent and child symbols differ")
            if node.observed_time_ns < parent.observed_time_ns:
                raise LiquidityGraphError("child observation cannot precede its exact parent generation")

        # Build every replacement before mutating dictionaries.  This keeps the
        # subtree transition atomic if validation above raises.
        replacements: dict[SourceIdentity, LiquidityNode] = {}
        superseded_roots = [item.identity for item in same_id if item.live]
        retire_keys: set[SourceIdentity] = set(superseded_roots)
        for root in superseded_roots:
            retire_keys.update(self._descendant_keys(root))
        if parent_key in retire_keys:
            raise LiquidityGraphError("new generation cannot bind to a parent retired by its own observation")
        for key in retire_keys:
            prior = self._nodes[key]
            replacements[key] = self._terminal_replacement(
                prior,
                Lifecycle.SUPERSEDED,
                node.observed_time_ns,
                f"SUPERSEDED_BY:{node.source_id}:{node.generation}",
            )

        self._nodes.update(replacements)
        self._nodes[node.identity] = node
        self._identities_by_source_id.setdefault(node.source_id, set()).add(node.identity)
        self._children.setdefault(node.identity, set())
        if parent_key is not None:
            self._children.setdefault(parent_key, set()).add(node.identity)
        return node

    def activate(self, identity: SourceIdentity, *, time_ns: int, reason: str) -> LiquidityNode:
        node = self.node(identity)
        if node.lifecycle is Lifecycle.ACTIVE:
            return node
        if node.lifecycle is not Lifecycle.FRESH:
            raise LiquidityGraphError("only a fresh node can become active")
        if time_ns < node.observed_time_ns or not reason:
            raise LiquidityGraphError("activation must be causal and have a reason")
        active = replace(
            node,
            lifecycle=Lifecycle.ACTIVE,
            lifecycle_time_ns=time_ns,
            lifecycle_reason=reason,
        )
        self._nodes[identity] = active
        return active

    def retire(
        self,
        identity: SourceIdentity,
        *,
        lifecycle: Lifecycle,
        time_ns: int,
        reason: str,
    ) -> tuple[LiquidityNode, ...]:
        """Atomically retire a node and every live descendant."""

        root = self.node(identity)
        if not lifecycle.terminal:
            raise LiquidityGraphError("retire requires a terminal lifecycle")
        keys = {identity, *self._descendant_keys(identity)}
        replacements: dict[SourceIdentity, LiquidityNode] = {}
        for key in keys:
            current = self._nodes[key]
            descendant_lifecycle = lifecycle if key == identity else (
                Lifecycle.SUPERSEDED if lifecycle is Lifecycle.SUPERSEDED else Lifecycle.INVALIDATED
            )
            descendant_reason = reason if key == identity else f"PARENT_RETIRED:{identity.source_id}:{identity.generation}:{reason}"
            replacements[key] = self._terminal_replacement(
                current,
                descendant_lifecycle,
                time_ns,
                descendant_reason,
            )
        self._nodes.update(replacements)
        return tuple(self._nodes[key] for key in sorted(keys))

    def invalidate(self, identity: SourceIdentity, *, time_ns: int, reason: str) -> tuple[LiquidityNode, ...]:
        return self.retire(
            identity,
            lifecycle=Lifecycle.INVALIDATED,
            time_ns=time_ns,
            reason=reason,
        )

    @staticmethod
    def _touched(node: LiquidityNode, *, low: float, high: float) -> bool:
        return high >= node.lower and low <= node.upper

    def consume_touch(
        self,
        identity: SourceIdentity,
        *,
        time_ns: int,
        low: float,
        high: float,
        reason: str = "ACTUAL_PRICE_TOUCH",
    ) -> tuple[LiquidityNode, ...]:
        """Consume a node only after an actual observed band intersection."""

        low = _finite("touch low", low)
        high = _finite("touch high", high)
        if low > high:
            raise LiquidityGraphError("touch low cannot exceed high")
        node = self.node(identity)
        if node.lifecycle.terminal:
            raise LiquidityGraphError("a retired node cannot be consumed again")
        if time_ns < node.observed_time_ns:
            raise LiquidityGraphError("touch cannot precede node observation")
        if not self._touched(node, low=low, high=high):
            raise LiquidityGraphError("reported touch does not intersect the liquidity band")
        return self.retire(
            identity,
            lifecycle=Lifecycle.CONSUMED,
            time_ns=time_ns,
            reason=reason,
        )

    @staticmethod
    def _is_outward(source: LiquidityNode, candidate: LiquidityNode, side: LiquiditySide) -> bool:
        if side is LiquiditySide.HIGH:
            return candidate.lower > source.upper
        return candidate.upper < source.lower

    @staticmethod
    def _outward_distance(source: LiquidityNode, candidate: LiquidityNode, side: LiquiditySide) -> float:
        if side is LiquiditySide.HIGH:
            return candidate.lower - source.upper
        return source.lower - candidate.upper

    def _fresh_objectives(
        self,
        *,
        decision_time_ns: int,
        side: LiquiditySide,
    ) -> Iterable[LiquidityNode]:
        return (
            node
            for node in self._nodes.values()
            if node.role is NodeRole.OBJECTIVE
            and node.side is side
            and node.lifecycle is Lifecycle.FRESH
            and node.observed_time_ns <= decision_time_ns
        )

    def select_target(
        self,
        source_identity: SourceIdentity,
        *,
        route: TargetRoute,
        decision_time_ns: int,
        reference_price: float | None = None,
    ) -> TargetSelection | None:
        """Select the first causal obstacle from source or signal price."""

        source = self.node(source_identity)
        if source.role is not NodeRole.SOURCE:
            raise LiquidityGraphError("target selection requires a SOURCE node")
        if source.lifecycle.terminal:
            return None
        source_state_time = source.lifecycle_time_ns or source.observed_time_ns
        if decision_time_ns < source_state_time:
            raise LiquidityGraphError("decision cannot precede source observation")
        if reference_price is not None:
            reference_price = _finite("reference_price", reference_price)

        target_side = source.side if route is TargetRoute.ACCEPTANCE else source.side.opposite
        objectives = list(self._fresh_objectives(decision_time_ns=decision_time_ns, side=target_side))
        boundary = (
            reference_price
            if reference_price is not None
            else (source.upper if target_side is LiquiditySide.HIGH else source.lower)
        )
        outward = [candidate for candidate in objectives if (
            candidate.lower > boundary
            if target_side is LiquiditySide.HIGH
            else candidate.upper < boundary
        )]
        if not outward:
            return None
        candidate = min(
            outward,
            key=lambda item: (
                item.lower - boundary
                if target_side is LiquiditySide.HIGH
                else boundary - item.upper,
                item.observed_time_ns,
                item.identity,
            ),
        )
        relation = (
            TargetRelation.PAIRED_PARENT_OPPOSITE_EDGE
            if route is TargetRoute.REJECTION
            and source.parent is not None
            and candidate.parent == source.parent
            and candidate.paired_parent_edge
            else TargetRelation.FIRST_OUTWARD_OBJECTIVE
        )
        return self._selection(
            source,
            candidate,
            route,
            relation,
            decision_time_ns,
        )

    @staticmethod
    def _selection(
        source: LiquidityNode,
        target: LiquidityNode,
        route: TargetRoute,
        relation: TargetRelation,
        decision_time_ns: int,
    ) -> TargetSelection:
        target_price = target.lower if target.side is LiquiditySide.HIGH else target.upper
        return TargetSelection(
            source=source.identity,
            target=target.identity,
            route=route,
            relation=relation,
            decision_time_ns=decision_time_ns,
            target_price=target_price,
        )

    def snapshot(self) -> dict[str, Any]:
        """Return a stable, JSON-serializable state representation."""

        return {
            "version": self.SNAPSHOT_VERSION,
            "symbol": self.symbol,
            "nodes": [self._node_snapshot(node) for node in self.nodes()],
        }

    @staticmethod
    def _node_snapshot(node: LiquidityNode) -> dict[str, Any]:
        return {
            "source_id": node.source_id,
            "generation": node.generation,
            "symbol": node.symbol,
            "role": node.role.value,
            "side": node.side.value,
            "scale_rank": node.scale_rank,
            "lower": node.lower,
            "upper": node.upper,
            "observed_time_ns": node.observed_time_ns,
            "parent_component_id": node.parent.component_id if node.parent else None,
            "parent_generation": node.parent.generation if node.parent else None,
            "paired_parent_edge": node.paired_parent_edge,
            "lifecycle": node.lifecycle.value,
            "lifecycle_time_ns": node.lifecycle_time_ns,
            "lifecycle_reason": node.lifecycle_reason,
        }

    @classmethod
    def restore(cls, snapshot: Mapping[str, Any]) -> LiquidityGraph:
        """Restore state without replaying lifecycle side effects."""

        if int(snapshot.get("version", -1)) != cls.SNAPSHOT_VERSION:
            raise LiquidityGraphError("unsupported liquidity graph snapshot version")
        graph = cls(str(snapshot["symbol"]))
        raw_nodes = snapshot.get("nodes")
        if not isinstance(raw_nodes, list):
            raise LiquidityGraphError("snapshot nodes must be a list")

        parsed: dict[SourceIdentity, LiquidityNode] = {}
        for raw in raw_nodes:
            if not isinstance(raw, Mapping):
                raise LiquidityGraphError("snapshot node must be a mapping")
            parent_id = raw.get("parent_component_id")
            parent_generation = raw.get("parent_generation")
            if (parent_id is None) != (parent_generation is None):
                raise LiquidityGraphError("snapshot parent identity is incomplete")
            parent = None if parent_id is None else ParentIdentity(str(parent_id), int(parent_generation))
            node = LiquidityNode(
                identity=SourceIdentity(str(raw["source_id"]), int(raw["generation"])),
                symbol=str(raw["symbol"]),
                role=NodeRole(str(raw["role"])),
                side=LiquiditySide(str(raw["side"])),
                scale_rank=int(raw["scale_rank"]),
                lower=float(raw["lower"]),
                upper=float(raw["upper"]),
                observed_time_ns=int(raw["observed_time_ns"]),
                parent=parent,
                paired_parent_edge=bool(raw.get("paired_parent_edge", False)),
                lifecycle=Lifecycle(str(raw["lifecycle"])),
                lifecycle_time_ns=None if raw.get("lifecycle_time_ns") is None else int(raw["lifecycle_time_ns"]),
                lifecycle_reason=None if raw.get("lifecycle_reason") is None else str(raw["lifecycle_reason"]),
            )
            if node.symbol != graph.symbol or node.identity in parsed:
                raise LiquidityGraphError("snapshot contains a duplicate or foreign-symbol node")
            parsed[node.identity] = node

        for node in parsed.values():
            if node.parent is None:
                continue
            parent = parsed.get(node.parent.key)
            if parent is None:
                raise LiquidityGraphError("snapshot child references a missing parent generation")
            if parent.symbol != node.symbol:
                raise LiquidityGraphError("snapshot parent and child symbols differ")
            if node.observed_time_ns < parent.observed_time_ns:
                raise LiquidityGraphError("snapshot child predates its exact parent generation")
            if parent.lifecycle.terminal and node.live:
                raise LiquidityGraphError("snapshot leaves a live child under a retired parent")

        graph._nodes = parsed
        graph._children = {key: set() for key in parsed}
        graph._identities_by_source_id = {}
        for node in parsed.values():
            graph._identities_by_source_id.setdefault(node.source_id, set()).add(node.identity)
            if node.parent is not None:
                graph._children[node.parent.key].add(node.identity)
        return graph


__all__ = [
    "Lifecycle",
    "LiquidityGraph",
    "LiquidityGraphError",
    "LiquidityNode",
    "LiquiditySide",
    "NodeRole",
    "ParentIdentity",
    "SourceIdentity",
    "TargetRelation",
    "TargetRoute",
    "TargetSelection",
]
