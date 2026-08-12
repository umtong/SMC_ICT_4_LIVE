"""Causal dump/spike reversal state machine adapted from public DRPT v2.

This is an N-to-1 adaptation, not a new simulator.  NautilusTrader and the
Candidate 35 execution shell continue to own real OHLCV bars, matching, fees,
slippage reserves, bracket orders, positions, liquidation and continuous
portfolio accounting.  The public ``dump-reversal-peak-trail-v2`` source
contributes only the decision mechanism:

    capitulation -> ARM -> update the causal extreme while the leg extends
    -> require a stop-entry style reclaim -> structural invalidation
    -> peak-retrace / time-in-loss management.

The public source's fixed percentage loss and profit thresholds are not copied.
The adapted policy uses completed 15-minute crypto-normalized capitulation
candles, completed one-minute close confirmation, a stop beyond the frozen
causal extreme, the existing current-NAV 3% planned-loss sizing, and a target
whose modeled payoff is +2.5R after the same cost reserves used by execution.
No result-dependent symbol, period or order allowlist is accepted.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from statistics import median
from typing import Any, Sequence

from router import RouteDecision
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell
from strategy_inventory_release_absorption import _cost_aware_target


WINDOW_NS = 15 * 60 * 1_000_000_000
MINUTE_NS = 60 * 1_000_000_000
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}
CAPITULATION_RECLAIM_LONG = "DRPT_CAPITULATION_RECLAIM_LONG"
CAPITULATION_RECLAIM_SHORT = "DRPT_CAPITULATION_RECLAIM_SHORT"


@dataclass(frozen=True, slots=True)
class _Candle15:
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class _Arm:
    symbol: str
    side: int
    episode_ts: int
    updated_ts: int
    expiry_ts: int
    extreme: float
    trigger: float
    atr: float
    score: float
    event_open: float
    event_high: float
    event_low: float
    event_close: float
    body_atr: float
    range_atr: float
    volume_burst: float
    breakout_depth_atr: float
    extension_count: int = 0


class Candidate35Config(_ExecutionConfig, frozen=True):
    drpt_bucket_minutes: int = 15
    drpt_extreme_lookback_bars: int = 24
    drpt_atr_period: int = 14
    drpt_body_atr_min: float = 0.75
    drpt_range_atr_min: float = 1.25
    drpt_close_location_max: float = 0.35
    drpt_volume_burst_min: float = 1.25
    drpt_entry_buffer_atr: float = 0.50
    drpt_stop_buffer_atr: float = 0.15
    drpt_arm_ttl_minutes: int = 180
    drpt_target_net_r: float = 2.50
    drpt_peak_activation_r: float = 1.25
    drpt_peak_retrace_r: float = 0.75
    drpt_time_in_loss_minutes: int = 90


def _complete_15m_candles(rows: Sequence[Any]) -> list[_Candle15]:
    """Aggregate only complete, gap-free groups of fifteen one-minute bars."""
    groups: dict[int, list[Any]] = {}
    for row in rows:
        bucket = int(row.ts_event) // WINDOW_NS
        groups.setdefault(bucket, []).append(row)
    candles: list[_Candle15] = []
    for bucket in sorted(groups):
        group = sorted(groups[bucket], key=lambda item: int(item.ts_event))
        if len(group) != 15:
            continue
        times = [int(item.ts_event) for item in group]
        if any(times[index] - times[index - 1] != MINUTE_NS for index in range(1, 15)):
            continue
        candles.append(
            _Candle15(
                ts_event=times[-1],
                open=float(group[0].open),
                high=max(float(item.high) for item in group),
                low=min(float(item.low) for item in group),
                close=float(group[-1].close),
                volume=sum(float(item.volume) for item in group),
            )
        )
    return candles


def _atr15(candles: Sequence[_Candle15], period: int) -> float:
    if period <= 0 or len(candles) < period + 1:
        return math.nan
    true_ranges: list[float] = []
    for index in range(len(candles) - period, len(candles)):
        candle = candles[index]
        previous = candles[index - 1].close
        true_ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous),
                abs(candle.low - previous),
            )
        )
    value = sum(true_ranges) / len(true_ranges)
    return value if math.isfinite(value) and value > 0.0 else math.nan


def _finite_positive(*values: float) -> bool:
    return all(math.isfinite(float(value)) and float(value) > 0.0 for value in values)


class Candidate35Strategy(_ExecutionShell):
    """One-account symmetric capitulation-reclaim policy."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        frozen = {
            "drpt_bucket_minutes": 15,
            "drpt_extreme_lookback_bars": 24,
            "drpt_atr_period": 14,
            "drpt_body_atr_min": 0.75,
            "drpt_range_atr_min": 1.25,
            "drpt_close_location_max": 0.35,
            "drpt_volume_burst_min": 1.25,
            "drpt_entry_buffer_atr": 0.50,
            "drpt_stop_buffer_atr": 0.15,
            "drpt_arm_ttl_minutes": 180,
            "drpt_target_net_r": 2.50,
            "drpt_peak_activation_r": 1.25,
            "drpt_peak_retrace_r": 0.75,
            "drpt_time_in_loss_minutes": 90,
        }
        for key, expected in frozen.items():
            actual = getattr(config, key)
            if isinstance(expected, float):
                if abs(float(actual) - expected) > 1e-12:
                    raise ValueError(f"frozen {key} must be {expected}, received {actual}")
            elif int(actual) != expected:
                raise ValueError(f"frozen {key} must be {expected}, received {actual}")
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=12_000)
            for symbol in SYMBOLS
        }
        self.arms: dict[str, _Arm] = {}
        self.used_episodes: set[tuple[str, int, int]] = set()
        self._peak_active = False
        self._best_r = 0.0
        self.diagnostics.update(
            {
                "candidate": "candidate-55-capitulation-reclaim-drpt-v1",
                "external_source": "grinay/geektrade-strategies:dump-reversal-peak-trail-v2",
                "source_components_reused": [
                    "capitulation arm",
                    "extend causal extreme",
                    "stop-entry style reclaim",
                    "arm expiry",
                    "peak-retrace exit",
                    "time-in-loss exit",
                ],
                "complete_15m_candles": 0,
                "capitulation_long_events": 0,
                "capitulation_short_events": 0,
                "arm_extensions": 0,
                "arm_expirations": 0,
                "reclaim_candidates": 0,
                "reclaim_competitions": 0,
                "reclaim_entries": 0,
                "geometry_rejections": 0,
                "funding_runway_rejections": 0,
                "cooldown_rejections": 0,
                "used_episode_rejections": 0,
                "peak_retrace_activations": 0,
                "peak_retrace_exits": 0,
                "time_in_loss_exits": 0,
                "arm_events_by_symbol": {},
                "reclaim_entries_by_symbol": {},
            }
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)
        open_symbols = [
            symbol
            for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]),
            len(open_symbols),
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
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES",
                )
                self._clear_trade_state()
            return
        if not (
            self.config.evaluation_start_ns
            <= ts_event
            <= self.config.evaluation_end_ns
        ):
            return

        self._expire_arms(ts_event)
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % int(self.config.drpt_bucket_minutes) == 14:
            self._observe_completed_15m(ts_event)

        candidates = self._reclaim_candidates(ts_event)
        if not candidates:
            return
        self.diagnostics["reclaim_candidates"] += len(candidates)
        if len(candidates) > 1:
            self.diagnostics["reclaim_competitions"] += 1
        candidates.sort(
            key=lambda item: (
                -float(item[0].score),
                _SYMBOL_PRIORITY[item[0].symbol],
                int(item[0].episode_ts),
            )
        )
        decision, arm, target_diagnostics = candidates[0]
        episode_key = (decision.symbol, int(decision.side), int(decision.episode_ts))
        if episode_key in self.used_episodes:
            self.diagnostics["used_episode_rejections"] += 1
            self.arms.pop(decision.symbol, None)
            return
        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            self.diagnostics["cooldown_rejections"] += 1
            return

        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(decision, ts_event)
        if int(self.diagnostics["entry_submissions"]) <= before:
            return
        self.used_episodes.add(episode_key)
        self._peak_active = False
        self._best_r = 0.0
        self.diagnostics["reclaim_entries"] += 1
        counts = self.diagnostics["reclaim_entries_by_symbol"]
        counts[decision.symbol] = int(counts.get(decision.symbol, 0)) + 1
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-55-capitulation-reclaim-drpt-v1",
                    "state_model": (
                        "15m capitulation and local-extreme break -> arm -> "
                        "extend causal extreme -> completed-1m reclaim"
                    ),
                    "external_source": (
                        "grinay/geektrade-strategies/"
                        "dump-reversal-peak-trail-v2"
                    ),
                    "arm": {
                        "episode_ts": arm.episode_ts,
                        "updated_ts": arm.updated_ts,
                        "expiry_ts": arm.expiry_ts,
                        "extreme": arm.extreme,
                        "trigger": arm.trigger,
                        "atr": arm.atr,
                        "extension_count": arm.extension_count,
                        "body_atr": arm.body_atr,
                        "range_atr": arm.range_atr,
                        "volume_burst": arm.volume_burst,
                        "breakout_depth_atr": arm.breakout_depth_atr,
                    },
                    "cost_aware_target": target_diagnostics,
                    "management": (
                        "+2.5R modeled net target, 1.25R peak activation, "
                        "0.75R retrace, 90-minute time-in-loss, daytrade exit"
                    ),
                }
            )
        # A single cross-asset liquidation wave is one causal opportunity.  Once
        # the global account selects its owner, correlated arms are discarded.
        self.arms.clear()

    def _expire_arms(self, ts_event: int) -> None:
        expired = [
            symbol for symbol, arm in self.arms.items() if ts_event > arm.expiry_ts
        ]
        for symbol in expired:
            self.arms.pop(symbol, None)
            self.diagnostics["arm_expirations"] += 1

    def _observe_completed_15m(self, ts_event: int) -> None:
        lookback = int(self.config.drpt_extreme_lookback_bars)
        period = int(self.config.drpt_atr_period)
        for symbol in SYMBOLS:
            candles = _complete_15m_candles(tuple(self.bars[symbol]))
            if not candles or candles[-1].ts_event != ts_event:
                continue
            self.diagnostics["complete_15m_candles"] += 1
            if len(candles) < max(lookback + 1, period + 1):
                continue
            current = candles[-1]
            prior = candles[-lookback - 1 : -1]
            atr = _atr15(candles, period)
            prior_volumes = [item.volume for item in prior if item.volume > 0.0]
            if not _finite_positive(atr, current.open, current.high, current.low, current.close):
                continue
            if not prior_volumes:
                continue
            volume_reference = median(prior_volumes)
            if volume_reference <= 0.0:
                continue
            candle_range = current.high - current.low
            if candle_range <= 0.0:
                continue
            body_atr = abs(current.close - current.open) / atr
            range_atr = candle_range / atr
            close_location = (current.close - current.low) / candle_range
            volume_burst = current.volume / volume_reference
            prior_low = min(item.low for item in prior)
            prior_high = max(item.high for item in prior)
            long_depth = max(0.0, (prior_low - current.low) / atr)
            short_depth = max(0.0, (current.high - prior_high) / atr)
            long_event = (
                current.low < prior_low
                and current.close < current.open
                and body_atr >= float(self.config.drpt_body_atr_min)
                and range_atr >= float(self.config.drpt_range_atr_min)
                and close_location <= float(self.config.drpt_close_location_max)
                and volume_burst >= float(self.config.drpt_volume_burst_min)
            )
            short_event = (
                current.high > prior_high
                and current.close > current.open
                and body_atr >= float(self.config.drpt_body_atr_min)
                and range_atr >= float(self.config.drpt_range_atr_min)
                and close_location >= 1.0 - float(self.config.drpt_close_location_max)
                and volume_burst >= float(self.config.drpt_volume_burst_min)
            )

            existing = self.arms.get(symbol)
            if existing is not None and existing.side > 0 and current.low < existing.extreme:
                existing.extreme = current.low
                existing.trigger = current.high + float(self.config.drpt_entry_buffer_atr) * atr
                existing.atr = atr
                existing.updated_ts = ts_event
                existing.extension_count += 1
                self.diagnostics["arm_extensions"] += 1
            elif existing is not None and existing.side < 0 and current.high > existing.extreme:
                existing.extreme = current.high
                existing.trigger = current.low - float(self.config.drpt_entry_buffer_atr) * atr
                existing.atr = atr
                existing.updated_ts = ts_event
                existing.extension_count += 1
                self.diagnostics["arm_extensions"] += 1

            if not long_event and not short_event:
                continue
            side = 1 if long_event else -1
            state = CAPITULATION_RECLAIM_LONG if side > 0 else CAPITULATION_RECLAIM_SHORT
            depth = long_depth if side > 0 else short_depth
            score = body_atr + range_atr + min(volume_burst, 4.0) + depth
            extreme = current.low if side > 0 else current.high
            trigger = (
                current.high + float(self.config.drpt_entry_buffer_atr) * atr
                if side > 0
                else current.low - float(self.config.drpt_entry_buffer_atr) * atr
            )
            self.arms[symbol] = _Arm(
                symbol=symbol,
                side=side,
                episode_ts=ts_event,
                updated_ts=ts_event,
                expiry_ts=ts_event + int(self.config.drpt_arm_ttl_minutes) * MINUTE_NS,
                extreme=extreme,
                trigger=trigger,
                atr=atr,
                score=score,
                event_open=current.open,
                event_high=current.high,
                event_low=current.low,
                event_close=current.close,
                body_atr=body_atr,
                range_atr=range_atr,
                volume_burst=volume_burst,
                breakout_depth_atr=depth,
            )
            key = "capitulation_long_events" if side > 0 else "capitulation_short_events"
            self.diagnostics[key] += 1
            counts = self.diagnostics["arm_events_by_symbol"]
            counts[symbol] = int(counts.get(symbol, 0)) + 1
            self._event(
                "DRPT_ARMED",
                ts_event,
                symbol=symbol,
                state=state,
                side=side,
                trigger=trigger,
                extreme=extreme,
                atr=atr,
                score=score,
                body_atr=body_atr,
                range_atr=range_atr,
                close_location=close_location,
                volume_burst=volume_burst,
                breakout_depth_atr=depth,
            )

    def _reclaim_candidates(
        self,
        ts_event: int,
    ) -> list[tuple[RouteDecision, _Arm, dict[str, float]]]:
        candidates: list[tuple[RouteDecision, _Arm, dict[str, float]]] = []
        for symbol, arm in list(self.arms.items()):
            if ts_event <= arm.updated_ts or ts_event > arm.expiry_ts:
                continue
            close = float(self.bars[symbol][-1].close)
            reclaimed = close >= arm.trigger if arm.side > 0 else close <= arm.trigger
            if not reclaimed:
                continue
            stop = (
                arm.extreme - float(self.config.drpt_stop_buffer_atr) * arm.atr
                if arm.side > 0
                else arm.extreme + float(self.config.drpt_stop_buffer_atr) * arm.atr
            )
            if arm.side > 0 and not (0.0 < stop < close):
                self.diagnostics["geometry_rejections"] += 1
                self.arms.pop(symbol, None)
                continue
            if arm.side < 0 and not (0.0 < close < stop):
                self.diagnostics["geometry_rejections"] += 1
                self.arms.pop(symbol, None)
                continue
            try:
                target, target_diagnostics = _cost_aware_target(
                    entry=close,
                    stop=stop,
                    side=arm.side,
                    fee_bps_each_side=float(self.config.all_in_cost_bps_each_side),
                    slippage_bps_each_side=float(
                        self.config.adverse_slippage_bps_each_side
                    ),
                    funding_reserve_bps=float(self.config.funding_reserve_bps),
                    net_target_r=float(self.config.drpt_target_net_r),
                )
            except ValueError:
                self.diagnostics["geometry_rejections"] += 1
                self.arms.pop(symbol, None)
                continue
            excess = arm.side * (close - arm.trigger) / arm.atr
            score = arm.score + max(0.0, excess)
            state = CAPITULATION_RECLAIM_LONG if arm.side > 0 else CAPITULATION_RECLAIM_SHORT
            decision = RouteDecision(
                symbol=symbol,
                state=state,
                side=arm.side,
                score=score,
                entry_reference=close,
                stop_reference=stop,
                objective_reference=target,
                episode_ts=arm.episode_ts,
                reasons=(
                    "CAPITULATION_LOCAL_EXTREME_BREAK",
                    "CAUSAL_EXTREME_FROZEN",
                    "COMPLETED_1M_RECLAIM_OF_BUFFERED_EVENT_RANGE",
                    "COST_AWARE_POSITIVE_REWARD_SPACE",
                ),
                diagnostics={
                    "arm_score": arm.score,
                    "reclaim_excess_atr": excess,
                    "arm_age_minutes": (ts_event - arm.episode_ts) / MINUTE_NS,
                    "extension_count": arm.extension_count,
                    **target_diagnostics,
                },
            )
            candidates.append((decision, arm, target_diagnostics))
        return candidates

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        state = str(scenario.get("state", ""))
        if state in (CAPITULATION_RECLAIM_LONG, CAPITULATION_RECLAIM_SHORT):
            side = int(scenario.get("side", 0))
            entry = float(scenario.get("entry_reference", math.nan))
            stop = float(scenario.get("stop", math.nan))
            risk = abs(entry - stop)
            if side in (-1, 1) and _finite_positive(entry, stop, risk):
                bar = self.bars[self.current_symbol][-1]
                favourable = float(bar.high) if side > 0 else float(bar.low)
                current = float(bar.close)
                favourable_r = side * (favourable - entry) / risk
                current_r = side * (current - entry) / risk
                if self._peak_active:
                    self._best_r = max(self._best_r, favourable_r)
                    if self._best_r - current_r >= float(
                        self.config.drpt_peak_retrace_r
                    ):
                        instrument_id = self.instrument_ids[self.current_symbol]
                        self.cancel_all_orders(instrument_id)
                        self.close_all_positions(instrument_id)
                        self.diagnostics["peak_retrace_exits"] += 1
                        self._event(
                            "DRPT_PEAK_RETRACE_EXIT",
                            ts_event,
                            best_r=self._best_r,
                            current_r=current_r,
                            retrace_r=self._best_r - current_r,
                        )
                        return
                elif favourable_r >= float(self.config.drpt_peak_activation_r):
                    self._peak_active = True
                    self._best_r = favourable_r
                    self.diagnostics["peak_retrace_activations"] += 1
                    self._event(
                        "DRPT_PEAK_RETRACE_ACTIVATED",
                        ts_event,
                        favourable_r=favourable_r,
                    )

                age = (
                    self.minute_index - self.position_open_minute
                    if self.position_open_minute >= 0
                    else 0
                )
                if (
                    age >= int(self.config.drpt_time_in_loss_minutes)
                    and current_r <= 0.0
                ):
                    instrument_id = self.instrument_ids[self.current_symbol]
                    self.cancel_all_orders(instrument_id)
                    self.close_all_positions(instrument_id)
                    self.diagnostics["time_in_loss_exits"] += 1
                    self._event(
                        "DRPT_TIME_IN_LOSS_EXIT",
                        ts_event,
                        age_minutes=age,
                        current_r=current_r,
                    )
                    return
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._peak_active = False
        self._best_r = 0.0


__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "CAPITULATION_RECLAIM_LONG",
    "CAPITULATION_RECLAIM_SHORT",
    "_complete_15m_candles",
    "_atr15",
]
