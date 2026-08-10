"""NautilusTrader execution for the causal NOTank extrema family.

The source's retrospective extrema labels are not used at impossible timestamps.
This strategy evaluates confirmed pivots or current rolling rejections across the
four project instruments, preserves one global slot and the 3%-NAV planned-loss
budget, and separates entry timing from exit/management behavior.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import math
from typing import Any

import router as _router
import router_picasso as _ta
import strategy_base as _base

SYMBOLS = _base.SYMBOLS
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


class Candidate35Config(_base.Candidate35Config, frozen=True):
    notank_bucket_minutes: int = 15
    notank_entry_mode: str = "confirmed_pivot"
    notank_direction_mode: str = "long_only"
    notank_episode_mode: str = "rising_edge"
    notank_pivot_order: int = 5
    notank_rsi_period: int = 14
    notank_long_rsi_max: float = 30.0
    notank_short_rsi_min: float = 70.0
    notank_stop_atr_buffer: float = 0.25
    notank_max_confirmation_atr: float = 2.5
    notank_min_reclaim_fraction: float = 0.0
    notank_target_r: float = 2.0
    notank_rolling_window: int = 11
    notank_min_wick_fraction: float = 0.25

    # r_target | opposite_confirmed | r_trail | opposite_or_trail | progress
    notank_management_mode: str = "r_target"
    notank_trail_activation_r: float = 1.0
    notank_trail_distance_r: float = 0.50
    notank_progress_minutes: int = 180
    notank_progress_mfe_r: float = 0.50


class Candidate35Strategy(_base.Candidate35Strategy):
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
            notank_bucket_minutes=config.notank_bucket_minutes,
            notank_entry_mode=config.notank_entry_mode,
            notank_direction_mode=config.notank_direction_mode,
            notank_pivot_order=config.notank_pivot_order,
            notank_rsi_period=config.notank_rsi_period,
            notank_long_rsi_max=config.notank_long_rsi_max,
            notank_short_rsi_min=config.notank_short_rsi_min,
            notank_stop_atr_buffer=config.notank_stop_atr_buffer,
            notank_max_confirmation_atr=config.notank_max_confirmation_atr,
            notank_min_reclaim_fraction=config.notank_min_reclaim_fraction,
            notank_target_r=config.notank_target_r,
            notank_rolling_window=config.notank_rolling_window,
            notank_min_wick_fraction=config.notank_min_wick_fraction,
        )
        self.bars = {symbol: deque(maxlen=20_000) for symbol in SYMBOLS}
        self._exit_pending = False
        self._trail_active = False
        self._trail_best = math.nan
        self._prior_actionable: dict[str, int] = {symbol: 0 for symbol in SYMBOLS}
        self._consumed_episodes: set[tuple[str, int, int]] = set()
        self.diagnostics.update(
            {
                "notank_decisions": 0,
                "notank_source_candidates": 0,
                "notank_entry_candidates": 0,
                "notank_duplicate_episode_rejections": 0,
                "notank_contiguous_signal_rejections": 0,
                "notank_trailing_activations": 0,
                "notank_trailing_exits": 0,
                "notank_opposite_pivot_exits": 0,
                "notank_progress_exits": 0,
                "notank_path_updates": 0,
                "notank_exit_counts": {},
                "notank_entry_mode": str(config.notank_entry_mode),
                "notank_direction_mode": str(config.notank_direction_mode),
                "notank_episode_mode": str(config.notank_episode_mode),
                "notank_management_mode": str(config.notank_management_mode),
                "unresolved_reason_counts": {},
                "notank_decision_trace": [],
            }
        )

    def _reset_policy_state(self) -> None:
        self._exit_pending = False
        self._trail_active = False
        self._trail_best = math.nan

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._reset_policy_state()

    def on_position_opened(self, event: Any) -> None:
        super().on_position_opened(event)
        self._exit_pending = False
        self._trail_active = False
        scenario = self.current_scenario
        if scenario is None:
            return
        entry = float(scenario.get("entry_reference") or math.nan)
        self._trail_best = entry
        scenario.update(
            {
                "notank_mfe_fraction": 0.0,
                "notank_mae_fraction": 0.0,
                "notank_current_fraction": 0.0,
                "notank_mfe_r": 0.0,
                "notank_mae_r": 0.0,
                "notank_elapsed_minutes": 0,
                "notank_trail_activation_minutes": None,
                "notank_exit_driver": None,
            }
        )

    def on_position_closed(self, event: Any) -> None:
        super().on_position_closed(event)
        self._reset_policy_state()

    def _decision_cycle_due(self, ts_event: int) -> bool:
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        bucket = int(self.config.notank_bucket_minutes)
        return bucket > 0 and moment.minute % bucket == bucket - 1

    @staticmethod
    def _episode_key(decision: Any) -> tuple[str, int, int]:
        return str(decision.symbol), int(decision.side), int(decision.episode_ts)

    def _eligible_decisions(
        self,
        ts_event: int,
        decisions: dict[str, Any],
    ) -> list[Any]:
        episode_mode = str(self.config.notank_episode_mode).strip().lower()
        if episode_mode not in {"rising_edge", "all_confirmed"}:
            raise ValueError(f"unsupported notank_episode_mode={episode_mode!r}")
        eligible = []
        trace = []
        for symbol in SYMBOLS:
            decision = decisions[symbol]
            side = int(decision.side) if decision.actionable else 0
            prior = int(self._prior_actionable.get(symbol, 0))
            key = self._episode_key(decision) if decision.actionable else None
            reason = "ELIGIBLE"
            allow = bool(decision.actionable)
            if allow and key in self._consumed_episodes:
                allow = False
                reason = "DUPLICATE_CAUSAL_EPISODE"
                self.diagnostics["notank_duplicate_episode_rejections"] += 1
            elif allow and episode_mode == "rising_edge" and prior == side and str(self.config.notank_entry_mode) == "rolling_reclaim":
                allow = False
                reason = "CONTIGUOUS_ROLLING_REJECTION_ALREADY_ACTIVE"
                self.diagnostics["notank_contiguous_signal_rejections"] += 1
            trace.append(
                {
                    "symbol": symbol,
                    "actionable": bool(decision.actionable),
                    "side": side,
                    "score": float(decision.score),
                    "episode_ts": int(decision.episode_ts),
                    "allow": allow,
                    "reason": reason if decision.actionable else (
                        decision.reasons[0] if decision.reasons else "UNRESOLVED"
                    ),
                    "diagnostics": dict(decision.diagnostics),
                }
            )
            self._prior_actionable[symbol] = side
            if allow:
                eligible.append(decision)
        eligible.sort(
            key=lambda decision: (
                -float(decision.score),
                _SYMBOL_PRIORITY.get(decision.symbol, 99),
                -int(decision.side),
            )
        )
        self.diagnostics["notank_decision_trace"].append(
            {
                "ts_event": int(ts_event),
                "selected_symbol": eligible[0].symbol if eligible else None,
                "candidates": trace,
            }
        )
        return eligible

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
                self._event("ENTRY_EXPIRED", ts_event, reason="NOTANK_PARENT_NOT_FILLED")
                self._clear_trade_state()
            return
        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return
        if not self._decision_cycle_due(ts_event):
            return

        self.diagnostics["notank_decisions"] += 1
        features = {
            symbol: _router.FeatureObservation(
                observed_time_ns=int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        _, decisions = _router.route_universe(
            {symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
            features,
            self.route_config,
        )
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if decision.actionable:
                self.diagnostics["notank_source_candidates"] += 1
            else:
                reason = decision.reasons[0] if decision.reasons else "UNKNOWN"
                reasons = self.diagnostics["unresolved_reason_counts"]
                reasons[reason] = int(reasons.get(reason, 0)) + 1
        eligible = self._eligible_decisions(ts_event, decisions)
        if not eligible:
            self.diagnostics["unresolved_episodes"] += 1
            return
        winner = eligible[0]
        self.diagnostics["notank_entry_candidates"] += 1
        before = int(self.diagnostics["entry_submissions"])
        super()._submit_decision(winner, ts_event)
        if int(self.diagnostics["entry_submissions"]) <= before:
            return
        self._consumed_episodes.add(self._episode_key(winner))
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "notank_entry_mode": str(self.config.notank_entry_mode),
                    "notank_direction_mode": str(self.config.notank_direction_mode),
                    "notank_episode_mode": str(self.config.notank_episode_mode),
                    "notank_management_mode": str(self.config.notank_management_mode),
                }
            )

    def _update_path(self) -> None:
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is None or symbol is None or not self.bars[symbol]:
            return
        entry = float(scenario.get("entry_reference") or math.nan)
        stop = float(scenario.get("stop") or math.nan)
        side = int(scenario.get("side") or 0)
        if not all(math.isfinite(value) for value in (entry, stop)) or side not in (-1, 1):
            return
        risk = side * (entry - stop)
        if risk <= 0.0:
            return
        bar = self.bars[symbol][-1]
        favourable_price = float(bar.high) if side > 0 else float(bar.low)
        adverse_price = float(bar.low) if side > 0 else float(bar.high)
        favourable = side * (favourable_price - entry)
        adverse = side * (adverse_price - entry)
        current = side * (float(bar.close) - entry)
        elapsed = max(0, self.minute_index - self.position_open_minute)
        prior_mfe_r = float(scenario.get("notank_mfe_r") or 0.0)
        prior_mae_r = float(scenario.get("notank_mae_r") or 0.0)
        scenario.update(
            {
                "notank_mfe_fraction": max(
                    float(scenario.get("notank_mfe_fraction") or 0.0),
                    favourable / entry,
                ),
                "notank_mae_fraction": min(
                    float(scenario.get("notank_mae_fraction") or 0.0),
                    adverse / entry,
                ),
                "notank_current_fraction": current / entry,
                "notank_mfe_r": max(prior_mfe_r, favourable / risk),
                "notank_mae_r": min(prior_mae_r, adverse / risk),
                "notank_current_r": current / risk,
                "notank_elapsed_minutes": elapsed,
            }
        )
        self.diagnostics["notank_path_updates"] += 1

    def _submit_exit(self, ts_event: int, reason: str, **details: Any) -> None:
        if self.current_symbol is None or self._exit_pending:
            return
        if self.current_scenario is not None:
            self.current_scenario["notank_exit_driver"] = reason
            self.current_scenario["notank_exit_details"] = details
        counts = self.diagnostics["notank_exit_counts"]
        counts[reason] = int(counts.get(reason, 0)) + 1
        self._exit_pending = True
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event("NOTANK_EXIT", ts_event, reason=reason, **details)

    def _opposite_confirmed(self) -> tuple[bool, dict[str, Any]]:
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is None or symbol is None:
            return False, {}
        state = _router.inspect_state(tuple(self.bars[symbol]), self.route_config)
        if not int(state.get("ready") or 0):
            return False, state
        side = int(scenario.get("side") or 0)
        opposite = bool(state.get("pivot_short")) if side > 0 else bool(state.get("pivot_long"))
        return opposite, state

    def _manage_open_position(self, ts_event: int) -> None:
        if self._exit_pending:
            return
        self._update_path()
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is None or symbol is None or not self.bars[symbol]:
            return
        mode = str(self.config.notank_management_mode).strip().lower()
        if mode not in {
            "r_target", "opposite_confirmed", "r_trail",
            "opposite_or_trail", "progress",
        }:
            raise ValueError(f"unsupported notank_management_mode={mode!r}")
        entry = float(scenario.get("entry_reference") or math.nan)
        stop = float(scenario.get("stop") or math.nan)
        side = int(scenario.get("side") or 0)
        risk = side * (entry - stop)
        if not all(math.isfinite(value) for value in (entry, stop, risk)) or risk <= 0.0:
            super()._manage_open_position(ts_event)
            return
        bar = self.bars[symbol][-1]

        if mode in {"r_trail", "opposite_or_trail"}:
            favourable_price = float(bar.high) if side > 0 else float(bar.low)
            favourable_r = side * (favourable_price - entry) / risk
            if not self._trail_active and favourable_r >= float(self.config.notank_trail_activation_r):
                self._trail_active = True
                self._trail_best = favourable_price
                self.diagnostics["notank_trailing_activations"] += 1
                scenario["notank_trail_activation_minutes"] = max(
                    0, self.minute_index - self.position_open_minute
                )
            elif self._trail_active:
                self._trail_best = (
                    max(self._trail_best, favourable_price)
                    if side > 0 else min(self._trail_best, favourable_price)
                )
            if self._trail_active and math.isfinite(self._trail_best):
                distance = float(self.config.notank_trail_distance_r) * risk
                trail = self._trail_best - side * distance
                crossed = float(bar.low) <= trail if side > 0 else float(bar.high) >= trail
                if crossed:
                    self.diagnostics["notank_trailing_exits"] += 1
                    self._submit_exit(
                        ts_event,
                        "NOTANK_R_TRAILING_EXIT",
                        trail_level=trail,
                        best_price=self._trail_best,
                        risk_per_unit=risk,
                    )
                    return

        if mode in {"opposite_confirmed", "opposite_or_trail"} and self._decision_cycle_due(ts_event):
            opposite, state = self._opposite_confirmed()
            if opposite:
                self.diagnostics["notank_opposite_pivot_exits"] += 1
                self._submit_exit(
                    ts_event,
                    "NOTANK_OPPOSITE_CONFIRMED_PIVOT",
                    pivot_ts=int(state.get("pivot_ts") or 0),
                    pivot_rsi=float(state.get("pivot_rsi") or 0.0),
                    current_rsi=float(state.get("current_rsi") or 0.0),
                )
                return

        if mode == "progress":
            held = max(0, self.minute_index - self.position_open_minute)
            mfe_r = float(scenario.get("notank_mfe_r") or 0.0)
            current_r = float(scenario.get("notank_current_r") or 0.0)
            if (
                held >= int(self.config.notank_progress_minutes)
                and mfe_r < float(self.config.notank_progress_mfe_r)
                and current_r <= 0.0
            ):
                self.diagnostics["notank_progress_exits"] += 1
                self._submit_exit(
                    ts_event,
                    "NOTANK_NO_PROGRESS_EXIT",
                    held_minutes=held,
                    mfe_r=mfe_r,
                    current_r=current_r,
                )
                return

        before_events = len(self.events)
        super()._manage_open_position(ts_event)
        if scenario.get("notank_exit_driver") is None and any(
            item.get("event_type") == "FORCED_DAYTRADE_EXIT"
            for item in self.events[before_events:]
        ):
            scenario["notank_exit_driver"] = "FORCED_DAYTRADE_EXIT"
            self._exit_pending = True


__all__ = ["Candidate35Config", "Candidate35Strategy"]
