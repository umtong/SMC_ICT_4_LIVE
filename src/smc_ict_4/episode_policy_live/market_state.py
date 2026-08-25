"""Causal multi-timeframe market state and public-liquidity geometry."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from statistics import median
from typing import Iterable, Sequence

from .domain import Bar, LiquidityBoundary, Pivot, stable_id


NS_PER_MINUTE = 60_000_000_000


class BarAggregator:
    """Aggregate already-closed bars without rewriting event time."""

    def __init__(self, symbol: str, source_minutes: int, target_minutes: int) -> None:
        if target_minutes % source_minutes:
            raise ValueError("target interval must be a multiple of source interval")
        self.symbol = symbol
        self.source_minutes = source_minutes
        self.target_minutes = target_minutes
        self._bucket: list[Bar] = []
        self._bucket_key: int | None = None
        self._last_open_time_ns: int | None = None
        # A live process may attach in the middle of a target interval.  Such
        # an initial fragment is ignored until the first aligned source bar;
        # once aligned, every source bar is required to be contiguous.
        self._synchronized = False

    def push(self, bar: Bar) -> Bar | None:
        if bar.symbol != self.symbol or bar.interval_minutes != self.source_minutes:
            raise ValueError("bar does not match aggregator")
        source_ns = self.source_minutes * NS_PER_MINUTE
        bucket_ns = self.target_minutes * NS_PER_MINUTE
        interval_end = bar.open_time_ns + source_ns
        # Binance raw rows store an inclusive final millisecond, while a
        # Nautilus external bar is timestamped at the exact right edge when the
        # completed interval becomes observable.  Both identify the same
        # closed interval; anything earlier or later does not.
        if (
            bar.open_time_ns % source_ns
            or not interval_end - 1_000_000 <= bar.close_time_ns <= interval_end
        ):
            raise ValueError("source bar timestamps do not match its interval")

        if not self._synchronized:
            if bar.open_time_ns % bucket_ns:
                # Do not turn a process-start fragment into a target bar.
                return None
            self._synchronized = True

        if self._last_open_time_ns is not None:
            expected_open = self._last_open_time_ns + source_ns
            if bar.open_time_ns != expected_open:
                raise RuntimeError("source bars are out of order or gapped")

        key = bar.open_time_ns // bucket_ns
        if self._bucket_key is None:
            self._bucket_key = key
        elif key != self._bucket_key:
            # A key change before a complete bucket is evidence of a missing
            # source bar.  Never manufacture an OHLC bar from that fragment.
            raise RuntimeError("incomplete aggregation bucket")
        self._bucket.append(bar)
        self._last_open_time_ns = bar.open_time_ns
        expected = self.target_minutes // self.source_minutes
        if len(self._bucket) == expected:
            completed = self._finish()
            self._bucket_key = None
            return completed
        if len(self._bucket) > expected:
            raise RuntimeError("aggregation bucket exceeded expected size")
        return None

    def _finish(self) -> Bar | None:
        if not self._bucket:
            return None
        bars = self._bucket
        self._bucket = []
        return Bar(
            symbol=self.symbol,
            interval_minutes=self.target_minutes,
            open_time_ns=bars[0].open_time_ns,
            close_time_ns=bars[-1].close_time_ns,
            open=bars[0].open,
            high=max(item.high for item in bars),
            low=min(item.low for item in bars),
            close=bars[-1].close,
            volume=sum(item.volume for item in bars),
            quote_volume=sum(item.quote_volume for item in bars),
            taker_buy_quote_volume=sum(item.taker_buy_quote_volume for item in bars),
            trade_count=sum(item.trade_count for item in bars),
        )


@dataclass(slots=True)
class PivotTracker:
    symbol: str
    timeframe_minutes: int
    span: int
    bars: list[Bar] = field(default_factory=list)
    pivots: list[Pivot] = field(default_factory=list)

    def push(self, bar: Bar) -> list[Pivot]:
        if bar.symbol != self.symbol or bar.interval_minutes != self.timeframe_minutes:
            raise ValueError("bar does not match pivot tracker")
        self.bars.append(bar)
        width = 2 * self.span + 1
        if len(self.bars) < width:
            return []
        center_index = len(self.bars) - self.span - 1
        window = self.bars[center_index - self.span : center_index + self.span + 1]
        center = self.bars[center_index]
        output: list[Pivot] = []
        highs = [item.high for item in window]
        lows = [item.low for item in window]
        prior_ranges = [max(item.range, 1e-12) for item in self.bars[max(0, center_index - 20) : center_index]]
        scale = median(prior_ranges) if prior_ranges else max(center.range, 1e-12)
        if center.high == max(highs) and highs.count(center.high) == 1:
            prominence = min(center.high - min(lows[: self.span]), center.high - min(lows[self.span + 1 :]))
            output.append(self._make("HIGH", center.high, center, len(self.bars) - 1, prominence / scale))
        if center.low == min(lows) and lows.count(center.low) == 1:
            prominence = min(max(highs[: self.span]) - center.low, max(highs[self.span + 1 :]) - center.low)
            output.append(self._make("LOW", center.low, center, len(self.bars) - 1, prominence / scale))
        self.pivots.extend(output)
        return output

    def _make(self, side: str, price: float, event_bar: Bar, observed_serial: int, strength: float) -> Pivot:
        return Pivot(
            pivot_id=stable_id(
                self.symbol,
                self.timeframe_minutes,
                self.span,
                side,
                event_bar.open_time_ns,
                price,
                prefix="PIVOT:",
            ),
            symbol=self.symbol,
            timeframe_minutes=self.timeframe_minutes,
            side=side,
            price=float(price),
            event_time_ns=event_bar.close_time_ns,
            observed_time_ns=self.bars[-1].close_time_ns,
            serial=observed_serial,
            strength=float(max(strength, 0.0)),
        )


@dataclass(slots=True)
class BoundaryBook:
    symbol: str
    tick_size: float
    boundaries: dict[str, LiquidityBoundary] = field(default_factory=dict)
    pivots_by_tf_side: dict[tuple[int, str], list[Pivot]] = field(default_factory=dict)

    def add_pivots(self, pivots: Iterable[Pivot], current_serial: int, atr: float) -> list[LiquidityBoundary]:
        created: list[LiquidityBoundary] = []
        for pivot in pivots:
            width = max(2.0 * self.tick_size, 0.06 * atr)
            boundary = LiquidityBoundary(
                boundary_id=stable_id(pivot.pivot_id, prefix="BOUNDARY:"),
                symbol=pivot.symbol,
                side=pivot.side,
                kind=f"SWING_{pivot.timeframe_minutes}M",
                timeframe_minutes=pivot.timeframe_minutes,
                observed_time_ns=pivot.observed_time_ns,
                lower=pivot.price - width,
                upper=pivot.price + width,
                price=pivot.price,
                strength=1.0 + pivot.strength + 0.15 * pivot.timeframe_minutes / 15.0,
                anchor_serial=current_serial,
            )
            self.boundaries[boundary.boundary_id] = boundary
            created.append(boundary)
            key = (pivot.timeframe_minutes, pivot.side)
            history = self.pivots_by_tf_side.setdefault(key, [])
            # PivotTracker serials are local to their own timeframe.  Dynamic
            # boundaries, however, are queried with the global 5-minute
            # serial.  Record the observation in that coordinate system so a
            # 60-minute line is not projected twelve times too quickly.
            history.append(replace(pivot, serial=current_serial))
            history[:] = history[-12:]
            created.extend(self._build_dynamic(key, current_serial, atr))
        return created

    def add_repeated_defenses(
        self,
        *,
        new_pivots: Iterable[Pivot],
        all_pivots: Sequence[Pivot],
        bars: Sequence[Bar],
        current_serial: int,
    ) -> list[LiquidityBoundary]:
        """Publish causally confirmed alternating-swing defense bands.

        This ports the source semantics from EasyChart v19: only the adjacent
        prior same-side physical swing may pair with a new pivot; an opposite
        swing must sit between them; their wick-to-body rejection areas must
        overlap; and that shared area must remain defended through the new
        pivot's confirmation.  Individual pivots remain available separately
        as public liquidity objectives and obstacles.
        """

        bar_by_event = {item.close_time_ns: item for item in bars}
        created: list[LiquidityBoundary] = []
        ordered = sorted(
            all_pivots,
            key=lambda item: (item.event_time_ns, item.observed_time_ns, item.pivot_id),
        )
        for pivot in new_pivots:
            if pivot.timeframe_minutes != 15:
                continue
            same_side = [
                item
                for item in ordered
                if item.side == pivot.side
                and item.event_time_ns < pivot.event_time_ns
                and item.observed_time_ns <= pivot.observed_time_ns
            ]
            if not same_side:
                continue
            prior = max(
                same_side,
                key=lambda item: (item.event_time_ns, item.observed_time_ns, item.pivot_id),
            )
            opposite = "HIGH" if pivot.side == "LOW" else "LOW"
            if not any(
                item.side == opposite
                and prior.event_time_ns < item.event_time_ns < pivot.event_time_ns
                and item.observed_time_ns <= pivot.observed_time_ns
                for item in ordered
            ):
                continue
            prior_bar = bar_by_event.get(prior.event_time_ns)
            pivot_bar = bar_by_event.get(pivot.event_time_ns)
            if prior_bar is None or pivot_bar is None:
                continue

            def rejection_area(item: Bar, side: str) -> tuple[float, float]:
                body_low = min(item.open, item.close)
                body_high = max(item.open, item.close)
                return (
                    (item.low, body_low)
                    if side == "LOW"
                    else (body_high, item.high)
                )

            prior_lower, prior_upper = rejection_area(prior_bar, pivot.side)
            pivot_lower, pivot_upper = rejection_area(pivot_bar, pivot.side)
            lower = max(prior_lower, pivot_lower)
            upper = min(prior_upper, pivot_upper)
            if lower >= upper:
                continue
            through_confirmation = [
                item
                for item in bars
                if prior.event_time_ns < item.close_time_ns <= pivot.observed_time_ns
            ]
            held = (
                all(item.close >= lower for item in through_confirmation)
                if pivot.side == "LOW"
                else all(item.close <= upper for item in through_confirmation)
            )
            if not through_confirmation or not held:
                continue
            boundary_id = stable_id(
                prior.pivot_id,
                pivot.pivot_id,
                "REPEATED_DEFENSE_15M",
                prefix="DEFENSE:",
            )
            if boundary_id in self.boundaries:
                continue
            semantic = "SUPPORT" if pivot.side == "LOW" else "RESISTANCE"
            boundary = LiquidityBoundary(
                boundary_id=boundary_id,
                symbol=self.symbol,
                side=pivot.side,
                kind=f"REPEATED_DEFENSE_{semantic}_15M",
                timeframe_minutes=15,
                observed_time_ns=pivot.observed_time_ns,
                lower=lower,
                upper=upper,
                price=0.5 * (lower + upper),
                strength=2.0 + min(prior.strength, pivot.strength),
                anchor_serial=current_serial,
            )
            self.boundaries[boundary_id] = boundary
            created.append(boundary)
        return created

    def add_prior_day(
        self,
        *,
        day_key: int,
        high: float,
        low: float,
        observed_time_ns: int,
        current_serial: int,
        atr: float,
    ) -> list[LiquidityBoundary]:
        """Publish a completed UTC day's extremes at the next day's open."""

        # PRIOR_DAY means exactly the immediately preceding completed day.
        # Old levels may still be represented by swing pivots, but retaining
        # them under this label would silently make the source stale.
        self.boundaries = {
            key: item
            for key, item in self.boundaries.items()
            if item.kind not in {"PRIOR_DAY_HIGH", "PRIOR_DAY_LOW"}
        }
        width = max(2.0 * self.tick_size, 0.04 * atr)
        output: list[LiquidityBoundary] = []
        for side, price in (("HIGH", high), ("LOW", low)):
            boundary = LiquidityBoundary(
                boundary_id=stable_id(self.symbol, day_key, side, prefix="PD:"),
                symbol=self.symbol,
                side=side,
                kind=f"PRIOR_DAY_{side}",
                timeframe_minutes=1440,
                observed_time_ns=observed_time_ns,
                lower=price - width,
                upper=price + width,
                price=price,
                strength=4.0,
                anchor_serial=current_serial,
            )
            self.boundaries[boundary.boundary_id] = boundary
            output.append(boundary)
        return output

    def _build_dynamic(self, key: tuple[int, str], current_serial: int, atr: float) -> list[LiquidityBoundary]:
        timeframe, side = key
        points = self.pivots_by_tf_side.get(key, [])
        if len(points) < 2:
            return []
        first, second = points[-2], points[-1]
        serial_distance = second.serial - first.serial
        if serial_distance <= 0:
            return []
        slope = (second.price - first.price) / serial_distance
        # Uptrend lines use lows; downtrend lines use highs.  The opposite
        # combinations remain liquidity lines but receive lower strength.
        directional = (side == "LOW" and slope > 0.0) or (side == "HIGH" and slope < 0.0)
        width = max(2.0 * self.tick_size, 0.05 * atr)
        center = second.price + slope * (current_serial - second.serial)
        kind = "UPTREND_LINE" if side == "LOW" and slope > 0 else "DOWNTREND_LINE" if side == "HIGH" and slope < 0 else "DIAGONAL_LIQUIDITY"
        boundary = LiquidityBoundary(
            boundary_id=stable_id(first.pivot_id, second.pivot_id, kind, prefix="DYN:"),
            symbol=self.symbol,
            side=side,
            kind=f"{kind}_{timeframe}M",
            timeframe_minutes=timeframe,
            observed_time_ns=second.observed_time_ns,
            lower=center - width,
            upper=center + width,
            price=center,
            strength=(2.0 if directional else 1.0) + 0.5 * min(first.strength, second.strength),
            dynamic_slope_per_bar=slope,
            anchor_serial=current_serial,
        )
        self.boundaries[boundary.boundary_id] = boundary
        return [boundary]

    def mark_consumed(self, bar: Bar, serial: int) -> None:
        for key, boundary in list(self.boundaries.items()):
            if boundary.consumed_time_ns is not None:
                continue
            lower, upper = boundary.band_at(serial)
            hit = bar.high >= lower if boundary.side == "HIGH" else bar.low <= upper
            crossed = bar.high >= upper if boundary.side == "HIGH" else bar.low <= lower
            if hit and crossed and bar.close_time_ns > boundary.observed_time_ns:
                self.boundaries[key] = replace(boundary, consumed_time_ns=bar.close_time_ns)

    def active(self, decision_time_ns: int) -> list[LiquidityBoundary]:
        return [item for item in self.boundaries.values() if item.is_fresh(decision_time_ns)]

    def source_candidates(self, price: float, decision_time_ns: int, serial: int, atr: float) -> list[LiquidityBoundary]:
        max_distance = 1.5 * max(atr, self.tick_size)
        output: list[LiquidityBoundary] = []
        for item in self.boundaries.values():
            if not item.is_fresh(decision_time_ns):
                continue
            lower, upper = item.band_at(serial)
            if lower - max_distance <= price <= upper + max_distance:
                output.append(item)
        return sorted(output, key=lambda item: (-item.strength, -item.timeframe_minutes, item.boundary_id))

    def destination_candidates(
        self,
        *,
        side: str,
        entry: float,
        decision_time_ns: int,
        serial: int,
    ) -> list[LiquidityBoundary]:
        wanted = "HIGH" if side == "LONG" else "LOW"
        direction = 1.0 if side == "LONG" else -1.0
        output: list[LiquidityBoundary] = []
        for item in self.active(decision_time_ns):
            if item.side != wanted:
                continue
            price = item.price_at(serial)
            if direction * (price - entry) > self.tick_size:
                output.append(item)
        return sorted(
            output,
            key=lambda item: (
                direction * (item.price_at(serial) - entry),
                -item.strength,
                -item.timeframe_minutes,
                item.boundary_id,
            ),
        )


@dataclass(slots=True)
class ObjectiveBook:
    """Horizontal, causal, first-touch objective registry.

    EasyChart RE1's objective experiments deliberately separated the profit
    objective from the richer source/context book.  Only already-confirmed
    horizontal pivots enter this registry; diagonal structures and prior-day
    levels therefore cannot be silently promoted to ordinary take-profit
    destinations.
    """

    symbol: str
    tick_size: float
    objectives: dict[str, LiquidityBoundary] = field(default_factory=dict)
    source_boundary_by_objective: dict[str, str] = field(default_factory=dict)
    _active_ids: set[str] = field(default_factory=set, repr=False)

    def register(
        self,
        objective: LiquidityBoundary,
        *,
        source_boundary_id: str,
    ) -> bool:
        if objective.symbol != self.symbol:
            raise ValueError("objective symbol mismatch")
        if objective.timeframe_minutes not in {1, 5, 15}:
            raise ValueError("ordinary objectives must be 1m, 5m or 15m pivots")
        existing = self.objectives.get(objective.boundary_id)
        if existing is not None:
            if existing != objective:
                raise ValueError("objective identity was reused with different geometry")
            return False
        self.objectives[objective.boundary_id] = objective
        self.source_boundary_by_objective[objective.boundary_id] = source_boundary_id
        if objective.consumed_time_ns is None:
            self._active_ids.add(objective.boundary_id)
        return True

    def add_pivots(self, pivots: Iterable[Pivot]) -> list[LiquidityBoundary]:
        created: list[LiquidityBoundary] = []
        for pivot in pivots:
            objective_id = stable_id(pivot.pivot_id, prefix="OBJECTIVE:")
            objective = LiquidityBoundary(
                boundary_id=objective_id,
                symbol=pivot.symbol,
                side=pivot.side,
                kind=f"HORIZONTAL_OBJECTIVE_{pivot.timeframe_minutes}M",
                timeframe_minutes=pivot.timeframe_minutes,
                observed_time_ns=pivot.observed_time_ns,
                lower=pivot.price,
                upper=pivot.price,
                price=pivot.price,
                strength=pivot.strength,
            )
            # A 15m pivot can simultaneously be a source in BoundaryBook.  Its
            # objective identity is separate, but the current source itself is
            # never a destination for the same episode.
            if self.register(
                objective,
                source_boundary_id=stable_id(
                    pivot.pivot_id,
                    prefix="BOUNDARY:",
                ),
            ):
                created.append(objective)
        return created

    def observe_price(self, bar: Bar) -> None:
        """Consume a pivot only on a touch strictly after it became known."""

        for objective_id in list(self._active_ids):
            objective = self.objectives[objective_id]
            if (
                bar.close_time_ns <= objective.observed_time_ns
            ):
                continue
            touched = (
                bar.high >= objective.price
                if objective.side == "HIGH"
                else bar.low <= objective.price
            )
            if touched:
                self.objectives[objective_id] = replace(
                    objective,
                    consumed_time_ns=bar.close_time_ns,
                )
                self._active_ids.remove(objective_id)

    def active(
        self,
        decision_time_ns: int,
        *,
        source_boundary_id: str | None = None,
    ) -> list[LiquidityBoundary]:
        return [
            objective
            for objective_id in self._active_ids
            for objective in (self.objectives[objective_id],)
            # Strictly-before is the RE1 contract: a pivot confirmed at this
            # close becomes usable by a later decision, never retroactively by
            # the close which supplied its final confirmation bar.
            if objective.observed_time_ns < decision_time_ns
            and self.source_boundary_by_objective.get(objective_id)
            != source_boundary_id
        ]

    def active_at(
        self,
        decision_time_ns: int,
        *,
        source_boundary_id: str | None = None,
    ) -> list[LiquidityBoundary]:
        """Reconstruct the objective book exactly as it existed at ``time``.

        Consumed objectives remain in ``objectives`` for this purpose.  This
        lets an auction commit its first destination at settlement and later
        reject the episode if that destination was spent before entry, instead
        of silently substituting a farther target.
        """

        return [
            objective
            for objective_id, objective in self.objectives.items()
            if objective.observed_time_ns < decision_time_ns
            and (
                objective.consumed_time_ns is None
                or objective.consumed_time_ns > decision_time_ns
            )
            and self.source_boundary_by_objective.get(objective_id)
            != source_boundary_id
        ]

    def destination_candidates_at(
        self,
        *,
        side: str,
        reference_price: float,
        decision_time_ns: int,
        source_boundary_id: str | None = None,
    ) -> list[LiquidityBoundary]:
        """Return the first opposing objectives visible at a past settlement."""

        wanted = "HIGH" if side == "LONG" else "LOW"
        direction = 1.0 if side == "LONG" else -1.0
        output = [
            objective
            for objective in self.active_at(
                decision_time_ns,
                source_boundary_id=source_boundary_id,
            )
            if objective.side == wanted
            and direction * (objective.price - reference_price) > self.tick_size
        ]
        return sorted(
            output,
            key=lambda item: (
                direction * (item.price - reference_price),
                -item.timeframe_minutes,
                -item.strength,
                item.boundary_id,
            ),
        )

    def destination_candidates(
        self,
        *,
        side: str,
        entry: float,
        decision_time_ns: int,
        source_boundary_id: str | None = None,
    ) -> list[LiquidityBoundary]:
        wanted = "HIGH" if side == "LONG" else "LOW"
        direction = 1.0 if side == "LONG" else -1.0
        output = [
            objective
            for objective in self.active(
                decision_time_ns,
                source_boundary_id=source_boundary_id,
            )
            if objective.side == wanted
            and direction * (objective.price - entry) > self.tick_size
        ]
        # Price owns the first absorbing objective.  For an exact price tie,
        # preserve the more established timeframe, then make identity stable.
        return sorted(
            output,
            key=lambda item: (
                direction * (item.price - entry),
                -item.timeframe_minutes,
                -item.strength,
                item.boundary_id,
            ),
        )


@dataclass(slots=True)
class SymbolMarketState:
    symbol: str
    tick_size: float
    one_minute: deque[Bar] = field(default_factory=lambda: deque(maxlen=7200))
    five_minute: list[Bar] = field(default_factory=list)
    fifteen_minute: list[Bar] = field(default_factory=list)
    sixty_minute: list[Bar] = field(default_factory=list)
    serial_5m: int = -1
    _agg_5: BarAggregator = field(init=False, repr=False)
    _agg_15: BarAggregator = field(init=False, repr=False)
    _agg_60: BarAggregator = field(init=False, repr=False)
    _pivot_1: PivotTracker = field(init=False, repr=False)
    _pivot_5: PivotTracker = field(init=False, repr=False)
    _pivot_15: PivotTracker = field(init=False, repr=False)
    _pivot_60: PivotTracker = field(init=False, repr=False)
    boundary_book: BoundaryBook = field(init=False)
    objective_book: ObjectiveBook = field(init=False)
    _day_key: int | None = field(default=None, init=False, repr=False)
    _day_bars: list[Bar] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._agg_5 = BarAggregator(self.symbol, 1, 5)
        self._agg_15 = BarAggregator(self.symbol, 5, 15)
        self._agg_60 = BarAggregator(self.symbol, 15, 60)
        self._pivot_1 = PivotTracker(self.symbol, 1, 6)
        self._pivot_5 = PivotTracker(self.symbol, 5, 2)
        self._pivot_15 = PivotTracker(self.symbol, 15, 2)
        self._pivot_60 = PivotTracker(self.symbol, 60, 2)
        self.boundary_book = BoundaryBook(self.symbol, self.tick_size)
        self.objective_book = ObjectiveBook(self.symbol, self.tick_size)

    def push_one_minute(self, bar: Bar) -> tuple[Bar | None, list[LiquidityBoundary]]:
        self.one_minute.append(bar)
        self.objective_book.add_pivots(self._pivot_1.push(bar))
        five = self._agg_5.push(bar)
        if five is None:
            self.objective_book.observe_price(bar)
            return None, []
        created = self.push_five_minute(five, observe_objectives=False)
        self.objective_book.observe_price(bar)
        return five, created

    def push_five_minute(
        self,
        five: Bar,
        *,
        observe_objectives: bool = True,
    ) -> list[LiquidityBoundary]:
        if five.symbol != self.symbol or five.interval_minutes != 5:
            raise ValueError("bar does not match five-minute market state")
        if self.five_minute and five.close_time_ns <= self.five_minute[-1].close_time_ns:
            if five == self.five_minute[-1]:
                return []
            raise RuntimeError("five-minute bar is out of order or mutated")
        self.five_minute.append(five)
        self.serial_5m += 1
        created: list[LiquidityBoundary] = []
        self.objective_book.add_pivots(self._pivot_5.push(five))
        created.extend(self._update_prior_day(five))
        fifteen = self._agg_15.push(five)
        if fifteen is not None:
            self.fifteen_minute.append(fifteen)
            atr_15 = self.atr(self.fifteen_minute)
            pivots_15 = self._pivot_15.push(fifteen)
            self.objective_book.add_pivots(pivots_15)
            created.extend(self.boundary_book.add_pivots(pivots_15, self.serial_5m, atr_15))
            created.extend(
                self.boundary_book.add_repeated_defenses(
                    new_pivots=pivots_15,
                    all_pivots=self._pivot_15.pivots,
                    bars=self.fifteen_minute,
                    current_serial=self.serial_5m,
                )
            )
            sixty = self._agg_60.push(fifteen)
            if sixty is not None:
                self.sixty_minute.append(sixty)
                atr_60 = self.atr(self.sixty_minute)
                created.extend(self.boundary_book.add_pivots(self._pivot_60.push(sixty), self.serial_5m, atr_60))
        if observe_objectives:
            self.objective_book.observe_price(five)
        return created

    def _update_prior_day(self, five: Bar) -> list[LiquidityBoundary]:
        day_ns = 1440 * NS_PER_MINUTE
        day_key = five.open_time_ns // day_ns
        if self._day_key is None:
            self._day_key = day_key
        if day_key == self._day_key:
            self._day_bars.append(five)
            return []
        if day_key < self._day_key:
            raise RuntimeError("five-minute day moved backwards")

        previous_key = self._day_key
        previous = self._day_bars
        self._day_key = day_key
        self._day_bars = [five]
        expected = 1440 // 5
        complete = (
            len(previous) == expected
            and previous[0].open_time_ns == previous_key * day_ns
            and previous[-1].open_time_ns == (previous_key + 1) * day_ns - 5 * NS_PER_MINUTE
            and (previous_key + 1) * day_ns - 1_000_000
            <= previous[-1].close_time_ns
            < (previous_key + 1) * day_ns
        )
        if not complete or day_key != previous_key + 1:
            return []
        return self.boundary_book.add_prior_day(
            day_key=previous_key,
            high=max(item.high for item in previous),
            low=min(item.low for item in previous),
            # The complete prior day is knowable at the new day's open.  The
            # first decision that can use it occurs when this five-minute bar
            # closes, so this remains strictly causal.
            observed_time_ns=five.open_time_ns,
            current_serial=self.serial_5m,
            atr=max(self.atr(self.five_minute[:-1]), self.tick_size),
        )

    @staticmethod
    def atr(bars: list[Bar], length: int = 20) -> float:
        if not bars:
            return 0.0
        work = bars[-length:]
        ranges: list[float] = []
        previous_close: float | None = None
        for item in work:
            if previous_close is None:
                ranges.append(item.range)
            else:
                ranges.append(max(item.range, abs(item.high - previous_close), abs(item.low - previous_close)))
            previous_close = item.close
        return median(ranges) if ranges else 0.0

    def rolling_median(self, field: str, length: int = 60) -> float:
        values = [float(getattr(item, field)) for item in self.five_minute[-length:]]
        return median(values) if values else 0.0
