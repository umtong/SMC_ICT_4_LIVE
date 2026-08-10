"""One-account Nautilus execution for the public ``ichiV2_1`` family.

The source long-only control, its accidental EMA-period semantics, ROI schedule,
EMA-cross exit and 5% stop are preserved.  Reciprocal shorts, alignment
ablations, structural risk and alternative lifecycle exits are separately named
mechanisms so their contributions can be measured rather than blended into a
single pass/fail result.
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
    ichiv21_bucket_minutes: int = 5
    ichiv21_episode_mode: str = "condition_reentry"
    ichiv21_direction_mode: str = "long_only"
    ichiv21_alignment_mode: str = "all8"
    ichiv21_fan_gain_min: float = 1.002
    ichiv21_fan_shift_count: int = 3
    ichiv21_stop_fraction: float = 0.05
    ichiv21_remote_target_fraction: float = 0.05

    # source | signal_extreme_ema120_atr
    ichiv21_stop_mode: str = "source"
    ichiv21_stop_atr_period: int = 14
    ichiv21_stop_atr_buffer: float = 0.25

    # source | no_signal | fan_lifecycle | roi_progress | lifecycle_progress
    ichiv21_management_mode: str = "source"
    ichiv21_roi_0: float = 0.05
    ichiv21_roi_10: float = 0.03
    ichiv21_roi_41: float = 0.01
    ichiv21_roi_114: float = 0.0


class Candidate35Strategy(_base.Candidate35Strategy):
    """One global slot across BTC, ETH, SOL and XRP."""

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
            ichiv21_bucket_minutes=config.ichiv21_bucket_minutes,
            ichiv21_episode_mode=config.ichiv21_episode_mode,
            ichiv21_direction_mode=config.ichiv21_direction_mode,
            ichiv21_alignment_mode=config.ichiv21_alignment_mode,
            ichiv21_fan_gain_min=config.ichiv21_fan_gain_min,
            ichiv21_fan_shift_count=config.ichiv21_fan_shift_count,
            ichiv21_stop_fraction=config.ichiv21_stop_fraction,
            ichiv21_remote_target_fraction=config.ichiv21_remote_target_fraction,
        )
        # Source EMA(480) means 480 five-minute candles. Keep enough causal
        # minute history for warm-up and management without retaining a dataset.
        self.bars = {symbol: deque(maxlen=12_000) for symbol in SYMBOLS}
        self._exit_pending = False
        self.diagnostics.update(
            {
                "ichiv21_five_minute_decisions": 0,
                "ichiv21_source_entry_states": 0,
                "ichiv21_entry_candidates": 0,
                "ichiv21_source_exit_signals": 0,
                "ichiv21_fan_lifecycle_exits": 0,
                "ichiv21_roi_progress_exits": 0,
                "ichiv21_roi_exits": 0,
                "ichiv21_structural_stop_submissions": 0,
                "ichiv21_path_updates": 0,
                "ichiv21_exit_counts": {},
                "ichiv21_episode_mode": str(config.ichiv21_episode_mode),
                "ichiv21_direction_mode": str(config.ichiv21_direction_mode),
                "ichiv21_alignment_mode": str(config.ichiv21_alignment_mode),
                "ichiv21_stop_mode": str(config.ichiv21_stop_mode),
                "ichiv21_management_mode": str(config.ichiv21_management_mode),
                "unresolved_reason_counts": {},
            }
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._exit_pending = False

    def _after_position_opened(self, event: Any, scenario: dict[str, Any]) -> None:
        del event
        self._exit_pending = False
        scenario.update(
            {
                "ichiv21_mfe_fraction": 0.0,
                "ichiv21_mae_fraction": 0.0,
                "ichiv21_current_fraction": 0.0,
                "ichiv21_elapsed_minutes": 0,
                "ichiv21_exit_driver": None,
                "ichiv21_first_positive_minute": None,
                "ichiv21_first_one_percent_mfe_minute": None,
            }
        )

    def _after_position_closed(self, event: Any, record: dict[str, Any]) -> None:
        del event, record
        self._exit_pending = False

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
                self._event("ENTRY_EXPIRED", ts_event, reason="ICHIV21_PARENT_NOT_FILLED")
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
        if any(len(self.bars[symbol]) < 2_500 for symbol in SYMBOLS):
            return

        self.diagnostics["ichiv21_five_minute_decisions"] += 1
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
                self.diagnostics["ichiv21_source_entry_states"] += 1
            else:
                reason = decision.reasons[0] if decision.reasons else "UNKNOWN"
                reasons = self.diagnostics["unresolved_reason_counts"]
                reasons[reason] = int(reasons.get(reason, 0)) + 1
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        self.diagnostics["ichiv21_entry_candidates"] += 1
        self._submit_decision(winner, ts_event)

    def _five_minute_candles(self, symbol: str):
        return _router.aggregate_five_minute(tuple(self.bars[symbol]))

    def _repaired_stop(self, decision) -> tuple[float, dict[str, float | str]] | None:
        mode = str(self.config.ichiv21_stop_mode).strip().lower()
        source_stop = float(decision.stop_reference)
        if mode == "source":
            return source_stop, {
                "ichiv21_stop_mode": mode,
                "ichiv21_source_stop": source_stop,
                "ichiv21_repaired_stop": source_stop,
            }
        if mode != "signal_extreme_ema120_atr":
            raise ValueError(f"unsupported ichiv21_stop_mode={mode!r}")
        candles = self._five_minute_candles(decision.symbol)
        period = int(self.config.ichiv21_stop_atr_period)
        if len(candles) < period + 2:
            return None
        atr = float(_ta._atr(candles, period)[-1])
        diagnostics = dict(decision.diagnostics)
        try:
            ema_anchor = float(diagnostics["ema120_anchor_original_price"])
            signal_low = float(diagnostics["signal_low_original"])
            signal_high = float(diagnostics["signal_high_original"])
        except (KeyError, TypeError, ValueError):
            return None
        if not all(math.isfinite(value) for value in (atr, ema_anchor, signal_low, signal_high)) or atr <= 0.0:
            return None
        buffer = max(0.0, float(self.config.ichiv21_stop_atr_buffer)) * atr
        if int(decision.side) > 0:
            structural = min(signal_low, ema_anchor) - buffer
            stop = max(source_stop, structural)
        else:
            structural = max(signal_high, ema_anchor) + buffer
            stop = min(source_stop, structural)
        if not math.isfinite(stop) or stop <= 0.0:
            return None
        return stop, {
            "ichiv21_stop_mode": mode,
            "ichiv21_source_stop": source_stop,
            "ichiv21_structural_stop_raw": structural,
            "ichiv21_repaired_stop": stop,
            "ichiv21_stop_atr": atr,
            "ichiv21_stop_buffer": buffer,
            "ichiv21_ema120_anchor_original_price": ema_anchor,
            "ichiv21_signal_low": signal_low,
            "ichiv21_signal_high": signal_high,
        }

    def _submit_decision(self, decision, ts_event: int) -> None:
        repaired = self._repaired_stop(decision)
        if repaired is None:
            self._event("ICHIV21_STOP_UNAVAILABLE", ts_event, symbol=decision.symbol)
            return
        stop, details = repaired
        before = int(self.diagnostics["entry_submissions"])
        super()._submit_decision(replace(decision, stop_reference=stop), ts_event)
        if int(self.diagnostics["entry_submissions"]) <= before or self.current_scenario is None:
            return
        if str(self.config.ichiv21_stop_mode).strip().lower() != "source":
            self.diagnostics["ichiv21_structural_stop_submissions"] += 1
        self.current_scenario.update(
            {
                "ichiv21_management_mode": str(self.config.ichiv21_management_mode),
                "ichiv21_direction_mode": str(self.config.ichiv21_direction_mode),
                "ichiv21_alignment_mode": str(self.config.ichiv21_alignment_mode),
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
        prior_mfe = float(scenario.get("ichiv21_mfe_fraction") or 0.0)
        prior_mae = float(scenario.get("ichiv21_mae_fraction") or 0.0)
        scenario.update(
            {
                "ichiv21_mfe_fraction": max(prior_mfe, favourable),
                "ichiv21_mae_fraction": min(prior_mae, adverse),
                "ichiv21_current_fraction": current,
                "ichiv21_elapsed_minutes": elapsed,
            }
        )
        if scenario.get("ichiv21_first_positive_minute") is None and current > 0.0:
            scenario["ichiv21_first_positive_minute"] = elapsed
        if scenario.get("ichiv21_first_one_percent_mfe_minute") is None and max(prior_mfe, favourable) >= 0.01:
            scenario["ichiv21_first_one_percent_mfe_minute"] = elapsed
        self.diagnostics["ichiv21_path_updates"] += 1

    def _position_state(self) -> _router.IchiV21State | None:
        if self.current_symbol is None or self.current_scenario is None:
            return None
        candles = self._five_minute_candles(self.current_symbol)
        if not candles:
            return None
        side = int(self.current_scenario.get("side", 0))
        states = _router.ichiv21_states(
            candles,
            self.route_config,
            reciprocal=side < 0,
        )
        return states[-1] if states and states[-1].ready else None

    def _roi_threshold(self, held_minutes: int) -> float:
        if held_minutes >= 114:
            return float(self.config.ichiv21_roi_114)
        if held_minutes >= 41:
            return float(self.config.ichiv21_roi_41)
        if held_minutes >= 10:
            return float(self.config.ichiv21_roi_10)
        return float(self.config.ichiv21_roi_0)

    def _submit_exit(self, ts_event: int, reason: str, **details: Any) -> None:
        if self.current_symbol is None or self._exit_pending:
            return
        if self.current_scenario is not None:
            self.current_scenario["ichiv21_exit_driver"] = reason
            self.current_scenario["ichiv21_exit_details"] = details
        counts = self.diagnostics["ichiv21_exit_counts"]
        counts[reason] = int(counts.get(reason, 0)) + 1
        self._exit_pending = True
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event("ICHIV21_EXIT", ts_event, reason=reason, **details)

    def _fan_failure(self, state: _router.IchiV21State, current_return: float) -> bool:
        return (
            state.fan_magnitude <= 1.0
            or (
                state.trend_close_5 < state.trend_close_30
                and current_return <= 0.0
            )
        )

    def _progress_failure(self, held: int, mfe: float, current: float) -> str | None:
        # Reuse the source ROI clock itself: after the strategy says 1% is enough,
        # never reaching 1% while underwater is a failed opportunity; after the
        # zero-ROI time, remaining underwater means the ROI engine cannot close.
        if held >= 114 and current < 0.0:
            return "SOURCE_ZERO_ROI_REACHED_WHILE_UNDERWATER"
        if held >= 41 and mfe < float(self.config.ichiv21_roi_41) and current < 0.0:
            return "SOURCE_ONE_PERCENT_ROI_NEVER_REACHED"
        return None

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

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 == 4:
            bar = self.bars[symbol][-1]
            held = max(0, self.minute_index - self.position_open_minute)
            favourable_price = float(bar.high) if side > 0 else float(bar.low)
            favourable_return = side * (favourable_price - entry) / entry
            current_return = side * (float(bar.close) - entry) / entry
            roi = self._roi_threshold(held)
            roi_reached = (
                held >= 114 and current_return >= 0.0
            ) or (
                held < 114 and roi > 0.0 and favourable_return >= roi
            )
            if roi_reached:
                self.diagnostics["ichiv21_roi_exits"] += 1
                self._submit_exit(
                    ts_event,
                    "PUBLIC_ROI_EXIT",
                    held_minutes=held,
                    threshold_fraction=roi,
                    favourable_return_fraction=favourable_return,
                    current_return_fraction=current_return,
                )
                return

            state = self._position_state()
            mode = str(self.config.ichiv21_management_mode).strip().lower()
            if mode == "source":
                if state is not None and state.exit_cross_down:
                    self.diagnostics["ichiv21_source_exit_signals"] += 1
                    self._submit_exit(
                        ts_event,
                        "PUBLIC_EMA5_BELOW_EMA120_EXIT",
                        fan_magnitude=float(state.fan_magnitude),
                        trend_close_5=float(state.trend_close_5),
                        trend_close_120=float(state.trend_close_120),
                        current_return_fraction=current_return,
                    )
                    return
            elif mode == "no_signal":
                pass
            elif mode in {"fan_lifecycle", "lifecycle_progress"}:
                if state is not None and self._fan_failure(state, current_return):
                    self.diagnostics["ichiv21_fan_lifecycle_exits"] += 1
                    self._submit_exit(
                        ts_event,
                        "FAN_THESIS_FAILURE",
                        fan_magnitude=float(state.fan_magnitude),
                        trend_close_5=float(state.trend_close_5),
                        trend_close_30=float(state.trend_close_30),
                        current_return_fraction=current_return,
                    )
                    return
                if mode == "lifecycle_progress":
                    reason = self._progress_failure(
                        held,
                        float(scenario.get("ichiv21_mfe_fraction") or 0.0),
                        current_return,
                    )
                    if reason is not None:
                        self.diagnostics["ichiv21_roi_progress_exits"] += 1
                        self._submit_exit(
                            ts_event,
                            reason,
                            held_minutes=held,
                            mfe_fraction=float(scenario.get("ichiv21_mfe_fraction") or 0.0),
                            current_return_fraction=current_return,
                        )
                        return
            elif mode == "roi_progress":
                reason = self._progress_failure(
                    held,
                    float(scenario.get("ichiv21_mfe_fraction") or 0.0),
                    current_return,
                )
                if reason is not None:
                    self.diagnostics["ichiv21_roi_progress_exits"] += 1
                    self._submit_exit(
                        ts_event,
                        reason,
                        held_minutes=held,
                        mfe_fraction=float(scenario.get("ichiv21_mfe_fraction") or 0.0),
                        current_return_fraction=current_return,
                    )
                    return
            else:
                raise ValueError(f"unsupported ichiv21_management_mode={mode!r}")

        before_events = len(self.events)
        super()._manage_open_position(ts_event)
        if scenario.get("ichiv21_exit_driver") is None:
            if any(
                item.get("event_type") == "FORCED_DAYTRADE_EXIT"
                for item in self.events[before_events:]
            ):
                scenario["ichiv21_exit_driver"] = "FORCED_DAYTRADE_EXIT"
                self._exit_pending = True


__all__ = ["Candidate35Config", "Candidate35Strategy"]
