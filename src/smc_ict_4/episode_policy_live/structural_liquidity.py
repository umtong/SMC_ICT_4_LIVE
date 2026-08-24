"""Causal structural-liquidity graph for an already-owned auction episode.

This module is synthesis, not a new alpha claim.  It ports the smallest common
structural contract from these existing research implementations:

* EasyChart v18 ``mainline_origin_stop_v18.py`` at
  ``b45f9e3f1626e940fe2cdc59dba05e3100b7d61d``;
* EasyChart v19 ``alternating_horizontal_v19.py`` at
  ``5f4e36cee3aed6d59b8dce4675f200062a145c30``;
* RE1 ``easychart_re1_phase.py``, ``easychart_re1_episode_geometry.py``,
  ``easychart_re1_mature_balance*.py``, ``easychart_re1_objectives.py`` and
  ``easychart_re1_contextual_5m_ob.py`` at
  ``97b3919e8055c8dcfac7b0cbb0819136d0611118``;
* structural auction control ``structural_auction_control_v5_market.py`` at
  ``93452747c56d1c4649cba85f700013c0912bfa5d``.

It deliberately cannot originate a trade.  Callers must supply an auction
episode and its side.  The graph then answers only structural questions:

* which version of a drawable wick trend line/channel was feasible;
* which fact is the source, a route obstacle, or a fresh destination;
* whether a two-sided balance has completed its midpoint traversal;
* which OB/FVG location was born inside the current event;
* where the complete episode is structurally invalidated; and
* whether the first destination, without substituting a farther one, offers
  at least one gross R.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import math
from typing import Iterable, Sequence

from .domain import Bar, Pivot, stable_id


class StructureRole(str, Enum):
    SOURCE = "SOURCE"
    ROUTE_OBSTACLE = "ROUTE_OBSTACLE"
    DESTINATION = "DESTINATION"


@dataclass(frozen=True, slots=True)
class StructuralNode:
    """A projected price fact with one explicit responsibility."""

    node_id: str
    symbol: str
    side: str  # HIGH / LOW
    kind: str
    role: StructureRole
    timeframe_minutes: int
    observed_time_ns: int
    lower: float
    upper: float
    anchor_serial: int
    slope_per_bar: float = 0.0
    version: int = 1
    invalidation: float | None = None
    consumed_time_ns: int | None = None
    superseded_time_ns: int | None = None

    def __post_init__(self) -> None:
        if self.side not in {"HIGH", "LOW"}:
            raise ValueError("structural node side must be HIGH or LOW")
        if self.lower > self.upper:
            raise ValueError("structural node lower cannot exceed upper")
        if self.version < 1:
            raise ValueError("structural node version must be positive")
        for value in (self.lower, self.upper, self.slope_per_bar):
            if not math.isfinite(value):
                raise ValueError("structural node prices must be finite")

    def band_at(self, serial: int) -> tuple[float, float]:
        shift = self.slope_per_bar * (serial - self.anchor_serial)
        return self.lower + shift, self.upper + shift

    def is_fresh(self, decision_time_ns: int) -> bool:
        # A boundary retired by the current completed bar was still fresh when
        # that bar opened.  It may own that first interaction exactly once and
        # disappears from the next decision onward.
        return (
            self.observed_time_ns < decision_time_ns
            and (self.consumed_time_ns is None or self.consumed_time_ns >= decision_time_ns)
            and (self.superseded_time_ns is None or self.superseded_time_ns >= decision_time_ns)
        )

    def as_role(self, role: StructureRole) -> "StructuralNode":
        return replace(self, role=role)


@dataclass(slots=True)
class TrendLineVersion:
    line_id: str
    symbol: str
    side: str
    timeframe_minutes: int
    first_pivot_id: str
    second_pivot_id: str
    first_serial: int
    second_serial: int
    first_price: float
    second_price: float
    observed_time_ns: int
    version: int
    superseded_time_ns: int | None = None
    first_interaction_time_ns: int | None = None

    @property
    def slope_per_bar(self) -> float:
        return (self.second_price - self.first_price) / (
            self.second_serial - self.first_serial
        )

    def value_at(self, serial: int) -> float:
        return self.second_price + self.slope_per_bar * (serial - self.second_serial)


@dataclass(slots=True)
class ChannelVersion:
    channel_id: str
    main_line_id: str
    symbol: str
    direction: str
    offset: float
    observed_time_ns: int
    version: int
    opposite_edge_reached_time_ns: int | None = None
    first_edge_consumed_time_ns: int | None = None
    superseded_time_ns: int | None = None
    edge_first_interaction_time_ns: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class FeasibleTrendChannelBook:
    """Wick geometry with observation-time versions and four-point phase.

    Pivots are supplied only after their caller has causally confirmed them.
    A line needs two compatible same-side pivots and no intervening wick breach.
    A channel additionally needs an already-observed opposite pivot between the
    anchors.  Its main edge (and coincident source line) remains hidden until a
    later bar reaches the opposite fourth point.
    """

    symbol: str
    timeframe_minutes: int
    tick_size: float
    bars: list[Bar] = field(default_factory=list)
    pivots: list[Pivot] = field(default_factory=list)
    lines: list[TrendLineVersion] = field(default_factory=list)
    channels: list[ChannelVersion] = field(default_factory=list)
    _bar_serial_by_close: dict[int, int] = field(default_factory=dict)
    _pivot_ids: set[str] = field(default_factory=set)
    _line_versions: dict[str, int] = field(default_factory=dict)
    _channel_versions: dict[str, int] = field(default_factory=dict)

    def observe_bar(self, bar: Bar) -> None:
        if bar.symbol != self.symbol or bar.interval_minutes != self.timeframe_minutes:
            raise ValueError("bar does not belong to this structural book")
        if self.bars and bar.close_time_ns <= self.bars[-1].close_time_ns:
            raise ValueError("structural bars must be strictly ordered")
        serial = len(self.bars)
        self.bars.append(bar)
        self._bar_serial_by_close[bar.close_time_ns] = serial
        self._advance_channel_phase(bar, serial)

    @staticmethod
    def _bar_touches_band(bar: Bar, lower: float, upper: float) -> bool:
        return bar.low <= upper and bar.high >= lower

    def _projection_serial(self, close_time_ns: int) -> float | None:
        if not self.bars:
            return None
        elapsed = close_time_ns - self.bars[-1].close_time_ns
        return len(self.bars) - 1 + elapsed / (self.timeframe_minutes * 60_000_000_000)

    def observe_price(self, bar: Bar) -> None:
        """Retire each projected line/channel edge after its first later touch.

        This is the causal lifecycle from EasyChart's
        ``LifecycleAwareStructureBook``.  The caller classifies the current
        completed bar after this update; equality-aware freshness above keeps
        the just-touched boundary available for that one interaction only.
        """

        serial = self._projection_serial(bar.close_time_ns)
        if serial is None:
            return
        for line in self.lines:
            if (
                line.first_interaction_time_ns is not None
                or line.observed_time_ns > bar.open_time_ns
                or (
                    line.superseded_time_ns is not None
                    and line.superseded_time_ns <= bar.open_time_ns
                )
            ):
                continue
            value = line.value_at(serial)
            if self._bar_touches_band(
                bar,
                value - self.tick_size,
                value + self.tick_size,
            ):
                line.first_interaction_time_ns = bar.close_time_ns

        for channel in self.channels:
            if (
                channel.observed_time_ns > bar.open_time_ns
                or (
                    channel.superseded_time_ns is not None
                    and channel.superseded_time_ns <= bar.open_time_ns
                )
            ):
                continue
            # Before point four only the opposite edge is a visible price
            # fact.  A minute touch of that edge completes the channel phase;
            # the formerly hidden main edge becomes eligible only afterwards,
            # never retroactively on the same bar.
            phase_was_complete = (
                channel.opposite_edge_reached_time_ns is not None
                and channel.opposite_edge_reached_time_ns <= bar.open_time_ns
            )
            eligible_edges = (
                ("LOWER", "UPPER")
                if phase_was_complete
                else (self._first_edge(channel),)
            )
            for edge in eligible_edges:
                first_edge = edge == self._first_edge(channel)
                prior_interaction = channel.edge_first_interaction_time_ns.get(edge)
                if prior_interaction is None and first_edge:
                    prior_interaction = channel.first_edge_consumed_time_ns
                    if prior_interaction is not None:
                        channel.edge_first_interaction_time_ns[edge] = prior_interaction
                if prior_interaction is not None:
                    continue
                value = self._edge_value(channel, edge, serial)
                if self._bar_touches_band(
                    bar,
                    value - self.tick_size,
                    value + self.tick_size,
                ):
                    channel.edge_first_interaction_time_ns[edge] = bar.close_time_ns
                    if first_edge:
                        channel.first_edge_consumed_time_ns = (
                            bar.close_time_ns
                            if channel.first_edge_consumed_time_ns is None
                            else min(
                                channel.first_edge_consumed_time_ns,
                                bar.close_time_ns,
                            )
                        )
                        if not phase_was_complete:
                            channel.opposite_edge_reached_time_ns = bar.close_time_ns

    def _pivot_serial(self, pivot: Pivot) -> int:
        try:
            return self._bar_serial_by_close[pivot.event_time_ns]
        except KeyError as exc:
            raise ValueError("pivot event bar is not present in the book") from exc

    def _line_value(self, first: Pivot, second: Pivot, serial: int) -> float:
        first_serial = self._pivot_serial(first)
        second_serial = self._pivot_serial(second)
        slope = (second.price - first.price) / (second_serial - first_serial)
        return first.price + slope * (serial - first_serial)

    def _line_respected(self, first: Pivot, second: Pivot) -> bool:
        first_serial = self._pivot_serial(first)
        second_serial = self._pivot_serial(second)
        for serial in range(first_serial, second_serial + 1):
            expected = self._line_value(first, second, serial)
            bar = self.bars[serial]
            if first.side == "LOW" and bar.low < expected - self.tick_size:
                return False
            if first.side == "HIGH" and bar.high > expected + self.tick_size:
                return False
        return True

    def _compatible_previous(self, pivot: Pivot) -> Pivot | None:
        current_serial = self._pivot_serial(pivot)
        candidates = [
            item
            for item in self.pivots
            if item.side == pivot.side
            and self._pivot_serial(item) < current_serial
            and (
                (pivot.side == "LOW" and item.price < pivot.price)
                or (pivot.side == "HIGH" and item.price > pivot.price)
            )
        ]
        for item in sorted(candidates, key=self._pivot_serial, reverse=True):
            if self._line_respected(item, pivot):
                return item
        return None

    def _opposite_between(self, first: Pivot, second: Pivot) -> Pivot | None:
        wanted = "HIGH" if first.side == "LOW" else "LOW"
        lo = self._pivot_serial(first)
        hi = self._pivot_serial(second)
        candidates = [
            item
            for item in self.pivots
            if item.side == wanted
            and lo < self._pivot_serial(item) < hi
            and item.observed_time_ns <= second.observed_time_ns
        ]
        return max(
            candidates,
            key=lambda item: (item.strength, self._pivot_serial(item), item.pivot_id),
            default=None,
        )

    def _channel_respected(
        self,
        line: TrendLineVersion,
        direction: str,
        offset: float,
    ) -> bool:
        for serial in range(line.first_serial, line.second_serial + 1):
            main = line.value_at(serial)
            lower = main if direction == "ASCENDING" else main - offset
            upper = main + offset if direction == "ASCENDING" else main
            bar = self.bars[serial]
            if bar.low < lower - self.tick_size or bar.high > upper + self.tick_size:
                return False
        return True

    def observe_pivot(self, pivot: Pivot) -> tuple[TrendLineVersion | None, ChannelVersion | None]:
        if pivot.symbol != self.symbol or pivot.timeframe_minutes != self.timeframe_minutes:
            raise ValueError("pivot does not belong to this structural book")
        if pivot.pivot_id in self._pivot_ids:
            return None, None
        if not self.bars or pivot.observed_time_ns > self.bars[-1].close_time_ns:
            raise ValueError("pivot is not observable yet")
        self._pivot_serial(pivot)
        first = self._compatible_previous(pivot)
        self._pivot_ids.add(pivot.pivot_id)
        self.pivots.append(pivot)
        if first is None:
            return None, None

        family = f"{pivot.side}:{self.timeframe_minutes}"
        version = self._line_versions.get(family, 0) + 1
        self._line_versions[family] = version
        line_id = stable_id(first.pivot_id, pivot.pivot_id, version, prefix="STRUCT_LINE:")
        line = TrendLineVersion(
            line_id=line_id,
            symbol=self.symbol,
            side=pivot.side,
            timeframe_minutes=self.timeframe_minutes,
            first_pivot_id=first.pivot_id,
            second_pivot_id=pivot.pivot_id,
            first_serial=self._pivot_serial(first),
            second_serial=self._pivot_serial(pivot),
            first_price=first.price,
            second_price=pivot.price,
            observed_time_ns=max(first.observed_time_ns, pivot.observed_time_ns),
            version=version,
        )
        superseded_line_ids: set[str] = set()
        for old in self.lines:
            if old.side == line.side and old.second_pivot_id == first.pivot_id:
                old.superseded_time_ns = (
                    line.observed_time_ns
                    if old.superseded_time_ns is None
                    else min(old.superseded_time_ns, line.observed_time_ns)
                )
                superseded_line_ids.add(old.line_id)
        # A channel cannot outlive the chained main line which defined it.
        # Supersession is therefore caused by the successor line itself, even
        # when that successor does not also form a valid new channel.
        for old_channel in self.channels:
            if old_channel.main_line_id in superseded_line_ids:
                old_channel.superseded_time_ns = (
                    line.observed_time_ns
                    if old_channel.superseded_time_ns is None
                    else min(
                        old_channel.superseded_time_ns,
                        line.observed_time_ns,
                    )
                )
        self.lines.append(line)

        opposite = self._opposite_between(first, pivot)
        if opposite is None:
            return line, None
        base = line.value_at(self._pivot_serial(opposite))
        offset = opposite.price - base if pivot.side == "LOW" else base - opposite.price
        if offset <= 2.0 * self.tick_size:
            return line, None
        direction = "ASCENDING" if pivot.side == "LOW" else "DESCENDING"
        if not self._channel_respected(line, direction, offset):
            return line, None
        channel_family = f"{direction}:{self.timeframe_minutes}"
        channel_version = self._channel_versions.get(channel_family, 0) + 1
        self._channel_versions[channel_family] = channel_version
        channel = ChannelVersion(
            channel_id=stable_id(
                first.pivot_id,
                opposite.pivot_id,
                pivot.pivot_id,
                channel_version,
                prefix="STRUCT_CHANNEL:",
            ),
            main_line_id=line.line_id,
            symbol=self.symbol,
            direction=direction,
            offset=offset,
            observed_time_ns=max(line.observed_time_ns, opposite.observed_time_ns),
            version=channel_version,
        )
        for old in self.channels:
            old_line = self._line(old.main_line_id)
            if (
                old.direction == direction
                and old_line is not None
                and old_line.second_pivot_id == first.pivot_id
            ):
                old.superseded_time_ns = (
                    channel.observed_time_ns
                    if old.superseded_time_ns is None
                    else min(old.superseded_time_ns, channel.observed_time_ns)
                )
        self.channels.append(channel)
        return line, channel

    def _line(self, line_id: str) -> TrendLineVersion | None:
        return next((item for item in self.lines if item.line_id == line_id), None)

    @staticmethod
    def _first_edge(channel: ChannelVersion) -> str:
        return "UPPER" if channel.direction == "ASCENDING" else "LOWER"

    @staticmethod
    def _main_edge(channel: ChannelVersion) -> str:
        return "LOWER" if channel.direction == "ASCENDING" else "UPPER"

    def _edge_value(self, channel: ChannelVersion, edge: str, serial: int) -> float:
        line = self._line(channel.main_line_id)
        if line is None:
            raise RuntimeError("channel lost its main line")
        main = line.value_at(serial)
        if channel.direction == "ASCENDING":
            return main if edge == "LOWER" else main + channel.offset
        return main if edge == "UPPER" else main - channel.offset

    def _advance_channel_phase(self, bar: Bar, serial: int) -> None:
        for channel in self.channels:
            if (
                channel.opposite_edge_reached_time_ns is not None
                or channel.superseded_time_ns is not None
                or bar.close_time_ns <= channel.observed_time_ns
            ):
                continue
            value = self._edge_value(channel, self._first_edge(channel), serial)
            if bar.low <= value + self.tick_size and bar.high >= value - self.tick_size:
                channel.opposite_edge_reached_time_ns = bar.close_time_ns
                edge = self._first_edge(channel)
                channel.edge_first_interaction_time_ns.setdefault(
                    edge,
                    bar.close_time_ns,
                )
                channel.first_edge_consumed_time_ns = (
                    bar.close_time_ns
                    if channel.first_edge_consumed_time_ns is None
                    else min(channel.first_edge_consumed_time_ns, bar.close_time_ns)
                )

    def projected_nodes(self, decision_time_ns: int, serial: int) -> list[StructuralNode]:
        """Return only versions and channel phases available before decision."""

        output: list[StructuralNode] = []
        active_channels = [
            item
            for item in self.channels
            if item.observed_time_ns < decision_time_ns
            and (item.superseded_time_ns is None or item.superseded_time_ns >= decision_time_ns)
            and (
                (main_line := self._line(item.main_line_id)) is not None
                and (
                    main_line.superseded_time_ns is None
                    or main_line.superseded_time_ns >= decision_time_ns
                )
            )
        ]
        channel_line_ids = {item.main_line_id for item in active_channels}
        for line in self.lines:
            if (
                line.observed_time_ns >= decision_time_ns
                or (line.superseded_time_ns is not None and line.superseded_time_ns < decision_time_ns)
                or (
                    line.first_interaction_time_ns is not None
                    and line.first_interaction_time_ns < decision_time_ns
                )
            ):
                continue
            channel = next((item for item in active_channels if item.main_line_id == line.line_id), None)
            if channel is not None:
                # RE1 phase: the channel main edge and its trend-line alias are
                # one price fact.  The channel edge is the canonical node; it is
                # hidden before point four and replaces the duplicate line after.
                continue
            value = line.value_at(serial)
            output.append(
                StructuralNode(
                    node_id=line.line_id,
                    symbol=self.symbol,
                    side=line.side,
                    kind="UPTREND_LINE" if line.side == "LOW" else "DOWNTREND_LINE",
                    role=StructureRole.ROUTE_OBSTACLE,
                    timeframe_minutes=self.timeframe_minutes,
                    observed_time_ns=line.observed_time_ns,
                    lower=value - self.tick_size,
                    upper=value + self.tick_size,
                    anchor_serial=serial,
                    slope_per_bar=line.slope_per_bar,
                    version=line.version,
                    invalidation=(
                        value - 2.0 * self.tick_size
                        if line.side == "LOW"
                        else value + 2.0 * self.tick_size
                    ),
                    consumed_time_ns=line.first_interaction_time_ns,
                    superseded_time_ns=line.superseded_time_ns,
                )
            )
        for channel in active_channels:
            for edge in ("LOWER", "UPPER"):
                is_main = edge == self._main_edge(channel)
                if is_main and (
                    channel.opposite_edge_reached_time_ns is None
                    or channel.opposite_edge_reached_time_ns >= decision_time_ns
                ):
                    continue
                first_interaction = channel.edge_first_interaction_time_ns.get(edge)
                if first_interaction is None and edge == self._first_edge(channel):
                    first_interaction = channel.first_edge_consumed_time_ns
                if (
                    first_interaction is not None
                    and first_interaction < decision_time_ns
                ):
                    continue
                value = self._edge_value(channel, edge, serial)
                side = "LOW" if edge == "LOWER" else "HIGH"
                output.append(
                    StructuralNode(
                        node_id=f"{channel.channel_id}:{edge}",
                        symbol=self.symbol,
                        side=side,
                        kind=f"{channel.direction}_CHANNEL_{edge}",
                        role=StructureRole.ROUTE_OBSTACLE,
                        timeframe_minutes=self.timeframe_minutes,
                        observed_time_ns=channel.observed_time_ns,
                        lower=value - self.tick_size,
                        upper=value + self.tick_size,
                        anchor_serial=serial,
                        slope_per_bar=(self._line(channel.main_line_id) or self.lines[-1]).slope_per_bar,
                        version=channel.version,
                        invalidation=value - 2.0 * self.tick_size if side == "LOW" else value + 2.0 * self.tick_size,
                        consumed_time_ns=(
                            first_interaction
                        ),
                        superseded_time_ns=channel.superseded_time_ns,
                    )
                )
        return output

    def known_node_ids(self, decision_time_ns: int) -> set[str]:
        """Return non-superseded identities, including a retired episode source."""

        output = {
            line.line_id
            for line in self.lines
            if line.observed_time_ns < decision_time_ns
            and (
                line.superseded_time_ns is None
                or line.superseded_time_ns >= decision_time_ns
            )
        }
        for channel in self.channels:
            main_line = self._line(channel.main_line_id)
            if (
                channel.observed_time_ns < decision_time_ns
                and (
                    channel.superseded_time_ns is None
                    or channel.superseded_time_ns >= decision_time_ns
                )
                and main_line is not None
                and (
                    main_line.superseded_time_ns is None
                    or main_line.superseded_time_ns >= decision_time_ns
                )
            ):
                output.update(
                    {
                        f"{channel.channel_id}:LOWER",
                        f"{channel.channel_id}:UPPER",
                    }
                )
        return output


@dataclass(frozen=True, slots=True)
class DefenseBand:
    defense_id: str
    side: str  # SUPPORT / RESISTANCE
    lower: float
    upper: float
    observed_time_ns: int
    repeated_defenses: int

    def __post_init__(self) -> None:
        if self.side not in {"SUPPORT", "RESISTANCE"}:
            raise ValueError("defense side must be SUPPORT or RESISTANCE")
        if self.lower >= self.upper:
            raise ValueError("defense band must have positive width")


@dataclass(slots=True)
class MatureBalance:
    balance_id: str
    support: DefenseBand
    resistance: DefenseBand
    activation_time_ns: int
    later_side: str
    midpoint: float
    mature_time_ns: int | None = None
    retired_time_ns: int | None = None

    @property
    def lower(self) -> float:
        return self.support.upper

    @property
    def upper(self) -> float:
        return self.resistance.lower


@dataclass(frozen=True, slots=True)
class BalanceSweep:
    balance_id: str
    side: str
    event_time_ns: int
    event_extreme: float
    target: float


@dataclass(slots=True)
class MatureBalanceTracker:
    """One canonical box; two defenses plus later midpoint traversal."""

    symbol: str
    supports: list[DefenseBand] = field(default_factory=list)
    resistances: list[DefenseBand] = field(default_factory=list)
    active: MatureBalance | None = None
    claimed_defense_ids: set[str] = field(default_factory=set)

    def register_defense(self, defense: DefenseBand) -> None:
        if defense.repeated_defenses < 2:
            return
        collection = self.supports if defense.side == "SUPPORT" else self.resistances
        if defense.defense_id not in {item.defense_id for item in collection}:
            collection.append(defense)

    def _activate(self, bar: Bar) -> None:
        if self.active is not None:
            return
        pairs: list[tuple[DefenseBand, DefenseBand]] = []
        for support in self.supports:
            for resistance in self.resistances:
                if (
                    support.defense_id in self.claimed_defense_ids
                    or resistance.defense_id in self.claimed_defense_ids
                    or support.upper >= resistance.lower
                    or not support.upper < bar.close < resistance.lower
                    or max(support.observed_time_ns, resistance.observed_time_ns) > bar.close_time_ns
                ):
                    continue
                pairs.append((support, resistance))
        if not pairs:
            return
        support, resistance = max(
            pairs,
            key=lambda pair: (
                max(pair[0].observed_time_ns, pair[1].observed_time_ns),
                min(pair[0].observed_time_ns, pair[1].observed_time_ns),
                -(pair[1].lower - pair[0].upper),
                pair[0].defense_id,
                pair[1].defense_id,
            ),
        )
        self.active = MatureBalance(
            balance_id=stable_id(support.defense_id, resistance.defense_id, prefix="BALANCE:"),
            support=support,
            resistance=resistance,
            activation_time_ns=bar.close_time_ns,
            later_side=(
                support.side
                if support.observed_time_ns > resistance.observed_time_ns
                else resistance.side
            ),
            midpoint=(support.upper + resistance.lower) / 2.0,
        )

    def _retire(self, box: MatureBalance, time_ns: int) -> None:
        box.retired_time_ns = time_ns
        self.claimed_defense_ids.update((box.support.defense_id, box.resistance.defense_id))
        self.active = None

    def observe_bar(self, bar: Bar) -> BalanceSweep | None:
        if bar.symbol != self.symbol:
            raise ValueError("balance bar belongs to a different symbol")
        self._activate(bar)
        box = self.active
        if box is None or bar.close_time_ns <= box.activation_time_ns:
            return None
        inside = box.lower < bar.close < box.upper
        if box.mature_time_ns is None:
            if not inside:
                self._retire(box, bar.close_time_ns)
                return None
            traversed = (
                bar.close > box.midpoint
                if box.later_side == "SUPPORT"
                else bar.close < box.midpoint
            )
            if traversed:
                box.mature_time_ns = bar.close_time_ns
            return None
        if not inside:
            self._retire(box, bar.close_time_ns)
            return None
        support_sweep = bar.low < box.support.lower
        resistance_sweep = bar.high > box.resistance.upper
        if support_sweep == resistance_sweep:
            if support_sweep:
                self._retire(box, bar.close_time_ns)
            return None
        if support_sweep:
            if bar.high >= box.resistance.lower:
                self._retire(box, bar.close_time_ns)
                return None
            result = BalanceSweep(box.balance_id, "LONG", bar.close_time_ns, bar.low, box.resistance.lower)
        else:
            if bar.low <= box.support.upper:
                self._retire(box, bar.close_time_ns)
                return None
            result = BalanceSweep(box.balance_id, "SHORT", bar.close_time_ns, bar.high, box.support.upper)
        self._retire(box, bar.close_time_ns)
        return result


@dataclass(frozen=True, slots=True)
class EventLocation:
    location_id: str
    kind: str
    side: str
    lower: float
    upper: float
    invalidation: float
    source_time_ns: int
    observed_time_ns: int


def _overlaps(bar: Bar, lower: float, upper: float) -> bool:
    return bar.low <= upper and bar.high >= lower


def event_local_locations(
    bars: Sequence[Bar],
    *,
    side: str,
    event_start_time_ns: int,
    decision_time_ns: int,
    source_lower: float,
    source_upper: float,
    tick_size: float,
) -> list[EventLocation]:
    """Return OB/FVG entry locations born inside one structural event.

    These are locations only.  The first formation candle must touch the
    pre-existing structural source, and the completed formation must be known
    by ``decision_time_ns``.  A location therefore cannot create an episode.
    """

    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    local = [
        bar
        for bar in bars
        if event_start_time_ns <= bar.open_time_ns
        and bar.close_time_ns <= decision_time_ns
    ]
    output: list[EventLocation] = []
    for source, impulse in zip(local, local[1:]):
        touches = _overlaps(source, source_lower, source_upper)
        if side == "LONG":
            valid = source.close < source.open and impulse.close > source.high and impulse.close > impulse.open
            lower, upper = min(source.open, source.close), source.open
            invalidation = source.low - tick_size
        else:
            valid = source.close > source.open and impulse.close < source.low and impulse.close < impulse.open
            lower, upper = source.open, max(source.open, source.close)
            invalidation = source.high + tick_size
        if touches and valid and lower < upper:
            output.append(
                EventLocation(
                    stable_id("OB", source.open_time_ns, impulse.close_time_ns, side, prefix="LOC:"),
                    "ORDER_BLOCK",
                    side,
                    lower,
                    upper,
                    invalidation,
                    source.open_time_ns,
                    impulse.close_time_ns,
                )
            )
    for first, middle, third in zip(local, local[1:], local[2:]):
        if not _overlaps(first, source_lower, source_upper):
            continue
        if side == "LONG" and third.low > first.high and middle.close > middle.open:
            lower, upper, invalidation = first.high, third.low, first.low - tick_size
        elif side == "SHORT" and third.high < first.low and middle.close < middle.open:
            lower, upper, invalidation = third.high, first.low, first.high + tick_size
        else:
            continue
        output.append(
            EventLocation(
                stable_id("FVG", first.open_time_ns, third.close_time_ns, side, prefix="LOC:"),
                "FAIR_VALUE_GAP",
                side,
                lower,
                upper,
                invalidation,
                first.open_time_ns,
                third.close_time_ns,
            )
        )
    return sorted(output, key=lambda item: (item.observed_time_ns, item.kind, item.location_id))


def structural_stop(
    *,
    side: str,
    micro_stop: float,
    event_extreme: float,
    tick_size: float,
    source_invalidation: float | None = None,
    location_invalidation: float | None = None,
    acceptance_origin: float | None = None,
    adverse_noise: float | None = None,
) -> float:
    """Keep entry precision separate from complete-episode invalidation."""

    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    candidates = [micro_stop]
    candidates.append(event_extreme - tick_size if side == "LONG" else event_extreme + tick_size)
    candidates.extend(
        value
        for value in (source_invalidation, location_invalidation)
        if value is not None
    )
    if acceptance_origin is not None:
        # EasyChart v18: a main-line accepted reversal is falsified beyond the
        # breakout-wave origin, not at the narrow retest candle.
        candidates.append(
            acceptance_origin - tick_size if side == "LONG" else acceptance_origin + tick_size
        )
    if not all(math.isfinite(value) for value in candidates):
        raise ValueError("stop inputs must be finite")
    stop = min(candidates) if side == "LONG" else max(candidates)
    if adverse_noise is not None:
        if not math.isfinite(adverse_noise) or adverse_noise < 0.0:
            raise ValueError("adverse_noise must be finite and non-negative")
        # Every structural reference above already carries one adverse tick.
        # Expand only the remaining ordinary-wick allowance so the resulting
        # stop is reference +/- max(two ticks, causal median prior wick), as in
        # the liquidity-auction v1 invalidation geometry.
        expansion = max(0.0, adverse_noise - tick_size)
        stop = stop - expansion if side == "LONG" else stop + expansion
    return stop


@dataclass(frozen=True, slots=True)
class DestinationDecision:
    accepted: bool
    reason: str
    source: StructuralNode
    destination: StructuralNode | None
    route_obstacle: StructuralNode | None
    entry: float
    stop: float
    target: float | None
    gross_rr: float | None


def _path_price(node: StructuralNode, side: str, serial: int) -> float:
    lower, upper = node.band_at(serial)
    return lower if side == "LONG" else upper


def destination_first_geometry(
    *,
    side: str,
    source: StructuralNode,
    nodes: Iterable[StructuralNode],
    entry: float,
    stop: float,
    decision_time_ns: int,
    serial: int,
    minimum_gross_rr: float = 1.0,
) -> DestinationDecision:
    """Choose the nearest fresh destination, then test route and one-R.

    A sub-one-R first destination rejects the plan.  It is never replaced by a
    farther advertised objective.  Likewise, a distinct route obstacle before
    that destination makes the proposed route infeasible rather than silently
    relabelling the obstacle as profit target.
    """

    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    valid_geometry = stop < entry if side == "LONG" else stop > entry
    if not valid_geometry:
        return DestinationDecision(False, "INVALID_STRUCTURAL_STOP", source, None, None, entry, stop, None, None)
    wanted = "HIGH" if side == "LONG" else "LOW"
    available = [
        node
        for node in nodes
        if node.node_id != source.node_id
        and node.is_fresh(decision_time_ns)
        and (
            (_path_price(node, side, serial) > entry) if side == "LONG"
            else (_path_price(node, side, serial) < entry)
        )
    ]
    destinations = [
        node for node in available if node.role is StructureRole.DESTINATION and node.side == wanted
    ]
    if not destinations:
        return DestinationDecision(False, "NO_FRESH_DESTINATION", source, None, None, entry, stop, None, None)
    chooser = min if side == "LONG" else max
    destination = chooser(destinations, key=lambda node: _path_price(node, side, serial))
    target = _path_price(destination, side, serial)
    obstacles = [node for node in available if node.role is StructureRole.ROUTE_OBSTACLE]
    obstacle = (
        chooser(obstacles, key=lambda node: _path_price(node, side, serial))
        if obstacles
        else None
    )
    if obstacle is not None:
        obstacle_price = _path_price(obstacle, side, serial)
        blocked = obstacle_price < target if side == "LONG" else obstacle_price > target
        if blocked:
            return DestinationDecision(
                False,
                "ROUTE_OBSTACLE_BEFORE_DESTINATION",
                source,
                destination,
                obstacle,
                entry,
                stop,
                target,
                None,
            )
    risk = abs(entry - stop)
    reward = abs(target - entry)
    gross_rr = reward / risk
    if gross_rr + 1e-12 < minimum_gross_rr:
        return DestinationDecision(
            False,
            "FIRST_DESTINATION_BELOW_MINIMUM_R",
            source,
            destination,
            obstacle,
            entry,
            stop,
            target,
            gross_rr,
        )
    return DestinationDecision(
        True,
        "DESTINATION_ROUTE_FEASIBLE",
        source,
        destination,
        obstacle,
        entry,
        stop,
        target,
        gross_rr,
    )
