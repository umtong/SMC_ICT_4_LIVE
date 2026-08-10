"""NautilusTrader execution for the public Slope-is-Dope family.

The source entry, ROI, trailing and wide emergency stop are preserved as named
controls.  The source's asymmetric short rolling-low exit, a symmetric rolling-
high interpretation, MA-only management, no-signal management, independent
episode handling and structural invalidation are tested separately.  No account
results are merged outside a fresh one-slot simulation.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
import math
from typing import Any

import router as _router
import router_picasso as _ta
import strategy_base as _base

SYMBOLS = _base.SYMBOLS


class Candidate35Config(_base.Candidate35Config, frozen=True):
    slope_bucket_minutes: int = 60
    slope_episode_mode: str = "condition_reentry"
    slope_direction_mode: str = "dual"
    slope_adx_period: int = 14
    slope_rsi_period: int = 10
    slope_fast_period: int = 16
    slope_slow_period: int = 57
    slope_market_period: int = 97
    slope_lookback: int = 10
    slope_long_close_shift: int = 6
    slope_short_close_shift: int = 9
    slope_long_adx_min: float = 39.0
    slope_short_adx_min: float = 20.0
    slope_rsi_midline: float = 55.0
    slope_source_leverage: float = 2.0
    slope_source_stoploss_profit_ratio: float = 0.289
    slope_remote_target_fraction: float = 0.1415

    # source | signal_slow_atr
    slope_stop_mode: str = "source"
    slope_stop_atr_period: int = 14
    slope_stop_atr_buffer: float = 0.25

    # source_exact | corrected_symmetric | ma_only | no_signal
    slope_exit_mode: str = "source_exact"
    slope_exit_range_period: int = 9

    slope_trailing_positive_profit_ratio: float = 0.01
    slope_trailing_offset_profit_ratio: float = 0.021
    slope_roi_0_profit_ratio: float = 0.283
    slope_roi_132_profit_ratio: float = 0.16
    slope_roi_548_profit_ratio: float = 0.071
    slope_roi_961_profit_ratio: float = 0.0


class Candidate35Strategy(_base.Candidate35Strategy):
    """One global position across the four project instruments."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.route_config = _router.RouteConfig(
            atr_period=config.atr_period,
            min_impulse_atr_continuation=config.min_impulse_atr_continuation,
            min_impulse_atr_reversal=config.min_impulse_atr_reversal,
            min_response_atr=config.min_response_atr,
            min_participation_ratio=config.min_participation_ratio,
            min_route_score=config.min_route_score,
            ambiguity_score_gap=config.ambiguity_score_gap,
            continuation_target_r=config.continuation_target_r,
            reversal_target_r=config.reversal_target_r,
            slope_bucket_minutes=config.slope_bucket_minutes,
            slope_episode_mode=config.slope_episode_mode,
            slope_direction_mode=config.slope_direction_mode,
            slope_adx_period=config.slope_adx_period,
            slope_rsi_period=config.slope_rsi_period,
            slope_fast_period=config.slope_fast_period,
            slope_slow_period=config.slope_slow_period,
            slope_market_period=config.slope_market_period,
            slope_lookback=config.slope_lookback,
            slope_long_close_shift=config.slope_long_close_shift,
            slope_short_close_shift=config.slope_short_close_shift,
            slope_long_adx_min=config.slope_long_adx_min,
            slope_short_adx_min=config.slope_short_adx_min,
            slope_rsi_midline=config.slope_rsi_midline,
            slope_source_leverage=config.slope_source_leverage,
            slope_source_stoploss_profit_ratio=config.slope_source_stoploss_profit_ratio,
            slope_remote_target_fraction=config.slope_remote_target_fraction,
        )
        self.bars = {symbol: deque(maxlen=20_000) for symbol in SYMBOLS}
        self._exit_pending = False
        self._trail_active = False
        self._trail_best = math.nan
        self.diagnostics.update(
            {
                "slope_hourly_decisions": 0,
                "slope_source_conditions": 0,
                "slope_entry_candidates": 0,
                "slope_trailing_activations": 0,
                "slope_trailing_exits": 0,
                "slope_roi_exits": 0,
                "slope_source_exit_signals": 0,
                "slope_structural_stop_submissions": 0,
                "slope_path_updates": 0,
                "slope_exit_counts": {},
                "slope_episode_mode": str(config.slope_episode_mode),
                "slope_direction_mode": str(config.slope_direction_mode),
                "slope_stop_mode": str(config.slope_stop_mode),
                "slope_exit_mode": str(config.slope_exit_mode),
                "unresolved_reason_counts": {},
            }
        )

    def _reset_policy_state(self) -> None:
        self._exit_pending = False
        self._trail_active = False
        self._trail_best = math.nan

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._reset_policy_state()

    def _after_position_opened(self, event: Any, scenario: dict[str, Any]) -> None:
        del event
        entry = float(scenario.get("actual_entry_fill") or scenario.get("entry_reference") or math.nan)
        self._exit_pending = False
        self._trail_active = False
        self._trail_best = entry
        scenario.update(
            {
                "slope_mfe_fraction": 0.0,
                "slope_mae_fraction": 0.0,
                "slope_current_fraction": 0.0,
                "slope_elapsed_minutes": 0,
                "slope_trail_activation_minutes": None,
                "slope_exit_driver": None,
                "slope_first_positive_minute": None,
                "slope_first_one_percent_mfe_minute": None,
            }
        )

    def _after_position_closed(self, event: Any, record: dict[str, Any]) -> None:
        del event, record
        self._reset_policy_state()

    def _hourly_candles(self, symbol: str):
        return _ta._aggregate_complete(tuple(self.bars[symbol]), int(self.route_config.slope_bucket_minutes))

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
                self._event("ENTRY_EXPIRED", ts_event, reason="SLOPE_PARENT_NOT_FILLED")
                self._clear_trade_state()
            return
        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute != 59:
            return
        self.diagnostics["slope_hourly_decisions"] += 1
        features = {
            symbol: _router.FeatureObservation(
                observed_time_ns=int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        winner, decisions = _router.route_universe(
            {symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
            features,
            self.route_config,
        )
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if decision.actionable:
                self.diagnostics["slope_source_conditions"] += 1
            else:
                reason = decision.reasons[0] if decision.reasons else "UNKNOWN"
                reasons = self.diagnostics["unresolved_reason_counts"]
                reasons[reason] = int(reasons.get(reason, 0)) + 1
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        self.diagnostics["slope_entry_candidates"] += 1
        self._submit_decision(winner, ts_event)

    def _repaired_stop(self, decision) -> tuple[float, dict[str, float | str]] | None:
        mode = str(self.config.slope_stop_mode).strip().lower()
        source_stop = float(decision.stop_reference)
        if mode == "source":
            return source_stop, {
                "slope_stop_mode": mode,
                "slope_source_stop": source_stop,
                "slope_repaired_stop": source_stop,
            }
        if mode != "signal_slow_atr":
            raise ValueError(f"unsupported slope_stop_mode={mode!r}")
        candles = self._hourly_candles(decision.symbol)
        period = int(self.config.slope_stop_atr_period)
        if len(candles) < period + 2:
            return None
        atr = float(_ta._atr(candles, period)[-1])
        diagnostics = dict(decision.diagnostics)
        try:
            slow = float(diagnostics["slow_sma"])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(atr) or atr <= 0.0 or not math.isfinite(slow):
            return None
        signal = candles[-1]
        buffer = max(0.0, float(self.config.slope_stop_atr_buffer)) * atr
        if int(decision.side) > 0:
            structural = min(float(signal.low), slow) - buffer
            stop = max(source_stop, structural)
        else:
            structural = max(float(signal.high), slow) + buffer
            stop = min(source_stop, structural)
        if not math.isfinite(stop) or stop <= 0.0:
            return None
        return stop, {
            "slope_stop_mode": mode,
            "slope_source_stop": source_stop,
            "slope_structural_stop_raw": structural,
            "slope_repaired_stop": stop,
            "slope_stop_atr": atr,
            "slope_stop_buffer": buffer,
            "slope_signal_high": float(signal.high),
            "slope_signal_low": float(signal.low),
            "slope_slow_sma_anchor": slow,
        }

    def _submit_decision(self, decision, ts_event: int) -> None:
        repaired = self._repaired_stop(decision)
        if repaired is None:
            self._event("SLOPE_STOP_UNAVAILABLE", ts_event, symbol=decision.symbol)
            return
        stop, details = repaired
        before = int(self.diagnostics["entry_submissions"])
        super()._submit_decision(replace(decision, stop_reference=stop), ts_event)
        if int(self.diagnostics["entry_submissions"]) <= before or self.current_scenario is None:
            return
        if str(self.config.slope_stop_mode).strip().lower() != "source":
            self.diagnostics["slope_structural_stop_submissions"] += 1
        self.current_scenario.update(
            {
                "slope_exit_mode": str(self.config.slope_exit_mode),
                "slope_episode_mode": str(self.config.slope_episode_mode),
                "slope_direction_mode": str(self.config.slope_direction_mode),
                **details,
            }
        )

    def _update_path(self) -> None:
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is None or symbol is None or not self.bars[symbol]:
            return
        entry = float(scenario.get("actual_entry_fill") or scenario.get("entry_reference") or math.nan)
        side = int(scenario.get("side", 0))
        if not math.isfinite(entry) or entry <= 0.0 or side not in (-1, 1):
            return
        bar = self.bars[symbol][-1]
        favourable_price = float(bar.high) if side > 0 else float(bar.low)
        adverse_price = float(bar.low) if side > 0 else float(bar.high)
        favourable = side * (favourable_price - entry) / entry
        adverse = side * (adverse_price - entry) / entry
        current = side * (float(bar.close) - entry) / entry
        elapsed = max(0, self.minute_index - self.position_open_minute)
        prior_mfe = float(scenario.get("slope_mfe_fraction") or 0.0)
        prior_mae = float(scenario.get("slope_mae_fraction") or 0.0)
        scenario.update(
            {
                "slope_mfe_fraction": max(prior_mfe, favourable),
                "slope_mae_fraction": min(prior_mae, adverse),
                "slope_current_fraction": current,
                "slope_elapsed_minutes": elapsed,
            }
        )
        if scenario.get("slope_first_positive_minute") is None and current > 0.0:
            scenario["slope_first_positive_minute"] = elapsed
        if scenario.get("slope_first_one_percent_mfe_minute") is None and max(prior_mfe, favourable) >= 0.01:
            scenario["slope_first_one_percent_mfe_minute"] = elapsed
        self.diagnostics["slope_path_updates"] += 1

    def _roi_threshold(self, held_minutes: int) -> float:
        leverage = max(float(self.config.slope_source_leverage), 1e-12)
        if held_minutes >= 961:
            source = float(self.config.slope_roi_961_profit_ratio)
        elif held_minutes >= 548:
            source = float(self.config.slope_roi_548_profit_ratio)
        elif held_minutes >= 132:
            source = float(self.config.slope_roi_132_profit_ratio)
        else:
            source = float(self.config.slope_roi_0_profit_ratio)
        return source / leverage

    def _exit_snapshot(self) -> dict[str, float] | None:
        if self.current_symbol is None:
            return None
        candles = self._hourly_candles(self.current_symbol)
        period = int(self.config.slope_exit_range_period)
        if len(candles) < max(int(self.config.slope_slow_period), period + 1) + 2:
            return None
        closes = [float(candle.close) for candle in candles]
        fast = _ta._sma(closes, int(self.config.slope_fast_period))[-1]
        slow = _ta._sma(closes, int(self.config.slope_slow_period))[-1]
        prior = candles[-period - 1:-1]
        if not all(math.isfinite(value) for value in (fast, slow)) or len(prior) != period:
            return None
        return {
            "close": closes[-1],
            "fast_sma": float(fast),
            "slow_sma": float(slow),
            "prior_low": min(float(candle.low) for candle in prior),
            "prior_high": max(float(candle.high) for candle in prior),
        }

    def _source_exit(self) -> tuple[bool, dict[str, float | str]]:
        scenario = self.current_scenario
        if scenario is None:
            return False, {}
        mode = str(self.config.slope_exit_mode).strip().lower()
        if mode == "no_signal":
            return False, {}
        snapshot = self._exit_snapshot()
        if snapshot is None:
            return False, {}
        side = int(scenario.get("side", 0))
        ma_failure = (
            snapshot["fast_sma"] < snapshot["slow_sma"]
            if side > 0 else snapshot["fast_sma"] > snapshot["slow_sma"]
        )
        if mode == "ma_only":
            exit_now = ma_failure
            range_failure = False
        elif mode == "source_exact":
            range_failure = (
                snapshot["close"] < snapshot["prior_low"]
                if side > 0 else snapshot["close"] > snapshot["prior_low"]
            )
            exit_now = ma_failure or range_failure
        elif mode == "corrected_symmetric":
            range_failure = (
                snapshot["close"] < snapshot["prior_low"]
                if side > 0 else snapshot["close"] > snapshot["prior_high"]
            )
            exit_now = ma_failure or range_failure
        else:
            raise ValueError(f"unsupported slope_exit_mode={mode!r}")
        return exit_now, {
            "mode": mode,
            "ma_failure": int(ma_failure),
            "range_failure": int(range_failure),
            **snapshot,
        }

    def _submit_exit(self, ts_event: int, reason: str, **details: Any) -> None:
        if self.current_symbol is None or self._exit_pending:
            return
        if self.current_scenario is not None:
            self.current_scenario["slope_exit_driver"] = reason
            self.current_scenario["slope_exit_details"] = details
        counts = self.diagnostics["slope_exit_counts"]
        counts[reason] = int(counts.get(reason, 0)) + 1
        self._exit_pending = True
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event("SLOPE_EXIT", ts_event, reason=reason, **details)

    def _manage_open_position(self, ts_event: int) -> None:
        if self._exit_pending:
            return
        self._update_path()
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is None or symbol is None or not self.bars[symbol]:
            return
        entry = float(scenario.get("actual_entry_fill") or scenario.get("entry_reference") or math.nan)
        side = int(scenario.get("side", 0))
        if not math.isfinite(entry) or entry <= 0.0 or side not in (-1, 1):
            super()._manage_open_position(ts_event)
            return
        bar = self.bars[symbol][-1]
        leverage = max(float(self.config.slope_source_leverage), 1e-12)
        activation = float(self.config.slope_trailing_offset_profit_ratio) / leverage
        distance = float(self.config.slope_trailing_positive_profit_ratio) / leverage

        if self._trail_active and math.isfinite(self._trail_best):
            trail = self._trail_best * (1.0 - distance) if side > 0 else self._trail_best * (1.0 + distance)
            crossed = float(bar.low) <= trail if side > 0 else float(bar.high) >= trail
            if crossed:
                self.diagnostics["slope_trailing_exits"] += 1
                self._submit_exit(ts_event, "PUBLIC_TRAILING_EXIT", trail_level=trail)
                return

        favourable_price = float(bar.high) if side > 0 else float(bar.low)
        favourable_return = side * (favourable_price - entry) / entry
        if not self._trail_active and favourable_return >= activation:
            self._trail_active = True
            self._trail_best = favourable_price
            self.diagnostics["slope_trailing_activations"] += 1
            scenario["slope_trail_activation_minutes"] = max(
                0, self.minute_index - self.position_open_minute
            )
        elif self._trail_active:
            self._trail_best = (
                max(self._trail_best, favourable_price)
                if side > 0 else min(self._trail_best, favourable_price)
            )

        held = max(0, self.minute_index - self.position_open_minute)
        current_return = side * (float(bar.close) - entry) / entry
        roi = self._roi_threshold(held)
        roi_reached = (
            held >= 961 and current_return >= 0.0
        ) or (
            held < 961 and roi > 0.0 and favourable_return >= roi
        )
        if roi_reached:
            self.diagnostics["slope_roi_exits"] += 1
            self._submit_exit(
                ts_event,
                "PUBLIC_ROI_EXIT",
                held_minutes=held,
                threshold_fraction=roi,
                favourable_return_fraction=favourable_return,
                current_return_fraction=current_return,
            )
            return

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute == 59:
            exit_now, details = self._source_exit()
            if exit_now:
                self.diagnostics["slope_source_exit_signals"] += 1
                self._submit_exit(ts_event, "PUBLIC_SOURCE_EXIT_SIGNAL", **details)
                return

        before_events = len(self.events)
        super()._manage_open_position(ts_event)
        if scenario.get("slope_exit_driver") is None and any(
            item.get("event_type") == "FORCED_DAYTRADE_EXIT"
            for item in self.events[before_events:]
        ):
            scenario["slope_exit_driver"] = "FORCED_DAYTRADE_EXIT"
            self._exit_pending = True


__all__ = ["Candidate35Config", "Candidate35Strategy"]
