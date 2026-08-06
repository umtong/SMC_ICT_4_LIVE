"""Pure objective and directional-leg lifecycle contracts for candidate-07.

The module intentionally has no NautilusTrader dependency. It separates the
market-state claim from order construction and performance accounting:

* a causally confirmed liquidity objective can be used once;
* a directional leg can arm at most one entry until a new completed control
  auction accepts price in the same direction;
* a completed opposing control auction or loss of the active leg origin is an
  event-based invalidation, not a tuned wall-clock timeout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


_VALID_SIDES = {"UPPER", "LOWER"}
_VALID_DIRECTIONS = {"LONG", "SHORT"}


@dataclass(frozen=True, slots=True, order=True)
class ObjectiveKey:
    """Stable identity for one causally observable liquidity objective."""

    kind: str
    side: str
    source_id: str

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("objective kind must be non-empty")
        if self.side not in _VALID_SIDES:
            raise ValueError(f"unsupported objective side: {self.side}")
        if not self.source_id:
            raise ValueError("objective source_id must be non-empty")


@dataclass(slots=True)
class ObjectiveState:
    """Mutable lifecycle state for one price objective."""

    key: ObjectiveKey
    level: float
    reason: str
    confirmed_index: int
    confirmed_ts_ns: int
    metadata: dict[str, Any] = field(default_factory=dict)
    consumed_index: int | None = None
    reserved_index: int | None = None
    invalidated_index: int | None = None
    terminal_reason: str | None = None

    @property
    def available(self) -> bool:
        return (
            self.consumed_index is None
            and self.reserved_index is None
            and self.invalidated_index is None
        )

    def details(self) -> dict[str, Any]:
        return {
            "kind": self.key.kind,
            "side": self.key.side,
            "source_id": self.key.source_id,
            "level": self.level,
            "reason": self.reason,
            "confirmed_index": self.confirmed_index,
            "confirmed_ts_ns": self.confirmed_ts_ns,
            "consumed_index": self.consumed_index,
            "reserved_index": self.reserved_index,
            "invalidated_index": self.invalidated_index,
            "terminal_reason": self.terminal_reason,
            **dict(self.metadata),
        }


class ObjectiveLedger:
    """Causal one-use ledger for internal and external liquidity objectives."""

    def __init__(self) -> None:
        self._states: dict[ObjectiveKey, ObjectiveState] = {}

    def register(
        self,
        key: ObjectiveKey,
        *,
        level: float,
        reason: str,
        confirmed_index: int,
        confirmed_ts_ns: int,
        metadata: Mapping[str, Any] | None = None,
    ) -> ObjectiveState:
        if not reason:
            raise ValueError("objective reason must be non-empty")
        if confirmed_index < 0:
            raise ValueError("confirmed_index must be non-negative")
        if confirmed_ts_ns <= 0:
            raise ValueError("confirmed_ts_ns must be positive")
        value = float(level)
        existing = self._states.get(key)
        if existing is not None:
            if abs(existing.level - value) > 1e-12 or existing.reason != reason:
                raise ValueError(
                    f"objective identity reused with different contract: {key}",
                )
            existing.metadata.update(dict(metadata or {}))
            return existing
        state = ObjectiveState(
            key=key,
            level=value,
            reason=reason,
            confirmed_index=int(confirmed_index),
            confirmed_ts_ns=int(confirmed_ts_ns),
            metadata=dict(metadata or {}),
        )
        self._states[key] = state
        return state

    def get(self, key: ObjectiveKey) -> ObjectiveState | None:
        return self._states.get(key)

    def observe_completed_bar(
        self,
        *,
        index: int,
        high: float,
        low: float,
        sides: Iterable[str] | None = None,
    ) -> tuple[ObjectiveState, ...]:
        """Consume objectives touched strictly after their confirmation bar.

        ``sides`` lets the scenario engine process target-side objectives before
        an entry decision while leaving the opposite side visible long enough
        for the same completed bar to be classified as a liquidity sweep. The
        opposite side is then consumed at the end of that bar.
        """

        allowed = None if sides is None else set(sides)
        if allowed is not None and not allowed.issubset(_VALID_SIDES):
            raise ValueError(f"unsupported objective sides: {sorted(allowed - _VALID_SIDES)}")
        touched: list[ObjectiveState] = []
        for state in self._states.values():
            if not state.available or index <= state.confirmed_index:
                continue
            if allowed is not None and state.key.side not in allowed:
                continue
            reached = high >= state.level if state.key.side == "UPPER" else low <= state.level
            if reached:
                state.consumed_index = int(index)
                state.terminal_reason = "OBJECTIVE_PRICE_TOUCHED_AFTER_CONFIRMATION"
                touched.append(state)
        return tuple(touched)

    def reserve(self, key: ObjectiveKey, *, index: int) -> bool:
        state = self._states.get(key)
        if state is None or not state.available or index < state.confirmed_index:
            return False
        state.reserved_index = int(index)
        state.terminal_reason = "OBJECTIVE_RESERVED_BY_ENTRY_SIGNAL"
        return True

    def invalidate(self, key: ObjectiveKey, *, index: int, reason: str) -> bool:
        state = self._states.get(key)
        if state is None or not state.available:
            return False
        state.invalidated_index = int(index)
        state.terminal_reason = str(reason)
        return True

    def available_for_direction(
        self,
        *,
        direction: str,
        entry: float,
        kinds: Iterable[str] | None = None,
        source_ids: Iterable[str] | None = None,
    ) -> list[ObjectiveState]:
        if direction not in _VALID_DIRECTIONS:
            raise ValueError(f"unsupported direction: {direction}")
        allowed_kinds = None if kinds is None else set(kinds)
        allowed_sources = None if source_ids is None else set(source_ids)
        side = "UPPER" if direction == "LONG" else "LOWER"
        result: list[ObjectiveState] = []
        for state in self._states.values():
            if not state.available or state.key.side != side:
                continue
            if allowed_kinds is not None and state.key.kind not in allowed_kinds:
                continue
            if allowed_sources is not None and state.key.source_id not in allowed_sources:
                continue
            beyond = state.level > entry if direction == "LONG" else state.level < entry
            if beyond:
                result.append(state)
        result.sort(key=lambda value: abs(value.level - entry))
        return result

    def snapshot(self) -> list[dict[str, Any]]:
        return [self._states[key].details() for key in sorted(self._states)]


@dataclass(slots=True)
class DirectionalLeg:
    """One accepted directional delivery leg inside an HTF bias context."""

    context_id: str
    leg_id: int
    direction: str
    origin: float
    extreme: float
    created_index: int
    created_ts_ns: int
    objective_key: ObjectiveKey
    open_for_entry: bool = True
    reserved_scenario_id: str | None = None
    reserved_index: int | None = None
    closed_index: int | None = None
    closed_reason: str | None = None

    def __post_init__(self) -> None:
        if self.direction not in _VALID_DIRECTIONS:
            raise ValueError(f"unsupported leg direction: {self.direction}")
        if self.leg_id <= 0:
            raise ValueError("leg_id must be positive")

    def reserve_entry(self, *, scenario_id: str, index: int) -> bool:
        if not scenario_id or not self.open_for_entry or self.reserved_scenario_id is not None:
            return False
        self.open_for_entry = False
        self.reserved_scenario_id = str(scenario_id)
        self.reserved_index = int(index)
        return True

    def reserved_for(self, scenario_id: str) -> bool:
        return self.reserved_scenario_id == scenario_id

    def close(self, *, index: int, reason: str) -> None:
        self.open_for_entry = False
        self.closed_index = int(index)
        self.closed_reason = str(reason)

    def details(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "leg_id": self.leg_id,
            "direction": self.direction,
            "origin": self.origin,
            "extreme": self.extreme,
            "created_index": self.created_index,
            "created_ts_ns": self.created_ts_ns,
            "open_for_entry": self.open_for_entry,
            "reserved_scenario_id": self.reserved_scenario_id,
            "reserved_index": self.reserved_index,
            "closed_index": self.closed_index,
            "closed_reason": self.closed_reason,
            "objective": {
                "kind": self.objective_key.kind,
                "side": self.objective_key.side,
                "source_id": self.objective_key.source_id,
            },
        }


@dataclass(frozen=True, slots=True)
class ControlAuction:
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    end_ts_ns: int

    @property
    def candle_range(self) -> float:
        return max(self.high - self.low, 0.0)

    @property
    def body_fraction(self) -> float:
        return abs(self.close - self.open) / self.candle_range if self.candle_range > 0.0 else 0.0

    @property
    def close_location(self) -> float:
        return (self.close - self.low) / self.candle_range if self.candle_range > 0.0 else 0.5

    @property
    def flow_ratio(self) -> float:
        if self.volume <= 0.0:
            return 0.0
        return (2.0 * self.taker_buy_volume - self.volume) / self.volume


@dataclass(frozen=True, slots=True)
class ControlThresholds:
    acceptance_atr: float = 0.02
    minimum_range_atr: float = 0.75
    minimum_body_fraction: float = 0.50
    minimum_relative_volume: float = 0.95
    minimum_flow_ratio: float = 0.04
    outer_close_location: float = 0.68
    use_flow: bool = True

    def __post_init__(self) -> None:
        if self.acceptance_atr < 0.0:
            raise ValueError("acceptance_atr must be non-negative")
        if self.minimum_range_atr <= 0.0:
            raise ValueError("minimum_range_atr must be positive")
        if not 0.0 <= self.minimum_body_fraction <= 1.0:
            raise ValueError("minimum_body_fraction must be within [0, 1]")
        if self.minimum_relative_volume <= 0.0:
            raise ValueError("minimum_relative_volume must be positive")
        if self.minimum_flow_ratio < 0.0:
            raise ValueError("minimum_flow_ratio must be non-negative")
        if not 0.5 < self.outer_close_location <= 1.0:
            raise ValueError("outer_close_location must be within (0.5, 1]")


@dataclass(frozen=True, slots=True)
class ControlDecision:
    classification: str
    same_direction_renewal: bool
    opposing_acceptance: bool
    leg_origin_lost: bool
    details: Mapping[str, Any]


_DEF_NONE = ControlDecision("NO_CONTROL_STATE_CHANGE", False, False, False, {})


def classify_control_auction(
    *,
    direction: str,
    auction: ControlAuction,
    prior_high: float,
    prior_low: float,
    atr: float,
    baseline_volume: float,
    leg_origin: float | None,
    thresholds: ControlThresholds,
) -> ControlDecision:
    """Classify a completed control auction without looking at future bars.

    Priority is explicit: a full opposing acceptance invalidates the HTF context;
    same-direction acceptance creates a new delivery leg; otherwise loss of the
    currently active leg origin suspends the leg and exits a matching position.
    """

    if direction not in _VALID_DIRECTIONS:
        raise ValueError(f"unsupported direction: {direction}")
    if atr <= 0.0 or baseline_volume <= 0.0 or auction.candle_range <= 0.0:
        return _DEF_NONE

    distance = thresholds.acceptance_atr * atr
    range_atr = auction.candle_range / atr
    relative_volume = auction.volume / baseline_volume
    body_ok = auction.body_fraction >= thresholds.minimum_body_fraction
    range_ok = range_atr >= thresholds.minimum_range_atr
    volume_ok = relative_volume >= thresholds.minimum_relative_volume
    outer = thresholds.outer_close_location
    flow = auction.flow_ratio
    base_details = {
        "direction": direction,
        "prior_high": float(prior_high),
        "prior_low": float(prior_low),
        "atr": float(atr),
        "baseline_volume": float(baseline_volume),
        "relative_volume": relative_volume,
        "acceptance_distance": distance,
        "range_atr": range_atr,
        "body_fraction": auction.body_fraction,
        "close_location": auction.close_location,
        "flow_ratio": flow,
        "leg_origin": leg_origin,
        "control_end_ts_ns": auction.end_ts_ns,
    }

    long_accept = (
        auction.close > prior_high + distance
        and auction.close > auction.open
        and range_ok
        and body_ok
        and volume_ok
        and auction.close_location >= outer
        and ((flow >= thresholds.minimum_flow_ratio) if thresholds.use_flow else True)
    )
    short_accept = (
        auction.close < prior_low - distance
        and auction.close < auction.open
        and range_ok
        and body_ok
        and volume_ok
        and auction.close_location <= 1.0 - outer
        and ((flow <= -thresholds.minimum_flow_ratio) if thresholds.use_flow else True)
    )

    opposing = short_accept if direction == "LONG" else long_accept
    renewal = long_accept if direction == "LONG" else short_accept
    if opposing:
        return ControlDecision(
            "OPPOSING_CONTROL_AUCTION_ACCEPTED",
            False,
            True,
            False,
            base_details,
        )
    if renewal:
        return ControlDecision(
            "SAME_DIRECTION_CONTROL_AUCTION_ACCEPTED",
            True,
            False,
            False,
            base_details,
        )

    origin_lost = False
    if leg_origin is not None and body_ok and range_ok and volume_ok:
        if direction == "LONG":
            origin_lost = (
                auction.close < leg_origin
                and auction.close < auction.open
                and auction.close_location <= 1.0 - outer
                and ((flow <= -thresholds.minimum_flow_ratio) if thresholds.use_flow else True)
            )
        else:
            origin_lost = (
                auction.close > leg_origin
                and auction.close > auction.open
                and auction.close_location >= outer
                and ((flow >= thresholds.minimum_flow_ratio) if thresholds.use_flow else True)
            )
    if origin_lost:
        return ControlDecision(
            "ACTIVE_DIRECTIONAL_LEG_ORIGIN_LOST",
            False,
            False,
            True,
            base_details,
        )
    return ControlDecision(
        "NO_CONTROL_STATE_CHANGE",
        False,
        False,
        False,
        base_details,
    )
