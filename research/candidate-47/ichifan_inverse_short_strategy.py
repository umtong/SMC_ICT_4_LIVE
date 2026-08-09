"""Inverse-price short mirror of the causal public ``ichiV2`` policy.

Instead of inventing a second indicator stack, this policy maps each completed
five-minute OHLC bar into reciprocal-price space and reuses the exact tested
long fan/Ichimoku implementation.  A long acceleration in reciprocal price is
a short acceleration in original price.  This also lets the tested structural
risk function map back into a causal short stop without new thresholds.
"""
from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
import math
from pathlib import Path
import sys
from typing import Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ichifan_strategy as _exact
from ichifan_structural_strategy import causal_structural_stop
import router as _router

Candidate47IchiFanInverseShortConfig = _exact.Candidate47IchiFanConfig
Candidate35Config = Candidate47IchiFanInverseShortConfig
Candidate35StrategyBase = _exact._base.Candidate35Strategy
SYMBOLS = _exact.SYMBOLS


def reciprocal_bars(
    bars: Sequence[_exact.FiveMinuteBar],
) -> list[_exact.FiveMinuteBar]:
    """Map positive OHLC into reciprocal space while preserving candle geometry."""
    output: list[_exact.FiveMinuteBar] = []
    for bar in bars:
        values = (bar.open, bar.high, bar.low, bar.close)
        if not all(math.isfinite(value) and value > 0.0 for value in values):
            continue
        output.append(
            _exact.FiveMinuteBar(
                ts_event=bar.ts_event,
                open=1.0 / bar.open,
                high=1.0 / bar.low,
                low=1.0 / bar.high,
                close=1.0 / bar.close,
                volume=bar.volume,
            )
        )
    return output


def causal_inverse_short_stop(
    *,
    entry: float,
    signal_bar_high: float,
    inverse_trend_close_90m: float,
    inverse_cloud_a: float,
    inverse_cloud_b: float,
) -> tuple[float, dict[str, float]]:
    """Reuse long reciprocal-space invalidation and map it back to a short stop."""
    if not math.isfinite(entry) or entry <= 0.0:
        raise ValueError("entry must be finite and positive")
    inverse_entry = 1.0 / entry
    inverse_signal_low = 1.0 / signal_bar_high
    inverse_stop, inverse_geometry = causal_structural_stop(
        entry=inverse_entry,
        signal_bar_low=inverse_signal_low,
        trend_close_90m=inverse_trend_close_90m,
        cloud_a=inverse_cloud_a,
        cloud_b=inverse_cloud_b,
        emergency_fraction=0.10,
    )
    stop = 1.0 / inverse_stop
    if not entry < stop <= entry * 1.10 + 1e-12:
        raise ValueError(f"invalid mapped short stop {stop} for entry {entry}")
    geometry = {
        "inverse_entry": inverse_entry,
        "inverse_signal_bar_low": inverse_signal_low,
        "inverse_structural_stop": inverse_stop,
        "structural_stop": stop,
        "structural_stop_fraction": (stop - entry) / entry,
        "signal_bar_high": signal_bar_high,
        "source_emergency_stop": entry * 1.10,
        **{f"inverse_{key}": value for key, value in inverse_geometry.items()},
    }
    return stop, geometry


class Candidate47IchiFanInverseShortStrategy(_exact.Candidate47IchiFanStrategy):
    """One-slot four-symbol inverse-price short policy."""

    def __init__(self, config: Candidate47IchiFanInverseShortConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "inverse_short_decisions": 0,
                "inverse_short_ready_states": 0,
                "inverse_short_entry_candidates": 0,
                "inverse_short_rising_edges": 0,
                "inverse_short_structural_submissions": 0,
                "inverse_short_structural_failures": 0,
                "inverse_short_stop_distance_sum": 0.0,
                "inverse_short_stop_distance_min": None,
                "inverse_short_stop_distance_max": None,
                "inverse_short_exit_signals": 0,
                "inverse_short_trailing_exits": 0,
                "inverse_short_policy": "exact-ichiv2-in-reciprocal-price-space",
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
                self._event("ENTRY_EXPIRED", ts_event, reason="INVERSE_SHORT_PARENT_NOT_FILLED")
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
        if any(len(self.bars[symbol]) < 800 for symbol in SYMBOLS):
            return

        self.diagnostics["inverse_short_decisions"] += 1
        candidates: list[tuple[float, str, _exact.FanState]] = []
        for symbol in SYMBOLS:
            original = _exact.aggregate_five_minute(tuple(self.bars[symbol]))
            inverse = reciprocal_bars(original)
            states = _exact.fan_states(inverse)
            if len(states) < 2 or not states[-1].ready:
                continue
            self.diagnostics["inverse_short_ready_states"] += 1
            current, previous = states[-1], states[-2]
            if current.entry:
                self.diagnostics["inverse_short_entry_candidates"] += 1
            if not current.entry or previous.entry:
                continue
            self.diagnostics["inverse_short_rising_edges"] += 1
            candidates.append((current.score, symbol, current))

        if not candidates:
            self.diagnostics["unresolved_episodes"] += 1
            return
        candidates.sort(key=lambda item: (item[0], item[1] == "BTCUSDT", item[1]), reverse=True)
        score, symbol, state = candidates[0]
        episode_id = f"{symbol}:{state.ts_event}:ICHIFAN_INVERSE_SHORT"
        if episode_id in self._seen_episodes:
            self.diagnostics["ichifan_duplicate_episode_rejections"] += 1
            return

        entry = float(self.bars[symbol][-1].close)
        decision = _router.RouteDecision(
            symbol=symbol,
            state="ICHIMOKU_FAN_INVERSE_ACCELERATION_SHORT",
            side=-1,
            score=score,
            expected_target_r=3.0,
            atr=math.nan,
            entry_reference=entry,
            stop_reference=entry * 1.10,
            objective_reference=entry * 0.70,
            episode_ts=state.ts_event,
            reasons=(
                "RECIPROCAL_PRICE_ABOVE_SHIFTED_ICHIMOKU_CLOUD",
                "BULLISH_RECIPROCAL_HEIKIN_ASHI_FAN",
                "THREE_STEP_RECIPROCAL_FAN_ACCELERATION",
                "MAPPED_TO_ORIGINAL_PRICE_SHORT",
            ),
            diagnostics={
                "causal_episode_id": episode_id,
                "inverse_fan_magnitude": state.fan_magnitude,
                "inverse_fan_gain": state.fan_gain,
                "inverse_trend_close_5m": state.trend_close_5m,
                "inverse_trend_close_90m": state.trend_close_90m,
                "inverse_trend_close_8h": state.trend_close_8h,
                "inverse_cloud_a": state.cloud_a,
                "inverse_cloud_b": state.cloud_b,
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
                    "scenario_id": f"c47-inverse-short-{before + 1:07d}",
                    "candidate": "candidate-47-public-ichiv2-inverse-short",
                    "causal_episode_id": episode_id,
                    "entry_policy": "MARKET_BRACKET",
                    "external_dynamic_exit": "RECIPROCAL_5M_CROSS_BELOW_RECIPROCAL_90M_EMA",
                    "external_trailing_activation": 0.08,
                    "external_trailing_distance": 0.06,
                    "trough_price": entry,
                }
            )

    def _submit_decision(self, decision: _router.RouteDecision, ts_event: int) -> None:
        if decision.side >= 0:
            Candidate35StrategyBase._submit_decision(self, decision, ts_event)
            return
        symbol = decision.symbol
        original = _exact.aggregate_five_minute(tuple(self.bars[symbol]))
        inverse = reciprocal_bars(original)
        states = _exact.fan_states(inverse)
        if len(original) < 2 or not states or not states[-1].ready:
            self.diagnostics["inverse_short_structural_failures"] += 1
            return
        state = states[-1]
        signal_bar = original[-2]
        entry = float(self.bars[symbol][-1].close)
        try:
            stop, geometry = causal_inverse_short_stop(
                entry=entry,
                signal_bar_high=float(signal_bar.high),
                inverse_trend_close_90m=float(state.trend_close_90m),
                inverse_cloud_a=float(state.cloud_a),
                inverse_cloud_b=float(state.cloud_b),
            )
        except ValueError as error:
            self.diagnostics["inverse_short_structural_failures"] += 1
            self._event("INVERSE_SHORT_STRUCTURAL_STOP_UNAVAILABLE", ts_event, symbol=symbol, reason=str(error))
            return

        distance = float(geometry["structural_stop_fraction"])
        self.diagnostics["inverse_short_stop_distance_sum"] += distance
        current_min = self.diagnostics["inverse_short_stop_distance_min"]
        current_max = self.diagnostics["inverse_short_stop_distance_max"]
        self.diagnostics["inverse_short_stop_distance_min"] = distance if current_min is None else min(float(current_min), distance)
        self.diagnostics["inverse_short_stop_distance_max"] = distance if current_max is None else max(float(current_max), distance)
        mapped = replace(
            decision,
            stop_reference=stop,
            diagnostics={**dict(decision.diagnostics), "risk_geometry": "RECIPROCAL_CAUSAL_STRUCTURAL_INVALIDATION", **geometry},
        )
        before = int(self.diagnostics["entry_submissions"])
        Candidate35StrategyBase._submit_decision(self, mapped, ts_event)
        if int(self.diagnostics["entry_submissions"]) > before:
            self.diagnostics["inverse_short_structural_submissions"] += 1
            if self.current_scenario is not None:
                self.current_scenario.update({"candidate": "candidate-47-public-ichiv2-inverse-short", **geometry})

    def _manage_open_position(self, ts_event: int) -> None:
        symbol = self.current_symbol
        if symbol is None or not self.bars[symbol]:
            return
        latest = self.bars[symbol][-1]
        scenario = self.current_scenario or {}
        trough = min(float(scenario.get("trough_price", latest.low)), float(latest.low))
        scenario["trough_price"] = trough
        self.current_scenario = scenario
        entry = float(scenario.get("entry_reference", latest.close))

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 == 4:
            original = _exact.aggregate_five_minute(tuple(self.bars[symbol]))
            states = _exact.fan_states(reciprocal_bars(original))
            if states and states[-1].exit_cross_down:
                self.diagnostics["inverse_short_exit_signals"] += 1
                self._request_exit(
                    ts_event,
                    "ICHIFAN_INVERSE_SHORT_TREND_CROSS_EXIT",
                    inverse_close_5m=states[-1].trend_close_5m,
                    inverse_close_90m=states[-1].trend_close_90m,
                )
                return

        trailing_active = entry > 0.0 and 1.0 - trough / entry >= 0.08
        trailing_hit = trailing_active and float(latest.close) >= trough * 1.06
        if trailing_hit:
            self.diagnostics["inverse_short_trailing_exits"] += 1
            self._request_exit(ts_event, "ICHIFAN_INVERSE_SHORT_TRAILING_EXIT", entry=entry, trough=trough, close=float(latest.close))
            return

        Candidate35StrategyBase._manage_open_position(self, ts_event)


Candidate35Strategy = Candidate47IchiFanInverseShortStrategy
