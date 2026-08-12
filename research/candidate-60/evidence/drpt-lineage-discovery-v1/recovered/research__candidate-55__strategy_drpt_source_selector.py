"""Source-faithful DRPT selector diagnostic for Candidate 55.

The previous Candidate-55 DRPT experiment was not a faithful transfer of the
public strategy: it symmetrized a long-only dump reversal, replaced the prior
seven-day low with a six-hour local extreme, and added a different capitulation
classifier.  This module tests the actual decision mechanism which the source
claims solves falling-knife entries:

    1-2% confirmed 15m red candle
    -> break of the lowest low from seven completed UTC days
    -> long-only arm
    -> source BTC 4h-up and resistance filters
    -> close above dump-candle high + 0.5 ATR within 12 bars

Only the execution contract is adapted.  A completed one-minute close above the
source stop trigger is used instead of optimistic OHLC stop-order sequencing.
The causal dump low plus 0.15 ATR is the structural invalidation required by the
project risk contract, and the objective is fixed at +2R after the same fees,
slippage and funding reserve used by execution.  There is no result-dependent
symbol, period or order allowlist.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Sequence

from router import RouteDecision
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell
from strategy_inventory_release_absorption import _cost_aware_target


MINUTE_NS = 60 * 1_000_000_000
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}
STATE = "DRPT_SOURCE_DAILY_LOW_RECLAIM_LONG"


@dataclass(frozen=True, slots=True)
class _Candle:
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class _Arm:
    symbol: str
    episode_ts: int
    dump_open: float
    dump_high: float
    dump_low: float
    dump_close: float
    dump_pct: float
    prior_7d_low: float
    prior_3d_high: float
    atr: float
    trigger: float
    break_depth_atr: float
    active: bool = False
    activated_ts: int = 0
    expiry_ts: int = 0
    filter_wait_bars: int = 0
    reset_count: int = 0


class Candidate35Config(_ExecutionConfig, frozen=True):
    drpt_source_bucket_minutes: int = 15
    drpt_source_anomaly_pct: float = 1.0
    drpt_source_dump_cap_pct: float = 2.0
    drpt_source_days_lookback: int = 7
    drpt_source_resistance_days: int = 3
    drpt_source_resistance_proximity_pct: float = 1.0
    drpt_source_require_btc_4h_up: bool = True
    drpt_source_atr_period: int = 14
    drpt_source_entry_buffer_atr: float = 0.50
    drpt_source_stop_expiry_bars: int = 12
    drpt_source_stop_buffer_atr: float = 0.15
    drpt_source_target_net_r: float = 2.0
    drpt_source_cooldown_minutes_after_close: int = 240


def _complete_candles(rows: Sequence[Any], minutes: int) -> list[_Candle]:
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    window_ns = int(minutes) * MINUTE_NS
    groups: dict[int, list[Any]] = {}
    for row in rows:
        bucket = int(row.ts_event) // window_ns
        groups.setdefault(bucket, []).append(row)
    result: list[_Candle] = []
    for bucket in sorted(groups):
        group = sorted(groups[bucket], key=lambda item: int(item.ts_event))
        if len(group) != minutes:
            continue
        times = [int(item.ts_event) for item in group]
        if any(times[index] - times[index - 1] != MINUTE_NS for index in range(1, minutes)):
            continue
        result.append(
            _Candle(
                ts_event=times[-1],
                open=float(group[0].open),
                high=max(float(item.high) for item in group),
                low=min(float(item.low) for item in group),
                close=float(group[-1].close),
                volume=sum(float(item.volume) for item in group),
            )
        )
    return result


def _atr(candles: Sequence[_Candle], period: int) -> float:
    if period <= 0 or len(candles) < period + 1:
        return math.nan
    values: list[float] = []
    for index in range(len(candles) - period, len(candles)):
        candle = candles[index]
        previous_close = candles[index - 1].close
        values.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )
    value = sum(values) / len(values)
    return value if math.isfinite(value) and value > 0.0 else math.nan


def _finite_positive(*values: float) -> bool:
    return all(math.isfinite(float(value)) and float(value) > 0.0 for value in values)


class Candidate35Strategy(_ExecutionShell):
    """One-account source-selector diagnostic with one global execution slot."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        frozen: dict[str, float | int | bool] = {
            "drpt_source_bucket_minutes": 15,
            "drpt_source_anomaly_pct": 1.0,
            "drpt_source_dump_cap_pct": 2.0,
            "drpt_source_days_lookback": 7,
            "drpt_source_resistance_days": 3,
            "drpt_source_resistance_proximity_pct": 1.0,
            "drpt_source_require_btc_4h_up": True,
            "drpt_source_atr_period": 14,
            "drpt_source_entry_buffer_atr": 0.50,
            "drpt_source_stop_expiry_bars": 12,
            "drpt_source_stop_buffer_atr": 0.15,
            "drpt_source_target_net_r": 2.0,
            "drpt_source_cooldown_minutes_after_close": 240,
        }
        for key, expected in frozen.items():
            actual = getattr(config, key)
            if isinstance(expected, bool):
                if bool(actual) is not expected:
                    raise ValueError(f"frozen {key} must be {expected}, received {actual}")
            elif isinstance(expected, float):
                if abs(float(actual) - expected) > 1e-12:
                    raise ValueError(f"frozen {key} must be {expected}, received {actual}")
            elif int(actual) != expected:
                raise ValueError(f"frozen {key} must be {expected}, received {actual}")

        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=60_000)
            for symbol in SYMBOLS
        }
        self.arms: dict[str, _Arm] = {}
        self.used_episodes: set[tuple[str, int]] = set()
        self.cooldown_until_minute = -10**12
        self.diagnostics.update(
            {
                "candidate": "candidate-55-drpt-source-selector-v2",
                "external_source": "grinay/geektrade-strategies:dump-reversal-peak-trail-v2@855641d4667c6dd6bac2815690b15cd9d4991a6b",
                "source_direction": "long_only",
                "source_entry_mode": "ATRStop",
                "source_parity_changes": [
                    "one-minute close confirmation replaces optimistic intrabar stop sequencing",
                    "causal dump low plus 0.15 ATR supplies project structural invalidation",
                    "+2R modeled-net objective supplies finite daytrade reward geometry",
                    "one global four-asset slot replaces per-chart positions",
                ],
                "completed_15m_candles": 0,
                "completed_daily_candles": 0,
                "dump_anomalies": 0,
                "seven_day_low_breaks": 0,
                "source_dump_events": 0,
                "arms_created": 0,
                "arms_replaced": 0,
                "arms_activated": 0,
                "arms_expired": 0,
                "filter_wait_bars": 0,
                "btc_trend_blocks": 0,
                "resistance_blocks": 0,
                "reclaim_candidates": 0,
                "reclaim_competitions": 0,
                "reclaim_entries": 0,
                "used_episode_rejections": 0,
                "geometry_rejections": 0,
                "cooldown_rejections": 0,
                "dump_events_by_symbol": {},
                "entries_by_symbol": {},
            }
        )

    def on_position_closed(self, event: Any) -> None:
        super().on_position_closed(event)
        self.cooldown_until_minute = (
            self.minute_index
            + int(self.config.drpt_source_cooldown_minutes_after_close)
        )
        self.arms.clear()

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

        if self.minute_index < self.cooldown_until_minute:
            self.diagnostics["cooldown_rejections"] += 1
            self.arms.clear()
            return

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % int(self.config.drpt_source_bucket_minutes) == 14:
            self._observe_completed_15m(ts_event)

        self._expire_active_arms(ts_event)
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
        key = (decision.symbol, int(decision.episode_ts))
        if key in self.used_episodes:
            self.diagnostics["used_episode_rejections"] += 1
            self.arms.pop(decision.symbol, None)
            return
        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return

        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(decision, ts_event)
        if int(self.diagnostics["entry_submissions"]) <= before:
            return
        self.used_episodes.add(key)
        self.diagnostics["reclaim_entries"] += 1
        counts = self.diagnostics["entries_by_symbol"]
        counts[decision.symbol] = int(counts.get(decision.symbol, 0)) + 1
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-55-drpt-source-selector-v2",
                    "state_model": (
                        "15m 1-2% dump -> prior seven completed UTC-day low break "
                        "-> source filters -> completed-1m close above high+0.5ATR"
                    ),
                    "external_source": self.diagnostics["external_source"],
                    "arm": {
                        "episode_ts": arm.episode_ts,
                        "dump_open": arm.dump_open,
                        "dump_high": arm.dump_high,
                        "dump_low": arm.dump_low,
                        "dump_close": arm.dump_close,
                        "dump_pct": arm.dump_pct,
                        "prior_7d_low": arm.prior_7d_low,
                        "prior_3d_high": arm.prior_3d_high,
                        "atr": arm.atr,
                        "trigger": arm.trigger,
                        "break_depth_atr": arm.break_depth_atr,
                        "activated_ts": arm.activated_ts,
                        "filter_wait_bars": arm.filter_wait_bars,
                        "reset_count": arm.reset_count,
                    },
                    "cost_aware_target": target_diagnostics,
                    "management": (
                        "structural dump-low invalidation, +2R modeled-net bracket, "
                        "six-hour maximum hold and project funding exit"
                    ),
                }
            )
        selected_episode_ts = arm.episode_ts
        self.arms = {
            symbol: other
            for symbol, other in self.arms.items()
            if other.episode_ts != selected_episode_ts
            and symbol != decision.symbol
        }

    def _expire_active_arms(self, ts_event: int) -> None:
        expired = [
            symbol
            for symbol, arm in self.arms.items()
            if arm.active and ts_event > arm.expiry_ts
        ]
        for symbol in expired:
            self.arms.pop(symbol, None)
            self.diagnostics["arms_expired"] += 1

    def _observe_completed_15m(self, ts_event: int) -> None:
        btc_hours = _complete_candles(tuple(self.bars["BTCUSDT"]), 60)
        btc_4h_change = math.nan
        if len(btc_hours) >= 5:
            btc_4h_change = btc_hours[-1].close - btc_hours[-5].close

        for symbol in SYMBOLS:
            candles15 = _complete_candles(
                tuple(self.bars[symbol]),
                int(self.config.drpt_source_bucket_minutes),
            )
            if not candles15 or candles15[-1].ts_event != ts_event:
                continue
            self.diagnostics["completed_15m_candles"] += 1
            current = candles15[-1]
            atr = _atr(candles15, int(self.config.drpt_source_atr_period))
            days = _complete_candles(tuple(self.bars[symbol]), 1_440)
            self.diagnostics["completed_daily_candles"] = max(
                int(self.diagnostics["completed_daily_candles"]),
                len(days),
            )
            if len(days) < int(self.config.drpt_source_days_lookback):
                continue
            prior_days = days[-int(self.config.drpt_source_days_lookback):]
            prior_resistance = days[-int(self.config.drpt_source_resistance_days):]
            prior_7d_low = min(item.low for item in prior_days)
            prior_3d_high = max(item.high for item in prior_resistance)
            if not _finite_positive(
                current.open,
                current.high,
                current.low,
                current.close,
                atr,
                prior_7d_low,
                prior_3d_high,
            ):
                continue

            dump_pct = (current.close / current.open - 1.0) * 100.0
            anomaly = (
                dump_pct < -float(self.config.drpt_source_anomaly_pct)
                and abs(dump_pct) <= float(self.config.drpt_source_dump_cap_pct)
            )
            if anomaly:
                self.diagnostics["dump_anomalies"] += 1
            breaks_low = current.low < prior_7d_low
            if breaks_low:
                self.diagnostics["seven_day_low_breaks"] += 1

            existing = self.arms.get(symbol)
            if existing is not None and not existing.active:
                existing.filter_wait_bars += 1
                self.diagnostics["filter_wait_bars"] += 1
                current_diff = (current.close / current.open - 1.0) * 100.0
                if (
                    current.low < existing.dump_low
                    and abs(current_diff) <= float(self.config.drpt_source_dump_cap_pct)
                ):
                    existing.dump_open = current.open
                    existing.dump_high = current.high
                    existing.dump_low = current.low
                    existing.dump_close = current.close
                    existing.dump_pct = current_diff
                    existing.atr = atr
                    existing.trigger = (
                        current.high
                        + float(self.config.drpt_source_entry_buffer_atr) * atr
                    )
                    existing.reset_count += 1

            if anomaly and breaks_low:
                self.diagnostics["source_dump_events"] += 1
                counts = self.diagnostics["dump_events_by_symbol"]
                counts[symbol] = int(counts.get(symbol, 0)) + 1
                if symbol in self.arms:
                    self.diagnostics["arms_replaced"] += 1
                break_depth_atr = max(0.0, (prior_7d_low - current.low) / atr)
                self.arms[symbol] = _Arm(
                    symbol=symbol,
                    episode_ts=current.ts_event,
                    dump_open=current.open,
                    dump_high=current.high,
                    dump_low=current.low,
                    dump_close=current.close,
                    dump_pct=dump_pct,
                    prior_7d_low=prior_7d_low,
                    prior_3d_high=prior_3d_high,
                    atr=atr,
                    trigger=(
                        current.high
                        + float(self.config.drpt_source_entry_buffer_atr) * atr
                    ),
                    break_depth_atr=break_depth_atr,
                )
                self.diagnostics["arms_created"] += 1

            arm = self.arms.get(symbol)
            if arm is None or arm.active:
                continue
            near_resistance = (
                current.close < prior_3d_high
                and (
                    (prior_3d_high - current.close)
                    / prior_3d_high
                    * 100.0
                )
                <= float(self.config.drpt_source_resistance_proximity_pct)
            )
            btc_up = (
                math.isfinite(btc_4h_change)
                and btc_4h_change > 0.0
            )
            if near_resistance:
                self.diagnostics["resistance_blocks"] += 1
            if (
                bool(self.config.drpt_source_require_btc_4h_up)
                and not btc_up
            ):
                self.diagnostics["btc_trend_blocks"] += 1
            if near_resistance or (
                bool(self.config.drpt_source_require_btc_4h_up)
                and not btc_up
            ):
                continue
            arm.active = True
            arm.activated_ts = ts_event
            arm.expiry_ts = (
                ts_event
                + int(self.config.drpt_source_stop_expiry_bars)
                * int(self.config.drpt_source_bucket_minutes)
                * MINUTE_NS
            )
            self.diagnostics["arms_activated"] += 1
            self._event(
                "DRPT_SOURCE_ARM_ACTIVATED",
                ts_event,
                symbol=symbol,
                episode_ts=arm.episode_ts,
                trigger=arm.trigger,
                dump_pct=arm.dump_pct,
                break_depth_atr=arm.break_depth_atr,
                btc_4h_change=btc_4h_change,
                resistance=prior_3d_high,
            )

    def _reclaim_candidates(
        self,
        ts_event: int,
    ) -> list[tuple[RouteDecision, _Arm, dict[str, float]]]:
        result: list[tuple[RouteDecision, _Arm, dict[str, float]]] = []
        for symbol, arm in list(self.arms.items()):
            if not arm.active or ts_event > arm.expiry_ts:
                continue
            current = float(self.bars[symbol][-1].close)
            if not math.isfinite(current) or current <= arm.trigger:
                continue
            stop = (
                arm.dump_low
                - float(self.config.drpt_source_stop_buffer_atr) * arm.atr
            )
            if not _finite_positive(current, stop) or stop >= current:
                self.diagnostics["geometry_rejections"] += 1
                continue
            try:
                target, target_diagnostics = _cost_aware_target(
                    entry=current,
                    stop=stop,
                    side=1,
                    fee_bps_each_side=float(self.config.all_in_cost_bps_each_side),
                    slippage_bps_each_side=float(self.config.adverse_slippage_bps_each_side),
                    funding_reserve_bps=float(self.config.funding_reserve_bps),
                    net_target_r=float(self.config.drpt_source_target_net_r),
                )
            except ValueError:
                self.diagnostics["geometry_rejections"] += 1
                continue
            close_excess_atr = (current - arm.trigger) / max(arm.atr, 1e-12)
            score = (
                close_excess_atr
                + arm.break_depth_atr
                + abs(arm.dump_pct) / 2.0
            )
            diagnostics: dict[str, float | int | str] = {
                "dump_pct": arm.dump_pct,
                "break_depth_atr": arm.break_depth_atr,
                "close_excess_atr": close_excess_atr,
                "arm_age_minutes": (ts_event - arm.episode_ts) / MINUTE_NS,
                "filter_wait_bars": arm.filter_wait_bars,
                "reset_count": arm.reset_count,
                **target_diagnostics,
            }
            result.append(
                (
                    RouteDecision(
                        symbol=symbol,
                        state=STATE,
                        side=1,
                        score=score,
                        entry_reference=current,
                        stop_reference=stop,
                        objective_reference=target,
                        episode_ts=arm.episode_ts,
                        reasons=(
                            "SOURCE_1_TO_2_PERCENT_DUMP",
                            "PRIOR_SEVEN_COMPLETED_DAYS_LOW_BROKEN",
                            "SOURCE_BTC_AND_RESISTANCE_FILTERS_PASSED",
                            "COMPLETED_1M_CLOSE_ABOVE_DUMP_HIGH_PLUS_HALF_ATR",
                            "STRUCTURAL_DUMP_LOW_INVALIDATION",
                            "COST_AWARE_POSITIVE_REWARD_SPACE",
                        ),
                        diagnostics=diagnostics,
                    ),
                    arm,
                    target_diagnostics,
                )
            )
        return result


__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "_complete_candles",
    "_atr",
]
