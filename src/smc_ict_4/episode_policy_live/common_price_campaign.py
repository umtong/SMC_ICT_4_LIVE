"""Shared price ownership for one causal common-market attack wave.

The book owns price causality only: a synchronized broad attack, its first
opposite-control pause, causal pause pivots, and a later continuation through
those pivots.  Inventory responsibility and failed-cascade reversal geometry
belong to separate policies.  No elapsed-time rule, fitted score, or movement
threshold is used here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Mapping

from .domain import Bar, DEFAULT_CONTRACTS, SYMBOLS, stable_id


class CommonPriceCampaignError(ValueError):
    """A shared price campaign observation violates its causal contract."""


class CommonPricePhase(str, Enum):
    ATTACKING = "ATTACKING"
    PAUSE = "PAUSE"


@dataclass(frozen=True, slots=True)
class CommonSourceJoin:
    """One native source attached to a shared attack genealogy."""

    symbol: str
    source_campaign_root_id: str
    source_boundary_id: str
    source_lower: float
    source_upper: float
    source_observed_time_ns: int
    join_time_ns: int

    def __post_init__(self) -> None:
        if self.symbol not in SYMBOLS:
            raise CommonPriceCampaignError(f"unsupported symbol: {self.symbol}")
        if not self.source_campaign_root_id.strip() or not self.source_boundary_id.strip():
            raise CommonPriceCampaignError("source identities must be non-empty")
        if not math.isfinite(self.source_lower) or not math.isfinite(self.source_upper):
            raise CommonPriceCampaignError("source band must be finite")
        if self.source_lower >= self.source_upper:
            raise CommonPriceCampaignError("source band must have positive width")
        if self.source_observed_time_ns > self.join_time_ns:
            raise CommonPriceCampaignError("a source cannot be joined before observation")


@dataclass(frozen=True, slots=True)
class PausePivot:
    """A five-bar pause fractal observable at ``observed_time_ns``."""

    pivot_id: str
    root_id: str
    symbol: str
    side: str
    price: float
    event_time_ns: int
    observed_time_ns: int

    def __post_init__(self) -> None:
        if self.side not in {"HIGH", "LOW"}:
            raise CommonPriceCampaignError("pause pivot side must be HIGH or LOW")
        if self.observed_time_ns <= self.event_time_ns:
            raise CommonPriceCampaignError("pause pivot needs two following completed bars")
        if not math.isfinite(self.price):
            raise CommonPriceCampaignError("pause pivot price must be finite")


@dataclass(frozen=True, slots=True)
class CommonPriceOpportunity:
    """Immediate continuation geometry owned by one shared common root."""

    opportunity_id: str
    root_id: str
    symbol: str
    side: str
    attack_time_ns: int
    pause_time_ns: int
    confirmation_time_ns: int
    entry: float
    stop: float
    target: float
    entry_zone_lower: float
    entry_zone_upper: float
    broken_pivot: PausePivot
    stop_pivot: PausePivot
    attack_extreme: float
    confirmation_participants: tuple[str, ...]
    evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.side not in {"LONG", "SHORT"}:
            raise CommonPriceCampaignError("opportunity side must be LONG or SHORT")
        if self.confirmation_time_ns <= self.pause_time_ns:
            raise CommonPriceCampaignError("continuation must follow the pause")
        if not self.entry_zone_lower < self.entry < self.entry_zone_upper:
            raise CommonPriceCampaignError("entry zone must straddle the entry")
        valid = (
            self.stop < self.entry < self.target
            if self.side == "LONG"
            else self.target < self.entry < self.stop
        )
        if not valid:
            raise CommonPriceCampaignError("invalid continuation geometry")
        if self.gross_rr < 1.0 - 1e-12:
            raise CommonPriceCampaignError("gross planned RR must be at least one")

    @property
    def gross_rr(self) -> float:
        return abs(self.target - self.entry) / abs(self.entry - self.stop)


@dataclass(frozen=True, slots=True)
class CommonPriceSnapshot:
    root_id: str
    attack_side: str
    attack_time_ns: int
    phase: CommonPricePhase
    pause_time_ns: int | None
    participants: tuple[str, ...]
    attack_extremes: tuple[tuple[str, float], ...]
    origin_joins: tuple[CommonSourceJoin, ...]
    source_joins: tuple[CommonSourceJoin, ...]
    latest_pivots: tuple[PausePivot, ...]
    fully_reclaimed: bool
    fully_reclaimed_time_ns: int | None
    fully_reclaimed_participants: tuple[str, ...]


@dataclass(slots=True)
class _CommonPriceRoot:
    root_id: str
    attack_side: str
    attack_time_ns: int
    phase: CommonPricePhase
    last_time_ns: int
    participants: list[str]
    origin_joins: dict[str, CommonSourceJoin]
    joins: dict[str, list[CommonSourceJoin]]
    attack_extremes: dict[str, float]
    attack_extreme_times: dict[str, int]
    pause_time_ns: int | None = None
    pause_bars: dict[str, list[Bar]] = field(default_factory=dict)
    latest_pivots: dict[tuple[str, str], PausePivot] = field(default_factory=dict)
    consumed_pivot_ids: set[str] = field(default_factory=set)
    observed_transfer_ids: set[str] = field(default_factory=set)
    targets: dict[str, float] = field(default_factory=dict)
    target_touched: set[str] = field(default_factory=set)
    target_ineligible: set[str] = field(default_factory=set)
    fully_reclaimed: bool = False
    fully_reclaimed_time_ns: int | None = None
    fully_reclaimed_participants: tuple[str, ...] = ()


def _normalize_side(value: str) -> str:
    side = value.upper()
    if side not in {"LONG", "SHORT"}:
        raise CommonPriceCampaignError("attack side must be LONG or SHORT")
    return side


def _opposite(side: str) -> str:
    return "SHORT" if side == "LONG" else "LONG"


def _supports(bar: Bar, side: str) -> bool:
    return (
        bar.body > 0.0 and bar.signed_quote_flow > 0.0
        if side == "LONG"
        else bar.body < 0.0 and bar.signed_quote_flow < 0.0
    )


def _join_to_payload(value: CommonSourceJoin) -> dict[str, object]:
    return {
        "symbol": value.symbol,
        "source_campaign_root_id": value.source_campaign_root_id,
        "source_boundary_id": value.source_boundary_id,
        "source_lower": value.source_lower,
        "source_upper": value.source_upper,
        "source_observed_time_ns": value.source_observed_time_ns,
        "join_time_ns": value.join_time_ns,
    }


def _join_from_payload(raw: Mapping[str, Any]) -> CommonSourceJoin:
    return CommonSourceJoin(
        symbol=str(raw["symbol"]),
        source_campaign_root_id=str(raw["source_campaign_root_id"]),
        source_boundary_id=str(raw["source_boundary_id"]),
        source_lower=float(raw["source_lower"]),
        source_upper=float(raw["source_upper"]),
        source_observed_time_ns=int(raw["source_observed_time_ns"]),
        join_time_ns=int(raw["join_time_ns"]),
    )


def _pivot_to_payload(value: PausePivot) -> dict[str, object]:
    return {
        "pivot_id": value.pivot_id,
        "root_id": value.root_id,
        "symbol": value.symbol,
        "side": value.side,
        "price": value.price,
        "event_time_ns": value.event_time_ns,
        "observed_time_ns": value.observed_time_ns,
    }


def _pivot_from_payload(raw: Mapping[str, Any]) -> PausePivot:
    return PausePivot(
        pivot_id=str(raw["pivot_id"]),
        root_id=str(raw["root_id"]),
        symbol=str(raw["symbol"]),
        side=str(raw["side"]),
        price=float(raw["price"]),
        event_time_ns=int(raw["event_time_ns"]),
        observed_time_ns=int(raw["observed_time_ns"]),
    )


def _bar_from_payload(raw: Mapping[str, Any]) -> Bar:
    return Bar(
        symbol=str(raw["symbol"]),
        interval_minutes=int(raw["interval_minutes"]),
        open_time_ns=int(raw["open_time_ns"]),
        close_time_ns=int(raw["close_time_ns"]),
        open=float(raw["open"]),
        high=float(raw["high"]),
        low=float(raw["low"]),
        close=float(raw["close"]),
        volume=float(raw["volume"]),
        quote_volume=float(raw["quote_volume"]),
        taker_buy_quote_volume=float(raw["taker_buy_quote_volume"]),
        trade_count=int(raw.get("trade_count", 0)),
    )


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CommonPriceCampaignError("checkpoint contains non-finite evidence")
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise CommonPriceCampaignError("checkpoint evidence is not JSON-compatible")


def _opportunity_to_payload(value: CommonPriceOpportunity) -> dict[str, object]:
    return {
        "opportunity_id": value.opportunity_id,
        "root_id": value.root_id,
        "symbol": value.symbol,
        "side": value.side,
        "attack_time_ns": value.attack_time_ns,
        "pause_time_ns": value.pause_time_ns,
        "confirmation_time_ns": value.confirmation_time_ns,
        "entry": value.entry,
        "stop": value.stop,
        "target": value.target,
        "entry_zone_lower": value.entry_zone_lower,
        "entry_zone_upper": value.entry_zone_upper,
        "broken_pivot": _pivot_to_payload(value.broken_pivot),
        "stop_pivot": _pivot_to_payload(value.stop_pivot),
        "attack_extreme": value.attack_extreme,
        "confirmation_participants": list(value.confirmation_participants),
        "evidence": _json_value(value.evidence),
    }


def _opportunity_from_payload(raw: Mapping[str, Any]) -> CommonPriceOpportunity:
    evidence = _json_value(raw["evidence"])
    if not isinstance(evidence, Mapping):
        raise CommonPriceCampaignError("opportunity evidence must be a mapping")
    return CommonPriceOpportunity(
        opportunity_id=str(raw["opportunity_id"]),
        root_id=str(raw["root_id"]),
        symbol=str(raw["symbol"]),
        side=_normalize_side(str(raw["side"])),
        attack_time_ns=int(raw["attack_time_ns"]),
        pause_time_ns=int(raw["pause_time_ns"]),
        confirmation_time_ns=int(raw["confirmation_time_ns"]),
        entry=float(raw["entry"]),
        stop=float(raw["stop"]),
        target=float(raw["target"]),
        entry_zone_lower=float(raw["entry_zone_lower"]),
        entry_zone_upper=float(raw["entry_zone_upper"]),
        broken_pivot=_pivot_from_payload(raw["broken_pivot"]),
        stop_pivot=_pivot_from_payload(raw["stop_pivot"]),
        attack_extreme=float(raw["attack_extreme"]),
        confirmation_participants=tuple(str(item) for item in raw["confirmation_participants"]),
        evidence=evidence,
    )


class CommonPriceCampaignBook:
    """Own synchronized common-attack price state and continuation geometry."""

    STATE_VERSION = 1

    def __init__(self, *, tick_sizes: Mapping[str, float] | None = None) -> None:
        supplied = tick_sizes or {
            symbol: float(DEFAULT_CONTRACTS[symbol].tick_size) for symbol in SYMBOLS
        }
        if set(supplied) != set(SYMBOLS):
            raise CommonPriceCampaignError("tick sizes must cover all four markets")
        self._ticks = {symbol: float(supplied[symbol]) for symbol in SYMBOLS}
        if any(not math.isfinite(tick) or tick <= 0.0 for tick in self._ticks.values()):
            raise CommonPriceCampaignError("tick sizes must be finite and positive")
        self._roots: dict[str, _CommonPriceRoot] = {}
        self._source_to_root: dict[str, str] = {}
        self._opportunities: dict[str, CommonPriceOpportunity] = {}

    @property
    def roots(self) -> tuple[str, ...]:
        return tuple(sorted(self._roots))

    @property
    def opportunities(self) -> tuple[CommonPriceOpportunity, ...]:
        return tuple(self._opportunities[key] for key in sorted(self._opportunities))

    def export_state(self) -> dict[str, object]:
        """Return all price genealogy and transfer state as JSON data."""

        roots: list[dict[str, object]] = []
        for root_id in sorted(self._roots):
            root = self._roots[root_id]
            roots.append({
                "root_id": root.root_id,
                "attack_side": root.attack_side,
                "attack_time_ns": root.attack_time_ns,
                "phase": root.phase.value,
                "last_time_ns": root.last_time_ns,
                "participants": list(root.participants),
                "origin_joins": [_join_to_payload(root.origin_joins[symbol]) for symbol in root.participants],
                "joins": [
                    {"symbol": symbol, "items": [_join_to_payload(item) for item in root.joins[symbol]]}
                    for symbol in root.participants
                ],
                "attack_extremes": [[symbol, root.attack_extremes[symbol]] for symbol in root.participants],
                "attack_extreme_times": [[symbol, root.attack_extreme_times[symbol]] for symbol in root.participants],
                "pause_time_ns": root.pause_time_ns,
                "pause_bars": [
                    {"symbol": symbol, "items": [bar.to_dict() for bar in root.pause_bars[symbol]]}
                    for symbol in root.participants
                ],
                "latest_pivots": [_pivot_to_payload(root.latest_pivots[key]) for key in sorted(root.latest_pivots)],
                "consumed_pivot_ids": sorted(root.consumed_pivot_ids),
                "observed_transfer_ids": sorted(root.observed_transfer_ids),
                "targets": [[symbol, root.targets[symbol]] for symbol in root.participants if symbol in root.targets],
                "target_touched": sorted(root.target_touched, key=SYMBOLS.index),
                "target_ineligible": sorted(root.target_ineligible, key=SYMBOLS.index),
                "fully_reclaimed": root.fully_reclaimed,
                "fully_reclaimed_time_ns": root.fully_reclaimed_time_ns,
                "fully_reclaimed_participants": list(root.fully_reclaimed_participants),
            })
        return {
            "version": self.STATE_VERSION,
            "tick_sizes": [[symbol, self._ticks[symbol]] for symbol in SYMBOLS],
            "roots": roots,
            "source_to_root": [list(pair) for pair in sorted(self._source_to_root.items())],
            "opportunities": [_opportunity_to_payload(self._opportunities[key]) for key in sorted(self._opportunities)],
        }

    @classmethod
    def restore_state(cls, payload: Mapping[str, Any]) -> "CommonPriceCampaignBook":
        """Validate a full checkpoint before publishing a restored book."""

        if int(payload.get("version", -1)) != cls.STATE_VERSION:
            raise CommonPriceCampaignError("unsupported common price state version")
        try:
            raw_ticks = payload["tick_sizes"]
            raw_roots = payload["roots"]
            raw_source_map = payload["source_to_root"]
            raw_opportunities = payload["opportunities"]
            if any(not isinstance(item, list) for item in (raw_ticks, raw_roots, raw_source_map, raw_opportunities)):
                raise CommonPriceCampaignError("common price state collections must be lists")
            ticks = {str(pair[0]): float(pair[1]) for pair in raw_ticks}
            if len(ticks) != len(raw_ticks) or set(ticks) != set(SYMBOLS):
                raise CommonPriceCampaignError("checkpoint tick sizes are incomplete")
            # Constructor performs finiteness and positivity validation.
            candidate = cls(tick_sizes=ticks)
            roots: dict[str, _CommonPriceRoot] = {}
            derived_source_map: dict[str, str] = {}

            def _symbol_numbers(raw: object, participants: tuple[str, ...], name: str, *, integer: bool) -> dict[str, float] | dict[str, int]:
                result: dict[str, float] | dict[str, int] = {}
                for pair in raw:
                    symbol = str(pair[0])
                    if symbol in result:
                        raise CommonPriceCampaignError(f"duplicate {name} symbol")
                    result[symbol] = int(pair[1]) if integer else float(pair[1])
                if set(result) != set(participants):
                    raise CommonPriceCampaignError(f"{name} participant mismatch")
                return result

            for raw in raw_roots:
                if not isinstance(raw, Mapping):
                    raise CommonPriceCampaignError("common price root must be a mapping")
                root_id = str(raw["root_id"])
                participants = tuple(str(item) for item in raw["participants"])
                expected_order = tuple(symbol for symbol in SYMBOLS if symbol in participants)
                if not root_id.strip() or root_id in roots or len(participants) < 3 or len(set(participants)) != len(participants) or participants != expected_order:
                    raise CommonPriceCampaignError("invalid common price root identity")
                attack_side = _normalize_side(str(raw["attack_side"]))
                attack_time_ns = int(raw["attack_time_ns"])
                last_time_ns = int(raw["last_time_ns"])
                if attack_time_ns <= 0 or last_time_ns < attack_time_ns:
                    raise CommonPriceCampaignError("invalid common price clock")
                phase = CommonPricePhase(str(raw["phase"]))
                pause_time_ns = None if raw["pause_time_ns"] is None else int(raw["pause_time_ns"])
                if (phase is CommonPricePhase.ATTACKING and pause_time_ns is not None) or (phase is CommonPricePhase.PAUSE and (pause_time_ns is None or pause_time_ns <= attack_time_ns or pause_time_ns > last_time_ns)):
                    raise CommonPriceCampaignError("phase and pause clock disagree")

                origins_list = [_join_from_payload(item) for item in raw["origin_joins"]]
                origins = {item.symbol: item for item in origins_list}
                if len(origins) != len(origins_list) or set(origins) != set(participants):
                    raise CommonPriceCampaignError("origin joins mismatch participants")
                joins: dict[str, list[CommonSourceJoin]] = {}
                for group in raw["joins"]:
                    symbol = str(group["symbol"])
                    if symbol in joins:
                        raise CommonPriceCampaignError("duplicate source-join group")
                    items = [_join_from_payload(item) for item in group["items"]]
                    if not items or any(item.symbol != symbol for item in items):
                        raise CommonPriceCampaignError("source joins belong to another symbol")
                    joins[symbol] = items
                if set(joins) != set(participants):
                    raise CommonPriceCampaignError("source joins mismatch participants")
                for symbol in participants:
                    if origins[symbol] not in joins[symbol]:
                        raise CommonPriceCampaignError("origin join is absent from genealogy")
                    for item in joins[symbol]:
                        if item.join_time_ns > last_time_ns:
                            raise CommonPriceCampaignError("future source join in checkpoint")
                        prior = derived_source_map.setdefault(item.source_campaign_root_id, root_id)
                        if prior != root_id:
                            raise CommonPriceCampaignError("source belongs to two price roots")

                attack_extremes_raw = _symbol_numbers(raw["attack_extremes"], participants, "attack_extremes", integer=False)
                attack_extremes = {symbol: float(value) for symbol, value in attack_extremes_raw.items()}
                if any(not math.isfinite(value) for value in attack_extremes.values()):
                    raise CommonPriceCampaignError("attack extremes must be finite")
                attack_times_raw = _symbol_numbers(raw["attack_extreme_times"], participants, "attack_extreme_times", integer=True)
                attack_times = {symbol: int(value) for symbol, value in attack_times_raw.items()}
                if any(value < attack_time_ns or value > last_time_ns for value in attack_times.values()):
                    raise CommonPriceCampaignError("attack extreme time is outside root history")

                pause_bars: dict[str, list[Bar]] = {}
                for group in raw["pause_bars"]:
                    symbol = str(group["symbol"])
                    if symbol in pause_bars:
                        raise CommonPriceCampaignError("duplicate pause-bar group")
                    bars = [_bar_from_payload(item) for item in group["items"]]
                    if any(bar.symbol != symbol or bar.close_time_ns > last_time_ns for bar in bars):
                        raise CommonPriceCampaignError("pause bar violates root clock")
                    if any(left.close_time_ns >= right.close_time_ns for left, right in zip(bars, bars[1:])):
                        raise CommonPriceCampaignError("pause bars are not strictly ordered")
                    pause_bars[symbol] = bars
                if set(pause_bars) != set(participants):
                    raise CommonPriceCampaignError("pause bars mismatch participants")
                if phase is CommonPricePhase.ATTACKING and any(pause_bars.values()):
                    raise CommonPriceCampaignError("attacking root cannot contain pause bars")

                pivots: dict[tuple[str, str], PausePivot] = {}
                for item in raw["latest_pivots"]:
                    pivot = _pivot_from_payload(item)
                    key = (pivot.symbol, pivot.side)
                    if key in pivots or pivot.root_id != root_id or pivot.symbol not in participants or pivot.observed_time_ns > last_time_ns:
                        raise CommonPriceCampaignError("invalid latest pause pivot")
                    pivots[key] = pivot
                consumed = {str(item) for item in raw["consumed_pivot_ids"]}
                transfers = {str(item) for item in raw["observed_transfer_ids"]}
                if not all(item.strip() for item in consumed | transfers):
                    raise CommonPriceCampaignError("empty pivot/transfer identity")
                target_pairs = raw["targets"]
                targets = {str(pair[0]): float(pair[1]) for pair in target_pairs}
                if len(targets) != len(target_pairs) or any(symbol not in participants or not math.isfinite(value) for symbol, value in targets.items()):
                    raise CommonPriceCampaignError("invalid frozen targets")
                if phase is CommonPricePhase.PAUSE and set(targets) != set(participants):
                    raise CommonPriceCampaignError("pause root must freeze every target")
                if phase is CommonPricePhase.ATTACKING and targets:
                    raise CommonPriceCampaignError("attacking root cannot have targets")
                touched = {str(item) for item in raw["target_touched"]}
                ineligible = {str(item) for item in raw["target_ineligible"]}
                if not touched <= set(participants) or not ineligible <= set(participants) or touched & ineligible:
                    raise CommonPriceCampaignError("invalid target eligibility state")

                reclaimed = raw["fully_reclaimed"]
                if not isinstance(reclaimed, bool):
                    raise CommonPriceCampaignError("fully_reclaimed must be bool")
                reclaimed_time = None if raw["fully_reclaimed_time_ns"] is None else int(raw["fully_reclaimed_time_ns"])
                reclaimed_participants = tuple(str(item) for item in raw["fully_reclaimed_participants"])
                if reclaimed:
                    if reclaimed_time is None or reclaimed_time > last_time_ns or len(reclaimed_participants) < 3 or not set(reclaimed_participants) <= set(participants):
                        raise CommonPriceCampaignError("invalid broad reclaim state")
                elif reclaimed_time is not None or reclaimed_participants:
                    raise CommonPriceCampaignError("unreclaimed root has reclaim fields")
                roots[root_id] = _CommonPriceRoot(
                    root_id=root_id,
                    attack_side=attack_side,
                    attack_time_ns=attack_time_ns,
                    phase=phase,
                    last_time_ns=last_time_ns,
                    participants=list(participants),
                    origin_joins=origins,
                    joins=joins,
                    attack_extremes=attack_extremes,
                    attack_extreme_times=attack_times,
                    pause_time_ns=pause_time_ns,
                    pause_bars=pause_bars,
                    latest_pivots=pivots,
                    consumed_pivot_ids=consumed,
                    observed_transfer_ids=transfers,
                    targets=targets,
                    target_touched=touched,
                    target_ineligible=ineligible,
                    fully_reclaimed=reclaimed,
                    fully_reclaimed_time_ns=reclaimed_time,
                    fully_reclaimed_participants=reclaimed_participants,
                )

            source_to_root = {str(pair[0]): str(pair[1]) for pair in raw_source_map}
            if len(source_to_root) != len(raw_source_map) or source_to_root != derived_source_map:
                raise CommonPriceCampaignError("source-to-root mapping is inconsistent")
            opportunities: dict[str, CommonPriceOpportunity] = {}
            for raw in raw_opportunities:
                if not isinstance(raw, Mapping):
                    raise CommonPriceCampaignError("opportunity must be a mapping")
                opportunity = _opportunity_from_payload(raw)
                root = roots.get(opportunity.root_id)
                if not opportunity.opportunity_id.strip() or opportunity.opportunity_id in opportunities or root is None:
                    raise CommonPriceCampaignError("invalid or duplicate opportunity")
                if (
                    opportunity.symbol not in root.participants
                    or opportunity.side != root.attack_side
                    or opportunity.attack_time_ns != root.attack_time_ns
                    or opportunity.pause_time_ns != root.pause_time_ns
                    or opportunity.confirmation_time_ns > root.last_time_ns
                    or opportunity.broken_pivot.root_id != root.root_id
                    or opportunity.stop_pivot.root_id != root.root_id
                    or opportunity.broken_pivot.symbol != opportunity.symbol
                    or opportunity.stop_pivot.symbol != opportunity.symbol
                    or len(opportunity.confirmation_participants) < 3
                    or not set(opportunity.confirmation_participants) <= set(root.participants)
                ):
                    raise CommonPriceCampaignError("opportunity contradicts its price root")
                opportunities[opportunity.opportunity_id] = opportunity
        except CommonPriceCampaignError:
            raise
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise CommonPriceCampaignError("malformed common price state") from exc

        candidate._roots = roots
        candidate._source_to_root = source_to_root
        candidate._opportunities = opportunities
        return candidate

    def register_attack(
        self,
        *,
        attack_side: str,
        bars: Mapping[str, Bar],
        source_joins: Mapping[str, CommonSourceJoin],
        root_id: str | None = None,
    ) -> str | None:
        """Register the first >=3-market outward attack, or return ``None``.

        Each initial participant supplies exactly one origin join.  Additional
        sources from the same wave are attached later with :meth:`add_source_join`.
        """

        side = _normalize_side(attack_side)
        participants = tuple(symbol for symbol in SYMBOLS if symbol in source_joins)
        if len(participants) < 3:
            return None
        decision_time_ns = self._validate_frame(bars, participants)
        for symbol in participants:
            join = source_joins[symbol]
            bar = bars[symbol]
            if join.symbol != symbol or join.join_time_ns != decision_time_ns:
                raise CommonPriceCampaignError("origin join does not match its attack bar")
            if not _supports(bar, side) or not self._closes_outward(bar, join, side):
                return None

        generated = stable_id(
            "COMMON_PRICE_ATTACK",
            decision_time_ns,
            side,
            *(f"{symbol}:{source_joins[symbol].source_campaign_root_id}" for symbol in participants),
            prefix="common-price-",
        )
        root_id = generated if root_id is None else root_id
        if not root_id.strip():
            raise CommonPriceCampaignError("root_id must be non-empty")
        existing = self._roots.get(root_id)
        if existing is not None:
            expected = tuple(existing.origin_joins[symbol] for symbol in existing.participants)
            supplied = tuple(source_joins[symbol] for symbol in participants)
            if (
                existing.attack_side != side
                or existing.attack_time_ns != decision_time_ns
                or expected != supplied
            ):
                raise CommonPriceCampaignError("root_id conflicts with another attack")
            return root_id

        for join in source_joins.values():
            self._assert_unowned_source(join.source_campaign_root_id, root_id)
        root = _CommonPriceRoot(
            root_id=root_id,
            attack_side=side,
            attack_time_ns=decision_time_ns,
            phase=CommonPricePhase.ATTACKING,
            last_time_ns=decision_time_ns,
            participants=list(participants),
            origin_joins={symbol: source_joins[symbol] for symbol in participants},
            joins={symbol: [source_joins[symbol]] for symbol in participants},
            attack_extremes={
                symbol: bars[symbol].high if side == "LONG" else bars[symbol].low
                for symbol in participants
            },
            attack_extreme_times={symbol: decision_time_ns for symbol in participants},
            pause_bars={symbol: [] for symbol in participants},
        )
        self._roots[root_id] = root
        for join in source_joins.values():
            self._source_to_root[join.source_campaign_root_id] = root_id
        return root_id

    def add_source_join(
        self,
        root_id: str,
        *,
        source_join: CommonSourceJoin,
        bar: Bar,
    ) -> None:
        """Attach a later same-wave source alias without rewriting its origin."""

        root = self._root(root_id)
        if root.fully_reclaimed:
            raise CommonPriceCampaignError("a fully reclaimed attack cannot extend")
        if source_join.join_time_ns < root.attack_time_ns:
            raise CommonPriceCampaignError("a source join cannot precede the first attack")
        if bar.symbol != source_join.symbol or bar.close_time_ns != source_join.join_time_ns:
            raise CommonPriceCampaignError("source join does not match its completed bar")
        if source_join.join_time_ns < root.last_time_ns:
            raise CommonPriceCampaignError("source joins cannot move campaign time backward")
        if not _supports(bar, root.attack_side) or not self._closes_outward(
            bar, source_join, root.attack_side
        ):
            raise CommonPriceCampaignError("source alias is not part of the outward attack")
        prior_alias = next(
            (
                join
                for joins in root.joins.values()
                for join in joins
                if join.source_campaign_root_id
                == source_join.source_campaign_root_id
            ),
            None,
        )
        if prior_alias is not None:
            if prior_alias != source_join:
                raise CommonPriceCampaignError(
                    "a source identity cannot be rebound to another join"
                )
            return
        self._assert_unowned_source(source_join.source_campaign_root_id, root_id)

        symbol = source_join.symbol
        if symbol not in root.origin_joins:
            root.participants.append(symbol)
            root.participants.sort(key=SYMBOLS.index)
            root.origin_joins[symbol] = source_join
            root.joins[symbol] = [source_join]
            root.attack_extremes[symbol] = (
                bar.high if root.attack_side == "LONG" else bar.low
            )
            root.attack_extreme_times[symbol] = bar.close_time_ns
            root.pause_bars[symbol] = []
            if root.phase is CommonPricePhase.PAUSE:
                # A late participant belongs to the same unreclaimed wave but
                # cannot receive a retroactive pause-to-old-extreme trade.  Its
                # join extreme is therefore born already spent; it may still
                # contribute breadth to the existing participants' transfer.
                tick = self._ticks[symbol]
                root.targets[symbol] = (
                    root.attack_extremes[symbol] - tick
                    if root.attack_side == "LONG"
                    else root.attack_extremes[symbol] + tick
                )
                root.target_ineligible.add(symbol)
        else:
            root.joins[symbol].append(source_join)
        self._source_to_root[source_join.source_campaign_root_id] = root_id

    def observe(
        self,
        root_id: str,
        bars: Mapping[str, Bar],
    ) -> tuple[CommonPriceOpportunity, ...]:
        """Advance a root by one synchronized completed-bar frame."""

        root = self._root(root_id)
        participants = tuple(root.participants)
        decision_time_ns = self._validate_frame(bars, participants)
        if decision_time_ns <= root.last_time_ns:
            raise CommonPriceCampaignError("observations must be strictly increasing")
        root.last_time_ns = decision_time_ns

        reclaimed = tuple(
            symbol
            for symbol in participants
            if self._closes_fully_inside_origin(root, symbol, bars[symbol])
        )
        if not root.fully_reclaimed and len(reclaimed) >= 3:
            root.fully_reclaimed = True
            root.fully_reclaimed_time_ns = decision_time_ns
            root.fully_reclaimed_participants = reclaimed

        if root.phase is CommonPricePhase.ATTACKING:
            opposite_side = _opposite(root.attack_side)
            paused = tuple(
                symbol for symbol in participants if _supports(bars[symbol], opposite_side)
            )
            if len(paused) >= 3:
                # The first opposite-control close ends the attack, but its
                # wick is still part of the attack extreme formed before that
                # close.  The newly formed objective cannot be declared spent
                # by its own formation bar.
                self._advance_attack_extremes(root, bars)
                root.phase = CommonPricePhase.PAUSE
                root.pause_time_ns = decision_time_ns
                self._freeze_targets(root)
                self._append_pause_frame(root, bars)
                return ()
            if not root.fully_reclaimed:
                self._advance_attack_extremes(root, bars)
            return ()

        self._append_pause_frame(root, bars)
        self._mark_target_touches(root, bars)
        # Three delivered market objectives complete the shared continuation
        # thesis.  A lagging fourth symbol is not a new trade in the same
        # cascade.  Keep the genealogy alive only so a later broad reclaim
        # can authorize the distinct failed-cascade reversal branch.
        if root.fully_reclaimed or len(root.target_touched) >= 3:
            return ()

        confirming: list[str] = []
        broken: dict[str, PausePivot] = {}
        pivot_side = "HIGH" if root.attack_side == "LONG" else "LOW"
        for symbol in participants:
            pivot = root.latest_pivots.get((symbol, pivot_side))
            bar = bars[symbol]
            if (
                pivot is None
                or pivot.pivot_id in root.consumed_pivot_ids
                or not _supports(bar, root.attack_side)
            ):
                continue
            crossed = bar.close > pivot.price if root.attack_side == "LONG" else bar.close < pivot.price
            if crossed:
                confirming.append(symbol)
                broken[symbol] = pivot
        if len(confirming) < 3:
            self._mark_stop_pivot_touches(root, bars)
            return ()

        created: list[CommonPriceOpportunity] = []
        breadth = tuple(confirming)
        for symbol in confirming:
            opportunity = self._make_opportunity(
                root,
                symbol=symbol,
                bar=bars[symbol],
                broken_pivot=broken[symbol],
                confirmation_participants=breadth,
            )
            if opportunity is not None:
                created.append(opportunity)
        root.consumed_pivot_ids.update(
            pivot.pivot_id for pivot in broken.values()
        )
        self._mark_stop_pivot_touches(root, bars)
        return tuple(created)

    def snapshot(self, root_id: str) -> CommonPriceSnapshot:
        root = self._root(root_id)
        joins = tuple(
            join for symbol in root.participants for join in root.joins[symbol]
        )
        pivots = tuple(
            sorted(
                root.latest_pivots.values(),
                key=lambda pivot: (pivot.symbol, pivot.side, pivot.observed_time_ns),
            )
        )
        return CommonPriceSnapshot(
            root_id=root.root_id,
            attack_side=root.attack_side,
            attack_time_ns=root.attack_time_ns,
            phase=root.phase,
            pause_time_ns=root.pause_time_ns,
            participants=tuple(root.participants),
            attack_extremes=tuple(
                (symbol, root.attack_extremes[symbol]) for symbol in root.participants
            ),
            origin_joins=tuple(root.origin_joins[symbol] for symbol in root.participants),
            source_joins=joins,
            latest_pivots=pivots,
            fully_reclaimed=root.fully_reclaimed,
            fully_reclaimed_time_ns=root.fully_reclaimed_time_ns,
            fully_reclaimed_participants=root.fully_reclaimed_participants,
        )

    def shared_root_for_source(self, source_campaign_root_id: str) -> str:
        try:
            return self._source_to_root[source_campaign_root_id]
        except KeyError as exc:
            raise CommonPriceCampaignError("source does not belong to a common root") from exc

    def source_aliases(self, root_id: str, symbol: str) -> tuple[CommonSourceJoin, ...]:
        root = self._root(root_id)
        try:
            return tuple(root.joins[symbol])
        except KeyError as exc:
            raise CommonPriceCampaignError("symbol is not a root participant") from exc

    def fully_reclaimed(self, root_id: str) -> bool:
        return self._root(root_id).fully_reclaimed

    def continuation_delivered(self, root_id: str) -> bool:
        """Return whether the shared continuation already delivered broadly."""

        return len(self._root(root_id).target_touched) >= 3

    def fresh_attack_completes_prior_delivery(
        self,
        root_id: str,
        *,
        bars: Mapping[str, Bar],
        source_joins: Mapping[str, tuple[CommonSourceJoin, ...]],
    ) -> bool:
        """Separate a new broad source attack at the old wave's delivery edge.

        A repeated alias remains in the existing wave.  A paused campaign that
        has already delivered at least one objective is different: if this
        synchronized frame both completes broad delivery and breaks fresh
        structural facts in at least three markets, those fresh facts begin a
        new market episode.  This boundary depends only on observed market
        state, never on which sibling plan the account happened to claim.
        """

        root = self._root(root_id)
        if (
            root.phase is not CommonPricePhase.PAUSE
            or not root.target_touched
            or len(root.target_touched) >= 3
        ):
            return False
        old_facts = {
            (join.symbol, join.source_boundary_id, join.source_observed_time_ns)
            for joins in root.joins.values()
            for join in joins
        }
        fresh_symbols = {
            symbol
            for symbol, joins in source_joins.items()
            if any(
                (join.symbol, join.source_boundary_id, join.source_observed_time_ns)
                not in old_facts
                for join in joins
            )
        }
        if len(fresh_symbols) < 3:
            return False
        delivered = set(root.target_touched)
        for symbol, target in root.targets.items():
            if symbol in root.target_ineligible or symbol not in bars:
                continue
            bar = bars[symbol]
            if bar.symbol != symbol:
                raise CommonPriceCampaignError(
                    "delivery query bar belongs to another symbol"
                )
            touched = (
                bar.high >= target
                if root.attack_side == "LONG"
                else bar.low <= target
            )
            if touched:
                delivered.add(symbol)
        return len(delivered) >= 3

    def _append_pause_frame(self, root: _CommonPriceRoot, bars: Mapping[str, Bar]) -> None:
        for symbol in root.participants:
            history = root.pause_bars[symbol]
            history.append(bars[symbol])
            if len(history) < 5:
                continue
            window = history[-5:]
            center = window[2]
            other_highs = (window[0].high, window[1].high, window[3].high, window[4].high)
            other_lows = (window[0].low, window[1].low, window[3].low, window[4].low)
            if center.high > max(other_highs):
                self._record_pivot(root, symbol, "HIGH", center.high, center, window[-1])
            if center.low < min(other_lows):
                self._record_pivot(root, symbol, "LOW", center.low, center, window[-1])

    @staticmethod
    def _record_pivot(
        root: _CommonPriceRoot,
        symbol: str,
        side: str,
        price: float,
        event_bar: Bar,
        observed_bar: Bar,
    ) -> None:
        root.latest_pivots[(symbol, side)] = PausePivot(
            pivot_id=stable_id(
                root.root_id,
                symbol,
                side,
                event_bar.close_time_ns,
                prefix="common-pivot-",
            ),
            root_id=root.root_id,
            symbol=symbol,
            side=side,
            price=price,
            event_time_ns=event_bar.close_time_ns,
            observed_time_ns=observed_bar.close_time_ns,
        )

    def _make_opportunity(
        self,
        root: _CommonPriceRoot,
        *,
        symbol: str,
        bar: Bar,
        broken_pivot: PausePivot,
        confirmation_participants: tuple[str, ...],
    ) -> CommonPriceOpportunity | None:
        if symbol in root.target_touched or symbol in root.target_ineligible:
            return None
        opposite_pivot_side = "LOW" if root.attack_side == "LONG" else "HIGH"
        stop_pivot = root.latest_pivots.get((symbol, opposite_pivot_side))
        if stop_pivot is None or root.pause_time_ns is None:
            return None
        if stop_pivot.pivot_id in root.consumed_pivot_ids:
            return None
        if stop_pivot.event_time_ns <= broken_pivot.event_time_ns:
            return None
        if (
            root.attack_side == "LONG" and bar.low <= stop_pivot.price
            or root.attack_side == "SHORT" and bar.high >= stop_pivot.price
        ):
            return None
        transfer_id = stable_id(
            root.root_id,
            symbol,
            broken_pivot.pivot_id,
            stop_pivot.pivot_id,
            prefix="common-transfer-",
        )
        if transfer_id in root.observed_transfer_ids:
            return None
        root.observed_transfer_ids.add(transfer_id)
        tick = self._ticks[symbol]
        entry = bar.close
        stop = stop_pivot.price - tick if root.attack_side == "LONG" else stop_pivot.price + tick
        target = root.targets[symbol]
        valid = stop < entry < target if root.attack_side == "LONG" else target < entry < stop
        if not valid:
            return None
        gross_rr = abs(target - entry) / abs(entry - stop)
        if gross_rr < 1.0 - 1e-12:
            return None
        opportunity_id = stable_id(
            root.root_id,
            symbol,
            "CONTINUATION",
            bar.close_time_ns,
            prefix="common-price-opportunity-",
        )
        evidence: dict[str, object] = {
            "common_root_id": root.root_id,
            "phase_attack_time_ns": root.attack_time_ns,
            "phase_pause_time_ns": root.pause_time_ns,
            "phase_confirmation_time_ns": bar.close_time_ns,
            "common_transfer_id": transfer_id,
            "broken_pause_pivot_id": broken_pivot.pivot_id,
            "broken_pause_pivot_side": broken_pivot.side,
            "broken_pause_pivot_price": broken_pivot.price,
            "broken_pause_pivot_event_time_ns": broken_pivot.event_time_ns,
            "broken_pause_pivot_observed_time_ns": broken_pivot.observed_time_ns,
            "stop_pause_pivot_id": stop_pivot.pivot_id,
            "stop_pause_pivot_side": stop_pivot.side,
            "stop_pause_pivot_price": stop_pivot.price,
            "stop_pause_pivot_event_time_ns": stop_pivot.event_time_ns,
            "stop_pause_pivot_observed_time_ns": stop_pivot.observed_time_ns,
            "attack_extreme": root.attack_extremes[symbol],
            "attack_extreme_time_ns": root.attack_extreme_times[symbol],
            "confirmation_participants": confirmation_participants,
            "confirmation_breadth": len(confirmation_participants),
            "confirmation_quote_volume": bar.quote_volume,
            "confirmation_signed_quote_flow": bar.signed_quote_flow,
            "origin_source_campaign_root_id": root.origin_joins[symbol].source_campaign_root_id,
            "origin_source_boundary_id": root.origin_joins[symbol].source_boundary_id,
            "origin_source_lower": root.origin_joins[symbol].source_lower,
            "origin_source_upper": root.origin_joins[symbol].source_upper,
            "target_untouched_since_pause": True,
        }
        opportunity = CommonPriceOpportunity(
            opportunity_id=opportunity_id,
            root_id=root.root_id,
            symbol=symbol,
            side=root.attack_side,
            attack_time_ns=root.attack_time_ns,
            pause_time_ns=root.pause_time_ns,
            confirmation_time_ns=bar.close_time_ns,
            entry=entry,
            stop=stop,
            target=target,
            entry_zone_lower=entry - tick,
            entry_zone_upper=entry + tick,
            broken_pivot=broken_pivot,
            stop_pivot=stop_pivot,
            attack_extreme=root.attack_extremes[symbol],
            confirmation_participants=confirmation_participants,
            evidence=evidence,
        )
        self._opportunities[opportunity_id] = opportunity
        return opportunity

    def _freeze_targets(self, root: _CommonPriceRoot) -> None:
        for symbol in root.participants:
            tick = self._ticks[symbol]
            root.targets[symbol] = (
                root.attack_extremes[symbol] - tick
                if root.attack_side == "LONG"
                else root.attack_extremes[symbol] + tick
            )

    @staticmethod
    def _advance_attack_extremes(
        root: _CommonPriceRoot,
        bars: Mapping[str, Bar],
    ) -> None:
        for symbol in root.participants:
            candidate = bars[symbol].high if root.attack_side == "LONG" else bars[symbol].low
            prior = root.attack_extremes[symbol]
            advanced = candidate > prior if root.attack_side == "LONG" else candidate < prior
            if advanced:
                root.attack_extremes[symbol] = candidate
                root.attack_extreme_times[symbol] = bars[symbol].close_time_ns

    @staticmethod
    def _mark_target_touches(
        root: _CommonPriceRoot,
        bars: Mapping[str, Bar],
    ) -> None:
        for symbol, target in root.targets.items():
            if symbol in root.target_ineligible:
                continue
            bar = bars[symbol]
            touched = bar.high >= target if root.attack_side == "LONG" else bar.low <= target
            if touched:
                root.target_touched.add(symbol)

    @staticmethod
    def _mark_stop_pivot_touches(
        root: _CommonPriceRoot,
        bars: Mapping[str, Bar],
    ) -> None:
        """A breached invalidation pivot cannot anchor a later entry stop."""

        for (symbol, _), pivot in root.latest_pivots.items():
            stop_side = "LOW" if root.attack_side == "LONG" else "HIGH"
            if pivot.side != stop_side:
                continue
            bar = bars[symbol]
            if bar.close_time_ns <= pivot.observed_time_ns:
                continue
            touched = (
                bar.high >= pivot.price
                if pivot.side == "HIGH"
                else bar.low <= pivot.price
            )
            if touched:
                root.consumed_pivot_ids.add(pivot.pivot_id)

    @staticmethod
    def _closes_outward(bar: Bar, join: CommonSourceJoin, side: str) -> bool:
        return bar.close > join.source_upper if side == "LONG" else bar.close < join.source_lower

    @staticmethod
    def _closes_fully_inside_origin(
        root: _CommonPriceRoot,
        symbol: str,
        bar: Bar,
    ) -> bool:
        origin_time = root.origin_joins[symbol].join_time_ns
        origins = tuple(
            join
            for join in root.joins[symbol]
            if join.join_time_ns == origin_time
        )
        if root.attack_side == "LONG":
            return bar.close < min(join.source_lower for join in origins)
        return bar.close > max(join.source_upper for join in origins)

    @staticmethod
    def _validate_frame(bars: Mapping[str, Bar], participants: tuple[str, ...]) -> int:
        missing = set(participants) - set(bars)
        if missing:
            raise CommonPriceCampaignError(f"frame misses participants: {sorted(missing)}")
        close_times = {bars[symbol].close_time_ns for symbol in participants}
        intervals = {bars[symbol].interval_minutes for symbol in participants}
        if len(close_times) != 1 or len(intervals) != 1:
            raise CommonPriceCampaignError("participant bars must be synchronized")
        for symbol in participants:
            if bars[symbol].symbol != symbol:
                raise CommonPriceCampaignError("bar mapping key differs from symbol")
        return next(iter(close_times))

    def _assert_unowned_source(self, source_campaign_root_id: str, root_id: str) -> None:
        prior = self._source_to_root.get(source_campaign_root_id)
        if prior is not None and prior != root_id:
            raise CommonPriceCampaignError(f"source already belongs to common root {prior}")

    def _root(self, root_id: str) -> _CommonPriceRoot:
        try:
            return self._roots[root_id]
        except KeyError as exc:
            raise CommonPriceCampaignError(f"unknown common price root: {root_id}") from exc


__all__ = [
    "CommonPriceCampaignBook",
    "CommonPriceCampaignError",
    "CommonPriceOpportunity",
    "CommonPricePhase",
    "CommonPriceSnapshot",
    "CommonSourceJoin",
    "PausePivot",
]
