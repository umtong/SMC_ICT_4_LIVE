"""NautilusTrader execution of the public EDTMA long/short system.

The adapter preserves the public entry, leverage-normalized stop, trailing and
ROI logic while making the source's unusual chandelier exits separately
switchable.  A structural-stop experiment is exposed as a distinct mechanism;
it does not silently change the source control.
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
    edtma_bucket_minutes: int = 60
    edtma_episode_mode: str = "condition_reentry"
    edtma_adx_period: int = 14
    edtma_volume_period: int = 22
    edtma_long_adx_min: float = 35.0
    edtma_long_tema_period: int = 7
    edtma_long_dema_period: int = 45
    edtma_long_ema_period: int = 177
    edtma_short_adx_min: float = 26.0
    edtma_short_tema_period: int = 19
    edtma_short_dema_period: int = 53
    edtma_short_ema_period: int = 102
    edtma_source_leverage: float = 3.0
    edtma_source_stoploss_profit_ratio: float = 0.12
    edtma_remote_target_fraction: float = 0.10

    # source_exact | rolling_chandelier | no_signal
    edtma_exit_mode: str = "source_exact"
    # source | signal_extreme_atr
    edtma_stop_mode: str = "source"
    edtma_stop_atr_period: int = 14
    edtma_stop_atr_buffer: float = 0.25

    edtma_trailing_positive_profit_ratio: float = 0.01
    edtma_trailing_offset_profit_ratio: float = 0.02
    edtma_roi_0_profit_ratio: float = 0.238
    edtma_roi_362_profit_ratio: float = 0.148
    edtma_roi_881_profit_ratio: float = 0.066
    edtma_roi_1039_profit_ratio: float = 0.0
    edtma_long_chandelier_period: int = 23
    edtma_long_chandelier_multiple: float = 1.0
    edtma_short_chandelier_period: int = 26
    edtma_short_chandelier_multiple: float = 6.0


class Candidate35Strategy(_base.Candidate35Strategy):
    """One global-slot four-symbol EDTMA account."""

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
            edtma_bucket_minutes=config.edtma_bucket_minutes,
            edtma_episode_mode=config.edtma_episode_mode,
            edtma_adx_period=config.edtma_adx_period,
            edtma_volume_period=config.edtma_volume_period,
            edtma_long_adx_min=config.edtma_long_adx_min,
            edtma_long_tema_period=config.edtma_long_tema_period,
            edtma_long_dema_period=config.edtma_long_dema_period,
            edtma_long_ema_period=config.edtma_long_ema_period,
            edtma_short_adx_min=config.edtma_short_adx_min,
            edtma_short_tema_period=config.edtma_short_tema_period,
            edtma_short_dema_period=config.edtma_short_dema_period,
            edtma_short_ema_period=config.edtma_short_ema_period,
            edtma_source_leverage=config.edtma_source_leverage,
            edtma_source_stoploss_profit_ratio=config.edtma_source_stoploss_profit_ratio,
            edtma_remote_target_fraction=config.edtma_remote_target_fraction,
        )
        # EMA(177), TEMA/DEMA cascades and source-warm periods require much more
        # than the generic 2,000 one-minute observations.
        self.bars = {symbol: deque(maxlen=30_000) for symbol in SYMBOLS}
        self._exit_pending = False
        self._entry_fill = math.nan
        self._trail_active = False
        self._trail_best = math.nan
        self.diagnostics.update(
            {
                "edtma_hourly_decisions": 0,
                "edtma_source_conditions": 0,
                "edtma_entry_candidates": 0,
                "edtma_trailing_activations": 0,
                "edtma_trailing_exits": 0,
                "edtma_roi_exits": 0,
                "edtma_source_exit_signals": 0,
                "edtma_rolling_chandelier_exits": 0,
                "edtma_structural_stop_submissions": 0,
                "edtma_exit_counts": {},
                "unresolved_reason_counts": {},
                "edtma_episode_mode": str(config.edtma_episode_mode),
                "edtma_exit_mode": str(config.edtma_exit_mode),
                "edtma_stop_mode": str(config.edtma_stop_mode),
            }
        )

    def _reset_policy_state(self) -> None:
        self._exit_pending = False
        self._entry_fill = math.nan
        self._trail_active = False
        self._trail_best = math.nan

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._reset_policy_state()

    def _after_position_opened(self, event: Any, scenario: dict[str, Any]) -> None:
        del event
        raw = scenario.get("actual_entry_fill", scenario.get("entry_reference"))
        try:
            fill = float(raw)
        except (TypeError, ValueError):
            fill = math.nan
        self._entry_fill = fill if math.isfinite(fill) and fill > 0.0 else math.nan
        self._exit_pending = False
        self._trail_active = False
        self._trail_best = self._entry_fill
        scenario.update(
            {
                "edtma_mfe_fraction": 0.0,
                "edtma_mae_fraction": 0.0,
                "edtma_current_fraction": 0.0,
                "edtma_elapsed_minutes": 0,
                "edtma_trail_activation_minutes": None,
                "edtma_exit_driver": None,
            }
        )

    def _after_position_closed(self, event: Any, record: dict[str, Any]) -> None:
        del event, record
        self._reset_policy_state()

    def _hourly_candles(self, symbol: str):
        return _ta._aggregate_complete(
            tuple(self.bars[symbol]),
            int(self.route_config.edtma_bucket_minutes),
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
                self._event("ENTRY_EXPIRED", ts_event, reason="EDTMA_MARKET_PARENT_NOT_FILLED")
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

        self.diagnostics["edtma_hourly_decisions"] += 1
        features = {
            symbol: _router.FeatureObservation(
                observed_time_ns=int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        winner, decisions = _router.route_universe(
            bars_by_symbol={symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
            features_by_symbol=features,
            config=self.route_config,
        )
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if decision.actionable:
                self.diagnostics["edtma_source_conditions"] += 1
            else:
                reason = decision.reasons[0] if decision.reasons else "UNKNOWN"
                reasons = self.diagnostics["unresolved_reason_counts"]
                reasons[reason] = int(reasons.get(reason, 0)) + 1
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        self.diagnostics["edtma_entry_candidates"] += 1
        self._submit_decision(winner, ts_event)

    def _repaired_stop(self, decision) -> tuple[float, dict[str, float | str]] | None:
        mode = str(self.config.edtma_stop_mode).strip().lower()
        if mode == "source":
            return float(decision.stop_reference), {
                "edtma_stop_mode": mode,
                "edtma_source_stop": float(decision.stop_reference),
                "edtma_repaired_stop": float(decision.stop_reference),
            }
        if mode != "signal_extreme_atr":
            raise ValueError(f"unsupported edtma_stop_mode={mode!r}")
        candles = self._hourly_candles(decision.symbol)
        period = int(self.config.edtma_stop_atr_period)
        if len(candles) < period + 2:
            return None
        atr = float(_ta._atr(candles, period)[-1])
        if not math.isfinite(atr) or atr <= 0.0:
            return None
        signal = candles[-1]
        side = int(decision.side)
        diagnostics = dict(decision.diagnostics)
        trend_anchor = float(
            diagnostics["long_dema"] if side > 0 else diagnostics["short_dema"]
        )
        buffer = max(0.0, float(self.config.edtma_stop_atr_buffer)) * atr
        anchor = min(float(signal.low), trend_anchor) if side > 0 else max(float(signal.high), trend_anchor)
        stop = anchor - buffer if side > 0 else anchor + buffer
        if not math.isfinite(stop) or stop <= 0.0:
            return None
        return stop, {
            "edtma_stop_mode": mode,
            "edtma_source_stop": float(decision.stop_reference),
            "edtma_repaired_stop": stop,
            "edtma_stop_atr": atr,
            "edtma_stop_buffer": buffer,
            "edtma_signal_high": float(signal.high),
            "edtma_signal_low": float(signal.low),
            "edtma_trend_anchor": trend_anchor,
        }

    def _submit_decision(self, decision, ts_event: int) -> None:
        repaired = self._repaired_stop(decision)
        if repaired is None:
            self._event("EDTMA_STOP_UNAVAILABLE", ts_event, symbol=decision.symbol)
            return
        stop, details = repaired
        before = int(self.diagnostics["entry_submissions"])
        super()._submit_decision(replace(decision, stop_reference=stop), ts_event)
        if int(self.diagnostics["entry_submissions"]) <= before or self.current_scenario is None:
            return
        if str(self.config.edtma_stop_mode).strip().lower() != "source":
            self.diagnostics["edtma_structural_stop_submissions"] += 1
        self.current_scenario.update(
            {
                "edtma_episode_mode": str(self.config.edtma_episode_mode),
                "edtma_exit_mode": str(self.config.edtma_exit_mode),
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
        scenario["edtma_mfe_fraction"] = max(
            float(scenario.get("edtma_mfe_fraction") or 0.0), favourable
        )
        scenario["edtma_mae_fraction"] = min(
            float(scenario.get("edtma_mae_fraction") or 0.0), adverse
        )
        scenario["edtma_current_fraction"] = current
        scenario["edtma_elapsed_minutes"] = max(
            0, self.minute_index - self.position_open_minute
        )

    def _submit_exit(self, ts_event: int, reason: str, **details: Any) -> None:
        if self.current_symbol is None or self._exit_pending:
            return
        if self.current_scenario is not None:
            self.current_scenario["edtma_exit_driver"] = reason
            self.current_scenario["edtma_exit_details"] = details
        counts = self.diagnostics["edtma_exit_counts"]
        counts[reason] = int(counts.get(reason, 0)) + 1
        self._exit_pending = True
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event("EDTMA_EXIT", ts_event, reason=reason, **details)

    def _roi_threshold(self, held_minutes: int) -> float:
        leverage = max(float(self.config.edtma_source_leverage), 1e-12)
        if held_minutes >= 1039:
            source = float(self.config.edtma_roi_1039_profit_ratio)
        elif held_minutes >= 881:
            source = float(self.config.edtma_roi_881_profit_ratio)
        elif held_minutes >= 362:
            source = float(self.config.edtma_roi_362_profit_ratio)
        else:
            source = float(self.config.edtma_roi_0_profit_ratio)
        return source / leverage

    def _source_exit(self) -> tuple[bool, dict[str, float | str]]:
        if self.current_symbol is None or self.current_scenario is None:
            return False, {}
        mode = str(self.config.edtma_exit_mode).strip().lower()
        if mode == "no_signal":
            return False, {}
        candles = self._hourly_candles(self.current_symbol)
        if not candles:
            return False, {}
        side = int(self.current_scenario.get("side", 0))
        candle = candles[-1]
        if mode == "source_exact":
            if side > 0:
                period = int(self.config.edtma_long_chandelier_period)
                if len(candles) < period:
                    return False, {}
                atr = sum(float(item.high) - float(item.low) for item in candles[-period:]) / period
                level = float(candle.high) - atr * float(self.config.edtma_long_chandelier_multiple)
                return float(candle.low) <= level, {
                    "mode": mode, "level": level, "source_range_atr": atr,
                }
            period = int(self.config.edtma_short_chandelier_period)
            if len(candles) < period:
                return False, {}
            atr = sum(float(item.high) - float(item.low) for item in candles[-period:]) / period
            level = float(candle.close) + atr * float(self.config.edtma_short_chandelier_multiple)
            return float(candle.high) >= level, {
                "mode": mode, "level": level, "source_range_atr": atr,
            }
        if mode == "rolling_chandelier":
            if side > 0:
                period = int(self.config.edtma_long_chandelier_period)
                multiple = float(self.config.edtma_long_chandelier_multiple)
                if len(candles) < period + 1:
                    return False, {}
                atr = float(_ta._atr(candles, period)[-1])
                if not math.isfinite(atr) or atr <= 0.0:
                    return False, {}
                level = max(float(item.high) for item in candles[-period:]) - atr * multiple
                return float(candle.low) <= level, {
                    "mode": mode, "level": level, "true_atr": atr,
                }
            period = int(self.config.edtma_short_chandelier_period)
            multiple = float(self.config.edtma_short_chandelier_multiple)
            if len(candles) < period + 1:
                return False, {}
            atr = float(_ta._atr(candles, period)[-1])
            if not math.isfinite(atr) or atr <= 0.0:
                return False, {}
            level = min(float(item.low) for item in candles[-period:]) + atr * multiple
            return float(candle.high) >= level, {
                "mode": mode, "level": level, "true_atr": atr,
            }
        raise ValueError(f"unsupported edtma_exit_mode={mode!r}")

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
        leverage = max(float(self.config.edtma_source_leverage), 1e-12)
        activation = float(self.config.edtma_trailing_offset_profit_ratio) / leverage
        distance = float(self.config.edtma_trailing_positive_profit_ratio) / leverage

        # Check the previously established trail before incorporating this
        # minute's new extreme. This avoids assuming the high/low order inside a
        # one-minute bar.
        if self._trail_active and math.isfinite(self._trail_best):
            trail = self._trail_best * (1.0 - distance) if side > 0 else self._trail_best * (1.0 + distance)
            crossed = float(bar.low) <= trail if side > 0 else float(bar.high) >= trail
            if crossed:
                self.diagnostics["edtma_trailing_exits"] += 1
                self._submit_exit(ts_event, "PUBLIC_TRAILING_EXIT", trail_level=trail)
                return

        favourable_extreme = float(bar.high) if side > 0 else float(bar.low)
        favourable_return = side * (favourable_extreme - entry) / entry
        if not self._trail_active and favourable_return >= activation:
            self._trail_active = True
            self._trail_best = favourable_extreme
            self.diagnostics["edtma_trailing_activations"] += 1
            scenario["edtma_trail_activation_minutes"] = max(
                0, self.minute_index - self.position_open_minute
            )
        elif self._trail_active:
            if side > 0:
                self._trail_best = max(self._trail_best, favourable_extreme)
            else:
                self._trail_best = min(self._trail_best, favourable_extreme)

        held = max(0, self.minute_index - self.position_open_minute)
        roi = self._roi_threshold(held)
        current_return = side * (float(bar.close) - entry) / entry
        roi_reached = (
            held >= 1039 and current_return >= 0.0
        ) or (
            held < 1039 and roi > 0.0 and favourable_return >= roi
        )
        if roi_reached:
            self.diagnostics["edtma_roi_exits"] += 1
            self._submit_exit(
                ts_event,
                "PUBLIC_ROI_EXIT",
                threshold_fraction=roi,
                held_minutes=held,
                current_return_fraction=current_return,
                favourable_return_fraction=favourable_return,
            )
            return

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute == 59:
            exit_now, details = self._source_exit()
            if exit_now:
                mode = str(self.config.edtma_exit_mode).strip().lower()
                if mode == "source_exact":
                    self.diagnostics["edtma_source_exit_signals"] += 1
                    reason = "PUBLIC_SOURCE_CHANDELIER_EXIT"
                else:
                    self.diagnostics["edtma_rolling_chandelier_exits"] += 1
                    reason = "ROLLING_CHANDELIER_EXIT"
                self._submit_exit(ts_event, reason, **details)
                return

        before_events = len(self.events)
        super()._manage_open_position(ts_event)
        if scenario.get("edtma_exit_driver") is None:
            new_events = self.events[before_events:]
            if any(item.get("event_type") == "FORCED_DAYTRADE_EXIT" for item in new_events):
                scenario["edtma_exit_driver"] = "FORCED_DAYTRADE_EXIT"
                self._exit_pending = True


__all__ = ["Candidate35Config", "Candidate35Strategy"]
