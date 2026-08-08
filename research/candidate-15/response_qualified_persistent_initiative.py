"""Timeframe-consistent, response-qualified persistent initiative router.

V4 compared a five-minute impulse body with a one-minute ATR.  That unit mismatch
made roughly seventy percent of quarter-hour observations look displaced and
activated about twenty-four initiatives per day.  This router preserves the
independent post-activation continuation engine but corrects the state layer:

* the current five-minute impulse is compared with ATR from prior completed
  non-overlapping five-minute bars;
* a second same-direction event confirms persistence only when at least three
  common markets advance beyond the first event close and retain the first
  impulse origin;
* the information horizon equals the observed separation between the two
  confirming events, rather than an automatically refreshed four-hour constant.

All inputs are completed and causal.  No return outcome is used.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from math import log
from statistics import fmean, median
from typing import Any, Mapping

from logic import BarObs, Direction, LogicConfig, MINUTE_NS
from quarter_hour_persistent_initiative import (
    FOUR_HOURS_NS,
    SYMBOLS,
    CommonFlowEvent,
    PersistentQuarterHourRouter,
)


@dataclass(frozen=True, slots=True)
class ResponseQualifiedInitiativeState:
    scenario_id: str
    direction: Direction
    activated_ts_ns: int
    expires_ts_ns: int
    owner_symbol: str
    accepted_symbols: tuple[str, ...]
    origins: Mapping[str, float]
    source_event_ids: tuple[str, ...]
    confirmation_span_ns: int
    overlap_symbols: tuple[str, ...]
    median_directional_progress: float
    advancing_symbols: tuple[str, ...]
    origin_holding_symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.expires_ts_ns <= self.activated_ts_ns:
            raise ValueError("response-qualified initiative must have a positive horizon")
        if len(self.accepted_symbols) < 3 or len(self.overlap_symbols) < 3:
            raise ValueError("response-qualified initiative requires three common markets")
        if self.confirmation_span_ns <= 0 or self.confirmation_span_ns > FOUR_HOURS_NS:
            raise ValueError("confirmation span is outside the causal event horizon")
        if self.median_directional_progress <= 0.0:
            raise ValueError("persistent initiative requires positive cross-market progress")


@dataclass(frozen=True, slots=True)
class _ResponseEvidence:
    qualified: bool
    overlap: tuple[str, ...]
    directional_progress: Mapping[str, float]
    median_progress: float
    advancing: tuple[str, ...]
    origin_holding: tuple[str, ...]
    required_majority: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualified": self.qualified,
            "overlap_symbols": list(self.overlap),
            "directional_progress": dict(self.directional_progress),
            "median_directional_progress": self.median_progress,
            "advancing_symbols": list(self.advancing),
            "origin_holding_symbols": list(self.origin_holding),
            "required_majority": self.required_majority,
        }


class ResponseQualifiedPersistentQuarterHourRouter(PersistentQuarterHourRouter):
    """Persistent state only after timeframe-consistent price/flow conversion."""

    def __init__(self, config: LogicConfig, instrument_id: str = "PORTFOLIO.GLOBAL") -> None:
        super().__init__(config, instrument_id)
        lookback = max(24, config.atr_period + 8)
        self._five_minute_ranges: dict[str, deque[float]] = {
            symbol: deque(maxlen=lookback)
            for symbol in SYMBOLS
        }
        self._previous_five_minute_close: dict[str, float | None] = {
            symbol: None
            for symbol in SYMBOLS
        }

    def _atr(self, symbol: str) -> float | None:
        ranges = self._five_minute_ranges[symbol]
        if len(ranges) < self.config.atr_period:
            return None
        sample = list(ranges)[-self.config.atr_period :]
        return fmean(sample) if sample else None

    def _update_five_minute_atr(self, ts_ns: int) -> None:
        if not self._is_five_minute_boundary(ts_ns):
            return
        for symbol in SYMBOLS:
            parts = list(self._bars[symbol])[-5:]
            if len(parts) != 5 or parts[-1].ts_ns != ts_ns:
                self.skips["QHI_V5_INCOMPLETE_FIVE_MINUTE_ATR_BAR"] += 1
                continue
            expected = [parts[0].ts_ns + offset * MINUTE_NS for offset in range(5)]
            if [bar.ts_ns for bar in parts] != expected:
                self.skips["QHI_V5_NONCONTIGUOUS_FIVE_MINUTE_ATR_BAR"] += 1
                continue
            high = max(bar.high for bar in parts)
            low = min(bar.low for bar in parts)
            close = parts[-1].close
            previous_close = self._previous_five_minute_close[symbol]
            true_range = max(
                high - low,
                1e-12,
                0.0 if previous_close is None else abs(high - previous_close),
                0.0 if previous_close is None else abs(low - previous_close),
            )
            self._five_minute_ranges[symbol].append(true_range)
            self._previous_five_minute_close[symbol] = close

    @staticmethod
    def _response(
        first: CommonFlowEvent,
        second: CommonFlowEvent,
    ) -> _ResponseEvidence:
        overlap = tuple(sorted(set(first.accepted_symbols) & set(second.accepted_symbols)))
        if len(overlap) < 3:
            return _ResponseEvidence(False, overlap, {}, 0.0, (), (), 3)
        sign = 1.0 if second.direction is Direction.LONG else -1.0
        progress: dict[str, float] = {}
        advancing: list[str] = []
        origin_holding: list[str] = []
        for symbol in overlap:
            first_close = float(first.closes[symbol])
            second_close = float(second.closes[symbol])
            first_origin = float(first.origins[symbol])
            if first_close <= 0.0 or second_close <= 0.0:
                continue
            value = sign * log(second_close / first_close)
            progress[symbol] = value
            if value > 0.0:
                advancing.append(symbol)
            if sign * (second_close - first_origin) > 0.0:
                origin_holding.append(symbol)
        if len(progress) < 3:
            return _ResponseEvidence(False, overlap, progress, 0.0, tuple(advancing), tuple(origin_holding), 3)
        required = len(progress) // 2 + 1
        median_progress = median(progress.values())
        qualified = (
            median_progress > 0.0
            and len(advancing) >= required
            and len(origin_holding) >= required
        )
        return _ResponseEvidence(
            qualified,
            overlap,
            progress,
            median_progress,
            tuple(sorted(advancing)),
            tuple(sorted(origin_holding)),
            required,
        )

    def _handle_event(self, event: CommonFlowEvent) -> None:
        current = self._state
        if current is not None and current.direction is not event.direction:
            self._terminate(
                event.observed_ts_ns,
                "OPPOSITE_COMMON_FLOW_EVENT",
                {"opposite_event_id": event.event_id},
            )

        candidate = self._candidate
        if (
            candidate is None
            or candidate.direction is not event.direction
            or event.observed_ts_ns - candidate.observed_ts_ns > FOUR_HOURS_NS
        ):
            self._candidate = event
            return
        if candidate.event_id == event.event_id:
            return

        span_ns = event.observed_ts_ns - candidate.observed_ts_ns
        evidence = self._response(candidate, event)
        if span_ns <= 0 or span_ns > FOUR_HOURS_NS or not evidence.qualified:
            self.skips["QHI_V5_SAME_DIRECTION_EVENT_LACKED_PERSISTENT_RESPONSE"] += 1
            self._event(
                scenario_id=event.event_id,
                event_type="QHI_RESPONSE_REJECTED",
                event_time_ns=candidate.observed_ts_ns,
                observed_time_ns=event.observed_ts_ns,
                previous_state="OBSERVED",
                next_state="UNRESOLVED",
                reason_code="SAME_DIRECTION_FLOW_DID_NOT_ADVANCE_COMMON_MARKETS",
                reference_price=event.closes.get(event.owner_symbol),
                details={
                    "first_event_id": candidate.event_id,
                    "second_event_id": event.event_id,
                    "span_ns": span_ns,
                    **evidence.to_dict(),
                },
            )
            self._candidate = event
            return

        overlap = evidence.overlap
        owner = event.owner_symbol if event.owner_symbol in overlap else overlap[0]
        origins = {symbol: float(event.origins[symbol]) for symbol in overlap}
        if self._state is None:
            self._initiative_sequence += 1
            scenario_id = (
                f"QHI5-{event.observed_ts_ns}-{self._initiative_sequence:06d}-"
                f"{event.direction.value}"
            )
            state = ResponseQualifiedInitiativeState(
                scenario_id=scenario_id,
                direction=event.direction,
                activated_ts_ns=event.observed_ts_ns,
                expires_ts_ns=event.observed_ts_ns + span_ns,
                owner_symbol=owner,
                accepted_symbols=overlap,
                origins=origins,
                source_event_ids=(candidate.event_id, event.event_id),
                confirmation_span_ns=span_ns,
                overlap_symbols=overlap,
                median_directional_progress=evidence.median_progress,
                advancing_symbols=evidence.advancing,
                origin_holding_symbols=evidence.origin_holding,
            )
            self._state = state
            self._event(
                scenario_id=state.scenario_id,
                event_type="QHI_INITIATIVE_ACTIVATED",
                event_time_ns=candidate.observed_ts_ns,
                observed_time_ns=event.observed_ts_ns,
                previous_state="IDLE",
                next_state="ACTIVE",
                reason_code="TIMEFRAME_CONSISTENT_COMMON_FLOW_RESPONSE_CONFIRMED",
                reference_price=event.closes.get(owner),
                details={
                    "direction": state.direction.value,
                    "owner_symbol": state.owner_symbol,
                    "accepted_symbols": list(state.accepted_symbols),
                    "origins": dict(state.origins),
                    "source_event_ids": list(state.source_event_ids),
                    "confirmation_span_ns": span_ns,
                    "expires_ts_ns": state.expires_ts_ns,
                    **evidence.to_dict(),
                },
            )
        else:
            state = self._state
            assert state is not None and state.direction is event.direction
            refreshed = replace(
                state,
                expires_ts_ns=event.observed_ts_ns + span_ns,
                owner_symbol=owner,
                accepted_symbols=overlap,
                origins=origins,
                source_event_ids=tuple((*state.source_event_ids[-3:], event.event_id)),
                confirmation_span_ns=span_ns,
                overlap_symbols=overlap,
                median_directional_progress=evidence.median_progress,
                advancing_symbols=evidence.advancing,
                origin_holding_symbols=evidence.origin_holding,
            )
            self._state = refreshed
            self._event(
                scenario_id=refreshed.scenario_id,
                event_type="QHI_INITIATIVE_REFRESHED",
                event_time_ns=event.observed_ts_ns,
                observed_time_ns=event.observed_ts_ns,
                previous_state="ACTIVE",
                next_state="ACTIVE",
                reason_code="FRESH_TIMEFRAME_CONSISTENT_RESPONSE",
                reference_price=event.closes.get(owner),
                details={
                    "owner_symbol": owner,
                    "accepted_symbols": list(overlap),
                    "origins": origins,
                    "source_event_id": event.event_id,
                    "confirmation_span_ns": span_ns,
                    "expires_ts_ns": refreshed.expires_ts_ns,
                    **evidence.to_dict(),
                },
            )
        self._candidate = event

    def on_batch(
        self,
        ts_ns: int,
        bars: Mapping[str, BarObs],
    ) -> ResponseQualifiedInitiativeState | None:
        for symbol in SYMBOLS:
            observation = bars.get(symbol)
            if observation is None:
                self.skips["QHI_SYNCHRONIZED_SYMBOL_MISSING"] += 1
                return self._state
            self._bars[symbol].append(observation)
        self._maintain_state(ts_ns, bars)
        # Detect against prior completed five-minute ATR; add the current
        # five-minute range only after the decision to avoid self-normalization.
        event = self._detect_event(ts_ns)
        if event is not None:
            self._handle_event(event)
        self._update_five_minute_atr(ts_ns)
        return self._state
