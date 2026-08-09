"""Causal Nautilus adaptation of NFI squeeze-release conditions 166/664.

The external source contributes only one compact auction mechanism:

* 5-minute Bollinger Bands (20, 2 population standard deviations);
* a 20-bar Keltner envelope using mean high-low range times 1.5;
* at least 12 squeezed bars in the prior 24 completed 5-minute bars;
* the prior bar is squeezed, the current completed bar is not;
* the current close finishes outside the Bollinger band in the release direction.

The opposite edge of the completed compression is the hard invalidation.  The
objective is a one-compression-range measured move beyond the compression edge.
No NFI money management, DCA, pairlist or Freqtrade execution code is reused;
NautilusTrader owns orders, fills, costs, positions and continuous-account NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Sequence

import ichifan_strategy as _shared

_router = _shared._router
_base = _shared._base
FiveMinuteBar = _shared.FiveMinuteBar
aggregate_five_minute = _shared.aggregate_five_minute

Candidate47SqueezeConfig = _base.Candidate35Config
Candidate35Config = Candidate47SqueezeConfig
SYMBOLS = _base.SYMBOLS
_FIVE_MINUTES_NS = 5 * 60 * 1_000_000_000


@dataclass(frozen=True, slots=True)
class SqueezeReleaseState:
    ts_event: int
    ready: bool
    side: int
    squeeze_previous: bool
    squeeze_current: bool
    prior_squeeze_count: int
    bb_upper: float = math.nan
    bb_middle: float = math.nan
    bb_lower: float = math.nan
    kc_upper: float = math.nan
    kc_lower: float = math.nan
    coil_high: float = math.nan
    coil_low: float = math.nan
    stop: float = math.nan
    target: float = math.nan
    reward_risk: float = math.nan
    score: float = 0.0

    @property
    def actionable(self) -> bool:
        return self.ready and self.side in (-1, 1)


def _bands(window: Sequence[FiveMinuteBar]) -> tuple[float, float, float, float, float]:
    if len(window) != 20:
        raise ValueError("band window must contain 20 completed five-minute bars")
    closes = [float(item.close) for item in window]
    ranges = [float(item.high) - float(item.low) for item in window]
    values = closes + ranges
    if not all(math.isfinite(value) for value in values):
        raise ValueError("non-finite squeeze input")
    if any(close <= 0.0 for close in closes) or any(value < 0.0 for value in ranges):
        raise ValueError("non-positive squeeze price or negative range")

    middle = sum(closes) / 20.0
    variance = sum((value - middle) ** 2 for value in closes) / 20.0
    deviation = math.sqrt(max(variance, 0.0))
    bb_upper = middle + 2.0 * deviation
    bb_lower = middle - 2.0 * deviation
    mean_range = sum(ranges) / 20.0
    kc_upper = middle + 1.5 * mean_range
    kc_lower = middle - 1.5 * mean_range
    return bb_upper, middle, bb_lower, kc_upper, kc_lower


def causal_squeeze_release(
    bars: Sequence[FiveMinuteBar],
    *,
    current_ts: int,
) -> SqueezeReleaseState:
    """Return the exact current completed squeeze-release state.

    Forty-four contiguous five-minute bars are required: 20 bars to establish
    the first squeeze observation and 24 prior observations for the source's
    predeclared coil count.  Stale or future latest bars are rejected.
    """
    timestamp = int(current_ts)
    if timestamp <= 0:
        raise ValueError("current_ts must be positive")
    if len(bars) < 44:
        return SqueezeReleaseState(
            ts_event=timestamp,
            ready=False,
            side=0,
            squeeze_previous=False,
            squeeze_current=False,
            prior_squeeze_count=0,
        )

    selected = list(bars[-64:])
    if int(selected[-1].ts_event) != timestamp:
        relation = "future" if int(selected[-1].ts_event) > timestamp else "stale"
        raise ValueError(
            f"{relation} latest five-minute bar: {int(selected[-1].ts_event)} != {timestamp}"
        )
    for index in range(1, len(selected)):
        if int(selected[index].ts_event) - int(selected[index - 1].ts_event) != _FIVE_MINUTES_NS:
            raise ValueError("non-contiguous five-minute squeeze history")

    flags: list[bool | None] = [None] * len(selected)
    bands: list[tuple[float, float, float, float, float] | None] = [None] * len(selected)
    for index in range(19, len(selected)):
        values = _bands(selected[index - 19 : index + 1])
        bands[index] = values
        bb_upper, _, bb_lower, kc_upper, kc_lower = values
        flags[index] = bb_lower > kc_lower and bb_upper < kc_upper

    current_index = len(selected) - 1
    prior_index = current_index - 1
    count_window = flags[prior_index - 23 : prior_index + 1]
    if len(count_window) != 24 or any(item is None for item in count_window):
        return SqueezeReleaseState(
            ts_event=timestamp,
            ready=False,
            side=0,
            squeeze_previous=False,
            squeeze_current=False,
            prior_squeeze_count=0,
        )

    current_bands = bands[current_index]
    if current_bands is None:
        raise RuntimeError("current squeeze bands unexpectedly unavailable")
    bb_upper, bb_middle, bb_lower, kc_upper, kc_lower = current_bands
    squeeze_previous = bool(flags[prior_index])
    squeeze_current = bool(flags[current_index])
    prior_squeeze_count = sum(bool(item) for item in count_window)
    current = selected[current_index]
    close = float(current.close)
    if not math.isfinite(close) or close <= 0.0 or float(current.volume) <= 0.0:
        raise ValueError("invalid current squeeze-release bar")

    side = 0
    if squeeze_previous and not squeeze_current and prior_squeeze_count >= 12:
        if close > bb_upper:
            side = 1
        elif close < bb_lower:
            side = -1

    coil = selected[current_index - 24 : current_index]
    coil_high = max(float(item.high) for item in coil)
    coil_low = min(float(item.low) for item in coil)
    coil_range = coil_high - coil_low
    stop = math.nan
    target = math.nan
    reward_risk = math.nan
    score = 0.0
    if side != 0 and coil_range > 0.0:
        if side > 0:
            stop = max(close * 0.90, coil_low)
            target = coil_high + coil_range
            valid = stop < close < target
            breakout_distance = close - bb_upper
        else:
            stop = min(close * 1.10, coil_high)
            target = coil_low - coil_range
            valid = 0.0 < target < close < stop
            breakout_distance = bb_lower - close
        if valid:
            risk = abs(close - stop)
            reward = abs(target - close)
            reward_risk = reward / risk if risk > 0.0 else math.nan
            band_width = max(bb_upper - bb_lower, close * 1e-9)
            score = (
                breakout_distance / band_width
                + prior_squeeze_count / 24.0
                + coil_range / close
            )
        else:
            side = 0

    return SqueezeReleaseState(
        ts_event=timestamp,
        ready=True,
        side=side,
        squeeze_previous=squeeze_previous,
        squeeze_current=squeeze_current,
        prior_squeeze_count=prior_squeeze_count,
        bb_upper=bb_upper,
        bb_middle=bb_middle,
        bb_lower=bb_lower,
        kc_upper=kc_upper,
        kc_lower=kc_lower,
        coil_high=coil_high,
        coil_low=coil_low,
        stop=stop,
        target=target,
        reward_risk=reward_risk,
        score=score,
    )


class Candidate47SqueezeReleaseStrategy(_base.Candidate35Strategy):
    """One-slot, four-symbol NFI squeeze-release continuation policy."""

    def __init__(self, config: Candidate47SqueezeConfig) -> None:
        super().__init__(config)
        self._seen_episodes: set[str] = set()
        self.diagnostics.update(
            {
                "squeeze_five_minute_decisions": 0,
                "squeeze_ready_symbol_states": 0,
                "squeeze_release_candidates": 0,
                "squeeze_consumed_geometry_rejections": 0,
                "squeeze_duplicate_episode_rejections": 0,
                "squeeze_midline_exits": 0,
                "squeeze_source": "public-NFI-X7-conditions-166-664-causal-adaptation",
                "squeeze_geometry": "opposite-coil-edge-stop-and-one-coil-range-measured-move",
            }
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)

        open_symbols = [
            symbol for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]), len(open_symbols)
        )
        if len(open_symbols) > 1:
            self.diagnostics["global_position_violations"] += 1
            for symbol in open_symbols:
                self.cancel_all_orders(self.instrument_ids[symbol])
                self.close_all_positions(self.instrument_ids[symbol])
            return
        if open_symbols:
            self.current_symbol = open_symbols[0]
            self._manage_open_position(ts_event)
            return

        if self.entry_pending:
            self.diagnostics["max_simultaneous_entry_intents"] = max(
                int(self.diagnostics["max_simultaneous_entry_intents"]), 1
            )
            if self.minute_index - self.entry_pending_minute > 2:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event("ENTRY_EXPIRED", ts_event, reason="SQUEEZE_MARKET_PARENT_NOT_FILLED")
                self._clear_trade_state()
            return

        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 != 4:
            return
        if any(len(self.bars[symbol]) < 220 for symbol in SYMBOLS):
            return

        self.diagnostics["squeeze_five_minute_decisions"] += 1
        candidates: list[tuple[float, str, SqueezeReleaseState]] = []
        for symbol in SYMBOLS:
            five = aggregate_five_minute(tuple(self.bars[symbol]))
            if not five:
                continue
            state = causal_squeeze_release(five, current_ts=five[-1].ts_event)
            if not state.ready:
                continue
            self.diagnostics["squeeze_ready_symbol_states"] += 1
            if state.actionable:
                self.diagnostics["squeeze_release_candidates"] += 1
                candidates.append((state.score, symbol, state))

        if not candidates:
            self.diagnostics["unresolved_episodes"] += 1
            return
        candidates.sort(key=lambda item: (item[0], item[1] == "BTCUSDT", item[1]), reverse=True)
        score, symbol, state = candidates[0]
        direction = "LONG" if state.side > 0 else "SHORT"
        episode_id = f"{symbol}:{state.ts_event}:NFI_SQUEEZE_RELEASE_{direction}"
        if episode_id in self._seen_episodes:
            self.diagnostics["squeeze_duplicate_episode_rejections"] += 1
            return

        entry = float(self.bars[symbol][-1].close)
        if state.side > 0 and not (state.stop < entry < state.target):
            self.diagnostics["squeeze_consumed_geometry_rejections"] += 1
            return
        if state.side < 0 and not (0.0 < state.target < entry < state.stop):
            self.diagnostics["squeeze_consumed_geometry_rejections"] += 1
            return

        decision = _router.RouteDecision(
            symbol=symbol,
            state=f"NFI_SQUEEZE_RELEASE_{direction}",
            side=state.side,
            score=score,
            expected_target_r=state.reward_risk,
            atr=math.nan,
            entry_reference=entry,
            stop_reference=state.stop,
            objective_reference=state.target,
            episode_ts=state.ts_event,
            reasons=(
                "PRIOR_24_BAR_COMPRESSION",
                "SQUEEZE_STATE_RELEASED",
                "COMPLETED_5M_CLOSE_OUTSIDE_BOLLINGER_BAND",
                "PUBLIC_NFI_166_664_MECHANISM",
            ),
            diagnostics={
                "causal_episode_id": episode_id,
                "squeeze_previous": state.squeeze_previous,
                "squeeze_current": state.squeeze_current,
                "prior_squeeze_count": state.prior_squeeze_count,
                "bb_upper": state.bb_upper,
                "bb_middle": state.bb_middle,
                "bb_lower": state.bb_lower,
                "kc_upper": state.kc_upper,
                "kc_lower": state.kc_lower,
                "coil_high": state.coil_high,
                "coil_low": state.coil_low,
                "measured_move_target": state.target,
                "reward_risk": state.reward_risk,
            },
        )
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(decision, ts_event)
        if int(self.diagnostics["entry_submissions"]) == before:
            return
        self._seen_episodes.add(episode_id)
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "scenario_id": f"c47-squeeze-{before + 1:07d}",
                    "candidate": "candidate-47-public-nfi-squeeze-release",
                    "causal_episode_id": episode_id,
                    "entry_policy": "MARKET_BRACKET_AFTER_COMPLETED_5M_RELEASE",
                    "dynamic_invalidation": "5M_CLOSE_THROUGH_BOLLINGER_MIDLINE",
                }
            )

    def _request_exit(self, ts_event: int, reason: str, **details: Any) -> None:
        if self.current_symbol is None:
            return
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event(reason, ts_event, **details)

    def _manage_open_position(self, ts_event: int) -> None:
        symbol = self.current_symbol
        if symbol is None or not self.bars[symbol]:
            return
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 == 4:
            five = aggregate_five_minute(tuple(self.bars[symbol]))
            if five:
                state = causal_squeeze_release(five, current_ts=five[-1].ts_event)
                scenario = self.current_scenario or {}
                side = int(scenario.get("side", 0))
                close = float(five[-1].close)
                invalidated = (
                    side > 0 and close < state.bb_middle
                ) or (
                    side < 0 and close > state.bb_middle
                )
                if state.ready and invalidated:
                    self.diagnostics["squeeze_midline_exits"] += 1
                    self._request_exit(
                        ts_event,
                        "SQUEEZE_MIDLINE_INVALIDATION_EXIT",
                        side=side,
                        close=close,
                        bb_middle=state.bb_middle,
                    )
                    return
        super()._manage_open_position(ts_event)


Candidate35Strategy = Candidate47SqueezeReleaseStrategy
