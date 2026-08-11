"""Source-faithful structural episode engine for candidate-easychart v4.

The PDFs make the hierarchy explicit: trendlines/channels define the market
structure, Fake out/Trap defines the interaction with the liquidity accumulated
around that structure, and OB/FVG identify the sponsored response and its
mitigation zone.  This module encodes that sequence instead of treating every
local pivot and every engulfing candle as an independent trade.

All state transitions are causal.  A channel is usable only after its third
pivot has been confirmed, an interaction is known only after the reclaiming bar
closes, and an OB is executable only after a subsequent structural displacement
(BOS, optionally accompanied by a strict source-defined FVG).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from domain_v3 import (
    ArmedSetup,
    Candle,
    EasyChartOrderBlock,
    Side,
    TargetMode,
    detect_easychart_order_block,
)
from market_v3 import PivotConfirmation


@dataclass(frozen=True, slots=True)
class StructuralPivot:
    center_index: int
    observed_index: int
    side: str
    level: float
    event_time_ns: int
    observed_time_ns: int


@dataclass(frozen=True, slots=True)
class ParallelChannel:
    channel_id: str
    observed_time_ns: int
    timeframe_minutes: int
    anchor_side: str
    expected_side: Side
    base_time_ns: int
    base_level: float
    slope_per_ns: float
    width: float
    p1: StructuralPivot
    p2: StructuralPivot
    p3: StructuralPivot

    def anchor(self, time_ns: int) -> float:
        return self.base_level + self.slope_per_ns * (time_ns - self.base_time_ns)

    def lower(self, time_ns: int) -> float:
        value = self.anchor(time_ns)
        return value if self.anchor_side == "LOW" else value - self.width

    def upper(self, time_ns: int) -> float:
        value = self.anchor(time_ns)
        return value + self.width if self.anchor_side == "LOW" else value

    def entry_boundary(self, time_ns: int) -> float:
        return self.lower(time_ns) if self.expected_side is Side.LONG else self.upper(time_ns)

    def opposite_boundary(self, time_ns: int) -> float:
        return self.upper(time_ns) if self.expected_side is Side.LONG else self.lower(time_ns)


@dataclass(slots=True)
class OutsideExcursion:
    channel: ParallelChannel
    first_index: int
    extreme: float


@dataclass(slots=True)
class SponsoredEpisode:
    episode_id: str
    channel: ParallelChannel
    family_prefix: str
    side: Side
    interaction_index: int
    interaction_time_ns: int
    interaction_extreme: float
    bos_level: float
    origin_ob: EasyChartOrderBlock | None = None
    origin_ob_index: int | None = None
    bos_confirmed: bool = False
    fvg_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class ScenarioConfigV4:
    channel_timeframe_minutes: int = 15
    min_body_ratio: float = 2.0
    min_previous_body_atr: float = 0.10
    strict_fvg_body_ratio: float = 2.0
    require_bos: bool = True
    require_fvg: bool = False
    enable_immediate_fakeout: bool = True
    enable_one_bar_trap: bool = True
    enable_boundary_touch: bool = False
    tick_size: float = 0.1


def strict_fvg_side(
    candles: list[Candle],
    index: int,
    *,
    minimum_middle_body_ratio: float = 2.0,
) -> Side | None:
    """Return a strict source-defined FVG direction at ``index``.

    The first and third wick ranges must not overlap, the middle candle must
    point in the FVG direction, and its body must be at least the source-stated
    multiple of both neighbouring bodies.
    """
    if index < 2:
        return None
    first, middle, third = candles[index - 2], candles[index - 1], candles[index]
    neighbour = max(first.body, third.body)
    if middle.body + 1e-12 < minimum_middle_body_ratio * neighbour:
        return None
    if middle.bullish and first.high < third.low:
        return Side.LONG
    if middle.bearish and first.low > third.high:
        return Side.SHORT
    return None


class EasyChartStructuralEpisodeEngine:
    """One-symbol causal channel -> interaction -> sponsorship state machine."""

    def __init__(self, symbol: str, config: ScenarioConfigV4) -> None:
        self.symbol = symbol
        self.config = config
        self.structure_pivots: list[StructuralPivot] = []
        self.micro_high: StructuralPivot | None = None
        self.micro_low: StructuralPivot | None = None
        self.active_channel: ParallelChannel | None = None
        self.outside: OutsideExcursion | None = None
        self.episodes: list[SponsoredEpisode] = []
        self.true_ranges: list[float] = []
        self.sequence = 0
        self.used_channel_ids: set[str] = set()
        self.diagnostics: dict[str, int] = {}

    def _count(self, key: str, amount: int = 1) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + amount

    @staticmethod
    def _structural_pivot(
        pivot: PivotConfirmation,
        candles: list[Candle],
    ) -> StructuralPivot:
        center = candles[pivot.center_index]
        observed = candles[pivot.observed_index]
        return StructuralPivot(
            center_index=pivot.center_index,
            observed_index=pivot.observed_index,
            side=pivot.side,
            level=float(pivot.level),
            event_time_ns=center.ts_close_ns,
            observed_time_ns=observed.ts_close_ns,
        )

    def add_micro_pivot(self, pivot: PivotConfirmation, candles: list[Candle]) -> None:
        item = self._structural_pivot(pivot, candles)
        if item.side == "HIGH":
            self.micro_high = item
        else:
            self.micro_low = item
        self._count(f"micro_pivots_{item.side.lower()}")

    def add_channel_pivot(self, pivot: PivotConfirmation, candles: list[Candle]) -> None:
        item = self._structural_pivot(pivot, candles)
        self.structure_pivots.append(item)
        self.structure_pivots = self.structure_pivots[-12:]
        self._count(f"channel_pivots_{item.side.lower()}")
        channel = self._latest_channel(candles, item)
        if channel is None:
            return
        if channel.channel_id in self.used_channel_ids:
            return
        self.active_channel = channel
        self.outside = None
        self.used_channel_ids.add(channel.channel_id)
        self._count("channels_formed")
        self._count(f"channels_expect_{channel.expected_side.name.lower()}")

    def _latest_channel(
        self,
        candles: list[Candle],
        p3: StructuralPivot,
    ) -> ParallelChannel | None:
        # Use the most recent alternating A-B-A swing sequence.  This models
        # the source's three visible points without searching all point sets.
        p2 = next((p for p in reversed(self.structure_pivots[:-1]) if p.side != p3.side), None)
        if p2 is None:
            return None
        p1 = next(
            (
                p
                for p in reversed(self.structure_pivots)
                if p.center_index < p2.center_index and p.side == p3.side
            ),
            None,
        )
        if p1 is None or not (p1.center_index < p2.center_index < p3.center_index):
            return None
        elapsed = p3.event_time_ns - p1.event_time_ns
        if elapsed <= 0:
            return None
        slope = (p3.level - p1.level) / elapsed
        at_p2 = p1.level + slope * (p2.event_time_ns - p1.event_time_ns)
        if p3.side == "LOW":
            width = p2.level - at_p2
            expected = Side.SHORT
        else:
            width = at_p2 - p2.level
            expected = Side.LONG
        if width <= self.config.tick_size:
            self._count("channel_rejected_nonpositive_width")
            return None

        epsilon = max(self.config.tick_size, width * 1e-12)
        for bar in candles[p1.center_index : p3.center_index + 1]:
            anchor = p1.level + slope * (bar.ts_close_ns - p1.event_time_ns)
            lower = anchor if p3.side == "LOW" else anchor - width
            upper = anchor + width if p3.side == "LOW" else anchor
            # Wicks define the anchors, but a prior body close outside means
            # the alleged channel was not the accepted auction range.
            if bar.close < lower - epsilon or bar.close > upper + epsilon:
                self._count("channel_rejected_close_outside")
                return None

        channel_id = (
            f"{self.symbol}:{self.config.channel_timeframe_minutes}m:"
            f"{p1.side}:{p1.event_time_ns}:{p2.event_time_ns}:{p3.event_time_ns}"
        )
        return ParallelChannel(
            channel_id=channel_id,
            observed_time_ns=p3.observed_time_ns,
            timeframe_minutes=self.config.channel_timeframe_minutes,
            anchor_side=p3.side,
            expected_side=expected,
            base_time_ns=p1.event_time_ns,
            base_level=p1.level,
            slope_per_ns=slope,
            width=width,
            p1=p1,
            p2=p2,
            p3=p3,
        )

    def _atr(self) -> float | None:
        if len(self.true_ranges) < 14:
            return None
        return sum(self.true_ranges[-14:]) / 14.0

    def _ob_quality(self, ob: EasyChartOrderBlock) -> bool:
        if ob.body_ratio + 1e-12 < self.config.min_body_ratio:
            self._count("ob_ratio_too_small")
            return False
        atr = self._atr()
        if (
            atr is not None
            and atr > 0.0
            and self.config.min_previous_body_atr > 0.0
            and ob.previous_body / atr < self.config.min_previous_body_atr
        ):
            self._count("ob_previous_body_doji_like")
            return False
        return True

    def _new_id(self, prefix: str) -> str:
        self.sequence += 1
        return f"ec4-{self.symbol}-{prefix}-{self.sequence:08d}"

    def _interaction(
        self,
        channel: ParallelChannel,
        current: Candle,
        index: int,
        family_prefix: str,
        extreme: float,
    ) -> None:
        side = channel.expected_side
        bos = self.micro_high if side is Side.LONG else self.micro_low
        if bos is None or bos.observed_time_ns >= current.ts_close_ns:
            self._count("interaction_rejected_no_prior_bos_level")
            self.active_channel = None
            self.outside = None
            return
        episode = SponsoredEpisode(
            episode_id=self._new_id("episode"),
            channel=channel,
            family_prefix=family_prefix,
            side=side,
            interaction_index=index,
            interaction_time_ns=current.ts_close_ns,
            interaction_extreme=extreme,
            bos_level=bos.level,
        )
        self.episodes.append(episode)
        self.active_channel = None
        self.outside = None
        self._count("interactions")
        self._count(f"interactions_{family_prefix.lower()}")

    def _observe_channel_interaction(self, current: Candle, index: int) -> None:
        channel = self.active_channel
        if channel is None or channel.observed_time_ns >= current.ts_open_ns:
            return
        boundary = channel.entry_boundary(current.ts_close_ns)
        if channel.expected_side is Side.LONG:
            touched = current.low <= boundary
            crossed = current.low < boundary
            inside = current.close >= boundary
            extreme = current.low
            remains_outside = current.close < boundary
        else:
            touched = current.high >= boundary
            crossed = current.high > boundary
            inside = current.close <= boundary
            extreme = current.high
            remains_outside = current.close > boundary

        if self.outside is not None and self.outside.channel.channel_id == channel.channel_id:
            if channel.expected_side is Side.LONG:
                self.outside.extreme = min(self.outside.extreme, current.low)
            else:
                self.outside.extreme = max(self.outside.extreme, current.high)
            if inside and self.config.enable_one_bar_trap:
                self._interaction(
                    channel,
                    current,
                    index,
                    "CHANNEL_POINT4_TRAP_RECLAIM",
                    self.outside.extreme,
                )
                return
            # The source's objective breakout confirmation is an outside body
            # close followed by another candle opening and closing outside.
            if remains_outside:
                opened_outside = (
                    current.open < boundary
                    if channel.expected_side is Side.LONG
                    else current.open > boundary
                )
                if opened_outside and index > self.outside.first_index:
                    self._count("channel_accepted_break")
                    self.active_channel = None
                    self.outside = None
            return

        if crossed and inside and self.config.enable_immediate_fakeout:
            self._interaction(
                channel,
                current,
                index,
                "CHANNEL_POINT4_FAKEOUT",
                extreme,
            )
            return
        if touched and inside and self.config.enable_boundary_touch:
            self._interaction(
                channel,
                current,
                index,
                "CHANNEL_POINT4_TOUCH",
                extreme,
            )
            return
        if crossed and remains_outside:
            self.outside = OutsideExcursion(channel=channel, first_index=index, extreme=extreme)
            self._count("channel_outside_close")

    def _episode_stop(self, episode: SponsoredEpisode, ob: EasyChartOrderBlock) -> float:
        if episode.side is Side.LONG:
            return min(episode.interaction_extreme, ob.formation_low) - self.config.tick_size
        return max(episode.interaction_extreme, ob.formation_high) + self.config.tick_size

    @staticmethod
    def _bar_touches_ob(current: Candle, ob: EasyChartOrderBlock) -> bool:
        return current.low <= ob.zone_high and current.high >= ob.zone_low

    def _advance_episode(
        self,
        episode: SponsoredEpisode,
        candles: list[Candle],
        index: int,
        ob: EasyChartOrderBlock | None,
        fvg_side: Side | None,
    ) -> tuple[ArmedSetup | None, bool]:
        current = candles[index]
        side = episode.side

        if index > episode.interaction_index:
            if side is Side.LONG and current.low < episode.interaction_extreme:
                self._count("episode_invalidated_sweep_extreme")
                return None, True
            if side is Side.SHORT and current.high > episode.interaction_extreme:
                self._count("episode_invalidated_sweep_extreme")
                return None, True

        # A matching OB may be formed on the interaction bar itself.  Before
        # BOS, a later matching OB supersedes an earlier unsponsored one; this
        # selects the origin immediately preceding the actual displacement.
        if not episode.bos_confirmed and ob is not None and ob.side is side and self._ob_quality(ob):
            episode.origin_ob = ob
            episode.origin_ob_index = index
            self._count("episode_origin_ob")

        origin = episode.origin_ob
        if origin is not None and episode.origin_ob_index is not None:
            if index > episode.origin_ob_index and self._bar_touches_ob(current, origin):
                # Mitigation before sponsorship means this OB cannot be the
                # unfilled origin of the later displacement.
                if not episode.bos_confirmed:
                    episode.origin_ob = None
                    episode.origin_ob_index = None
                    self._count("ob_mitigated_before_bos")
                    origin = None
                else:
                    self._count("ob_mitigated_before_fvg")
                    return None, True

        if origin is not None and fvg_side is side and index >= (episode.origin_ob_index or index):
            episode.fvg_confirmed = True

        bos_now = (
            current.close > episode.bos_level
            if side is Side.LONG
            else current.close < episode.bos_level
        )
        if bos_now and not episode.bos_confirmed:
            if origin is None:
                self._count("bos_without_origin_ob")
                return None, True
            episode.bos_confirmed = True
            self._count("bos_confirmed")

        if self.config.require_bos and not episode.bos_confirmed:
            return None, False
        if self.config.require_fvg and not episode.fvg_confirmed:
            return None, False
        origin = episode.origin_ob
        if origin is None or episode.origin_ob_index is None:
            return None, False

        # If a bar after the origin both confirms sponsorship and already
        # revisits the entry zone, intrabar order is unknowable from OHLC and
        # the source's "first mitigation" has potentially already occurred.
        if index > episode.origin_ob_index and self._bar_touches_ob(current, origin):
            self._count("same_bar_sponsorship_mitigation_ambiguous")
            return None, True

        entry = origin.proximal
        stop = self._episode_stop(episode, origin)
        target = episode.channel.opposite_boundary(current.ts_close_ns)
        if side is Side.LONG and current.high >= target:
            self._count("channel_target_consumed_before_arm")
            return None, True
        if side is Side.SHORT and current.low <= target:
            self._count("channel_target_consumed_before_arm")
            return None, True

        family = f"{episode.family_prefix}_OB_BOS"
        if self.config.require_fvg:
            family += "_FVG"
        setup = ArmedSetup(
            setup_id=self._new_id("setup"),
            causal_event_id=f"{family}:{episode.channel.channel_id}:{episode.interaction_time_ns}",
            symbol=self.symbol,
            family=family,
            side=side,
            observed_time_ns=current.ts_close_ns,
            entry=entry,
            stop=stop,
            target_mode=TargetMode.FIXED_STRUCTURE,
            initial_target=target,
            fixed_target_id=f"CHANNEL_OPPOSITE:{episode.channel.channel_id}",
            source_pool_id=episode.channel.channel_id,
            zone_low=origin.zone_low,
            zone_high=origin.zone_high,
            formation_extreme=origin.formation_low if side is Side.LONG else origin.formation_high,
            body_ratio=origin.body_ratio,
            previous_body=origin.previous_body,
            current_body=origin.current_body,
            context_bias="CHANNEL_POINT4",
            source_timeframe_minutes=episode.channel.timeframe_minutes,
        )
        if setup.executable(target, target_id=setup.fixed_target_id, min_gross_rr=1.0) is None:
            self._count("armed_rr_lt_1")
            return None, True
        self._count("setups_armed")
        self._count(f"setups_armed_{family.lower()}")
        return setup, True

    def on_five_minute_close(self, candles: list[Candle], index: int) -> list[ArmedSetup]:
        if index < 1:
            return []
        current, previous = candles[index], candles[index - 1]
        true_range = max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        self.true_ranges.append(true_range)
        ob = detect_easychart_order_block(previous, current)
        if ob is not None:
            self._count("order_blocks")
        fvg = strict_fvg_side(
            candles,
            index,
            minimum_middle_body_ratio=self.config.strict_fvg_body_ratio,
        )
        if fvg is not None:
            self._count(f"strict_fvg_{fvg.name.lower()}")

        self._observe_channel_interaction(current, index)

        setups: list[ArmedSetup] = []
        survivors: list[SponsoredEpisode] = []
        for episode in self.episodes:
            setup, finished = self._advance_episode(episode, candles, index, ob, fvg)
            if setup is not None:
                setups.append(setup)
            if not finished:
                survivors.append(episode)
        self.episodes = survivors
        return sorted(
            {setup.causal_event_id: setup for setup in setups}.values(),
            key=lambda setup: (setup.observed_time_ns, setup.symbol, setup.setup_id),
        )


__all__ = [
    "EasyChartStructuralEpisodeEngine",
    "ParallelChannel",
    "ScenarioConfigV4",
    "StructuralPivot",
    "strict_fvg_side",
]
