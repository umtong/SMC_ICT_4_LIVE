#!/usr/bin/env python3
"""Derive V18 order-book-resilience auction states.

V17 separated deleveraging (falling open interest) from new-position expansion,
but its expansion family treated a same-direction close and one-minute hold as
acceptance. V18 broadens that event into a *pre-candidate* and lets observed
best-quote resilience decide the scenario:

* unusually high price impact with weak opposing queue replenishment confirms a
  liquidity-vacuum continuation;
* unusually strong directional OFI with poor price progress and strong opposing
  replenishment is absorption; only a later full event-range CHoCH confirms a
  reversal;
* mixed responses are NO_TRADE.

Every threshold is a causal quartile of the ten minutes immediately before
the expansion event, split into 30-second quote-response blocks. The expansion
event itself is excluded from both the baseline and the post-event observation.
The module creates schedules only. Orders, fills, fees, funding, positions,
margin, accounting and NAV remain the pinned NautilusTrader path.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import zipfile
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Any, Sequence

from derive_nt_lvcfr_v13_signals import (
    FIRST_BREAK_CHOCH_REVERSAL,
    MEASURED_ACCEPTANCE_CONTINUATION,
    derive_v13,
)
from nt_lvcfr_data import (
    FIVE_MINUTES_NS,
    NS_PER_MINUTE,
    NS_PER_SECOND,
    _atr,
    _five,
    load_kline_minutes,
    load_open_interest,
    normalize_timestamp_ns,
)

L1_VACUUM_CONTINUATION = "L1_VACUUM_CONTINUATION"
L1_ABSORPTION_FULL_RANGE_CHOCH_REVERSAL = (
    "L1_ABSORPTION_FULL_RANGE_CHOCH_REVERSAL"
)
L1_EXPANSION_PRE_CANDIDATE = "L1_EXPANSION_PRE_CANDIDATE"

BASELINE_MINUTES = 10
BLOCK_SECONDS = 30
BASELINE_BLOCKS = BASELINE_MINUTES * 60 // BLOCK_SECONDS
OBSERVATION_SECONDS = 30
REVERSAL_EXPIRY_MINUTES = 120
MIN_EFFECTIVE_UPDATES = 20


@dataclass(frozen=True, slots=True)
class Quote:
    ts_event: int
    ts_init: int
    bid: float
    bid_qty: float
    ask: float
    ask_qty: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_bp(self) -> float:
        mid = self.mid
        return (
            (self.ask - self.bid) / mid * 10_000.0
            if mid > 0.0
            else math.inf
        )

    @property
    def microprice(self) -> float:
        total = self.bid_qty + self.ask_qty
        if total <= 0.0:
            return self.mid
        return (self.ask * self.bid_qty + self.bid * self.ask_qty) / total


@dataclass(frozen=True, slots=True)
class L1Features:
    updates: int
    progress_bp: float
    microprice_bp: float
    directional_ofi_norm: float
    gross_flow_norm: float
    impact_efficiency: float
    opposing_replenishment_norm: float
    opposing_depletion_norm: float
    opposing_depth_ratio: float
    spread_end_bp: float
    low_mid: float
    high_mid: float
    last_mid: float


@dataclass(slots=True)
class BlockAccumulator:
    direction: int
    first: Quote | None = None
    last: Quote | None = None
    updates: int = 0
    ofi: float = 0.0
    gross_flow: float = 0.0
    opposing_replenishment: float = 0.0
    opposing_depletion: float = 0.0
    depth_sum: float = 0.0
    low_mid: float = math.inf
    high_mid: float = -math.inf

    def update(self, previous: Quote, current: Quote) -> None:
        if self.first is None:
            self.first = previous
            self.low_mid = previous.mid
            self.high_mid = previous.mid
        if (
            previous.bid == current.bid
            and previous.bid_qty == current.bid_qty
            and previous.ask == current.ask
            and previous.ask_qty == current.ask_qty
        ):
            self.last = current
            return
        if current.bid > previous.bid:
            bid_flow = current.bid_qty
        elif current.bid == previous.bid:
            bid_flow = current.bid_qty - previous.bid_qty
        else:
            bid_flow = -previous.bid_qty
        if current.ask < previous.ask:
            ask_flow = current.ask_qty
        elif current.ask == previous.ask:
            ask_flow = current.ask_qty - previous.ask_qty
        else:
            ask_flow = -previous.ask_qty
        self.ofi += bid_flow - ask_flow
        self.gross_flow += abs(bid_flow) + abs(ask_flow)
        opposing_flow = ask_flow if self.direction > 0 else bid_flow
        self.opposing_replenishment += max(opposing_flow, 0.0)
        self.opposing_depletion += max(-opposing_flow, 0.0)
        self.depth_sum += max(
            1e-12,
            (
                previous.bid_qty
                + previous.ask_qty
                + current.bid_qty
                + current.ask_qty
            )
            / 4.0,
        )
        self.updates += 1
        self.last = current
        self.low_mid = min(self.low_mid, current.mid)
        self.high_mid = max(self.high_mid, current.mid)

    def features(self) -> L1Features | None:
        if (
            self.first is None
            or self.last is None
            or self.updates < MIN_EFFECTIVE_UPDATES
            or self.depth_sum <= 0.0
        ):
            return None
        first_mid = self.first.mid
        last_mid = self.last.mid
        if min(first_mid, last_mid) <= 0.0:
            return None
        progress = self.direction * (last_mid / first_mid - 1.0) * 10_000.0
        micro = self.direction * (
            self.last.microprice / self.first.microprice - 1.0
        ) * 10_000.0
        ofi_norm = self.direction * self.ofi / self.depth_sum
        gross_flow_norm = self.gross_flow / self.depth_sum
        if gross_flow_norm <= 1e-12:
            return None
        replenish_norm = self.opposing_replenishment / self.depth_sum
        depletion_norm = self.opposing_depletion / self.depth_sum
        first_opposing = (
            self.first.ask_qty if self.direction > 0 else self.first.bid_qty
        )
        last_opposing = (
            self.last.ask_qty if self.direction > 0 else self.last.bid_qty
        )
        depth_ratio = last_opposing / max(first_opposing, 1e-12)
        # Price response per unit of total best-quote event flow. Gross flow
        # avoids an unstable division by nearly cancelling net OFI while still
        # identifying movement through a thin or withdrawing opposing queue.
        efficiency = progress / gross_flow_norm
        return L1Features(
            updates=self.updates,
            progress_bp=progress,
            microprice_bp=micro,
            directional_ofi_norm=ofi_norm,
            gross_flow_norm=gross_flow_norm,
            impact_efficiency=efficiency,
            opposing_replenishment_norm=replenish_norm,
            opposing_depletion_norm=depletion_norm,
            opposing_depth_ratio=depth_ratio,
            spread_end_bp=self.last.spread_bp,
            low_mid=self.low_mid,
            high_mid=self.high_mid,
            last_mid=last_mid,
        )


@dataclass(slots=True)
class CandidateBlocks:
    signal: dict[str, Any]
    start_ns: int
    baseline_end_ns: int
    confirm_ns: int
    end_ns: int
    blocks: list[BlockAccumulator] = field(default_factory=list)
    last_quote: Quote | None = None

    def __post_init__(self) -> None:
        direction = int(self.signal["direction"])
        self.blocks = [
            BlockAccumulator(direction=direction)
            for _ in range(BASELINE_BLOCKS + 1)
        ]

    def consume(self, quote: Quote) -> None:
        previous = self.last_quote
        self.last_quote = quote
        if previous is None:
            return
        block_ns = BLOCK_SECONDS * NS_PER_SECOND
        if self.start_ns <= quote.ts_init < self.baseline_end_ns:
            index = int((quote.ts_init - self.start_ns) // block_ns)
        elif self.confirm_ns <= quote.ts_init < self.end_ns:
            index = BASELINE_BLOCKS
        else:
            # During the ten-minute expansion event we advance last_quote but do
            # not contaminate the pre-shock baseline or post-shock observation.
            return
        if 0 <= index < len(self.blocks):
            self.blocks[index].update(previous, quote)


def _quantile(values: Sequence[float], probability: float) -> float:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        raise ValueError("quantile requires finite values")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be in [0, 1]")
    position = (len(clean) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return clean[lower]
    weight = position - lower
    return clean[lower] * (1.0 - weight) + clean[upper] * weight


def classify_resilience(
    baseline: Sequence[L1Features],
    observation: L1Features,
) -> str | None:
    """Classify a quote response using only its causal baseline distribution."""
    if len(baseline) < BASELINE_BLOCKS // 2:
        return None
    q75_progress = _quantile([item.progress_bp for item in baseline], 0.75)
    q25_progress = _quantile([item.progress_bp for item in baseline], 0.25)
    q75_efficiency = _quantile(
        [item.impact_efficiency for item in baseline], 0.75
    )
    q75_ofi = _quantile(
        [item.directional_ofi_norm for item in baseline], 0.75
    )
    q50_replenishment = _quantile(
        [item.opposing_replenishment_norm for item in baseline], 0.50
    )
    q50_depletion = _quantile(
        [item.opposing_depletion_norm for item in baseline], 0.50
    )
    q75_replenishment = _quantile(
        [item.opposing_replenishment_norm for item in baseline], 0.75
    )
    q50_depth_ratio = _quantile(
        [item.opposing_depth_ratio for item in baseline], 0.50
    )
    q75_spread = _quantile(
        [item.spread_end_bp for item in baseline], 0.75
    )

    continuation = (
        observation.progress_bp > max(0.0, q75_progress)
        and observation.microprice_bp > 0.0
        and observation.impact_efficiency >= q75_efficiency
        and observation.opposing_replenishment_norm <= q50_replenishment
        and observation.opposing_depletion_norm >= q50_depletion
        and observation.opposing_depth_ratio <= q50_depth_ratio
        and observation.spread_end_bp <= q75_spread
    )
    if continuation:
        return L1_VACUUM_CONTINUATION

    absorption = (
        observation.directional_ofi_norm >= q75_ofi
        and observation.progress_bp <= min(0.0, q25_progress)
        and observation.microprice_bp <= 0.0
        and observation.opposing_replenishment_norm >= q75_replenishment
        and observation.opposing_depth_ratio >= q50_depth_ratio
        and observation.spread_end_bp <= q75_spread
    )
    if absorption:
        return L1_ABSORPTION_FULL_RANGE_CHOCH_REVERSAL
    return None


def derive_expansion_pre_candidates(
    *,
    raw_root: Path,
    evaluation_start_ns: int,
    evaluation_end_ns: int,
    local_range_minutes: int = 30,
    waypoint_minutes: int = 240,
    first_displacement_bp: float = 12.0,
    total_oi_increase_bp: float = 10.0,
    activity_baseline_5m: int = 72,
    activity_min_periods: int = 24,
    second_activity_min: float = 0.70,
    atr_minutes: int = 60,
) -> list[dict[str, Any]]:
    futures = load_kline_minutes(
        sorted((raw_root / "futures_kline").glob("*.zip"))
    )
    spot = load_kline_minutes(
        sorted((raw_root / "spot_kline").glob("*.zip"))
    )
    oi = load_open_interest(
        sorted((raw_root / "open_interest").glob("*.zip"))
    )
    futures_by_minute = {bar.minute_index: bar for bar in futures}
    atr = _atr(futures, atr_minutes)
    futures_five = _five(futures)
    spot_five = _five(spot)
    aligned = sorted(set(futures_five) & set(spot_five) & set(oi))
    signals: list[dict[str, Any]] = []
    last_event_end_ns: int | None = None

    for index in range(2, len(aligned)):
        second_end = aligned[index]
        first_end = aligned[index - 1]
        before_end = aligned[index - 2]
        if not evaluation_start_ns <= second_end < evaluation_end_ns:
            continue
        if (
            second_end - first_end != FIVE_MINUTES_NS
            or first_end - before_end != FIVE_MINUTES_NS
        ):
            continue
        event_start_ns = first_end - FIVE_MINUTES_NS
        if last_event_end_ns is not None and event_start_ns < last_event_end_ns:
            continue
        baseline_times = aligned[
            max(0, index - activity_baseline_5m) : index
        ]
        baseline = [
            futures_five[timestamp].notional for timestamp in baseline_times
        ]
        if len(baseline) < activity_min_periods:
            continue
        baseline_notional = median(baseline)
        if baseline_notional <= 0.0:
            continue
        first = futures_five[first_end]
        second = futures_five[second_end]
        second_spot = spot_five[second_end]
        direction = (
            1 if first.return_bp > 0.0 else (-1 if first.return_bp < 0.0 else 0)
        )
        if direction == 0:
            continue
        first_displacement = direction * first.return_bp
        second_progress = (
            direction * (second.close / first.close - 1.0) * 10_000.0
        )
        oi_before = oi[before_end]
        oi_first = oi[first_end]
        oi_second = oi[second_end]
        first_oi_increase = (oi_first / oi_before - 1.0) * 10_000.0
        second_oi_increase = (oi_second / oi_first - 1.0) * 10_000.0
        total_oi_increase = (oi_second / oi_before - 1.0) * 10_000.0
        activity_ratio = second.notional / baseline_notional
        futures_flow = direction * second.flow
        spot_flow = direction * second_spot.flow
        end_minute = second_end // NS_PER_MINUTE
        at = atr.get(end_minute - 1)
        values = (
            first_displacement,
            second_progress,
            first_oi_increase,
            second_oi_increase,
            total_oi_increase,
            activity_ratio,
            futures_flow,
            spot_flow,
            at or float("nan"),
        )
        if (
            at is None
            or at <= 0.0
            or not all(math.isfinite(value) for value in values)
        ):
            continue
        if not (
            first_displacement >= first_displacement_bp
            and second_progress > 0.0
            and first_oi_increase > 0.0
            and second_oi_increase > 0.0
            and total_oi_increase >= total_oi_increase_bp
            and activity_ratio >= second_activity_min
            and futures_flow > 0.0
            and spot_flow > 0.0
        ):
            continue

        event_start_minute = end_minute - 10
        local_rows = [
            futures_by_minute.get(minute)
            for minute in range(
                event_start_minute - local_range_minutes,
                event_start_minute,
            )
        ]
        event_rows = [
            futures_by_minute.get(minute)
            for minute in range(event_start_minute, end_minute)
        ]
        waypoint_rows = [
            futures_by_minute.get(minute)
            for minute in range(
                event_start_minute - waypoint_minutes,
                event_start_minute,
            )
        ]
        if (
            any(row is None for row in local_rows)
            or any(row is None for row in event_rows)
            or any(row is None for row in waypoint_rows)
        ):
            continue
        local = [row for row in local_rows if row is not None]
        event = [row for row in event_rows if row is not None]
        prior = [row for row in waypoint_rows if row is not None]
        boundary = (
            max(row.high for row in local)
            if direction > 0
            else min(row.low for row in local)
        )
        if direction * (second.close - boundary) <= 0.0:
            continue
        waypoint = (
            max(row.high for row in prior)
            if direction > 0
            else min(row.low for row in prior)
        )
        scenario_id = "NT-LVCFR-V18-L1-PRECANDIDATE-" + sha256(
            (
                f"{second_end}|{direction}|{boundary:.12g}|"
                f"{second.close:.12g}"
            ).encode()
        ).hexdigest()[:16]
        signals.append(
            {
                "scenario_id": scenario_id,
                "scenario_kind": L1_EXPANSION_PRE_CANDIDATE,
                "entry_kind": "CONTINUATION",
                "confirm_time_ns": second_end,
                "eligible_time_ns": second_end,
                "direction": direction,
                "initial_stop": boundary - direction * 0.20 * at,
                "atr": at,
                "first_start_time_ns": event_start_minute * NS_PER_MINUTE,
                "first_end_time_ns": first_end,
                "details": {
                    "scenario_kind": L1_EXPANSION_PRE_CANDIDATE,
                    "original_direction": direction,
                    "event_start_minute": event_start_minute,
                    "event_end_minute": end_minute,
                    "event_low": min(row.low for row in event),
                    "event_high": max(row.high for row in event),
                    "broken_external_boundary": boundary,
                    "prior_240_external": waypoint,
                    "first_displacement_bp": first_displacement,
                    "second_progress_bp": second_progress,
                    "first_oi_increase_bp": first_oi_increase,
                    "second_oi_increase_bp": second_oi_increase,
                    "total_oi_increase_bp": total_oi_increase,
                    "second_activity_ratio": activity_ratio,
                    "second_directional_futures_flow": futures_flow,
                    "second_directional_spot_flow": spot_flow,
                },
            }
        )
        last_event_end_ns = second_end
    return signals


def collect_candidate_blocks(
    *,
    book_ticker_paths: Sequence[Path],
    candidates: Sequence[dict[str, Any]],
) -> list[CandidateBlocks]:
    contexts = [
        CandidateBlocks(
            signal=signal,
            start_ns=int(signal["first_start_time_ns"])
            - BASELINE_MINUTES * NS_PER_MINUTE,
            baseline_end_ns=int(signal["first_start_time_ns"]),
            confirm_ns=int(signal["confirm_time_ns"]),
            end_ns=int(signal["confirm_time_ns"])
            + OBSERVATION_SECONDS * NS_PER_SECOND,
        )
        for signal in candidates
    ]
    starts = sorted(
        (context.start_ns, index) for index, context in enumerate(contexts)
    )
    next_start = 0
    active: set[int] = set()
    previous_global: Quote | None = None
    previous_ts = -1
    maximum_end = max((context.end_ns for context in contexts), default=-1)

    for path in sorted(book_ticker_paths, key=lambda item: item.name):
        with zipfile.ZipFile(path) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.lower().endswith(".csv")
            ]
            if len(names) != 1:
                raise ValueError(f"expected one bookTicker CSV in {path}")
            with archive.open(names[0]) as raw:
                reader = csv.reader(
                    io.TextIOWrapper(raw, encoding="utf-8", newline="")
                )
                for row in reader:
                    if not row or not row[0] or not row[0][0].isdigit():
                        continue
                    if len(row) < 7:
                        raise ValueError(f"bookTicker row too short in {path}")
                    transaction_ns = normalize_timestamp_ns(int(row[5]))
                    observed_ns = max(
                        transaction_ns,
                        normalize_timestamp_ns(int(row[6])),
                    )
                    if observed_ns < previous_ts:
                        raise ValueError("bookTicker observed time moved backwards")
                    previous_ts = observed_ns
                    while (
                        next_start < len(starts)
                        and starts[next_start][0] <= observed_ns
                    ):
                        _, candidate_index = starts[next_start]
                        contexts[candidate_index].last_quote = previous_global
                        active.add(candidate_index)
                        next_start += 1
                    active = {
                        index
                        for index in active
                        if contexts[index].end_ns >= observed_ns
                    }
                    if (
                        not active
                        and next_start >= len(starts)
                        and observed_ns > maximum_end
                    ):
                        return contexts
                    quote = Quote(
                        ts_event=transaction_ns,
                        ts_init=observed_ns,
                        bid=float(row[1]),
                        bid_qty=float(row[2]),
                        ask=float(row[3]),
                        ask_qty=float(row[4]),
                    )
                    if min(
                        quote.bid,
                        quote.ask,
                        quote.bid_qty,
                        quote.ask_qty,
                    ) <= 0.0:
                        # Do not poison the next valid OFI transition with an
                        # invalid zero/negative quote.
                        continue
                    for index in active:
                        contexts[index].consume(quote)
                    previous_global = quote
    return contexts


def _first_full_range_choch(
    futures_by_minute: dict[int, Any],
    *,
    start_minute: int,
    original_direction: int,
    event_low: float,
    event_high: float,
) -> tuple[int, int, float] | None:
    for offset in range(REVERSAL_EXPIRY_MINUTES):
        minute = start_minute + offset
        row = futures_by_minute.get(minute)
        if row is None:
            return None
        close = float(row.close)
        if original_direction > 0 and close < event_low:
            return minute, -1, close
        if original_direction < 0 and close > event_high:
            return minute, 1, close
    return None


def route_l1_candidates(
    *,
    raw_root: Path,
    pre_candidates: Sequence[dict[str, Any]],
    stop_buffer_atr: float = 0.20,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    book_paths = sorted((raw_root / "book_ticker").glob("*.zip"))
    if not book_paths:
        raise ValueError("V18 requires official bookTicker archives")
    futures = load_kline_minutes(
        sorted((raw_root / "futures_kline").glob("*.zip"))
    )
    futures_by_minute = {row.minute_index: row for row in futures}
    contexts = collect_candidate_blocks(
        book_ticker_paths=book_paths,
        candidates=pre_candidates,
    )
    routed: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    def count(reason: str) -> None:
        counts[reason] = counts.get(reason, 0) + 1

    for context in contexts:
        baseline = [
            features
            for block in context.blocks[:BASELINE_BLOCKS]
            if (features := block.features()) is not None
        ]
        observation = context.blocks[BASELINE_BLOCKS].features()
        if observation is None or len(baseline) < BASELINE_BLOCKS // 2:
            count("INSUFFICIENT_L1_CONTEXT")
            continue
        state = classify_resilience(baseline, observation)
        if state is None:
            count("MIXED_RESILIENCE_NO_TRADE")
            continue
        original = context.signal
        details = dict(original.get("details", {}))
        original_direction = int(original["direction"])
        atr = float(original["atr"])
        boundary = float(details["broken_external_boundary"])
        event_low = float(details["event_low"])
        event_high = float(details["event_high"])

        baseline_summary = {
            "valid_blocks": len(baseline),
            "progress_q25": _quantile(
                [x.progress_bp for x in baseline], 0.25
            ),
            "progress_q75": _quantile(
                [x.progress_bp for x in baseline], 0.75
            ),
            "ofi_q75": _quantile(
                [x.directional_ofi_norm for x in baseline], 0.75
            ),
            "efficiency_q75": _quantile(
                [x.impact_efficiency for x in baseline], 0.75
            ),
            "opposing_replenishment_q50": _quantile(
                [x.opposing_replenishment_norm for x in baseline], 0.50
            ),
            "opposing_replenishment_q75": _quantile(
                [x.opposing_replenishment_norm for x in baseline], 0.75
            ),
            "opposing_depletion_q50": _quantile(
                [x.opposing_depletion_norm for x in baseline], 0.50
            ),
            "opposing_depth_ratio_q50": _quantile(
                [x.opposing_depth_ratio for x in baseline], 0.50
            ),
            "spread_q75": _quantile(
                [x.spread_end_bp for x in baseline], 0.75
            ),
        }
        observation_payload = {
            key: getattr(observation, key)
            for key in L1Features.__dataclass_fields__
        }

        if state == L1_VACUUM_CONTINUATION:
            direction = original_direction
            confirm_ns = context.end_ns
            stop = (
                min(observation.low_mid, boundary) - stop_buffer_atr * atr
                if direction > 0
                else max(observation.high_mid, boundary)
                + stop_buffer_atr * atr
            )
            entry_kind = "CONTINUATION"
            target_mode = "EXISTING_NET_R_OBJECTIVE"
            count(L1_VACUUM_CONTINUATION)
        else:
            start_minute = math.ceil(context.end_ns / NS_PER_MINUTE)
            choch = _first_full_range_choch(
                futures_by_minute,
                start_minute=start_minute,
                original_direction=original_direction,
                event_low=event_low,
                event_high=event_high,
            )
            if choch is None:
                count("ABSORPTION_WITHOUT_FULL_RANGE_CHOCH")
                continue
            minute, direction, choch_close = choch
            confirm_ns = (minute + 1) * NS_PER_MINUTE
            stop = (
                min(observation.low_mid, event_low) - stop_buffer_atr * atr
                if direction > 0
                else max(observation.high_mid, event_high)
                + stop_buffer_atr * atr
            )
            entry_kind = "REVERSAL"
            target_mode = "EXISTING_NET_R_OBJECTIVE"
            count(L1_ABSORPTION_FULL_RANGE_CHOCH_REVERSAL)

        reference = (
            observation.last_mid
            if state == L1_VACUUM_CONTINUATION
            else choch_close
        )
        if direction * (reference - stop) <= 0.0:
            count("NON_EXECUTABLE_STRUCTURAL_STOP")
            continue
        suffix = str(original["scenario_id"]).rsplit("-", 1)[-1]
        signal = dict(original)
        signal["scenario_id"] = f"NT-LVCFR-V18-{state}-{suffix}"
        signal["scenario_kind"] = state
        signal["entry_kind"] = entry_kind
        signal["direction"] = direction
        signal["confirm_time_ns"] = confirm_ns
        signal["eligible_time_ns"] = confirm_ns
        signal["initial_stop"] = stop
        signal["disable_rapid_failure_reversal"] = True
        signal["target_mode"] = target_mode
        signal.pop("structural_target", None)
        signal.pop("structural_protection_trigger", None)
        details.update(
            {
                "scenario_kind": state,
                "l1_baseline_minutes": BASELINE_MINUTES,
                "l1_baseline_relation": "PRE_EVENT_NOT_EXPANSION_EVENT",
                "l1_block_seconds": BLOCK_SECONDS,
                "l1_observation_seconds": OBSERVATION_SECONDS,
                "l1_baseline": baseline_summary,
                "l1_observation": observation_payload,
                "l1_decision_time_ns": context.end_ns,
                "routed_direction": direction,
                "stop_buffer_atr": stop_buffer_atr,
            }
        )
        signal["details"] = details
        routed.append(signal)
    return routed, counts


def derive_v18(
    *,
    source_signals: Path,
    raw_root: Path,
    data_manifest_path: Path,
    output_signals: Path,
    output_manifest: Path,
) -> list[dict[str, Any]]:
    data_manifest = json.loads(data_manifest_path.read_text(encoding="utf-8"))
    v13_signals_path = output_manifest.with_name(
        output_manifest.stem + "-v13-intermediate-signals.json"
    )
    v13_manifest_path = output_manifest.with_name(
        output_manifest.stem + "-v13-intermediate.json"
    )
    v13_all = derive_v13(
        source_signals=source_signals,
        raw_root=raw_root,
        output_signals=v13_signals_path,
        output_manifest=v13_manifest_path,
    )
    deleveraging = [
        signal
        for signal in v13_all
        if str(signal.get("scenario_kind"))
        in {FIRST_BREAK_CHOCH_REVERSAL, MEASURED_ACCEPTANCE_CONTINUATION}
    ]
    pre_candidates = derive_expansion_pre_candidates(
        raw_root=raw_root,
        evaluation_start_ns=int(data_manifest["evaluation_start_ns"]),
        evaluation_end_ns=int(data_manifest["evaluation_end_ns"]),
    )
    l1_routed, l1_counts = route_l1_candidates(
        raw_root=raw_root,
        pre_candidates=pre_candidates,
    )
    combined = sorted(
        [*deleveraging, *l1_routed],
        key=lambda item: (
            int(item["confirm_time_ns"]),
            str(item["scenario_id"]),
        ),
    )
    ids = [str(signal["scenario_id"]) for signal in combined]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate V18 scenario IDs")
    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(
        json.dumps(combined, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    state_counts: dict[str, int] = {}
    for signal in combined:
        state = str(signal.get("scenario_kind"))
        state_counts[state] = state_counts.get(state, 0) + 1
    v13_manifest = json.loads(v13_manifest_path.read_text(encoding="utf-8"))
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v18-order-book-resilience-router",
        "engine_status": "causal_schedule_only_no_backtest",
        "source_contraction_event_count": v13_manifest[
            "source_signal_count"
        ],
        "retained_deleveraging_signal_count": len(deleveraging),
        "expansion_pre_candidate_count": len(pre_candidates),
        "l1_routed_signal_count": len(l1_routed),
        "derived_signal_count": len(combined),
        "state_counts": dict(sorted(state_counts.items())),
        "l1_routing_counts": dict(sorted(l1_counts.items())),
        "baseline_minutes": BASELINE_MINUTES,
        "block_seconds": BLOCK_SECONDS,
        "observation_seconds": OBSERVATION_SECONDS,
        "reversal_expiry_minutes": REVERSAL_EXPIRY_MINUTES,
        "threshold_policy": (
            "candidate-local causal quartiles, no return-fit search"
        ),
        "source_signals": str(source_signals),
        "output_signals": str(output_signals),
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return combined


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    prepared = args.prepared_root.resolve()
    source = prepared / "signals-v1.json"
    if not source.exists():
        source = prepared / "signals.json"
    combined = derive_v18(
        source_signals=source,
        raw_root=prepared / "raw",
        data_manifest_path=prepared / "data_manifest.json",
        output_signals=prepared / "signals.json",
        output_manifest=args.output_manifest.resolve(),
    )
    print(json.dumps({"derived_signals": len(combined)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
