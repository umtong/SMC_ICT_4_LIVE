"""NautilusTrader adapter for the public E0V1EN 5-minute policy.

Source entry/exit equations, exported parameters, 25% hard stop, 3%/0.2%
trailing rule and 96-candle cooldown are retained.  The project supplies the
single continuous four-asset account, one-position arbitration, actual-fill
validity, fees/slippage/funding reserve and current-NAV 3% risk sizing.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import math
from typing import Sequence

from router import (
    BarObservation,
    E0V1EN_STATE,
    RouteConfig,
    RouteDecision,
    classify_symbol,
    indicators,
)
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    e0v1en_entry_mode: str = "exact"
    e0v1en_rsi_fast_period: int = 4
    e0v1en_rsi_period: int = 14
    e0v1en_rsi_slow_period: int = 20
    e0v1en_sma_period: int = 15
    e0v1en_cti_period: int = 20
    e0v1en_cci_period: int = 20
    e0v1en_stoch_period: int = 5
    e0v1en_24h_bars: int = 288
    e0v1en_buy1_rsi_fast_max: float = 40.0
    e0v1en_buy1_rsi_min: float = 42.0
    e0v1en_buy1_sma_fraction: float = 0.973
    e0v1en_buy1_cti_max: float = 0.69
    e0v1en_buy1_change_min_pct: float = -25.8
    e0v1en_buy1_change_max_pct: float = 122.9
    e0v1en_buynew_rsi_fast_max: float = 34.0
    e0v1en_buynew_rsi_min: float = 28.0
    e0v1en_buynew_sma_fraction: float = 0.96
    e0v1en_buynew_cti_max: float = 0.69
    e0v1en_buynew_change_min_pct: float = -24.3
    e0v1en_buynew_change_max_pct: float = 24.3
    e0v1en_stop_fraction: float = 0.25
    e0v1en_objective_fraction: float = 1.0
    e0v1en_cooldown_candles: int = 96
    e0v1en_trailing_trigger_fraction: float = 0.03
    e0v1en_trailing_distance_fraction: float = 0.002
    e0v1en_fastk_exit: float = 84.0
    e0v1en_cci_exit: float = 80.0
    e0v1en_cci_min_profit: float = -0.03
    e0v1en_time1_minutes: int = 420
    e0v1en_time1_min_profit: float = -0.05
    e0v1en_time2_minutes: int = 600
    e0v1en_time2_min_profit: float = -0.10


def _completed_bucket(
    bars: Sequence[BarObservation], minutes: int
) -> BarObservation | None:
    if minutes <= 0 or len(bars) < minutes:
        return None
    sample = list(bars)[-minutes:]
    minute_ns = 60_000_000_000
    timestamps = [int(bar.ts_event) for bar in sample]
    if any(
        timestamps[index] - timestamps[index - 1] != minute_ns
        for index in range(1, len(timestamps))
    ):
        return None
    phase = timestamps[-1] % minute_ns
    ordinal = (timestamps[-1] - phase) // minute_ns
    if ordinal % minutes != minutes - 1:
        return None
    return BarObservation(
        ts_event=timestamps[-1],
        open=float(sample[0].open),
        high=max(float(bar.high) for bar in sample),
        low=min(float(bar.low) for bar in sample),
        close=float(sample[-1].close),
        volume=sum(max(0.0, float(bar.volume)) for bar in sample),
    )


class Candidate35Strategy(_ExecutionShell):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=600) for symbol in SYMBOLS
        }
        self.five_bars = {symbol: deque(maxlen=10_000) for symbol in SYMBOLS}
        self.route_config = replace(
            self.route_config,
            e0v1en_entry_mode=str(config.e0v1en_entry_mode),
            e0v1en_rsi_fast_period=int(config.e0v1en_rsi_fast_period),
            e0v1en_rsi_period=int(config.e0v1en_rsi_period),
            e0v1en_rsi_slow_period=int(config.e0v1en_rsi_slow_period),
            e0v1en_sma_period=int(config.e0v1en_sma_period),
            e0v1en_cti_period=int(config.e0v1en_cti_period),
            e0v1en_cci_period=int(config.e0v1en_cci_period),
            e0v1en_stoch_period=int(config.e0v1en_stoch_period),
            e0v1en_24h_bars=int(config.e0v1en_24h_bars),
            e0v1en_buy1_rsi_fast_max=float(config.e0v1en_buy1_rsi_fast_max),
            e0v1en_buy1_rsi_min=float(config.e0v1en_buy1_rsi_min),
            e0v1en_buy1_sma_fraction=float(config.e0v1en_buy1_sma_fraction),
            e0v1en_buy1_cti_max=float(config.e0v1en_buy1_cti_max),
            e0v1en_buy1_change_min_pct=float(config.e0v1en_buy1_change_min_pct),
            e0v1en_buy1_change_max_pct=float(config.e0v1en_buy1_change_max_pct),
            e0v1en_buynew_rsi_fast_max=float(config.e0v1en_buynew_rsi_fast_max),
            e0v1en_buynew_rsi_min=float(config.e0v1en_buynew_rsi_min),
            e0v1en_buynew_sma_fraction=float(config.e0v1en_buynew_sma_fraction),
            e0v1en_buynew_cti_max=float(config.e0v1en_buynew_cti_max),
            e0v1en_buynew_change_min_pct=float(config.e0v1en_buynew_change_min_pct),
            e0v1en_buynew_change_max_pct=float(config.e0v1en_buynew_change_max_pct),
            e0v1en_stop_fraction=float(config.e0v1en_stop_fraction),
            e0v1en_objective_fraction=float(config.e0v1en_objective_fraction),
        )
        self.signal_tag: dict[str, str | None] = {symbol: None for symbol in SYMBOLS}
        self.signal_episode_ts: dict[str, int] = {symbol: -1 for symbol in SYMBOLS}
        self.used_episode_keys: set[tuple[str, int]] = set()
        self.last_source_exit_minute = -10**9
        self.source_exit_pending = False
        self.source_peak = math.nan
        self.source_trailing_active = False
        self.last_five_indicators: dict[str, dict[str, float]] = {}
        self.diagnostics.update(
            {
                "external_source": "eovie/freqtrade_strs/binance/dry_run/E0V1EN.py",
                "external_parameter_export": "2025-03-27",
                "external_performance_used_as_evidence": False,
                "e0v1en_entry_mode": str(config.e0v1en_entry_mode),
                "e0v1en_five_minute_bars_by_symbol": {},
                "e0v1en_source_candidates": 0,
                "e0v1en_used_episode_rejections": 0,
                "e0v1en_cooldown_rejections": 0,
                "e0v1en_exit_counts": {},
                "e0v1en_trailing_activations": 0,
                "e0v1en_signal_tags": {},
                "unresolved_reason_counts": {},
                "actionable_family_counts": {},
            }
        )

    def _after_position_opened(self, event, scenario) -> None:
        del event
        fill = scenario.get("actual_entry_fill")
        reference = scenario.get("entry_reference")
        value = float(fill if fill is not None else reference)
        self.source_peak = value if math.isfinite(value) else math.nan
        self.source_trailing_active = False
        self.source_exit_pending = False

    def _after_position_closed(self, event, record) -> None:
        del event, record
        self.last_source_exit_minute = self.minute_index
        self.source_peak = math.nan
        self.source_trailing_active = False
        self.source_exit_pending = False

    def _source_exit(self, ts_event: int, reason: str, **details) -> None:
        if self.current_symbol is None or self.source_exit_pending:
            return
        self.source_exit_pending = True
        counts = self.diagnostics["e0v1en_exit_counts"]
        counts[reason] = int(counts.get(reason, 0)) + 1
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event("E0V1EN_SOURCE_EXIT", ts_event, reason=reason, **details)

    def _manage_e0v1en(self, ts_event: int, five_completed: bool) -> None:
        if self.current_symbol is None or self.source_exit_pending:
            return
        scenario = self.current_scenario or {}
        entry_raw = scenario.get("actual_entry_fill", scenario.get("entry_reference"))
        try:
            entry = float(entry_raw)
        except (TypeError, ValueError):
            entry = math.nan
        if not math.isfinite(entry) or entry <= 0.0:
            self._manage_open_position(ts_event)
            return
        latest = self.bars[self.current_symbol][-1]
        high, low, close = float(latest.high), float(latest.low), float(latest.close)
        if not math.isfinite(self.source_peak):
            self.source_peak = entry

        # Conservative OHLC handling: a trail active before this minute may
        # stop on its old peak; a trigger first reached this minute cannot also
        # claim a favorable high-before-low ordering.
        if self.source_trailing_active:
            stop = self.source_peak * (1.0 - float(self.config.e0v1en_trailing_distance_fraction))
            if low <= stop:
                self._source_exit(
                    ts_event,
                    "TRAILING_0P2_AFTER_3P0",
                    source_peak=self.source_peak,
                    source_trailing_stop=stop,
                    minute_low=low,
                )
                return
            self.source_peak = max(self.source_peak, high)
        elif high >= entry * (1.0 + float(self.config.e0v1en_trailing_trigger_fraction)):
            self.source_trailing_active = True
            self.source_peak = max(self.source_peak, high)
            self.diagnostics["e0v1en_trailing_activations"] += 1
            self._event(
                "E0V1EN_TRAILING_ACTIVATED",
                ts_event,
                entry=entry,
                peak=self.source_peak,
            )

        profit = close / entry - 1.0
        held = (
            self.minute_index - self.position_open_minute
            if self.position_open_minute >= 0 else 0
        )
        if five_completed:
            values = self.last_five_indicators.get(self.current_symbol)
            tag = str(scenario.get("entry_tag", scenario.get("diagnostics", {}).get("entry_tag", "")))
            if values is not None:
                if (
                    tag == "buy_new"
                    and profit > 0.0
                    and values["fastk_5"] > float(self.config.e0v1en_fastk_exit)
                ):
                    self._source_exit(ts_event, "FASTK_PROFIT_SELL", profit=profit, fastk=values["fastk_5"])
                    return
                if (
                    profit > float(self.config.e0v1en_cci_min_profit)
                    and values["cci_20"] > float(self.config.e0v1en_cci_exit)
                ):
                    self._source_exit(ts_event, "CCI_LOSS_SELL", profit=profit, cci=values["cci_20"])
                    return
        if held >= int(self.config.e0v1en_time1_minutes) and profit >= float(self.config.e0v1en_time1_min_profit):
            self._source_exit(ts_event, "TIME_LOSS_SELL_7H", profit=profit, held_minutes=held)
            return
        if held >= int(self.config.e0v1en_time2_minutes) and profit >= float(self.config.e0v1en_time2_min_profit):
            self._source_exit(ts_event, "TIME_LOSS_SELL_10H", profit=profit, held_minutes=held)
            return
        self._manage_open_position(ts_event)

    def _five_minute_decisions(self, ts_event: int):
        minute_ordinal = int(ts_event // 60_000_000_000)
        if minute_ordinal % 5 != 4:
            return None
        decisions: dict[str, RouteDecision] = {}
        tag_counts = self.diagnostics["e0v1en_signal_tags"]
        for symbol in SYMBOLS:
            candle = _completed_bucket(tuple(self.bars[symbol]), 5)
            if candle is None:
                continue
            if self.five_bars[symbol] and self.five_bars[symbol][-1].ts_event == candle.ts_event:
                continue
            self.five_bars[symbol].append(candle)
            values = indicators(tuple(self.five_bars[symbol]), self.route_config)
            if values is not None:
                self.last_five_indicators[symbol] = values
            decision = classify_symbol(symbol, tuple(self.five_bars[symbol]), None, self.route_config)
            if decision.actionable:
                tag = str(decision.diagnostics.get("entry_tag", ""))
                if self.signal_tag[symbol] != tag:
                    self.signal_tag[symbol] = tag
                    self.signal_episode_ts[symbol] = int(decision.episode_ts)
                decision = replace(decision, episode_ts=self.signal_episode_ts[symbol])
                tag_counts[tag] = int(tag_counts.get(tag, 0)) + 1
            else:
                self.signal_tag[symbol] = None
                self.signal_episode_ts[symbol] = -1
            decisions[symbol] = decision
        self.diagnostics["e0v1en_five_minute_bars_by_symbol"] = {
            symbol: len(self.five_bars[symbol]) for symbol in SYMBOLS
        }
        return decisions if len(decisions) == len(SYMBOLS) else None

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        decisions = self._five_minute_decisions(ts_event)
        five_completed = decisions is not None
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
            self._manage_e0v1en(ts_event, five_completed)
            return
        if self.entry_pending:
            self.diagnostics["max_simultaneous_entry_intents"] = max(
                int(self.diagnostics["max_simultaneous_entry_intents"]), 1
            )
            if self.minute_index - self.entry_pending_minute > 2:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event("ENTRY_EXPIRED", ts_event, reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES")
                self._clear_trade_state()
            return
        if decisions is None:
            return
        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return

        self.diagnostics["quarter_hour_decisions"] += 1
        candidates = []
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
        route_counts = self.diagnostics["route_counts"]
        for decision in decisions.values():
            route_counts[decision.state] = int(route_counts.get(decision.state, 0)) + 1
            if decision.actionable:
                family_counts[decision.state] = int(family_counts.get(decision.state, 0)) + 1
                self.diagnostics["e0v1en_source_candidates"] += 1
                key = (decision.symbol, int(decision.episode_ts))
                if key in self.used_episode_keys:
                    self.diagnostics["e0v1en_used_episode_rejections"] += 1
                else:
                    candidates.append(decision)
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1
        if not candidates:
            self.diagnostics["unresolved_episodes"] += 1
            return
        cooldown_minutes = int(self.config.e0v1en_cooldown_candles) * 5
        if self.minute_index - self.last_source_exit_minute < cooldown_minutes:
            self.diagnostics["e0v1en_cooldown_rejections"] += len(candidates)
            return
        candidates.sort(key=lambda item: (-float(item.score), item.symbol))
        winner = candidates[0]
        key = (winner.symbol, int(winner.episode_ts))
        self.used_episode_keys.add(key)
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if int(self.diagnostics["entry_submissions"]) > before and self.current_scenario is not None:
            tag = str(winner.diagnostics.get("entry_tag", ""))
            self.current_scenario.update(
                {
                    "candidate": "candidate-51-e0v1en-v29",
                    "state_family": E0V1EN_STATE,
                    "entry_tag": tag,
                    "source_performance_used": False,
                    "source_policy": "eovie E0V1EN exported 2025-03-27",
                    "management": "source stop/trail/fastk/cci/7h/10h plus evaluation-end close",
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
