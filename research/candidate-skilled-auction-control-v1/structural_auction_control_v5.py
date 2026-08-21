"""Structural auction control v5: event-time auction journeys.

This policy stops treating a completed pattern label as the decision.  Existing
EasyChart engines are reused only as causal public-structure and immutable
geometry sensors.  Execution is owned by an explicit auction journey:

* failed auction: public liquidity sweep -> absorption -> reclaim -> held
  control -> delivery;
* accepted auction: outside break -> outside hold -> controlled return ->
  reacceleration;
* defended auction: mature public boundary touch -> completed response ->
  delivery.

The journey is read from completed one-minute price/volume bars between the
recorded structure interaction and the proposed entry.  Bars are divided by
cumulative traded activity rather than fixed clock slices, so the state observer
adapts to changing liquidity.  Rolling medians use only bars preceding the
interaction.  No trained score, PnL feedback, symbol rule, time exit, target
lattice, trade quota, confidence sizing or silent fallback is used.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import math
from statistics import median
from typing import Any, Iterable

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle
from structural_auction_control_v2 import (
    FIVE_MINUTES_NS,
    StructuralProposal,
    _construct_bundle,
    _descriptor,
    _gross_rr,
    _has_any,
    _number,
    _price_geometry,
    _structure_id,
    _text,
)
from structural_auction_control_v2_strict import StructuralAuctionControlV2Bundle as _Base


EVENT_TIME_JOURNEY_RULE = (
    "EXTERNAL_METHOD:COMPLETED_AUCTION_BARS_ARE_SEGMENTED_BY_CUMULATIVE_TRADED_"
    "ACTIVITY_AND_INTERPRETED_AS_AN_EXPLICIT_DURATION_STATE_JOURNEY"
)
FAILED_AUCTION_JOURNEY_RULE = (
    "SOURCE_EXPLICIT:PUBLIC_LIQUIDITY_SWEEP_ABSORPTION_RECLAIM_HOLD_AND_FIRST_"
    "DELIVERY_OWN_A_FAILED_AUCTION_REVERSAL"
)
ACCEPTED_AUCTION_JOURNEY_RULE = (
    "SOURCE_EXPLICIT:OUTSIDE_BREAK_OUTSIDE_HOLD_CONTROLLED_RETURN_AND_FIRST_"
    "REACCELERATION_OWN_AN_ACCEPTED_AUCTION_CONTINUATION"
)
DEFENDED_AUCTION_JOURNEY_RULE = (
    "SOURCE_EXPLICIT:MATURE_PUBLIC_BOUNDARY_TOUCH_COMPLETED_PRICE_VOLUME_"
    "RESPONSE_AND_DELIVERY_OWN_A_DEFENDED_CONTINUATION"
)
FIRST_CAUSAL_DESTINATION_RULE = (
    "SOURCE_EXPLICIT:THE_FIRST_UNSPENT_OPPOSING_PUBLIC_STRUCTURE_SELECTED_"
    "BEFORE_ENTRY_OWNS_THE_FULL_TARGET"
)
NO_SYNTHETIC_GEOMETRY_RULE = (
    "IMPLEMENTATION_VALIDITY:FIXED_RR_ATR_PERCENT_AND_CLOCK_GEOMETRY_CANNOT_"
    "ENTER_THE_EVENT_TIME_AUCTION_POLICY"
)
ONE_CAUSAL_JOURNEY_RULE = (
    "IMPLEMENTATION_VALIDITY:ONE_STRUCTURE_INTERACTION_TIME_AND_OVERLAPPING_"
    "PRICE_EPISODE_HAS_ONE_EXECUTABLE_OWNER_WITHOUT_CLOCK_EXPIRY"
)
for _rule in (
    EVENT_TIME_JOURNEY_RULE,
    FAILED_AUCTION_JOURNEY_RULE,
    ACCEPTED_AUCTION_JOURNEY_RULE,
    DEFENDED_AUCTION_JOURNEY_RULE,
    FIRST_CAUSAL_DESTINATION_RULE,
    NO_SYNTHETIC_GEOMETRY_RULE,
    ONE_CAUSAL_JOURNEY_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


@dataclass(frozen=True, slots=True)
class TapeBar:
    ts_close_ns: int
    open: float
    high: float
    low: float
    close: float
    activity: float
    signed_flow: float


@dataclass(frozen=True, slots=True)
class ActivityBlock:
    start_ns: int
    end_ns: int
    bars: int
    progress: float
    path_efficiency: float
    flow_share: float
    close_location: float
    activity_ratio: float
    favorable_excursion: float
    adverse_excursion: float


@dataclass(frozen=True, slots=True)
class JourneyEvidence:
    accepted: bool
    terminal_state: str
    completed_states: tuple[str, ...]
    reason: str
    blocks: tuple[ActivityBlock, ...]
    baseline_range: float
    baseline_activity: float
    target_fresh: bool
    stop_intact: bool
    interaction_time_ns: int
    observed_time_ns: int


@dataclass(frozen=True, slots=True)
class JourneyClaim:
    structure_id: str
    interaction_time_ns: int
    lower: float
    upper: float
    plan_id: str
    owner: str


class CausalJourneyRegistry:
    """Exact causal ownership; no arbitrary thirty-minute episode lifetime."""

    def __init__(self, tick_size: float) -> None:
        self.tick_size = tick_size
        self.claims: list[JourneyClaim] = []
        self._exact: dict[tuple[str, int], JourneyClaim] = {}

    @staticmethod
    def _finite_overlap(left: StructuralProposal, right: JourneyClaim, tick: float) -> bool:
        values = (left.lower, left.upper, right.lower, right.upper)
        return all(math.isfinite(value) for value in values) and (
            max(left.lower, right.lower) <= min(left.upper, right.upper) + tick
        )

    def existing_owner(self, proposal: StructuralProposal) -> str | None:
        exact = self._exact.get((proposal.structure_id, proposal.interaction_time_ns))
        if exact is not None:
            return exact.owner
        for claim in reversed(self.claims):
            if abs(claim.interaction_time_ns - proposal.interaction_time_ns) > FIVE_MINUTES_NS:
                continue
            if self._finite_overlap(proposal, claim, self.tick_size):
                return claim.owner
        return None

    def claim(self, proposal: StructuralProposal, plan: V5TradePlan) -> None:
        claim = JourneyClaim(
            proposal.structure_id,
            proposal.interaction_time_ns,
            proposal.lower,
            proposal.upper,
            plan.plan_id,
            proposal.mechanism,
        )
        self.claims.append(claim)
        self._exact[(claim.structure_id, claim.interaction_time_ns)] = claim


class EventTimeTape:
    """Causal one-minute tape with equal-activity episode segmentation."""

    HISTORY_BARS = 4320
    BASELINE_BARS = 360
    MAX_EPISODE_BARS = 180

    def __init__(self, tick_size: float) -> None:
        self.tick_size = tick_size
        self.bars: deque[TapeBar] = deque(maxlen=self.HISTORY_BARS)

    @staticmethod
    def _value(bar: Any, *names: str) -> float:
        for name in names:
            value = _number(getattr(bar, name, math.nan))
            if math.isfinite(value):
                return value
        return math.nan

    def observe(self, bar: Candle) -> None:
        ts = int(self._value(bar, "ts_close_ns", "ts_event", "close_time_ns"))
        opened = self._value(bar, "open", "open_price")
        high = self._value(bar, "high", "high_price")
        low = self._value(bar, "low", "low_price")
        closed = self._value(bar, "close", "close_price")
        if not all(math.isfinite(value) for value in (opened, high, low, closed)):
            return
        quote = self._value(bar, "quote_volume")
        base = self._value(bar, "volume", "base_volume")
        activity = quote if math.isfinite(quote) and quote > 0.0 else base
        if not math.isfinite(activity) or activity <= 0.0:
            activity = max(high - low, self.tick_size)

        buy_quote = self._value(bar, "taker_buy_quote_volume")
        if math.isfinite(quote) and quote > 0.0 and math.isfinite(buy_quote):
            signed_flow = 2.0 * buy_quote - quote
        else:
            buy_base = self._value(
                bar,
                "taker_buy_base_volume",
                "taker_buy_volume",
                "buy_volume",
                "aggressive_buy_volume",
            )
            signed_flow = (
                2.0 * buy_base - base
                if math.isfinite(base) and base > 0.0 and math.isfinite(buy_base)
                else 0.0
            )
        self.bars.append(TapeBar(ts, opened, high, low, closed, activity, signed_flow))

    def _baseline(self, interaction_ns: int) -> tuple[float, float, float]:
        prior = [bar for bar in self.bars if bar.ts_close_ns < interaction_ns]
        prior = prior[-self.BASELINE_BARS :]
        ranges = [max(bar.high - bar.low, self.tick_size) for bar in prior]
        activities = [bar.activity for bar in prior if bar.activity > 0.0]
        bodies = [abs(bar.close - bar.open) for bar in prior]
        return (
            max(median(ranges) if ranges else self.tick_size, self.tick_size),
            max(median(activities) if activities else 1.0, 1e-12),
            max(median(bodies) if bodies else self.tick_size, self.tick_size),
        )

    @staticmethod
    def _aligned_close_location(bar: TapeBar, sign: int) -> float:
        width = max(bar.high - bar.low, 1e-12)
        raw = (bar.close - bar.low) / width
        return raw if sign > 0 else 1.0 - raw

    @staticmethod
    def _split_by_activity(bars: list[TapeBar], blocks: int = 3) -> list[list[TapeBar]]:
        if not bars:
            return []
        if len(bars) <= blocks:
            return [[bar] for bar in bars]
        weights = [max(bar.activity, 1e-12) for bar in bars]
        total = sum(weights)
        cumulative = 0.0
        groups: list[list[TapeBar]] = [[] for _ in range(blocks)]
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

    def _block(self, bars: list[TapeBar], sign: int, baseline_range: float, baseline_activity: float) -> ActivityBlock:
        first, last = bars[0], bars[-1]
        progress = sign * (last.close - first.open) / baseline_range
        moves = [
            bars[index].close - (first.open if index == 0 else bars[index - 1].close)
            for index in range(len(bars))
        ]
        path = sum(abs(move) for move in moves)
        path_efficiency = sign * (last.close - first.open) / max(path, self.tick_size)
        total_flow = sum(bar.signed_flow for bar in bars)
        absolute_flow = sum(abs(bar.signed_flow) for bar in bars)
        flow_share = sign * total_flow / absolute_flow if absolute_flow > 0.0 else 0.0
        close_location = self._aligned_close_location(last, sign)
        activity_ratio = sum(bar.activity for bar in bars) / (baseline_activity * len(bars))
        origin = first.open
        favorable = max(
            sign * (bar.high - origin) if sign > 0 else sign * (bar.low - origin)
            for bar in bars
        )
        adverse = max(
            -sign * (bar.low - origin) if sign > 0 else -sign * (bar.high - origin)
            for bar in bars
        )
        return ActivityBlock(
            first.ts_close_ns,
            last.ts_close_ns,
            len(bars),
            progress,
            path_efficiency,
            flow_share,
            close_location,
            activity_ratio,
            max(favorable, 0.0) / baseline_range,
            max(adverse, 0.0) / baseline_range,
        )

    @staticmethod
    def _side_sign(plan: V5TradePlan) -> int:
        side = _text(getattr(plan, "side", ""))
        if any(token in side for token in ("LONG", "BUY", "BULL", "UP")):
            return 1
        if any(token in side for token in ("SHORT", "SELL", "BEAR", "DOWN")):
            return -1
        entry, _, target = _price_geometry(plan)
        return 1 if target > entry else -1

    @staticmethod
    def _first_index(values: Iterable[bool]) -> int | None:
        for index, value in enumerate(values):
            if value:
                return index
        return None

    def evaluate(self, plan: V5TradePlan, mechanism: str) -> JourneyEvidence:
        sign = self._side_sign(plan)
        interaction_ns = int(_number(getattr(plan, "interaction_time_ns", 0), 0.0))
        observed_ns = int(_number(getattr(plan, "observed_time_ns", interaction_ns), float(interaction_ns)))
        baseline_range, baseline_activity, baseline_body = self._baseline(interaction_ns)
        episode = [bar for bar in self.bars if interaction_ns <= bar.ts_close_ns <= observed_ns][
            -self.MAX_EPISODE_BARS :
        ]
        if not episode:
            return JourneyEvidence(
                False,
                "NO_CAUSAL_TAPE",
                (),
                "no completed one-minute bars between interaction and decision",
                (),
                baseline_range,
                baseline_activity,
                False,
                False,
                interaction_ns,
                observed_ns,
            )
        blocks = tuple(
            self._block(group, sign, baseline_range, baseline_activity)
            for group in self._split_by_activity(episode, 3)
        )
        first, last = blocks[0], blocks[-1]
        entry, stop, target = _price_geometry(plan)
        minimum = min(bar.low for bar in episode)
        maximum = max(bar.high for bar in episode)
        final_close = episode[-1].close
        lower = _number(getattr(plan, "overlap_lower", math.nan))
        upper = _number(getattr(plan, "overlap_upper", math.nan))
        center = (lower + upper) / 2.0 if math.isfinite(lower) and math.isfinite(upper) else entry
        stop_intact = minimum > stop if sign > 0 else maximum < stop
        target_fresh = maximum < target if sign > 0 else minimum > target

        latest = episode[-1]
        direct_impulse = (
            sign * (latest.close - latest.open) > baseline_body
            and latest.activity >= baseline_activity
            and self._aligned_close_location(latest, sign) >= 0.5
        )
        late_delivery = (
            last.progress > 0.0
            and last.path_efficiency > 0.0
            and last.close_location >= 0.5
        )
        late_control = late_delivery and (
            last.flow_share >= 0.0 or (last.flow_share < 0.0 and last.progress > 0.0)
        )

        completed: list[str] = []
        accepted = False
        terminal = "UNRESOLVED"

        if mechanism == "FAILED_AUCTION_REVERSAL":
            sweep = (
                minimum < lower
                if sign > 0 and math.isfinite(lower)
                else maximum > upper
                if sign < 0 and math.isfinite(upper)
                else first.adverse_excursion > 0.0
            )
            if sweep:
                completed.append("SWEEP")
            adverse_flow_absorbed = (
                sum(bar.signed_flow for bar in episode) * sign < 0.0
                and sign * (final_close - episode[0].open) > 0.0
            )
            wick_rejection = (
                first.adverse_excursion > first.favorable_excursion
                and sign * (final_close - center) > 0.0
            )
            absorption = adverse_flow_absorbed or wick_rejection
            if absorption:
                completed.append("ABSORPTION")
            reclaimed = (
                final_close > upper
                if sign > 0 and math.isfinite(upper)
                else final_close < lower
                if sign < 0 and math.isfinite(lower)
                else sign * (final_close - center) > 0.0
            )
            if reclaimed:
                completed.append("RECLAIM")
            reclaim_flags = [
                bar.close > upper
                if sign > 0 and math.isfinite(upper)
                else bar.close < lower
                if sign < 0 and math.isfinite(lower)
                else sign * (bar.close - center) > 0.0
                for bar in episode
            ]
            reclaim_index = self._first_index(reclaim_flags)
            held = False
            if reclaim_index is not None:
                held = all(
                    bar.close >= lower
                    if sign > 0 and math.isfinite(lower)
                    else bar.close <= upper
                    if sign < 0 and math.isfinite(upper)
                    else sign * (bar.close - center) >= 0.0
                    for bar in episode[reclaim_index:]
                )
            if held:
                completed.append("HOLD")
            if late_control or direct_impulse:
                completed.append("DELIVERY")
            accepted = (
                sweep
                and reclaimed
                and held
                and (absorption or last.flow_share >= 0.0)
                and (late_control or direct_impulse)
                and stop_intact
                and target_fresh
            )
            terminal = "FAILED_AUCTION_DELIVERY" if accepted else "FAILED_AUCTION_INCOMPLETE"

        elif mechanism in {
            "ACCEPTED_AUCTION_CONTINUATION",
            "HORIZONTAL_ACCEPTANCE",
            "STRUCTURAL_PULLBACK",
        }:
            outside = [
                bar.close > upper
                if sign > 0 and math.isfinite(upper)
                else bar.close < lower
                if sign < 0 and math.isfinite(lower)
                else sign * (bar.close - center) > 0.0
                for bar in episode
            ]
            break_index = self._first_index(outside)
            broken = break_index is not None
            if broken:
                completed.append("BREAK")
            held = False
            returned = False
            if break_index is not None:
                after = episode[break_index + 1 :]
                held = bool(after) and any(
                    bar.close > upper
                    if sign > 0 and math.isfinite(upper)
                    else bar.close < lower
                    if sign < 0 and math.isfinite(lower)
                    else sign * (bar.close - center) > 0.0
                    for bar in after
                )
                returned = any(
                    bar.low <= upper
                    if sign > 0 and math.isfinite(upper)
                    else bar.high >= lower
                    if sign < 0 and math.isfinite(lower)
                    else sign * (bar.close - episode[break_index].close) < 0.0
                    for bar in after
                )
            if held:
                completed.append("HOLD")
            if returned:
                completed.append("CONTROLLED_RETURN")
            outside_final = (
                final_close > upper
                if sign > 0 and math.isfinite(upper)
                else final_close < lower
                if sign < 0 and math.isfinite(lower)
                else sign * (final_close - center) > 0.0
            )
            reaccelerated = late_control and outside_final
            if reaccelerated:
                completed.append("REACCELERATION")
            accepted = broken and held and returned and reaccelerated and stop_intact and target_fresh
            terminal = "ACCEPTED_AUCTION_REACCELERATION" if accepted else "ACCEPTED_AUCTION_INCOMPLETE"

        else:
            touched = (
                minimum <= upper and maximum >= lower
                if math.isfinite(lower) and math.isfinite(upper)
                else True
            )
            if touched:
                completed.append("DEFENDED_TOUCH")
            responded = sign * (final_close - center) > 0.0 and last.close_location >= 0.5
            if responded:
                completed.append("COMPLETED_RESPONSE")
            if late_control or direct_impulse:
                completed.append("DELIVERY")
            accepted = touched and responded and (late_control or direct_impulse) and stop_intact and target_fresh
            terminal = "DEFENDED_AUCTION_DELIVERY" if accepted else "DEFENDED_AUCTION_INCOMPLETE"

        reason = " -> ".join(completed) if completed else "required auction stages absent"
        if not stop_intact:
            terminal = "STOP_ALREADY_INVALIDATED"
            reason = "structural invalidation traded before decision"
            accepted = False
        elif not target_fresh:
            terminal = "DESTINATION_ALREADY_SPENT"
            reason = "first causal destination traded before decision"
            accepted = False

        return JourneyEvidence(
            accepted,
            terminal,
            tuple(completed),
            reason,
            blocks,
            baseline_range,
            baseline_activity,
            target_fresh,
            stop_intact,
            interaction_ns,
            observed_ns,
        )


def _required_bundle(module_name: str, symbol: str, tick_size: float, minimum_gross_rr: float) -> Any:
    bundle = _construct_bundle(module_name, symbol, tick_size, minimum_gross_rr)
    if bundle is None:
        raise RuntimeError(f"required structural sensor {module_name!r} could not be constructed")
    return bundle


def _journey_mechanism(plan: V5TradePlan, source: str) -> str | None:
    descriptor = _descriptor(plan)
    path = _text(getattr(plan, "scenario_path", ""))
    if not _has_any(
        descriptor,
        (
            "CHANNEL",
            "TREND",
            "DIAGONAL",
            "HORIZONTAL",
            "SUPPORT",
            "RESISTANCE",
            "LIQUIDITY",
            "SWING",
            "ORDER_BLOCK",
            "FVG",
            "PULLBACK",
            "DECISION_AREA",
        ),
    ):
        return None
    if "REJECTION" in path or "REJECTION" in descriptor or "REVERSAL" in descriptor:
        return "FAILED_AUCTION_REVERSAL"
    if "PULLBACK" in descriptor:
        return "STRUCTURAL_PULLBACK"
    if "ACCEPTANCE" in path or "ACCEPTANCE" in descriptor or "FLIP" in descriptor:
        return "HORIZONTAL_ACCEPTANCE" if "HORIZONTAL" in descriptor else "ACCEPTED_AUCTION_CONTINUATION"
    if any(token in path or token in descriptor for token in ("BOUNCE", "ROTATION", "CONTINUATION")):
        return "DEFENDED_AUCTION_CONTINUATION"
    return None


class StructuralAuctionControlV5Bundle(_Base):
    """Multiple structural sensors, one event-time causal decision policy."""

    _PRIORITY = {
        "ACCEPTED_AUCTION_CONTINUATION": 0,
        "HORIZONTAL_ACCEPTANCE": 1,
        "FAILED_AUCTION_REVERSAL": 2,
        "STRUCTURAL_PULLBACK": 3,
        "DEFENDED_AUCTION_CONTINUATION": 4,
    }
    _SYNTHETIC = (
        "FIXED_RR",
        "RR_LATTICE",
        "ATR_TARGET",
        "ATR_STOP",
        "PERCENT_TARGET",
        "PERCENT_STOP",
        "CLOCK_TARGET",
        "CLOCK_STOP",
    )
    _DESTINATION_PRIORITY = (
        ("CHANNEL_MID", "CHANNEL_EDGE", "CHANNEL_EXTENSION", "CHANNEL"),
        ("SWING", "PRIOR_HIGH", "PRIOR_LOW", "EQUAL_HIGH", "EQUAL_LOW", "LIQUIDITY"),
        ("TREND", "DIAGONAL"),
        ("ORDER_BLOCK", "ORDERBLOCK", "FVG", "IMBALANCE"),
        ("VOLUME_NODE", "VOLUME_PROFILE"),
    )

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        complete = _required_bundle(
            "easychart_re1_complete_bot_policy_v2", symbol, tick_size, self.minimum_gross_rr
        )
        human = _required_bundle(
            "easychart_re1_human_policy", symbol, tick_size, self.minimum_gross_rr
        )
        self.sources = [
            ("CHANNEL_CONTROL", self.channel_control),
            ("SKILLED_STRUCTURE", self.natural_geometry),
            ("COMPLETE_OPPORTUNITY", complete),
            ("HUMAN_IMMEDIATE", human),
        ]
        self.detectors = self.channel_control.detectors
        self.registry = CausalJourneyRegistry(tick_size)
        self.tape = EventTimeTape(tick_size)
        self._journey_by_raw_plan: dict[str, JourneyEvidence] = {}
        self._journey_trace: list[dict[str, Any]] = []

    @classmethod
    def _destination_rank(cls, descriptor: str) -> int:
        for index, group in enumerate(cls._DESTINATION_PRIORITY):
            if _has_any(descriptor, group):
                return index
        return len(cls._DESTINATION_PRIORITY)

    def _geometry_rank(self, proposal: StructuralProposal) -> tuple[Any, ...]:
        plan = proposal.plan
        entry, _, target = _price_geometry(plan)
        distance = abs(target - entry) if math.isfinite(entry) and math.isfinite(target) else math.inf
        descriptor = _descriptor(plan)
        journey = self._journey_by_raw_plan.get(plan.plan_id)
        blocks = () if journey is None else journey.blocks
        late_progress = blocks[-1].progress if blocks else -math.inf
        return (
            self._PRIORITY[proposal.mechanism],
            self._destination_rank(descriptor),
            -late_progress,
            distance,
            proposal.observed_time_ns,
            plan.plan_id,
        )

    def _proposal(self, plan: V5TradePlan, source: str) -> StructuralProposal | None:
        descriptor = _descriptor(plan)
        if _has_any(descriptor, self._SYNTHETIC):
            self._inc("synthetic_geometry_rejected")
            return None
        mechanism = _journey_mechanism(plan, source)
        if mechanism is None:
            self._inc("proposal_not_owned_by_event_time_policy")
            return None
        entry, stop, target = _price_geometry(plan)
        gross_rr = _gross_rr(plan)
        if not all(math.isfinite(value) for value in (entry, stop, target, gross_rr)):
            self._inc("nonfinite_preentry_geometry")
            return None
        sign = EventTimeTape._side_sign(plan)
        valid = stop < entry < target if sign > 0 else target < entry < stop
        if not valid or gross_rr + 1e-12 < self.minimum_gross_rr:
            self._inc("invalid_or_sub_one_r_geometry")
            return None
        observed = int(_number(getattr(plan, "observed_time_ns", 0), 0.0))
        interaction = int(_number(getattr(plan, "interaction_time_ns", observed), float(observed)))
        proposal = StructuralProposal(
            plan,
            source,
            mechanism,
            _structure_id(plan),
            interaction,
            observed,
            _number(getattr(plan, "overlap_lower", math.nan)),
            _number(getattr(plan, "overlap_upper", math.nan)),
        )
        journey = self.tape.evaluate(plan, mechanism)
        self._journey_by_raw_plan[plan.plan_id] = journey
        self._journey_trace.append(
            {
                "scenario_kind": "event_time_auction_journey",
                "event_time_ns": observed,
                "plan_id": plan.plan_id,
                "symbol": self.symbol,
                "source": source,
                "mechanism": mechanism,
                "accepted": journey.accepted,
                "terminal_state": journey.terminal_state,
                "completed_states": journey.completed_states,
                "reason": journey.reason,
                "interaction_time_ns": journey.interaction_time_ns,
                "observed_time_ns": journey.observed_time_ns,
                "target_fresh": journey.target_fresh,
                "stop_intact": journey.stop_intact,
                "blocks": [
                    {
                        "start_ns": block.start_ns,
                        "end_ns": block.end_ns,
                        "bars": block.bars,
                        "progress": block.progress,
                        "path_efficiency": block.path_efficiency,
                        "flow_share": block.flow_share,
                        "close_location": block.close_location,
                        "activity_ratio": block.activity_ratio,
                        "favorable_excursion": block.favorable_excursion,
                        "adverse_excursion": block.adverse_excursion,
                    }
                    for block in journey.blocks
                ],
                "rule_provenance": (
                    EVENT_TIME_JOURNEY_RULE,
                    FAILED_AUCTION_JOURNEY_RULE,
                    ACCEPTED_AUCTION_JOURNEY_RULE,
                    DEFENDED_AUCTION_JOURNEY_RULE,
                ),
            }
        )
        if not journey.accepted:
            self._inc(f"journey_rejected_{journey.terminal_state.lower()}")
            return None
        self._inc(f"journey_completed_{journey.terminal_state.lower()}")
        return proposal

    def _namespace(self, proposal: StructuralProposal) -> V5TradePlan:
        plan = super()._namespace(proposal)
        journey = self._journey_by_raw_plan.get(proposal.plan.plan_id)
        terminal = "UNKNOWN" if journey is None else journey.terminal_state
        return replace(
            plan,
            family=f"SAC_V5_{terminal}:{plan.family}",
            causal_event_id=(
                f"SAC_V5:{proposal.structure_id}:{proposal.interaction_time_ns}:"
                f"{proposal.plan.causal_event_id}"
            ),
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == 1:
            self.tape.observe(bar)
        return super().on_bar(timeframe_minutes, bar)

    def drain_trace(self) -> list[dict[str, Any]]:
        output = super().drain_trace() + self._journey_trace
        self._journey_trace = []
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = super().diagnostics
        output["structural_auction_control_v5"] = {
            "tape_bars": len(self.tape.bars),
            "journeys_observed": len(self._journey_by_raw_plan),
            "sensors": tuple(name for name, _ in self.sources),
            "owners": tuple(self._PRIORITY),
            "rules": (
                EVENT_TIME_JOURNEY_RULE,
                FAILED_AUCTION_JOURNEY_RULE,
                ACCEPTED_AUCTION_JOURNEY_RULE,
                DEFENDED_AUCTION_JOURNEY_RULE,
                FIRST_CAUSAL_DESTINATION_RULE,
                NO_SYNTHETIC_GEOMETRY_RULE,
                ONE_CAUSAL_JOURNEY_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = StructuralAuctionControlV5Bundle