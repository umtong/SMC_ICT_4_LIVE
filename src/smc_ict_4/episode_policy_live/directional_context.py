"""Causal direction and active two-sided liquidity context.

This production port synthesizes existing research rather than claiming a new
mechanism.  Its exact provenance is:

* ``research_directional_liquidity_policy_v2`` lineage commit
  ``ea5df7efc0a43b7085372b37f9fcb18ba1aa56de``,
  ``research/candidate-directional-liquidity-policy-v2/directional_context.py``
  (multi-horizon path efficiency, flow, common factor and residual strength);
* commit ``3984182f07b40cb38b3fe2bd8571209ebcb3409d``,
  ``research/candidate-liquidity-auction-v1/semantic_liquidity_full.py``
  (direction-source and route-obstacle roles); and
* commit ``f46546cf5f0833ca3e0725a144384eef37757754``,
  ``research/candidate-coherent-auction-system-v5/coherent_system_v5.py``
  (pre-event direction is distinct from the event update).

Only closed :class:`~smc_ict_4.episode_policy_live.domain.Bar` objects and
point-in-time active boundaries are accepted.  The module contains no fitted
gate, outcome label, admission threshold or period/symbol identity.  Missing
measurements are represented by ``None`` rather than a neutral-looking zero.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import median
from typing import Iterable, Mapping, Sequence

from .domain import Bar, LiquidityBoundary


EPS = 1e-12
HORIZON_MINUTES: tuple[int, ...] = (15, 60, 240, 720)
HORIZON_WEIGHTS: tuple[float, ...] = (0.18, 0.27, 0.33, 0.22)


def _direction(side: str) -> float:
    if side == "LONG":
        return 1.0
    if side == "SHORT":
        return -1.0
    raise ValueError(f"unsupported side: {side}")


def _finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _weighted_available(values: Sequence[float | None]) -> float | None:
    pairs = [
        (weight, value)
        for weight, value in zip(HORIZON_WEIGHTS, values)
        if value is not None
    ]
    if not pairs:
        return None
    weight_sum = sum(item[0] for item in pairs)
    return sum(weight * float(value) for weight, value in pairs) / weight_sum


def _closed_bars(
    bars: Sequence[Bar],
    *,
    symbol: str,
    decision_time_ns: int,
    interval_minutes: int,
) -> list[Bar]:
    closed = [
        item
        for item in bars
        if item.symbol == symbol
        and item.interval_minutes == interval_minutes
        and item.close_time_ns <= decision_time_ns
    ]
    closed.sort(key=lambda item: item.close_time_ns)
    for left, right in zip(closed, closed[1:]):
        if right.close_time_ns <= left.close_time_ns:
            raise ValueError("bars must have unique increasing close times")
        expected_open = left.open_time_ns + interval_minutes * 60_000_000_000
        if right.open_time_ns != expected_open:
            raise ValueError("bars must be contiguous")
    return closed


def _causal_atr(bars: Sequence[Bar], length: int = 20) -> float | None:
    if len(bars) < 2:
        return None
    ranges: list[float] = []
    prior_close: float | None = None
    for item in bars[-length:]:
        true_range = item.range
        if prior_close is not None:
            true_range = max(
                true_range,
                abs(item.high - prior_close),
                abs(item.low - prior_close),
            )
        ranges.append(true_range)
        prior_close = item.close
    value = median(ranges)
    return float(value) if value > EPS and math.isfinite(value) else None


def _window_move(
    bars: Sequence[Bar],
    *,
    horizon_minutes: int,
    atr_price: float | None,
) -> tuple[float | None, float | None]:
    if not bars:
        return None, None
    interval = bars[-1].interval_minutes
    required_steps = max(1, horizon_minutes // interval)
    if len(bars) <= required_steps:
        return None, None
    closes = [item.close for item in bars[-(required_steps + 1) :]]
    raw_move = closes[-1] - closes[0]
    travel = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
    efficiency = abs(raw_move) / travel if travel > EPS else None
    move_atr = raw_move / atr_price if atr_price is not None and atr_price > EPS else None
    return _finite_or_none(move_atr), _finite_or_none(efficiency)


@dataclass(frozen=True, slots=True)
class HorizonDirection:
    """One horizon, positive when aligned with the proposed side."""

    horizon_minutes: int
    move_atr: float | None
    path_efficiency: float | None
    common_move_atr: float | None
    residual_move_atr: float | None

    def to_dict(self) -> dict[str, int | float | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DirectionalContext:
    """Side-aligned market state known at one decision timestamp."""

    symbol: str
    side: str
    decision_time_ns: int
    atr_price: float | None
    horizons: tuple[HorizonDirection, ...]
    trend_alignment: float | None
    trend_consensus: float | None
    path_efficiency: float | None
    signed_flow_share: float | None
    activity_ratio: float | None
    common_component: float | None
    symbol_residual: float | None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["horizons"] = [item.to_dict() for item in self.horizons]
        return payload


@dataclass(frozen=True, slots=True)
class DirectionalUpdate:
    """Pre-event direction and its separately observable event-time update."""

    prior: DirectionalContext
    posterior: DirectionalContext
    trend_alignment_update: float | None
    common_component_update: float | None
    symbol_residual_update: float | None


def _difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return right - left


def _flow_context(bars: Sequence[Bar], side: str) -> tuple[float | None, float | None]:
    if not bars:
        return None, None
    direction = _direction(side)
    current = bars[-min(12, len(bars)) :]
    quote = sum(item.quote_volume for item in current)
    signed_flow = sum(item.signed_quote_flow for item in current)
    share = direction * signed_flow / quote if quote > EPS else None

    prior_end = len(bars) - len(current)
    prior = bars[max(0, prior_end - 36) : prior_end]
    current_activity = sum(item.quote_volume for item in current) / len(current)
    if not prior or current_activity <= EPS:
        activity_ratio = None
    else:
        prior_activity = sum(item.quote_volume for item in prior) / len(prior)
        activity_ratio = current_activity / prior_activity if prior_activity > EPS else None
    return _finite_or_none(share), _finite_or_none(activity_ratio)


def build_directional_context(
    *,
    symbol: str,
    side: str,
    decision_time_ns: int,
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    interval_minutes: int = 5,
) -> DirectionalContext:
    """Build direction while separating synchronous common and residual moves.

    Peer observations are timestamp matched at the decision bar.  If no peer has
    enough synchronous history, ``common_component`` and ``symbol_residual`` are
    unknown; the symbol move is never silently treated as wholly idiosyncratic.
    """

    direction = _direction(side)
    target = _closed_bars(
        bars_by_symbol.get(symbol, ()),
        symbol=symbol,
        decision_time_ns=decision_time_ns,
        interval_minutes=interval_minutes,
    )
    atr_price = _causal_atr(target)
    target_close_time = target[-1].close_time_ns if target else None

    peers: dict[str, list[Bar]] = {}
    if target_close_time is not None:
        for peer_symbol, values in bars_by_symbol.items():
            if peer_symbol == symbol:
                continue
            closed = _closed_bars(
                values,
                symbol=peer_symbol,
                decision_time_ns=decision_time_ns,
                interval_minutes=interval_minutes,
            )
            if closed and closed[-1].close_time_ns == target_close_time:
                peers[peer_symbol] = closed

    rows: list[HorizonDirection] = []
    aligned_moves: list[float | None] = []
    efficiencies: list[float | None] = []
    common_moves: list[float | None] = []
    residual_moves: list[float | None] = []
    for horizon in HORIZON_MINUTES:
        raw_move, efficiency = _window_move(
            target,
            horizon_minutes=horizon,
            atr_price=atr_price,
        )
        move = direction * raw_move if raw_move is not None else None
        peer_moves: list[float] = []
        for peer in peers.values():
            peer_move, _ = _window_move(
                peer,
                horizon_minutes=horizon,
                atr_price=_causal_atr(peer),
            )
            if peer_move is not None:
                peer_moves.append(direction * peer_move)
        common = float(median(peer_moves)) if peer_moves else None
        residual = move - common if move is not None and common is not None else None
        rows.append(
            HorizonDirection(
                horizon_minutes=horizon,
                move_atr=move,
                path_efficiency=efficiency,
                common_move_atr=common,
                residual_move_atr=residual,
            )
        )
        aligned_moves.append(move)
        efficiencies.append(efficiency)
        common_moves.append(common)
        residual_moves.append(residual)

    trend_parts = [
        None
        if move is None or efficiency is None
        else math.tanh(move) * (0.35 + 0.65 * efficiency)
        for move, efficiency in zip(aligned_moves, efficiencies)
    ]
    signs = [None if value is None else float(math.copysign(1.0, value)) if value else 0.0 for value in aligned_moves]
    flow_share, activity_ratio = _flow_context(target, side)
    return DirectionalContext(
        symbol=symbol,
        side=side,
        decision_time_ns=decision_time_ns,
        atr_price=atr_price,
        horizons=tuple(rows),
        trend_alignment=_weighted_available(trend_parts),
        trend_consensus=_weighted_available(signs),
        path_efficiency=_weighted_available(efficiencies),
        signed_flow_share=flow_share,
        activity_ratio=activity_ratio,
        common_component=_weighted_available(common_moves),
        symbol_residual=_weighted_available(residual_moves),
    )


def build_directional_update(
    *,
    symbol: str,
    side: str,
    prior_time_ns: int,
    decision_time_ns: int,
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    interval_minutes: int = 5,
) -> DirectionalUpdate:
    """Keep the direction carried into an event separate from its update."""

    if prior_time_ns >= decision_time_ns:
        raise ValueError("prior_time_ns must precede decision_time_ns")
    prior = build_directional_context(
        symbol=symbol,
        side=side,
        decision_time_ns=prior_time_ns,
        bars_by_symbol=bars_by_symbol,
        interval_minutes=interval_minutes,
    )
    posterior = build_directional_context(
        symbol=symbol,
        side=side,
        decision_time_ns=decision_time_ns,
        bars_by_symbol=bars_by_symbol,
        interval_minutes=interval_minutes,
    )
    return DirectionalUpdate(
        prior=prior,
        posterior=posterior,
        trend_alignment_update=_difference(prior.trend_alignment, posterior.trend_alignment),
        common_component_update=_difference(prior.common_component, posterior.common_component),
        symbol_residual_update=_difference(prior.symbol_residual, posterior.symbol_residual),
    )


@dataclass(frozen=True, slots=True)
class LiquidityRole:
    """Semantic role of a causally observed public-liquidity boundary."""

    direction_source: bool
    route_obstacle: bool
    semantic_kind: str


def boundary_role(boundary: LiquidityBoundary) -> LiquidityRole:
    """Adapt the coherent semantic ledger to the production boundary domain.

    Every boundary emitted by :mod:`market_state` is already causally confirmed,
    so even minor local structure remains a route obstacle.  Only completed
    period extremes and sufficiently structural swings are allowed to supply a
    directional prior.  This is a role distinction, not a trade admission gate.
    """

    kind = boundary.kind.upper()
    period = "PRIOR_DAY" in kind or "PREVIOUS_DAY" in kind or "PREVIOUS_WEEK" in kind
    if "REPEATED_DEFENSE" in kind:
        semantic = "CONFIRMED_REPEATED_DEFENSE_BAND"
        source = True
    elif period:
        semantic = "COMPLETED_PERIOD_EXTREME"
        source = True
    elif boundary.timeframe_minutes >= 240:
        semantic = "MAJOR_EXTERNAL_SWING"
        source = True
    elif boundary.timeframe_minutes >= 60:
        semantic = "EXTERNAL_SWING"
        source = True
    elif boundary.timeframe_minutes >= 15 and boundary.strength >= 1.20:
        semantic = "DEFENDED_INTRADAY_SWING"
        source = True
    else:
        semantic = "MINOR_ROUTE_STRUCTURE"
        source = False
    return LiquidityRole(
        direction_source=source,
        route_obstacle=True,
        semantic_kind=semantic,
    )


@dataclass(frozen=True, slots=True)
class ActiveLiquidityLevel:
    boundary_id: str
    side: str
    price: float
    distance_atr: float | None
    pull: float | None
    direction_source: bool
    route_obstacle: bool
    semantic_kind: str


@dataclass(frozen=True, slots=True)
class ActiveLiquidityContext:
    """Active public liquidity on both sides of the current price."""

    decision_time_ns: int
    price: float
    above: tuple[ActiveLiquidityLevel, ...]
    below: tuple[ActiveLiquidityLevel, ...]
    nearest_long_obstacle: ActiveLiquidityLevel | None
    nearest_short_obstacle: ActiveLiquidityLevel | None
    direction_source_balance: float | None
    two_sided_source_pull: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PreEventAuthority:
    """Categorical structure and liquidity facts frozen before interaction."""

    observed_time_ns: int
    structure_side: str | None
    structure_event_time_ns: int | None
    draw_side: str | None
    draw_balance: float | None
    source_semantic_kind: str
    source_outward_side: str
    source_was_prior_draw_destination: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _active_level(
    boundary: LiquidityBoundary,
    *,
    price: float,
    serial: int,
    atr_price: float | None,
) -> ActiveLiquidityLevel:
    role = boundary_role(boundary)
    boundary_price = boundary.price_at(serial)
    distance = abs(boundary_price - price)
    distance_atr = distance / atr_price if atr_price is not None and atr_price > EPS else None
    pull = (
        math.log1p(max(boundary.strength, 0.0)) / (0.50 + distance_atr)
        if distance_atr is not None
        else None
    )
    return ActiveLiquidityLevel(
        boundary_id=boundary.boundary_id,
        side=boundary.side,
        price=boundary_price,
        distance_atr=_finite_or_none(distance_atr),
        pull=_finite_or_none(pull),
        direction_source=role.direction_source,
        route_obstacle=role.route_obstacle,
        semantic_kind=role.semantic_kind,
    )


def build_active_liquidity_context(
    *,
    boundaries: Iterable[LiquidityBoundary],
    price: float,
    decision_time_ns: int,
    serial: int,
    atr_price: float | None,
) -> ActiveLiquidityContext:
    """Build the live two-sided map without letting obstacles create direction."""

    above: list[ActiveLiquidityLevel] = []
    below: list[ActiveLiquidityLevel] = []
    for boundary in boundaries:
        if not boundary.is_fresh(decision_time_ns):
            continue
        level = _active_level(
            boundary,
            price=price,
            serial=serial,
            atr_price=atr_price,
        )
        if level.price > price:
            above.append(level)
        elif level.price < price:
            below.append(level)
    above.sort(key=lambda item: (item.price - price, item.boundary_id))
    below.sort(key=lambda item: (price - item.price, item.boundary_id))

    long_obstacles = [item for item in above if item.route_obstacle and item.side == "HIGH"]
    short_obstacles = [item for item in below if item.route_obstacle and item.side == "LOW"]
    high_sources = [item for item in above if item.direction_source and item.pull is not None]
    low_sources = [item for item in below if item.direction_source and item.pull is not None]
    high_pull = sum(float(item.pull) for item in high_sources) if high_sources else None
    low_pull = sum(float(item.pull) for item in low_sources) if low_sources else None
    if high_pull is None and low_pull is None:
        balance = None
        two_sided = None
    elif low_pull is None:
        balance = 1.0
        two_sided = None
    elif high_pull is None:
        balance = -1.0
        two_sided = None
    else:
        balance = math.tanh(high_pull - low_pull)
        two_sided = min(high_pull, low_pull)
    return ActiveLiquidityContext(
        decision_time_ns=decision_time_ns,
        price=float(price),
        above=tuple(above),
        below=tuple(below),
        nearest_long_obstacle=long_obstacles[0] if long_obstacles else None,
        nearest_short_obstacle=short_obstacles[0] if short_obstacles else None,
        direction_source_balance=_finite_or_none(balance),
        two_sided_source_pull=_finite_or_none(two_sided),
    )


__all__ = [
    "ActiveLiquidityContext",
    "ActiveLiquidityLevel",
    "DirectionalContext",
    "DirectionalUpdate",
    "HORIZON_MINUTES",
    "HorizonDirection",
    "LiquidityRole",
    "PreEventAuthority",
    "boundary_role",
    "build_active_liquidity_context",
    "build_directional_context",
    "build_directional_update",
]
