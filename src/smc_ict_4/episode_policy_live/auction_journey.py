"""Causal event-time auction journeys around one public structure interaction.

This is a direct production-domain port of mechanisms already present in:

* ``structural_auction_control_v5.py`` at commit ``1fc9c69``: equal-activity
  episode phases and one causal journey owner;
* ``auction_response.py`` at commit ``fa6e291``: pre-interaction baselines,
  shock/settlement/path evidence and signed-flow response;
* ``response_event_detection.py`` at commit ``05ac6de``: completed failed and
  accepted auction sequences, with response evidence attached at the same
  causal decision timestamp.
* ``easychart_re1_reaction.py`` at commit ``1142aca``: an accepted break's
  first retest is owned by the first later completed micro candle; that candle
  either confirms beyond the retest's favorable extreme or terminally ends the
  accepted journey without waiting for a later breakout.

The module adapts those existing mechanisms to :class:`domain.Bar`.  It does
not impose a clock- or bar-count expiry.  A journey changes phase only because
completed bars establish a structural transition, while its evidence blocks
are segmented by cumulative traded activity.  When activity or taker flow is
not supplied, that fact remains ``False``/``None`` in the evidence; structural
range may segment the path but is explicitly named as such.

The flow-response fields below are observations, not an admission rule.  In
particular, ``flow_coherence`` gives the old ``flow_share`` calculation an
honest name, while ``pressure`` retains magnitude by dividing signed taker
flow by total quote activity.  ``absorption_outcome_proxy`` is only the gap
between observed taker pressure and realized OHLC path efficiency.  Public
klines cannot observe L2 replenishment, queue depletion, or passive order
identity, so the proxy must never be described as direct replenishment.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median
from typing import Iterable, Literal

from .domain import Bar, PolicyError


JourneyFamily = Literal[
    "FAILED_AUCTION_REVERSAL",
    "ACCEPTED_AUCTION_CONTINUATION",
    "DEFENDED_AUCTION_CONTINUATION",
]

PROVENANCE: tuple[str, ...] = (
    "structural_auction_control_v5.py@1fc9c69",
    "auction_response.py@fa6e291",
    "response_event_detection.py@05ac6de",
    "easychart_re1_reaction.py@1142aca",
)

ACCEPTANCE_FIRST_RESPONSE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ACCEPTED_BREAK_FIRST_RETEST_REQUIRES_NEXT_COMPLETED_MICRO_CLOSE_BEYOND_RETEST_EXTREME"
)


@dataclass(frozen=True, slots=True)
class StructureInteraction:
    """One observable interaction with one public structure band."""

    structure_id: str
    symbol: str
    source_side: Literal["HIGH", "LOW"]
    lower: float
    upper: float
    interaction_time_ns: int

    def __post_init__(self) -> None:
        if not self.structure_id:
            raise PolicyError("structure_id cannot be empty")
        if self.source_side not in {"HIGH", "LOW"}:
            raise PolicyError("source_side must be HIGH or LOW")
        if not (math.isfinite(self.lower) and math.isfinite(self.upper)):
            raise PolicyError("structure band must be finite")
        if self.lower > self.upper:
            raise PolicyError("structure lower cannot exceed upper")
        if self.interaction_time_ns < 0:
            raise PolicyError("interaction_time_ns cannot be negative")

    @property
    def outward_sign(self) -> int:
        return 1 if self.source_side == "HIGH" else -1

    @property
    def inward_sign(self) -> int:
        return -self.outward_sign


@dataclass(frozen=True, slots=True)
class TapeBar:
    close_time_ns: int
    open: float
    high: float
    low: float
    close: float
    activity: float | None
    signed_flow: float | None


@dataclass(frozen=True, slots=True)
class ActivityBlock:
    start_ns: int
    end_ns: int
    bars: int
    progress: float
    path_efficiency: float
    close_location: float
    favorable_excursion: float
    adverse_excursion: float
    activity_ratio: float | None
    flow_coherence: float | None
    flow_share: float | None
    pressure: float | None
    price_response: float
    impact_per_pressure: float | None
    realized_capacity: float
    absorption_outcome_proxy: float | None


@dataclass(frozen=True, slots=True)
class JourneyEvidence:
    """One mutually exclusive journey interpretation at an observed time."""

    family: JourneyFamily | None
    completed: bool
    terminal_state: str
    completed_states: tuple[str, ...]
    interaction_time_ns: int
    observed_time_ns: int
    phase_basis: Literal["TRADED_ACTIVITY", "STRUCTURAL_RANGE"]
    blocks: tuple[ActivityBlock, ...]
    baseline_range: float | None
    baseline_body: float | None
    baseline_activity: float | None
    baseline_pressure: float | None
    activity_input_known: bool
    flow_input_known: bool
    impulse_energy: float | None
    settlement: str
    control_transfer: bool
    control_flow_coherence: float | None
    control_flow_share: float | None
    control_pressure: float | None
    control_pressure_surprise: float | None
    control_price_response: float
    control_impact_per_pressure: float | None
    realized_capacity_progression: tuple[float, ...]
    absorption_outcome_proxy_progression: tuple[float | None, ...]
    reclaim_time_ns: int | None
    retest_time_ns: int | None
    response_time_ns: int | None
    response_close: float | None
    response_required_extreme: float | None
    stop_intact: bool | None
    target_fresh: bool | None
    reason: str
    provenance: tuple[str, ...] = PROVENANCE


@dataclass(frozen=True, slots=True)
class JourneyClaim:
    structure_id: str
    symbol: str
    interaction_time_ns: int
    lower: float
    upper: float
    family: JourneyFamily
    owner_id: str


@dataclass(slots=True)
class _IncrementalJourneyState:
    """Derived, non-authoritative state for one monotonic evaluation stream."""

    key: tuple[object, ...]
    start_seq: int
    last_seq: int
    endpoint_seq: int
    break_seq: int | None = None
    retest_seq: int | None = None
    retest_held: bool | None = None
    response_seq: int | None = None
    impulse_seq: int | None = None
    reclaim_seq: int | None = None
    outside_after_reclaim: bool = False
    any_touch: bool = False
    any_outside_extreme: bool = False
    terminal_outside: bool = False
    minimum: float = math.inf
    maximum: float = -math.inf
    activity_known: bool = True
    flow_known: bool = True
    activity_total: float = 0.0
    structural_total: float = 0.0
    phase_basis: str = "TRADED_ACTIVITY"
    boundary_one_seq: int = 0
    boundary_two_seq: int = 0
    boundary_one_prefix: float = 0.0
    boundary_two_prefix: float = 0.0
    boundaries_initialized: bool = False
    impulse_range_median: float | None = None
    impulse_body_median: float | None = None
    impulse_activity_median: float | None = None
    impulse_activity_known: bool = False
    impulse_summary_seq: int = -1


class CausalJourneyRegistry:
    """Give one exact structure interaction one completed journey owner.

    Unlike the older proximity de-duplication, ownership has no rolling time
    window.  Exact structure identity wins first; simultaneous overlapping
    public bands are the same observed interaction and therefore also share an
    owner.  Unresolved observations cannot consume ownership.
    """

    def __init__(self, tick_size: float) -> None:
        if not math.isfinite(tick_size) or tick_size <= 0.0:
            raise PolicyError("tick_size must be positive and finite")
        self.tick_size = float(tick_size)
        self._claims: list[JourneyClaim] = []
        self._exact: dict[tuple[str, str, int], JourneyClaim] = {}

    @property
    def claims(self) -> tuple[JourneyClaim, ...]:
        return tuple(self._claims)

    def existing_owner(self, interaction: StructureInteraction) -> JourneyClaim | None:
        exact = self._exact.get(
            (interaction.symbol, interaction.structure_id, interaction.interaction_time_ns)
        )
        if exact is not None:
            return exact
        for claim in reversed(self._claims):
            if claim.symbol != interaction.symbol:
                continue
            if claim.interaction_time_ns != interaction.interaction_time_ns:
                continue
            if max(claim.lower, interaction.lower) <= min(claim.upper, interaction.upper) + self.tick_size:
                return claim
        return None

    def claim(
        self,
        interaction: StructureInteraction,
        evidence: JourneyEvidence,
        owner_id: str,
    ) -> JourneyClaim:
        if not evidence.completed or evidence.family is None:
            raise ValueError("only a completed journey can own an interaction")
        if not owner_id:
            raise ValueError("owner_id cannot be empty")
        existing = self.existing_owner(interaction)
        if existing is not None:
            if existing.family != evidence.family or existing.owner_id != owner_id:
                raise ValueError("structure interaction already has a different owner")
            return existing
        claim = JourneyClaim(
            structure_id=interaction.structure_id,
            symbol=interaction.symbol,
            interaction_time_ns=interaction.interaction_time_ns,
            lower=interaction.lower,
            upper=interaction.upper,
            family=evidence.family,
            owner_id=owner_id,
        )
        self._claims.append(claim)
        self._exact[(claim.symbol, claim.structure_id, claim.interaction_time_ns)] = claim
        return claim


class EventTimeAuctionJourney:
    """Observe completed one-minute bars and resolve causal auction journeys."""

    BASELINE_BARS = 360

    def __init__(self, symbol: str, tick_size: float, history_bars: int = 10_080) -> None:
        if history_bars <= self.BASELINE_BARS:
            raise ValueError("history_bars must exceed the baseline window")
        if not math.isfinite(tick_size) or tick_size <= 0.0:
            raise PolicyError("tick_size must be positive and finite")
        self.symbol = symbol
        self.tick_size = float(tick_size)
        # This bound controls memory only.  If it truncates the requested
        # interaction, evaluation returns HISTORY_UNAVAILABLE rather than
        # treating the truncation as a journey expiry.
        self._history_bars = int(history_bars)
        self._buffer: list[TapeBar | None] = [None] * self._history_bars
        self._start = 0
        self._size = 0
        self._next_seq = 0
        self._slot_seq: list[int] = [-1] * self._history_bars
        capacity = 1
        while capacity < self._history_bars:
            capacity <<= 1
        self._tree_capacity = capacity
        tree_size = capacity * 2
        self._tree_activity = [0.0] * tree_size
        self._tree_activity_known = [0] * tree_size
        self._tree_flow = [0.0] * tree_size
        self._tree_abs_flow = [0.0] * tree_size
        self._tree_flow_known = [0] * tree_size
        self._tree_structural = [0.0] * tree_size
        self._tree_edge = [0.0] * tree_size
        self._tree_min_low = [math.inf] * tree_size
        self._tree_max_high = [-math.inf] * tree_size
        self._states: dict[tuple[object, ...], _IncrementalJourneyState] = {}
        self._baseline_cache: dict[
            tuple[int, int, int],
            tuple[float | None, float | None, float | None, float | None],
        ] = {}

    @property
    def bars(self) -> tuple[TapeBar, ...]:
        return tuple(self._bar_at(index) for index in range(self._size))

    def _bar_at(self, logical_index: int) -> TapeBar:
        if logical_index < 0 or logical_index >= self._size:
            raise IndexError("journey tape index out of range")
        item = self._buffer[(self._start + logical_index) % self._history_bars]
        if item is None:  # pragma: no cover - protected by ring invariants
            raise RuntimeError("journey tape ring contains an empty live slot")
        return item

    @property
    def _oldest_seq(self) -> int:
        return self._next_seq - self._size

    def _bar_by_seq(self, seq: int) -> TapeBar:
        if seq < self._oldest_seq or seq >= self._next_seq:
            raise IndexError("journey tape sequence is outside retained history")
        slot = seq % self._history_bars
        if self._slot_seq[slot] != seq:
            raise RuntimeError("journey tape sequence tag mismatch")
        item = self._buffer[slot]
        if item is None:  # pragma: no cover - protected by ring invariants
            raise RuntimeError("journey tape ring contains an empty live slot")
        return item

    def _set_tree_leaf(self, slot: int, bar: TapeBar, edge: float) -> None:
        node = self._tree_capacity + slot
        activity_known = int(bar.activity is not None)
        flow_known = int(bar.signed_flow is not None)
        self._tree_activity[node] = float(bar.activity or 0.0)
        self._tree_activity_known[node] = activity_known
        self._tree_flow[node] = float(bar.signed_flow or 0.0)
        self._tree_abs_flow[node] = abs(float(bar.signed_flow or 0.0))
        self._tree_flow_known[node] = flow_known
        self._tree_structural[node] = max(bar.high - bar.low, self.tick_size)
        self._tree_edge[node] = edge
        self._tree_min_low[node] = bar.low
        self._tree_max_high[node] = bar.high
        node //= 2
        while node:
            left = node * 2
            right = left + 1
            self._tree_activity[node] = self._tree_activity[left] + self._tree_activity[right]
            self._tree_activity_known[node] = (
                self._tree_activity_known[left] + self._tree_activity_known[right]
            )
            self._tree_flow[node] = self._tree_flow[left] + self._tree_flow[right]
            self._tree_abs_flow[node] = self._tree_abs_flow[left] + self._tree_abs_flow[right]
            self._tree_flow_known[node] = (
                self._tree_flow_known[left] + self._tree_flow_known[right]
            )
            self._tree_structural[node] = (
                self._tree_structural[left] + self._tree_structural[right]
            )
            self._tree_edge[node] = self._tree_edge[left] + self._tree_edge[right]
            self._tree_min_low[node] = min(
                self._tree_min_low[left], self._tree_min_low[right]
            )
            self._tree_max_high[node] = max(
                self._tree_max_high[left], self._tree_max_high[right]
            )
            node //= 2

    def _physical_query(self, left: int, right: int) -> tuple[float, int, float, float, int, float, float, float, float]:
        if right < left:
            return (0.0, 0, 0.0, 0.0, 0, 0.0, 0.0, math.inf, -math.inf)
        left += self._tree_capacity
        right += self._tree_capacity + 1
        activity = flow = abs_flow = structural = edge = 0.0
        activity_known = flow_known = 0
        minimum = math.inf
        maximum = -math.inf
        while left < right:
            if left & 1:
                activity += self._tree_activity[left]
                activity_known += self._tree_activity_known[left]
                flow += self._tree_flow[left]
                abs_flow += self._tree_abs_flow[left]
                flow_known += self._tree_flow_known[left]
                structural += self._tree_structural[left]
                edge += self._tree_edge[left]
                minimum = min(minimum, self._tree_min_low[left])
                maximum = max(maximum, self._tree_max_high[left])
                left += 1
            if right & 1:
                right -= 1
                activity += self._tree_activity[right]
                activity_known += self._tree_activity_known[right]
                flow += self._tree_flow[right]
                abs_flow += self._tree_abs_flow[right]
                flow_known += self._tree_flow_known[right]
                structural += self._tree_structural[right]
                edge += self._tree_edge[right]
                minimum = min(minimum, self._tree_min_low[right])
                maximum = max(maximum, self._tree_max_high[right])
            left //= 2
            right //= 2
        return (
            activity, activity_known, flow, abs_flow, flow_known,
            structural, edge, minimum, maximum,
        )

    @staticmethod
    def _merge_query(
        left: tuple[float, int, float, float, int, float, float, float, float],
        right: tuple[float, int, float, float, int, float, float, float, float],
    ) -> tuple[float, int, float, float, int, float, float, float, float]:
        return (
            left[0] + right[0], left[1] + right[1], left[2] + right[2],
            left[3] + right[3], left[4] + right[4], left[5] + right[5],
            left[6] + right[6], min(left[7], right[7]), max(left[8], right[8]),
        )

    def _range_query(self, start_seq: int, end_seq: int) -> tuple[float, int, float, float, int, float, float, float, float]:
        if end_seq < start_seq:
            return self._physical_query(0, -1)
        if start_seq < self._oldest_seq or end_seq >= self._next_seq:
            raise IndexError("journey range query is outside retained history")
        left = start_seq % self._history_bars
        right = end_seq % self._history_bars
        if left <= right:
            return self._physical_query(left, right)
        return self._merge_query(
            self._physical_query(left, self._history_bars - 1),
            self._physical_query(0, right),
        )

    def _bisect_time(self, time_ns: int, *, right: bool = False) -> int:
        """Locate one timestamp in chronological ring order in O(log history)."""

        low, high = 0, self._size
        while low < high:
            middle = (low + high) // 2
            close_time_ns = self._bar_at(middle).close_time_ns
            if close_time_ns < time_ns or (right and close_time_ns == time_ns):
                low = middle + 1
            else:
                high = middle
        return low

    def bars_between(self, start_time_ns: int, end_time_ns: int) -> tuple[TapeBar, ...]:
        """Return only completed bars in the inclusive causal time range."""

        if end_time_ns < start_time_ns or not self._size:
            return ()
        start = self._bisect_time(start_time_ns)
        end = self._bisect_time(end_time_ns, right=True)
        return tuple(self._bar_at(index) for index in range(start, end))

    def observe(self, bar: Bar) -> None:
        if bar.symbol != self.symbol:
            raise ValueError("bar symbol does not match journey tape")
        if bar.interval_minutes != 1:
            raise ValueError("event-time journey requires completed one-minute bars")
        if self._size and bar.close_time_ns <= self._bar_at(self._size - 1).close_time_ns:
            raise RuntimeError("journey bars must be strictly increasing")

        activity: float | None
        if bar.quote_volume > 0.0:
            activity = bar.quote_volume
        elif bar.volume > 0.0:
            activity = bar.volume
        else:
            activity = None

        # A zero quote observation is ambiguous (missing versus a true empty
        # interval), so it cannot support taker-flow inference.  Impossible
        # taker values are likewise preserved as unknown instead of clamped.
        signed_flow = (
            2.0 * bar.taker_buy_quote_volume - bar.quote_volume
            if bar.quote_volume > 0.0
            and 0.0 <= bar.taker_buy_quote_volume <= bar.quote_volume
            else None
        )
        item = TapeBar(
            close_time_ns=bar.close_time_ns,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            activity=activity,
            signed_flow=signed_flow,
        )
        seq = self._next_seq
        previous = self._bar_by_seq(seq - 1) if self._size else None
        if self._size < self._history_bars:
            write_index = (self._start + self._size) % self._history_bars
            self._size += 1
        else:
            write_index = self._start
            self._start = (self._start + 1) % self._history_bars
        self._buffer[write_index] = item
        self._slot_seq[write_index] = seq
        self._next_seq += 1
        self._set_tree_leaf(
            write_index,
            item,
            0.0 if previous is None else abs(item.close - previous.close),
        )
        oldest = self._oldest_seq
        if self._states and seq % 256 == 0:
            self._states = {
                key: state
                for key, state in self._states.items()
                if state.start_seq >= oldest
            }
        if self._baseline_cache and seq % 256 == 0:
            self._baseline_cache = {
                key: value
                for key, value in self._baseline_cache.items()
                if key[1] >= oldest
            }

    @staticmethod
    def _known_median(values: Iterable[float | None]) -> float | None:
        known = [float(value) for value in values if value is not None and value > 0.0]
        return float(median(known)) if known else None

    def _baseline(
        self,
        interaction_time_ns: int,
    ) -> tuple[float | None, float | None, float | None, float | None]:
        end = self._bisect_time(interaction_time_ns)
        start = max(0, end - self.BASELINE_BARS)
        absolute_start = self._oldest_seq + start
        absolute_end = self._oldest_seq + end
        # Absolute interval identity is sufficient: retained bars are
        # immutable, and truncation changes ``absolute_start``.  Including the
        # moving oldest sequence would defeat the cache after the ring fills.
        cache_key = (absolute_start, absolute_end, 0)
        cached = self._baseline_cache.get(cache_key)
        if cached is not None:
            return cached
        prior = [self._bar_at(index) for index in range(start, end)]
        if not prior:
            return None, None, None, None
        baseline_range = self._known_median(max(bar.high - bar.low, self.tick_size) for bar in prior)
        baseline_body = self._known_median(max(abs(bar.close - bar.open), self.tick_size) for bar in prior)
        baseline_activity = self._known_median(bar.activity for bar in prior)
        activity_total = sum(float(bar.activity or 0.0) for bar in prior)
        baseline_raw_pressure = (
            max(
                -1.0,
                min(
                    1.0,
                    sum(float(bar.signed_flow or 0.0) for bar in prior)
                    / activity_total,
                ),
            )
            if activity_total > 0.0
            and all(bar.activity is not None and bar.signed_flow is not None for bar in prior)
            else None
        )
        result = (
            baseline_range,
            baseline_body,
            baseline_activity,
            baseline_raw_pressure,
        )
        self._baseline_cache[cache_key] = result
        return result

    @staticmethod
    def _split_by_weight(bars: list[TapeBar], weights: list[float], blocks: int = 3) -> list[list[TapeBar]]:
        if len(bars) <= blocks:
            return [[bar] for bar in bars]
        total = sum(weights)
        groups: list[list[TapeBar]] = [[] for _ in range(blocks)]
        cumulative = 0.0
        for bar, weight in zip(bars, weights, strict=True):
            midpoint = cumulative + weight / 2.0
            index = min(blocks - 1, int(midpoint / total * blocks))
            groups[index].append(bar)
            cumulative += weight
        for index, group in enumerate(groups):
            if group:
                continue
            position = min(len(bars) - 1, round((index + 0.5) * len(bars) / blocks - 0.5))
            groups[index] = [bars[position]]
        return groups

    def _activity_groups(self, bars: list[TapeBar]) -> tuple[str, list[list[TapeBar]]]:
        if all(bar.activity is not None and bar.activity > 0.0 for bar in bars):
            return "TRADED_ACTIVITY", self._split_by_weight(
                bars, [float(bar.activity) for bar in bars if bar.activity is not None]
            )
        # Explicit structural fallback, not fabricated volume.  True range is
        # only a phase clock; activity_ratio and flow remain unknown.
        weights = [max(bar.high - bar.low, self.tick_size) for bar in bars]
        return "STRUCTURAL_RANGE", self._split_by_weight(bars, weights)

    @staticmethod
    def _close_location(bar: TapeBar, sign: int) -> float:
        width = max(bar.high - bar.low, 1e-12)
        raw = (bar.close - bar.low) / width
        return raw if sign > 0 else 1.0 - raw

    @staticmethod
    def _flow_metrics(
        *,
        sign: int,
        flow_sum: float,
        absolute_flow: float,
        activity_sum: float,
        inputs_known: bool,
    ) -> tuple[float | None, float | None]:
        """Return legacy coherence and magnitude-preserving taker pressure."""

        if not inputs_known:
            return None, None
        coherence = sign * flow_sum / absolute_flow if absolute_flow > 0.0 else 0.0
        pressure = (
            max(-1.0, min(1.0, sign * flow_sum / activity_sum))
            if activity_sum > 0.0
            else None
        )
        return coherence, pressure

    @staticmethod
    def _response_metrics(
        progress: float,
        path_efficiency: float,
        pressure: float | None,
    ) -> tuple[float | None, float, float | None]:
        """Relate public-kline price response to observed taker pressure.

        ``realized_capacity`` is the bounded signed fraction of total close
        travel realized in the journey direction.  The absorption value is an
        outcome proxy (pressure minus realized path efficiency), not an L2
        replenishment measurement.
        """

        realized_capacity = max(-1.0, min(1.0, path_efficiency))
        impact_per_pressure = (
            progress / pressure
            if pressure is not None and pressure != 0.0
            else None
        )
        absorption_proxy = (
            pressure - realized_capacity if pressure is not None else None
        )
        return impact_per_pressure, realized_capacity, absorption_proxy

    def _block(
        self,
        bars: list[TapeBar],
        sign: int,
        baseline_range: float | None,
        baseline_activity: float | None,
    ) -> ActivityBlock:
        scale = baseline_range or self.tick_size
        first, last = bars[0], bars[-1]
        displacement = sign * (last.close - first.open)
        moves = [
            bar.close - (first.open if index == 0 else bars[index - 1].close)
            for index, bar in enumerate(bars)
        ]
        travel = sum(abs(move) for move in moves)
        known_activity = [bar.activity for bar in bars if bar.activity is not None]
        activity_ratio = (
            sum(float(value) for value in known_activity) / (baseline_activity * len(bars))
            if baseline_activity is not None and len(known_activity) == len(bars)
            else None
        )
        known_flow = [bar.signed_flow for bar in bars if bar.signed_flow is not None]
        activity_sum = sum(float(value) for value in known_activity)
        flow_sum = sum(float(value) for value in known_flow)
        absolute_flow = sum(abs(float(value)) for value in known_flow)
        flow_coherence, pressure = self._flow_metrics(
            sign=sign,
            flow_sum=flow_sum,
            absolute_flow=absolute_flow,
            activity_sum=activity_sum,
            inputs_known=(
                len(known_activity) == len(bars) and len(known_flow) == len(bars)
            ),
        )
        favorable = max(
            sign * ((bar.high if sign > 0 else bar.low) - first.open) for bar in bars
        )
        adverse = max(
            -sign * ((bar.low if sign > 0 else bar.high) - first.open) for bar in bars
        )
        progress = displacement / scale
        path_efficiency = displacement / max(travel, self.tick_size)
        impact, realized_capacity, absorption_proxy = self._response_metrics(
            progress, path_efficiency, pressure,
        )
        return ActivityBlock(
            start_ns=first.close_time_ns,
            end_ns=last.close_time_ns,
            bars=len(bars),
            progress=progress,
            path_efficiency=path_efficiency,
            close_location=self._close_location(last, sign),
            favorable_excursion=max(favorable, 0.0) / scale,
            adverse_excursion=max(adverse, 0.0) / scale,
            activity_ratio=activity_ratio,
            flow_coherence=flow_coherence,
            flow_share=flow_coherence,
            pressure=pressure,
            price_response=progress,
            impact_per_pressure=impact,
            realized_capacity=realized_capacity,
            absorption_outcome_proxy=absorption_proxy,
        )

    @staticmethod
    def _first_index(values: Iterable[bool]) -> int | None:
        for index, value in enumerate(values):
            if value:
                return index
        return None

    @staticmethod
    def _later_index(values: list[bool], start: int) -> int | None:
        for index in range(start + 1, len(values)):
            if values[index]:
                return index
        return None

    def _impulse_energy(
        self,
        bars: list[TapeBar],
        baseline_range: float | None,
        baseline_body: float | None,
        baseline_activity: float | None,
    ) -> float | None:
        components: list[float] = []
        if baseline_range is not None:
            components.append(math.log(max(median(bar.high - bar.low for bar in bars), self.tick_size) / baseline_range))
        if baseline_body is not None:
            components.append(math.log(max(median(abs(bar.close - bar.open) for bar in bars), self.tick_size) / baseline_body))
        activities = [bar.activity for bar in bars if bar.activity is not None]
        if baseline_activity is not None and len(activities) == len(bars):
            components.append(math.log(max(median(float(value) for value in activities), 1e-12) / baseline_activity))
        return sum(components) / len(components) if components else None

    def _empty(
        self,
        interaction: StructureInteraction,
        observed_time_ns: int,
        terminal: str,
        reason: str,
    ) -> JourneyEvidence:
        return JourneyEvidence(
            family=None,
            completed=False,
            terminal_state=terminal,
            completed_states=(),
            interaction_time_ns=interaction.interaction_time_ns,
            observed_time_ns=observed_time_ns,
            phase_basis="STRUCTURAL_RANGE",
            blocks=(),
            baseline_range=None,
            baseline_body=None,
            baseline_activity=None,
            baseline_pressure=None,
            activity_input_known=False,
            flow_input_known=False,
            impulse_energy=None,
            settlement="UNKNOWN",
            control_transfer=False,
            control_flow_coherence=None,
            control_flow_share=None,
            control_pressure=None,
            control_pressure_surprise=None,
            control_price_response=0.0,
            control_impact_per_pressure=None,
            realized_capacity_progression=(),
            absorption_outcome_proxy_progression=(),
            reclaim_time_ns=None,
            retest_time_ns=None,
            response_time_ns=None,
            response_close=None,
            response_required_extreme=None,
            stop_intact=None,
            target_fresh=None,
            reason=reason,
        )

    @staticmethod
    def _state_key(interaction: StructureInteraction) -> tuple[object, ...]:
        return (
            interaction.symbol,
            interaction.structure_id,
            interaction.source_side,
            interaction.lower.hex(),
            interaction.upper.hex(),
            interaction.interaction_time_ns,
        )

    def _advance_incremental_state(
        self,
        interaction: StructureInteraction,
        end_seq: int,
    ) -> _IncrementalJourneyState | None:
        key = self._state_key(interaction)
        state = self._states.get(key)
        start_seq = self._oldest_seq + self._bisect_time(interaction.interaction_time_ns)
        if state is not None and state.start_seq != start_seq:
            # The retained baseline/episode boundary changed after ring wrap.
            state = None
            self._states.pop(key, None)
        if state is not None and end_seq < state.last_seq:
            # The public API permits historical/out-of-order observations.
            # Keep that rare path on the reference implementation.
            return None
        if state is None:
            state = _IncrementalJourneyState(
                key=key,
                start_seq=start_seq,
                last_seq=start_seq - 1,
                endpoint_seq=start_seq - 1,
                boundary_one_seq=start_seq,
                boundary_two_seq=start_seq,
            )
            self._states[key] = state
        if state.response_seq is not None:
            return state
        process_end = end_seq
        for seq in range(state.last_seq + 1, process_end + 1):
            item = self._bar_by_seq(seq)
            outside_close = (
                item.close > interaction.upper
                if interaction.outward_sign > 0
                else item.close < interaction.lower
            )
            outside_extreme = (
                item.high > interaction.upper
                if interaction.source_side == "HIGH"
                else item.low < interaction.lower
            )
            touched = item.low <= interaction.upper and item.high >= interaction.lower

            prior_break = state.break_seq
            prior_impulse = state.impulse_seq
            prior_reclaim = state.reclaim_seq
            if state.break_seq is None and outside_close:
                state.break_seq = seq
            if (
                state.retest_seq is None
                and prior_break is not None
                and seq > prior_break
                and touched
            ):
                state.retest_seq = seq
                state.retest_held = outside_close
            if state.impulse_seq is None and outside_extreme:
                state.impulse_seq = seq
            if (
                state.reclaim_seq is None
                and prior_impulse is not None
                and seq > prior_impulse
                and not outside_close
            ):
                state.reclaim_seq = seq
            if prior_reclaim is not None and seq >= prior_reclaim and outside_close:
                state.outside_after_reclaim = True

            state.any_touch = state.any_touch or touched
            state.any_outside_extreme = state.any_outside_extreme or outside_extreme
            state.terminal_outside = outside_close
            state.minimum = min(state.minimum, item.low)
            state.maximum = max(state.maximum, item.high)
            state.activity_known = state.activity_known and item.activity is not None
            state.flow_known = state.flow_known and item.signed_flow is not None
            if item.activity is not None:
                state.activity_total += float(item.activity)
            state.structural_total += max(item.high - item.low, self.tick_size)
            state.endpoint_seq = seq
            state.last_seq = seq

            if (
                state.retest_seq is not None
                and state.retest_held is True
                and seq == state.retest_seq + 1
            ):
                state.response_seq = seq
                break

        new_basis = "TRADED_ACTIVITY" if state.activity_known else "STRUCTURAL_RANGE"
        if new_basis != state.phase_basis:
            state.phase_basis = new_basis
            state.boundaries_initialized = False
        return state

    def _weight(self, seq: int, basis: str) -> float:
        bar = self._bar_by_seq(seq)
        if basis == "TRADED_ACTIVITY":
            if bar.activity is None:  # pragma: no cover - guarded by phase basis
                raise RuntimeError("activity phase contains an unknown activity bar")
            return float(bar.activity)
        return max(bar.high - bar.low, self.tick_size)

    def _incremental_groups(
        self,
        state: _IncrementalJourneyState,
    ) -> list[tuple[int, int]]:
        count = state.endpoint_seq - state.start_seq + 1
        if count <= 3:
            return [
                (seq, seq) for seq in range(state.start_seq, state.endpoint_seq + 1)
            ]
        total = (
            state.activity_total
            if state.phase_basis == "TRADED_ACTIVITY"
            else state.structural_total
        )
        if not state.boundaries_initialized:
            state.boundary_one_seq = state.start_seq
            state.boundary_two_seq = state.start_seq
            state.boundary_one_prefix = 0.0
            state.boundary_two_prefix = 0.0
            state.boundaries_initialized = True

        while state.boundary_one_seq <= state.endpoint_seq:
            weight = self._weight(state.boundary_one_seq, state.phase_basis)
            midpoint = state.boundary_one_prefix + weight / 2.0
            group = min(2, int(midpoint / total * 3))
            if group > 0:
                break
            state.boundary_one_prefix += weight
            state.boundary_one_seq += 1
        while state.boundary_two_seq <= state.endpoint_seq:
            weight = self._weight(state.boundary_two_seq, state.phase_basis)
            midpoint = state.boundary_two_prefix + weight / 2.0
            group = min(2, int(midpoint / total * 3))
            if group > 1:
                break
            state.boundary_two_prefix += weight
            state.boundary_two_seq += 1

        groups = [
            (state.start_seq, state.boundary_one_seq - 1),
            (state.boundary_one_seq, state.boundary_two_seq - 1),
            (state.boundary_two_seq, state.endpoint_seq),
        ]
        for index, (start, end) in enumerate(groups):
            if start <= end:
                continue
            position = min(
                count - 1,
                round((index + 0.5) * count / 3 - 0.5),
            )
            seq = state.start_seq + position
            groups[index] = (seq, seq)
        return groups

    def _block_range(
        self,
        start_seq: int,
        end_seq: int,
        sign: int,
        baseline_range: float | None,
        baseline_activity: float | None,
    ) -> ActivityBlock:
        first = self._bar_by_seq(start_seq)
        last = self._bar_by_seq(end_seq)
        stats = self._range_query(start_seq, end_seq)
        count = end_seq - start_seq + 1
        scale = baseline_range or self.tick_size
        displacement = sign * (last.close - first.open)
        edge = (
            self._range_query(start_seq + 1, end_seq)[6]
            if end_seq > start_seq else 0.0
        )
        travel = abs(first.close - first.open) + edge
        activity_ratio = (
            stats[0] / (baseline_activity * count)
            if baseline_activity is not None and stats[1] == count
            else None
        )
        flow_sum = stats[2]
        absolute_flow = stats[3]
        # Tree association can move the last ulp of a floating sum.  Preserve
        # the reference branch exactly whenever that error could change the
        # sign used by control-transfer admission.
        if (
            stats[4] == count
            and abs(flow_sum) <= absolute_flow * math.ulp(1.0) * max(count, 1)
        ):
            known_flow = [
                self._bar_by_seq(seq).signed_flow
                for seq in range(start_seq, end_seq + 1)
            ]
            flow_sum = sum(float(value) for value in known_flow if value is not None)
            absolute_flow = sum(
                abs(float(value)) for value in known_flow if value is not None
            )
        flow_coherence, pressure = self._flow_metrics(
            sign=sign,
            flow_sum=flow_sum,
            absolute_flow=absolute_flow,
            activity_sum=stats[0],
            inputs_known=(stats[1] == count and stats[4] == count),
        )
        favorable = (
            stats[8] - first.open if sign > 0 else first.open - stats[7]
        )
        adverse = (
            first.open - stats[7] if sign > 0 else stats[8] - first.open
        )
        progress = displacement / scale
        path_efficiency = displacement / max(travel, self.tick_size)
        impact, realized_capacity, absorption_proxy = self._response_metrics(
            progress, path_efficiency, pressure,
        )
        return ActivityBlock(
            start_ns=first.close_time_ns,
            end_ns=last.close_time_ns,
            bars=count,
            progress=progress,
            path_efficiency=path_efficiency,
            close_location=self._close_location(last, sign),
            favorable_excursion=max(favorable, 0.0) / scale,
            adverse_excursion=max(adverse, 0.0) / scale,
            activity_ratio=activity_ratio,
            flow_coherence=flow_coherence,
            flow_share=flow_coherence,
            pressure=pressure,
            price_response=progress,
            impact_per_pressure=impact,
            realized_capacity=realized_capacity,
            absorption_outcome_proxy=absorption_proxy,
        )

    def _materialize_incremental(
        self,
        interaction: StructureInteraction,
        observed_time_ns: int,
        state: _IncrementalJourneyState,
        baseline_range: float | None,
        baseline_body: float | None,
        baseline_activity: float | None,
        baseline_raw_pressure: float | None,
        stop: float | None,
        target: float | None,
    ) -> JourneyEvidence:
        inward = interaction.inward_sign
        outward = interaction.outward_sign
        groups = self._incremental_groups(state)
        inward_blocks = tuple(
            self._block_range(start, end, inward, baseline_range, baseline_activity)
            for start, end in groups
        )
        outward_blocks = tuple(
            self._block_range(start, end, outward, baseline_range, baseline_activity)
            for start, end in groups
        )
        late = inward_blocks[-1]
        late_outward = outward_blocks[-1]
        price_transfer = (
            late.progress > 0.0
            and late.path_efficiency > 0.0
            and late.close_location >= 0.5
        )
        flow_share = late.flow_share
        control_transfer = price_transfer and (flow_share is None or flow_share >= 0.0)

        retest_bar = (
            self._bar_by_seq(state.retest_seq) if state.retest_seq is not None else None
        )
        response_bar = (
            self._bar_by_seq(state.response_seq) if state.response_seq is not None else None
        )
        accepted_resolution: str | None = None
        accepted_completed = False
        response_confirms = False
        accepted_stop_intact: bool | None = None
        accepted_target_fresh: bool | None = None
        if retest_bar is not None and state.retest_held is True and response_bar is None:
            accepted_resolution = "ACCEPTANCE_WAITING_FIRST_RESPONSE"
        elif (
            retest_bar is not None
            and state.retest_held is True
            and response_bar is not None
        ):
            target_touched = (
                response_bar.high >= target if outward > 0 else response_bar.low <= target
            ) if target is not None else False
            stop_touched = (
                response_bar.low <= stop if outward > 0 else response_bar.high >= stop
            ) if stop is not None else False
            accepted_target_fresh = None if target is None else not target_touched
            accepted_stop_intact = None if stop is None else not stop_touched
            response_confirms = (
                response_bar.close > retest_bar.high
                if outward > 0 else response_bar.close < retest_bar.low
            )
            if target_touched:
                accepted_resolution = "ACCEPTANCE_TARGET_SPENT_ON_FIRST_RESPONSE"
            elif stop_touched:
                accepted_resolution = "ACCEPTANCE_STOP_TOUCHED_ON_FIRST_RESPONSE"
            elif not response_confirms:
                accepted_resolution = "ACCEPTANCE_FIRST_RESPONSE_FAILED"
            else:
                accepted_resolution = "ACCEPTED_AUCTION_FIRST_RESPONSE_COMPLETED"
                accepted_completed = True

        impulse_end = state.impulse_seq if state.impulse_seq is not None else state.start_seq
        if state.impulse_summary_seq != impulse_end:
            impulse = [
                self._bar_by_seq(seq) for seq in range(state.start_seq, impulse_end + 1)
            ]
            state.impulse_range_median = max(
                median(bar.high - bar.low for bar in impulse), self.tick_size
            )
            state.impulse_body_median = max(
                median(abs(bar.close - bar.open) for bar in impulse), self.tick_size
            )
            activities = [bar.activity for bar in impulse if bar.activity is not None]
            state.impulse_activity_known = len(activities) == len(impulse)
            state.impulse_activity_median = (
                median(float(value) for value in activities)
                if state.impulse_activity_known else None
            )
            state.impulse_summary_seq = impulse_end
        components: list[float] = []
        if baseline_range is not None:
            components.append(math.log(state.impulse_range_median / baseline_range))
        if baseline_body is not None and state.impulse_body_median is not None:
            components.append(math.log(state.impulse_body_median / baseline_body))
        if (
            baseline_activity is not None
            and state.impulse_activity_known
            and state.impulse_activity_median is not None
        ):
            components.append(
                math.log(max(state.impulse_activity_median, 1e-12) / baseline_activity)
            )
        impulse_energy = sum(components) / len(components) if components else None

        terminal_inside = not state.terminal_outside
        role_flip_progress = (
            interaction.inward_sign
            * (
                self._bar_by_seq(state.endpoint_seq).close
                - self._bar_by_seq(state.retest_seq).close
            )
            if state.retest_seq is not None and state.retest_held is False
            else None
        )
        role_flip_mature = (
            state.retest_seq is None
            or state.retest_held is not False
            or (
                state.endpoint_seq > state.retest_seq
                and role_flip_progress is not None
                and role_flip_progress > 0.0
            )
        )
        failed_complete = (
            state.impulse_seq is not None
            and state.reclaim_seq is not None
            and not state.outside_after_reclaim
            and terminal_inside
            and role_flip_mature
            and control_transfer
        )
        defended_complete = (
            state.any_touch
            and not state.any_outside_extreme
            and terminal_inside
            and control_transfer
        )
        family: JourneyFamily | None = None
        states: list[str] = []
        settlement = "UNSETTLED"
        completed = False
        if accepted_resolution is not None:
            family = "ACCEPTED_AUCTION_CONTINUATION"
            completed = accepted_completed
            states.extend(("OUTSIDE_BREAK", "FIRST_RETEST"))
            if response_bar is None:
                states.append("WAITING_FIRST_RESPONSE")
                settlement = "WAITING_FIRST_RESPONSE"
            elif accepted_resolution == "ACCEPTANCE_TARGET_SPENT_ON_FIRST_RESPONSE":
                states.append("TARGET_SPENT_ON_FIRST_RESPONSE")
                settlement = "DESTINATION_SPENT"
            elif accepted_resolution == "ACCEPTANCE_STOP_TOUCHED_ON_FIRST_RESPONSE":
                states.append("STOP_TOUCHED_ON_FIRST_RESPONSE")
                settlement = "INVALIDATED"
            elif accepted_resolution == "ACCEPTANCE_FIRST_RESPONSE_FAILED":
                states.append("FIRST_RESPONSE_FAILED")
                settlement = "TERMINAL_NO_TRADE"
            else:
                states.extend(("FIRST_RESPONSE_CONFIRMED", "CONTROL_TRANSFER"))
                settlement = "SETTLED_OUTSIDE"
        elif failed_complete:
            family = "FAILED_AUCTION_REVERSAL"
            completed = True
            if retest_bar is not None and state.retest_held is False:
                states.extend(("OUTSIDE_BREAK", "FIRST_RETEST_FAILED"))
            states.extend(("OUTWARD_SWEEP", "RECLAIM", "HELD_INSIDE", "CONTROL_TRANSFER"))
            settlement = "SETTLED_INSIDE"
        elif defended_complete:
            family = "DEFENDED_AUCTION_CONTINUATION"
            completed = True
            states.extend(("BOUNDARY_TOUCH", "COMPLETED_RESPONSE", "CONTROL_TRANSFER"))
            settlement = "DEFENDED_INSIDE"
        elif state.terminal_outside:
            states.append("OUTSIDE_UNSETTLED")
            settlement = "OUTSIDE_UNSETTLED"
        elif retest_bar is not None and state.retest_held is False:
            states.extend(
                (
                    "OUTSIDE_BREAK",
                    "FIRST_RETEST_FAILED",
                    "ROLE_FLIP_UNRESOLVED",
                    "WAIT_DISPLACEMENT",
                ),
            )
            settlement = "ROLE_FLIP_UNRESOLVED"
        elif state.impulse_seq is not None:
            states.append("SWEEP_UNRESOLVED")
            settlement = "INSIDE_UNRESOLVED"
        elif state.any_touch:
            states.append("TOUCH_UNRESOLVED")
            settlement = "INSIDE_UNRESOLVED"

        side_sign = outward if family == "ACCEPTED_AUCTION_CONTINUATION" else inward
        blocks = outward_blocks if family == "ACCEPTED_AUCTION_CONTINUATION" else inward_blocks
        control_block = late
        if family == "ACCEPTED_AUCTION_CONTINUATION":
            control_transfer = response_confirms and accepted_completed
            flow_share = late_outward.flow_share
            control_block = late_outward
        baseline_pressure = (
            side_sign * baseline_raw_pressure
            if baseline_raw_pressure is not None else None
        )
        pressure_surprise = (
            control_block.pressure - baseline_pressure
            if control_block.pressure is not None and baseline_pressure is not None
            else None
        )
        stop_intact = accepted_stop_intact if accepted_resolution is not None else None
        if accepted_resolution is None and stop is not None:
            stop_intact = state.minimum > stop if side_sign > 0 else state.maximum < stop
        target_fresh = accepted_target_fresh if accepted_resolution is not None else None
        if accepted_resolution is None and target is not None:
            target_fresh = state.maximum < target if side_sign > 0 else state.minimum > target
        if accepted_resolution is None and completed and stop_intact is False:
            completed = False
            states.append("STOP_INVALIDATED")
            settlement = "INVALIDATED"
        elif accepted_resolution is None and completed and target_fresh is False:
            completed = False
            states.append("DESTINATION_SPENT")
            settlement = "DESTINATION_SPENT"
        terminal = (
            accepted_resolution
            if accepted_resolution is not None
            else f"{family}_COMPLETED"
            if completed and family is not None
            else "STOP_ALREADY_INVALIDATED"
            if stop_intact is False and family is not None
            else "DESTINATION_ALREADY_SPENT"
            if target_fresh is False and family is not None
            else "AUCTION_JOURNEY_UNRESOLVED"
        )
        reason = " -> ".join(states) if states else "no completed structural response"
        return JourneyEvidence(
            family=family,
            completed=completed,
            terminal_state=terminal,
            completed_states=tuple(states),
            interaction_time_ns=interaction.interaction_time_ns,
            observed_time_ns=response_bar.close_time_ns if response_bar is not None else observed_time_ns,
            phase_basis=state.phase_basis,  # type: ignore[arg-type]
            blocks=blocks,
            baseline_range=baseline_range,
            baseline_body=baseline_body,
            baseline_activity=baseline_activity,
            baseline_pressure=baseline_pressure,
            activity_input_known=state.activity_known,
            flow_input_known=state.flow_known,
            impulse_energy=impulse_energy,
            settlement=settlement,
            control_transfer=control_transfer,
            control_flow_coherence=flow_share,
            control_flow_share=flow_share,
            control_pressure=control_block.pressure,
            control_pressure_surprise=pressure_surprise,
            control_price_response=control_block.price_response,
            control_impact_per_pressure=control_block.impact_per_pressure,
            realized_capacity_progression=tuple(
                block.realized_capacity for block in blocks
            ),
            absorption_outcome_proxy_progression=tuple(
                block.absorption_outcome_proxy for block in blocks
            ),
            reclaim_time_ns=(
                self._bar_by_seq(state.reclaim_seq).close_time_ns
                if state.reclaim_seq is not None else None
            ),
            retest_time_ns=retest_bar.close_time_ns if retest_bar is not None else None,
            response_time_ns=response_bar.close_time_ns if response_bar is not None else None,
            response_close=response_bar.close if response_bar is not None else None,
            response_required_extreme=(
                retest_bar.high if retest_bar is not None and outward > 0
                else retest_bar.low if retest_bar is not None else None
            ),
            stop_intact=stop_intact,
            target_fresh=target_fresh,
            reason=reason,
        )

    def evaluate(
        self,
        interaction: StructureInteraction,
        observed_time_ns: int,
        *,
        stop: float | None = None,
        target: float | None = None,
    ) -> JourneyEvidence:
        """Resolve at most one journey using information known by ``observed``."""

        if interaction.symbol != self.symbol:
            raise ValueError("interaction symbol does not match journey tape")
        if observed_time_ns < interaction.interaction_time_ns:
            raise ValueError("observation cannot precede interaction")
        if stop is not None and not math.isfinite(stop):
            raise ValueError("stop must be finite when provided")
        if target is not None and not math.isfinite(target):
            raise ValueError("target must be finite when provided")
        if self._size and interaction.interaction_time_ns < self._bar_at(0).close_time_ns:
            return self._empty(
                interaction,
                observed_time_ns,
                "HISTORY_UNAVAILABLE",
                "the memory bound truncated the requested structure interaction",
            )
        start_index = self._bisect_time(interaction.interaction_time_ns)
        end_index = self._bisect_time(observed_time_ns, right=True)
        if end_index > start_index:
            end_seq = self._oldest_seq + end_index - 1
            incremental = self._advance_incremental_state(interaction, end_seq)
            if incremental is not None:
                (
                    baseline_range,
                    baseline_body,
                    baseline_activity,
                    baseline_raw_pressure,
                ) = self._baseline(interaction.interaction_time_ns)
                return self._materialize_incremental(
                    interaction,
                    observed_time_ns,
                    incremental,
                    baseline_range,
                    baseline_body,
                    baseline_activity,
                    baseline_raw_pressure,
                    stop,
                    target,
                )
        episode = list(
            self.bars_between(interaction.interaction_time_ns, observed_time_ns)
        )
        if not episode:
            return self._empty(
                interaction,
                observed_time_ns,
                "NO_CAUSAL_TAPE",
                "no completed one-minute bars exist between interaction and observation",
            )

        (
            baseline_range,
            baseline_body,
            baseline_activity,
            baseline_raw_pressure,
        ) = self._baseline(interaction.interaction_time_ns)
        inward = interaction.inward_sign
        outward = interaction.outward_sign
        outer = interaction.upper if interaction.source_side == "HIGH" else interaction.lower
        outside_close_full = [
            bar.close > outer if outward > 0 else bar.close < outer for bar in episode
        ]
        band_touch_full = [
            bar.low <= interaction.upper and bar.high >= interaction.lower for bar in episode
        ]

        # The first physical post-break return owns the continuation decision.
        # A close back inside consumes continuation and leaves only a genuine
        # failed-auction role flip; a later cleaner retest cannot revive it.
        break_index = self._first_index(outside_close_full)
        retest_index: int | None = None
        if break_index is not None:
            retest_index = self._later_index(band_touch_full, break_index)
        retest_held = (
            outside_close_full[retest_index] if retest_index is not None else None
        )
        response_index = (
            retest_index + 1
            if (
                retest_index is not None
                and retest_held is True
                and retest_index + 1 < len(episode)
            )
            else None
        )
        retest_bar = episode[retest_index] if retest_index is not None else None
        response_bar = episode[response_index] if response_index is not None else None
        accepted_resolution: str | None = None
        accepted_completed = False
        response_confirms = False
        accepted_stop_intact: bool | None = None
        accepted_target_fresh: bool | None = None
        if retest_bar is not None and retest_held is True and response_bar is None:
            accepted_resolution = "ACCEPTANCE_WAITING_FIRST_RESPONSE"
        elif retest_bar is not None and retest_held is True and response_bar is not None:
            # Intrabar destination/stop touches own the outcome before the
            # response-close decision, in the same ordering as RE1.
            target_touched = (
                response_bar.high >= target if outward > 0 else response_bar.low <= target
            ) if target is not None else False
            stop_touched = (
                response_bar.low <= stop if outward > 0 else response_bar.high >= stop
            ) if stop is not None else False
            accepted_target_fresh = None if target is None else not target_touched
            accepted_stop_intact = None if stop is None else not stop_touched
            response_confirms = (
                response_bar.close > retest_bar.high
                if outward > 0
                else response_bar.close < retest_bar.low
            )
            if target_touched:
                accepted_resolution = "ACCEPTANCE_TARGET_SPENT_ON_FIRST_RESPONSE"
            elif stop_touched:
                accepted_resolution = "ACCEPTANCE_STOP_TOUCHED_ON_FIRST_RESPONSE"
            elif not response_confirms:
                accepted_resolution = "ACCEPTANCE_FIRST_RESPONSE_FAILED"
            else:
                accepted_resolution = "ACCEPTED_AUCTION_FIRST_RESPONSE_COMPLETED"
                accepted_completed = True
            # Freeze every later evaluation at the first response event.
            episode = episode[: response_index + 1]

        phase_basis, groups = self._activity_groups(episode)
        inward_blocks = tuple(
            self._block(group, inward, baseline_range, baseline_activity) for group in groups
        )
        outward_blocks = tuple(
            self._block(group, outward, baseline_range, baseline_activity) for group in groups
        )
        late = inward_blocks[-1]
        late_outward = outward_blocks[-1]
        flow_known = all(bar.signed_flow is not None for bar in episode)
        activity_known = all(bar.activity is not None for bar in episode)
        flow_share = late.flow_share
        # Price establishes control transfer.  Known flow may corroborate it;
        # missing flow remains unknown and is never converted into neutral flow.
        price_transfer = (
            late.progress > 0.0
            and late.path_efficiency > 0.0
            and late.close_location >= 0.5
        )
        control_transfer = price_transfer and (flow_share is None or flow_share >= 0.0)
        outward_flow_share = late_outward.flow_share

        outside_extreme = [
            bar.high > outer if interaction.source_side == "HIGH" else bar.low < outer
            for bar in episode
        ]
        outside_close = [
            bar.close > outer if interaction.source_side == "HIGH" else bar.close < outer
            for bar in episode
        ]
        band_touch = [bar.low <= interaction.upper and bar.high >= interaction.lower for bar in episode]
        terminal_outside = outside_close[-1]
        terminal_inside = not terminal_outside
        impulse_index = self._first_index(outside_extreme)
        impulse_bars = episode[: (impulse_index + 1 if impulse_index is not None else 1)]
        impulse_energy = self._impulse_energy(
            impulse_bars, baseline_range, baseline_body, baseline_activity
        )

        family: JourneyFamily | None = None
        states: list[str] = []
        settlement = "UNSETTLED"
        completed = False

        # Failed: a true outward excursion, reclaim through the boundary, no
        # subsequent outside close, and inward control transfer.
        reclaim_flags = [not value for value in outside_close]
        reclaim_index = (
            self._later_index(reclaim_flags, impulse_index)
            if impulse_index is not None
            else None
        )
        held_inside = (
            reclaim_index is not None and not any(outside_close[reclaim_index:])
        )
        failed_complete = (
            impulse_index is not None
            and reclaim_index is not None
            and held_inside
            and terminal_inside
            and (
                retest_index is None
                or retest_held is not False
                or (
                    len(episode) - 1 > retest_index
                    and inward * (episode[-1].close - episode[retest_index].close) > 0.0
                )
            )
            and control_transfer
        )

        # Defended is deliberately disjoint from failed: the band was touched
        # but price never auctioned beyond its outer edge.
        defended_complete = (
            any(band_touch)
            and not any(outside_extreme)
            and terminal_inside
            and control_transfer
        )

        if accepted_resolution is not None:
            family = "ACCEPTED_AUCTION_CONTINUATION"
            completed = accepted_completed
            states.extend(("OUTSIDE_BREAK", "FIRST_RETEST"))
            if response_bar is None:
                states.append("WAITING_FIRST_RESPONSE")
                settlement = "WAITING_FIRST_RESPONSE"
            elif accepted_resolution == "ACCEPTANCE_TARGET_SPENT_ON_FIRST_RESPONSE":
                states.append("TARGET_SPENT_ON_FIRST_RESPONSE")
                settlement = "DESTINATION_SPENT"
            elif accepted_resolution == "ACCEPTANCE_STOP_TOUCHED_ON_FIRST_RESPONSE":
                states.append("STOP_TOUCHED_ON_FIRST_RESPONSE")
                settlement = "INVALIDATED"
            elif accepted_resolution == "ACCEPTANCE_FIRST_RESPONSE_FAILED":
                states.append("FIRST_RESPONSE_FAILED")
                settlement = "TERMINAL_NO_TRADE"
            else:
                states.extend(("FIRST_RESPONSE_CONFIRMED", "CONTROL_TRANSFER"))
                settlement = "SETTLED_OUTSIDE"
        elif failed_complete:
            family = "FAILED_AUCTION_REVERSAL"
            completed = True
            if retest_bar is not None and retest_held is False:
                states.extend(("OUTSIDE_BREAK", "FIRST_RETEST_FAILED"))
            states.extend(("OUTWARD_SWEEP", "RECLAIM", "HELD_INSIDE", "CONTROL_TRANSFER"))
            settlement = "SETTLED_INSIDE"
        elif defended_complete:
            family = "DEFENDED_AUCTION_CONTINUATION"
            completed = True
            states.extend(("BOUNDARY_TOUCH", "COMPLETED_RESPONSE", "CONTROL_TRANSFER"))
            settlement = "DEFENDED_INSIDE"
        else:
            if terminal_outside:
                states.append("OUTSIDE_UNSETTLED")
                settlement = "OUTSIDE_UNSETTLED"
            elif retest_bar is not None and retest_held is False:
                states.extend(
                    (
                        "OUTSIDE_BREAK",
                        "FIRST_RETEST_FAILED",
                        "ROLE_FLIP_UNRESOLVED",
                        "WAIT_DISPLACEMENT",
                    ),
                )
                settlement = "ROLE_FLIP_UNRESOLVED"
            elif impulse_index is not None:
                states.append("SWEEP_UNRESOLVED")
                settlement = "INSIDE_UNRESOLVED"
            elif any(band_touch):
                states.append("TOUCH_UNRESOLVED")
                settlement = "INSIDE_UNRESOLVED"

        side_sign = outward if family == "ACCEPTED_AUCTION_CONTINUATION" else inward
        blocks = outward_blocks if family == "ACCEPTED_AUCTION_CONTINUATION" else inward_blocks
        control_block = late
        if family == "ACCEPTED_AUCTION_CONTINUATION":
            # The strict close beyond the retest favorable extreme is RE1's
            # accepted-auction control-transfer event.  Signed flow remains
            # evidence but does not override that exact price event.
            control_transfer = response_confirms and accepted_completed
            flow_share = outward_flow_share
            control_block = late_outward
        baseline_pressure = (
            side_sign * baseline_raw_pressure
            if baseline_raw_pressure is not None else None
        )
        pressure_surprise = (
            control_block.pressure - baseline_pressure
            if control_block.pressure is not None and baseline_pressure is not None
            else None
        )
        minimum = min(bar.low for bar in episode)
        maximum = max(bar.high for bar in episode)
        stop_intact = accepted_stop_intact if accepted_resolution is not None else None
        if accepted_resolution is None and stop is not None:
            stop_intact = minimum > stop if side_sign > 0 else maximum < stop
        target_fresh = accepted_target_fresh if accepted_resolution is not None else None
        if accepted_resolution is None and target is not None:
            target_fresh = maximum < target if side_sign > 0 else minimum > target
        if accepted_resolution is None and completed and stop_intact is False:
            completed = False
            states.append("STOP_INVALIDATED")
            settlement = "INVALIDATED"
        elif accepted_resolution is None and completed and target_fresh is False:
            completed = False
            states.append("DESTINATION_SPENT")
            settlement = "DESTINATION_SPENT"

        terminal = (
            accepted_resolution
            if accepted_resolution is not None
            else f"{family}_COMPLETED"
            if completed and family is not None
            else "STOP_ALREADY_INVALIDATED"
            if stop_intact is False and family is not None
            else "DESTINATION_ALREADY_SPENT"
            if target_fresh is False and family is not None
            else "AUCTION_JOURNEY_UNRESOLVED"
        )
        reason = " -> ".join(states) if states else "no completed structural response"
        return JourneyEvidence(
            family=family,
            completed=completed,
            terminal_state=terminal,
            completed_states=tuple(states),
            interaction_time_ns=interaction.interaction_time_ns,
            observed_time_ns=(
                response_bar.close_time_ns if response_bar is not None else observed_time_ns
            ),
            phase_basis=phase_basis,  # type: ignore[arg-type]
            blocks=blocks,
            baseline_range=baseline_range,
            baseline_body=baseline_body,
            baseline_activity=baseline_activity,
            baseline_pressure=baseline_pressure,
            activity_input_known=activity_known,
            flow_input_known=flow_known,
            impulse_energy=impulse_energy,
            settlement=settlement,
            control_transfer=control_transfer,
            control_flow_coherence=flow_share,
            control_flow_share=flow_share,
            control_pressure=control_block.pressure,
            control_pressure_surprise=pressure_surprise,
            control_price_response=control_block.price_response,
            control_impact_per_pressure=control_block.impact_per_pressure,
            realized_capacity_progression=tuple(
                block.realized_capacity for block in blocks
            ),
            absorption_outcome_proxy_progression=tuple(
                block.absorption_outcome_proxy for block in blocks
            ),
            reclaim_time_ns=(
                episode[reclaim_index].close_time_ns
                if reclaim_index is not None else None
            ),
            retest_time_ns=retest_bar.close_time_ns if retest_bar is not None else None,
            response_time_ns=response_bar.close_time_ns if response_bar is not None else None,
            response_close=response_bar.close if response_bar is not None else None,
            response_required_extreme=(
                (retest_bar.high if outward > 0 else retest_bar.low)
                if retest_bar is not None
                else None
            ),
            stop_intact=stop_intact,
            target_fresh=target_fresh,
            reason=reason,
        )


__all__ = [
    "ACCEPTANCE_FIRST_RESPONSE_RULE",
    "ActivityBlock",
    "CausalJourneyRegistry",
    "EventTimeAuctionJourney",
    "JourneyClaim",
    "JourneyEvidence",
    "JourneyFamily",
    "PROVENANCE",
    "StructureInteraction",
    "TapeBar",
]
