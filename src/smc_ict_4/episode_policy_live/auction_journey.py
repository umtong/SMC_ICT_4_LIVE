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
    flow_share: float | None


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
    activity_input_known: bool
    flow_input_known: bool
    impulse_energy: float | None
    settlement: str
    control_transfer: bool
    control_flow_share: float | None
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
        if self._size < self._history_bars:
            write_index = (self._start + self._size) % self._history_bars
            self._size += 1
        else:
            write_index = self._start
            self._start = (self._start + 1) % self._history_bars
        self._buffer[write_index] = item

    @staticmethod
    def _known_median(values: Iterable[float | None]) -> float | None:
        known = [float(value) for value in values if value is not None and value > 0.0]
        return float(median(known)) if known else None

    def _baseline(self, interaction_time_ns: int) -> tuple[float | None, float | None, float | None]:
        end = self._bisect_time(interaction_time_ns)
        start = max(0, end - self.BASELINE_BARS)
        prior = [self._bar_at(index) for index in range(start, end)]
        if not prior:
            return None, None, None
        baseline_range = self._known_median(max(bar.high - bar.low, self.tick_size) for bar in prior)
        baseline_body = self._known_median(max(abs(bar.close - bar.open), self.tick_size) for bar in prior)
        baseline_activity = self._known_median(bar.activity for bar in prior)
        return baseline_range, baseline_body, baseline_activity

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
        absolute_flow = sum(abs(float(value)) for value in known_flow)
        flow_share = (
            sign * sum(float(value) for value in known_flow) / absolute_flow
            if len(known_flow) == len(bars) and absolute_flow > 0.0
            else 0.0
            if len(known_flow) == len(bars)
            else None
        )
        favorable = max(
            sign * ((bar.high if sign > 0 else bar.low) - first.open) for bar in bars
        )
        adverse = max(
            -sign * ((bar.low if sign > 0 else bar.high) - first.open) for bar in bars
        )
        return ActivityBlock(
            start_ns=first.close_time_ns,
            end_ns=last.close_time_ns,
            bars=len(bars),
            progress=displacement / scale,
            path_efficiency=displacement / max(travel, self.tick_size),
            close_location=self._close_location(last, sign),
            favorable_excursion=max(favorable, 0.0) / scale,
            adverse_excursion=max(adverse, 0.0) / scale,
            activity_ratio=activity_ratio,
            flow_share=flow_share,
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
            activity_input_known=False,
            flow_input_known=False,
            impulse_energy=None,
            settlement="UNKNOWN",
            control_transfer=False,
            control_flow_share=None,
            retest_time_ns=None,
            response_time_ns=None,
            response_close=None,
            response_required_extreme=None,
            stop_intact=None,
            target_fresh=None,
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

        baseline_range, baseline_body, baseline_activity = self._baseline(
            interaction.interaction_time_ns
        )
        inward = interaction.inward_sign
        outward = interaction.outward_sign
        outer = interaction.upper if interaction.source_side == "HIGH" else interaction.lower
        outside_close_full = [
            bar.close > outer if outward > 0 else bar.close < outer for bar in episode
        ]
        band_touch_full = [
            bar.low <= interaction.upper and bar.high >= interaction.lower for bar in episode
        ]

        # Existing RE1 acceptance-response ownership (1142aca): once the first
        # held retest has closed outside, the very first later completed bar is
        # the whole response event.  It cannot be replaced by a more favorable
        # candle observed later.  This is a structural transition, not a minute
        # timeout.
        break_index = self._first_index(outside_close_full)
        retest_index: int | None = None
        if break_index is not None:
            held_retests = [
                touched and outside
                for touched, outside in zip(band_touch_full, outside_close_full, strict=True)
            ]
            retest_index = self._later_index(held_retests, break_index)
        response_index = (
            retest_index + 1
            if retest_index is not None and retest_index + 1 < len(episode)
            else None
        )
        retest_bar = episode[retest_index] if retest_index is not None else None
        response_bar = episode[response_index] if response_index is not None else None
        accepted_resolution: str | None = None
        accepted_completed = False
        response_confirms = False
        accepted_stop_intact: bool | None = None
        accepted_target_fresh: bool | None = None
        if retest_bar is not None and response_bar is None:
            accepted_resolution = "ACCEPTANCE_WAITING_FIRST_RESPONSE"
        elif retest_bar is not None and response_bar is not None:
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
            elif impulse_index is not None:
                states.append("SWEEP_UNRESOLVED")
                settlement = "INSIDE_UNRESOLVED"
            elif any(band_touch):
                states.append("TOUCH_UNRESOLVED")
                settlement = "INSIDE_UNRESOLVED"

        side_sign = outward if family == "ACCEPTED_AUCTION_CONTINUATION" else inward
        blocks = outward_blocks if family == "ACCEPTED_AUCTION_CONTINUATION" else inward_blocks
        if family == "ACCEPTED_AUCTION_CONTINUATION":
            # The strict close beyond the retest favorable extreme is RE1's
            # accepted-auction control-transfer event.  Signed flow remains
            # evidence but does not override that exact price event.
            control_transfer = response_confirms and accepted_completed
            flow_share = outward_flow_share
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
            activity_input_known=activity_known,
            flow_input_known=flow_known,
            impulse_energy=impulse_energy,
            settlement=settlement,
            control_transfer=control_transfer,
            control_flow_share=flow_share,
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
