"""Candidate 57 transient weak-reversion failure state machine."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math

from router import FeatureObservation, RouteDecision, route_universe
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


@dataclass(slots=True)
class PendingJump:
    decision: RouteDecision
    source_minute_index: int
    deadline_minute_index: int
    source_exit_minute_index: int


class Candidate35Config(_ExecutionConfig, frozen=True):
    jump_timeframe_minutes: int = 240
    jump_threshold_sigma: float = 2.0
    jump_volatility_window: int = 18
    jump_min_absolute_return: float = 0.0
    jump_terminal_atr_period: int = 14
    jump_stop_atr_multiple: float = 1.0
    jump_min_stop_fraction: float = 0.0015
    jump_emergency_target_fraction: float = 0.20
    jump_stop_mode: str = "terminal"
    jump_selection_mode: str = "source"
    jump_min_residual_share: float = 0.50
    jump_min_residual_z: float = 0.75
    jump_confirmation_minutes: int = 0
    jump_confirmation_bucket_minutes: int = 5
    jump_protection_mode: str = "none"
    jump_protection_activation_r: float = math.inf
    jump_protection_floor_r: float = 0.0
    jump_protection_trail_gap_r: float = math.inf
    jump_protection_escape_r: float = math.inf


class Candidate35Strategy(_ExecutionShell):
    """One account slot with optional post-jump rejection confirmation."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        required = int(config.jump_timeframe_minutes) * (
            int(config.jump_volatility_window) + 4
        ) + 400
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=max(8_000, required))
            for symbol in SYMBOLS
        }
        self.route_config = replace(
            self.route_config,
            jump_timeframe_minutes=int(config.jump_timeframe_minutes),
            jump_threshold_sigma=float(config.jump_threshold_sigma),
            jump_volatility_window=int(config.jump_volatility_window),
            jump_min_absolute_return=float(config.jump_min_absolute_return),
            jump_terminal_atr_period=int(config.jump_terminal_atr_period),
            jump_stop_atr_multiple=float(config.jump_stop_atr_multiple),
            jump_min_stop_fraction=float(config.jump_min_stop_fraction),
            jump_emergency_target_fraction=float(config.jump_emergency_target_fraction),
            jump_stop_mode=str(config.jump_stop_mode),
            jump_selection_mode=str(config.jump_selection_mode),
            jump_min_residual_share=float(config.jump_min_residual_share),
            jump_min_residual_z=float(config.jump_min_residual_z),
            jump_confirmation_minutes=int(config.jump_confirmation_minutes),
            jump_confirmation_bucket_minutes=int(config.jump_confirmation_bucket_minutes),
        )
        self.used_episode_keys: set[tuple[str, int]] = set()
        self.pending_jump: PendingJump | None = None
        self.diagnostics.update(
            {
                "external_source": "De Nicola 2021 jump reversion; Candidate 57 structural repair",
                "jump_timeframe_minutes": int(config.jump_timeframe_minutes),
                "jump_threshold_sigma": float(config.jump_threshold_sigma),
                "jump_volatility_window": int(config.jump_volatility_window),
                "jump_stop_mode": str(config.jump_stop_mode),
                "jump_selection_mode": str(config.jump_selection_mode),
                "jump_confirmation_minutes": int(config.jump_confirmation_minutes),
                "jump_confirmation_bucket_minutes": int(config.jump_confirmation_bucket_minutes),
                "jump_protection_mode": str(config.jump_protection_mode),
                "jump_protection_activation_r": float(config.jump_protection_activation_r),
                "jump_protection_floor_r": float(config.jump_protection_floor_r),
                "jump_protection_trail_gap_r": float(config.jump_protection_trail_gap_r),
                "jump_protection_escape_r": float(config.jump_protection_escape_r),
                "jump_protection_activations": 0,
                "jump_protection_ratchets": 0,
                "jump_protection_exit_requests": 0,
                "jump_protection_escape_events": 0,
                "jump_protection_disarms": 0,
                "jump_source_candidates": 0,
                "jump_used_episode_rejections": 0,
                "jump_pending_created": 0,
                "jump_pending_expired": 0,
                "jump_confirmed_entries": 0,
                "jump_time_exit_contract": int(config.jump_timeframe_minutes),
                "unresolved_reason_counts": {},
                "actionable_family_counts": {},
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

        if self.pending_jump is not None:
            if self._try_pending_confirmation(ts_event):
                return
            if self.pending_jump is not None:
                # A pending source jump owns the decision slot until it confirms
                # or expires. This prevents two causal episodes being blended.
                return

        timeframe = int(self.route_config.jump_timeframe_minutes)
        minute_ordinal = int(ts_event // 60_000_000_000)
        if minute_ordinal % timeframe != timeframe - 1:
            return
        required = timeframe * (int(self.route_config.jump_volatility_window) + 2)
        if any(len(self.bars[symbol]) < required for symbol in SYMBOLS):
            return

        observations = {
            symbol: FeatureObservation(
                observed_time_ns=int(self.bars[symbol][-1].ts_event), ready=True
            )
            for symbol in SYMBOLS
        }
        self.diagnostics["quarter_hour_decisions"] += 1
        winner, decisions = route_universe(
            bars_by_symbol={symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
            features_by_symbol=observations,
            config=self.route_config,
        )
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
        candidates: list[RouteDecision] = []
        for decision in decisions.values():
            route_counts = self.diagnostics["route_counts"]
            route_counts[decision.state] = int(route_counts.get(decision.state, 0)) + 1
            if decision.actionable:
                family_counts[decision.state] = int(
                    family_counts.get(decision.state, 0)
                ) + 1
                key = (decision.symbol, int(decision.episode_ts))
                if key in self.used_episode_keys:
                    self.diagnostics["jump_used_episode_rejections"] += 1
                else:
                    candidates.append(decision)
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1
        self.diagnostics["jump_source_candidates"] += len(candidates)
        if winner is None or not candidates:
            self.diagnostics["unresolved_episodes"] += 1
            return
        # route_universe already applied the configured arbitration score.
        selected = next(
            (item for item in candidates if item.symbol == winner.symbol), winner
        )
        self.used_episode_keys.add((selected.symbol, int(selected.episode_ts)))
        confirmation = int(self.route_config.jump_confirmation_minutes)
        if confirmation > 0:
            self.pending_jump = PendingJump(
                decision=selected,
                source_minute_index=self.minute_index,
                deadline_minute_index=self.minute_index + confirmation,
                source_exit_minute_index=self.minute_index + timeframe,
            )
            self.diagnostics["jump_pending_created"] += 1
            self._event(
                "JUMP_PENDING_CONFIRMATION",
                ts_event,
                symbol=selected.symbol,
                episode_ts=selected.episode_ts,
                deadline_minute_index=self.pending_jump.deadline_minute_index,
            )
            return
        self._submit_source_decision(
            selected, ts_event, source_exit_minute=self.minute_index + timeframe
        )

    def _try_pending_confirmation(self, ts_event: int) -> bool:
        pending = self.pending_jump
        if pending is None:
            return False
        elapsed = self.minute_index - pending.source_minute_index
        if self.minute_index > pending.deadline_minute_index:
            self.diagnostics["jump_pending_expired"] += 1
            self._event(
                "JUMP_CONFIRMATION_EXPIRED",
                ts_event,
                symbol=pending.decision.symbol,
                elapsed_minutes=elapsed,
            )
            self.pending_jump = None
            return False
        bucket = max(1, int(self.route_config.jump_confirmation_bucket_minutes))
        if elapsed < bucket or elapsed % bucket != 0:
            return False
        symbol = pending.decision.symbol
        recent = list(self.bars[symbol])[-elapsed:]
        if len(recent) < elapsed:
            return False
        diagnostics = dict(pending.decision.diagnostics)
        side = int(pending.decision.side)
        terminal_low = float(diagnostics["terminal_minute_low"])
        terminal_high = float(diagnostics["terminal_minute_high"])
        close = float(recent[-1].close)
        confirmed = close > terminal_high if side > 0 else close < terminal_low
        if not confirmed:
            return False

        extension_low = min(float(bar.low) for bar in recent)
        extension_high = max(float(bar.high) for bar in recent)
        atr = float(diagnostics.get("terminal_atr", math.nan))
        entry = close
        buffer = max(
            float(self.route_config.jump_stop_atr_multiple) * atr,
            float(self.route_config.jump_min_stop_fraction) * entry,
        )
        stop = extension_low - buffer if side > 0 else extension_high + buffer
        target = entry * (
            1.0 + side * float(self.route_config.jump_emergency_target_fraction)
        )
        geometry_ok = (
            0.0 < stop < entry < target
            if side > 0
            else 0.0 < target < entry < stop
        )
        if not geometry_ok:
            self.diagnostics["unresolved_episodes"] += 1
            self._event(
                "CONFIRMED_JUMP_GEOMETRY_INVALID",
                ts_event,
                symbol=symbol,
                entry=entry,
                stop=stop,
                target=target,
            )
            self.pending_jump = None
            return False
        diagnostics.update(
            {
                "confirmation_elapsed_minutes": elapsed,
                "confirmation_bucket_minutes": bucket,
                "confirmation_close": close,
                "post_jump_extension_low": extension_low,
                "post_jump_extension_high": extension_high,
                "confirmation_stop_buffer": buffer,
                "confirmation_stop_fraction": abs(entry - stop) / entry,
            }
        )
        decision = replace(
            pending.decision,
            entry_reference=entry,
            stop_reference=stop,
            objective_reference=target,
            reasons=(*pending.decision.reasons, "POST_JUMP_REENTRY_CONFIRMED"),
            diagnostics=diagnostics,
        )
        source_exit = pending.source_exit_minute_index
        self.pending_jump = None
        self.diagnostics["jump_confirmed_entries"] += 1
        self._submit_source_decision(
            decision, ts_event, source_exit_minute=source_exit
        )
        return self.entry_pending

    def _submit_source_decision(
        self,
        decision: RouteDecision,
        ts_event: int,
        *,
        source_exit_minute: int,
    ) -> None:
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(decision, ts_event)
        if (
            int(self.diagnostics["entry_submissions"]) > before
            and self.current_scenario is not None
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-57-jump-repair",
                    "source_holding_minutes": int(
                        self.route_config.jump_timeframe_minutes
                    ),
                    "source_jump_threshold_sigma": float(
                        self.route_config.jump_threshold_sigma
                    ),
                    "source_volatility_window": int(
                        self.route_config.jump_volatility_window
                    ),
                    "management": "original-horizon-plus-structural-stop",
                    "risk_geometry": str(self.route_config.jump_stop_mode),
                    "selection_mode": str(
                        self.route_config.jump_selection_mode
                    ),
                    "confirmation_minutes": int(
                        self.route_config.jump_confirmation_minutes
                    ),
                    "source_exit_minute_index": int(source_exit_minute),
                    "protection_mode": str(self.config.jump_protection_mode),
                    "protection_activation_r": float(
                        self.config.jump_protection_activation_r
                    ),
                    "protection_floor_r_config": float(
                        self.config.jump_protection_floor_r
                    ),
                    "protection_trail_gap_r": float(
                        self.config.jump_protection_trail_gap_r
                    ),
                    "protection_escape_r": float(
                        self.config.jump_protection_escape_r
                    ),
                    "protection_active": False,
                    "protection_escaped": False,
                    "protection_floor_price": None,
                    "protection_floor_r": None,
                    "favorable_net_r_peak": -math.inf,
                    "favorable_price": None,
                    "protection_activation_minute": None,
                    "protection_escape_minute": None,
                    "management_exit_reason": None,
                    "management_exit_requested": False,
                }
            )

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        scenario = self.current_scenario
        symbol = self.current_symbol
        instrument_id = self.instrument_ids[symbol]
        bar = self.bars[symbol][-1]
        if scenario is not None and bool(
            scenario.get("management_exit_requested")
        ):
            return

        # A floor created on a previous completed minute is tested first.
        # A floor activated or ratcheted by this bar is usable only from the
        # next completed minute; there is no retroactive same-bar fill.
        if scenario is not None and bool(scenario.get("protection_active")):
            floor_value = scenario.get("protection_floor_price")
            if floor_value is not None and math.isfinite(float(floor_value)):
                floor_price = float(floor_value)
                side = int(scenario["side"])
                crossed = (
                    float(bar.low) <= floor_price
                    if side > 0
                    else float(bar.high) >= floor_price
                )
                if crossed:
                    reason = (
                        f"PROTECTION_{str(scenario.get('protection_mode', 'unknown')).upper()}"
                    )
                    scenario["management_exit_reason"] = reason
                    scenario["management_exit_requested"] = True
                    scenario["management_exit_signal_ts"] = int(ts_event)
                    scenario["management_exit_floor_price"] = floor_price
                    scenario["management_exit_floor_r"] = scenario.get(
                        "protection_floor_r"
                    )
                    scenario["management_exit_bar_open"] = float(bar.open)
                    scenario["management_exit_bar_high"] = float(bar.high)
                    scenario["management_exit_bar_low"] = float(bar.low)
                    scenario["management_exit_bar_close"] = float(bar.close)
                    self.diagnostics["jump_protection_exit_requests"] += 1
                    self.cancel_all_orders(instrument_id)
                    self.close_all_positions(instrument_id)
                    self._event(
                        "JUMP_PROTECTION_EXIT",
                        ts_event,
                        symbol=symbol,
                        reason=reason,
                        floor_price=floor_price,
                        floor_r=scenario.get("protection_floor_r"),
                        bar_open=float(bar.open),
                        bar_high=float(bar.high),
                        bar_low=float(bar.low),
                        bar_close=float(bar.close),
                    )
                    return

        # After testing the old floor, update favorable excursion.  The
        # transient state machine distinguishes a weak reversion attempt from
        # a source-confirming escape.  A floor armed by this bar is usable only
        # from the next completed minute.  If an already-active floor and the
        # escape threshold are both touched in one bar, the old floor was tested
        # first above, which is the conservative no-intrabar-lookahead ordering.
        if scenario is not None:
            mode = str(scenario.get("protection_mode", "none"))
            arm_r = float(scenario.get("protection_activation_r", math.inf))
            escape_r = float(scenario.get("protection_escape_r", math.inf))
            if mode == "transient_be" and math.isfinite(arm_r):
                side = int(scenario["side"])
                entry = float(scenario["entry_reference"])
                stop = float(scenario["stop"])
                planned_loss = float(scenario["planned_loss_per_unit"])
                if planned_loss > 0.0 and math.isfinite(planned_loss):
                    favorable_price = float(bar.high) if side > 0 else float(bar.low)
                    previous_favorable = scenario.get("favorable_price")
                    if previous_favorable is not None and math.isfinite(float(previous_favorable)):
                        favorable_price = (
                            max(float(previous_favorable), favorable_price)
                            if side > 0
                            else min(float(previous_favorable), favorable_price)
                        )
                    scenario["favorable_price"] = favorable_price
                    gross_favorable = side * (favorable_price - entry)
                    price_risk_distance = abs(entry - stop)
                    estimated_cost_per_unit = max(0.0, planned_loss - price_risk_distance)
                    peak_net_r = (gross_favorable - estimated_cost_per_unit) / planned_loss
                    prior_peak = float(scenario.get("favorable_net_r_peak", -math.inf))
                    peak_net_r = max(prior_peak, peak_net_r)
                    scenario["favorable_net_r_peak"] = peak_net_r
                    scenario["estimated_round_trip_cost_per_unit"] = estimated_cost_per_unit

                    escaped = bool(scenario.get("protection_escaped"))
                    if not escaped and math.isfinite(escape_r) and peak_net_r >= escape_r:
                        scenario["protection_escaped"] = True
                        scenario["protection_escape_minute"] = self.minute_index
                        scenario["protection_escape_peak_r"] = peak_net_r
                        self.diagnostics["jump_protection_escape_events"] += 1
                        if bool(scenario.get("protection_active")):
                            scenario["protection_active"] = False
                            scenario["protection_floor_price"] = None
                            scenario["protection_floor_r"] = None
                            self.diagnostics["jump_protection_disarms"] += 1
                        self._event(
                            "JUMP_TRANSIENT_ESCAPE_CONFIRMED",
                            ts_event,
                            symbol=symbol,
                            peak_net_r=peak_net_r,
                            escape_r=escape_r,
                            protection_disarmed=True,
                            source_horizon_preserved=True,
                        )
                    elif (
                        not bool(scenario.get("protection_escaped"))
                        and peak_net_r >= arm_r
                        and not bool(scenario.get("protection_active"))
                    ):
                        floor_r = 0.0
                        floor_price = entry + side * estimated_cost_per_unit
                        scenario["protection_active"] = True
                        scenario["protection_floor_r"] = floor_r
                        scenario["protection_floor_price"] = floor_price
                        scenario["protection_activation_minute"] = self.minute_index
                        self.diagnostics["jump_protection_activations"] += 1
                        self._event(
                            "JUMP_TRANSIENT_BE_ARMED",
                            ts_event,
                            symbol=symbol,
                            peak_net_r=peak_net_r,
                            arm_r=arm_r,
                            escape_r=escape_r,
                            floor_r=floor_r,
                            floor_price=floor_price,
                            usable_from_next_complete_minute=True,
                        )

        moment = datetime.fromtimestamp(
            ts_event / 1_000_000_000, tz=timezone.utc
        )
        before_funding = (
            moment.hour in (7, 15, 23)
            and moment.minute >= self.config.funding_flatten_minute
        )
        source_exit = None
        if scenario is not None:
            source_exit = scenario.get("source_exit_minute_index")
        timed_out = (
            source_exit is not None and self.minute_index >= int(source_exit)
        ) or (
            source_exit is None
            and self.position_open_minute >= 0
            and self.minute_index - self.position_open_minute
            >= self.config.max_hold_minutes
        )
        evaluation_ended = ts_event >= self.config.evaluation_end_ns
        if before_funding or timed_out or evaluation_ended:
            if scenario is not None:
                scenario["management_exit_reason"] = (
                    "FUNDING_FLATTEN"
                    if before_funding
                    else "SOURCE_HORIZON"
                    if timed_out
                    else "EVALUATION_END"
                )
                scenario["management_exit_signal_ts"] = int(ts_event)
                scenario["management_exit_requested"] = True
            self.cancel_all_orders(instrument_id)
            self.close_all_positions(instrument_id)
            self._event(
                "FORCED_DAYTRADE_EXIT",
                ts_event,
                before_funding=before_funding,
                timed_out=timed_out,
                evaluation_ended=evaluation_ended,
                original_source_horizon=source_exit is not None,
            )



__all__ = ["Candidate35Config", "Candidate35Strategy"]
