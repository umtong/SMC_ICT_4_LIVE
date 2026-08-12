"""One-account N-to-1 policy: jump reversal specialist + ichi short continuation.

The two source families keep their own entry, invalidation and management.  On
a completed 4h boundary an actionable jump event has priority over the broader
five-minute ichi continuation state; otherwise ichi may use the single account
slot.  No open position is preempted and no outcome-derived score is used.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import math
from typing import Any

import router
from router import FeatureObservation, ICHI_STATE, JUMP_REVERSION_STATE
from strategy_base import SYMBOLS
from strategy_base import Candidate35Strategy as _ExecutionShell
from strategy_ichi_fast_base import (
    Candidate35Config as _IchiConfig,
    Candidate35Strategy as _IchiStrategy,
)
from strategy_jump_source_reference import Candidate35Strategy as _JumpStrategy

_PRIORITY = {symbol: index for index, symbol in enumerate(SYMBOLS)}
_MINUTE_NS = 60_000_000_000


class Candidate35Config(_IchiConfig, frozen=True):
    integration_mode: str = "integrated"
    signal_start_ns: int = 0
    signal_end_ns: int = 0
    ichi_family_max_hold_minutes: int = 480

    jump_timeframe_minutes: int = 240
    jump_threshold_sigma: float = 2.0
    jump_volatility_window: int = 18
    jump_min_absolute_return: float = 0.0
    jump_terminal_atr_period: int = 14
    jump_stop_atr_multiple: float = 1.0
    jump_min_stop_fraction: float = 0.0015
    jump_emergency_target_fraction: float = 0.20
    jump_stop_mode: str = "impulse"
    jump_confirmation_minutes: int = 0
    jump_confirmation_bucket_minutes: int = 5
    jump_protection_mode: str = "transient_be"
    jump_protection_activation_r: float = 0.4
    jump_protection_floor_r: float = 0.0
    jump_protection_trail_gap_r: float = 999.0
    jump_protection_escape_r: float = 1.0


class Candidate35Strategy(_IchiStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        mode = str(config.integration_mode).strip().lower()
        if mode not in {"jump_only", "ichi_only", "integrated"}:
            raise ValueError(f"unsupported integration_mode={mode!r}")
        if str(config.ichi_side_mode).strip().lower() != "short":
            raise ValueError("N-to-1 ichi component is frozen to report-short-level")
        if str(config.ichi_trigger_mode).strip().lower() != "level":
            raise ValueError("N-to-1 ichi component uses level triggering")
        if int(config.jump_confirmation_minutes) != 0:
            raise ValueError("frozen jump component uses immediate source entry")
        if str(config.jump_stop_mode).strip().lower() != "impulse":
            raise ValueError("frozen jump component uses whole-impulse stop geometry")
        if int(config.signal_start_ns) <= 0 or int(config.signal_end_ns) <= 0:
            raise ValueError("signal window must be explicit")
        if int(config.signal_end_ns) < int(config.signal_start_ns):
            raise ValueError("signal_end_ns precedes signal_start_ns")
        super().__init__(config)
        self.integration_mode = mode
        self.ichi_route_config = self.route_config
        self.jump_route_config = router.JumpRouteConfig(
            jump_timeframe_minutes=int(config.jump_timeframe_minutes),
            jump_threshold_sigma=float(config.jump_threshold_sigma),
            jump_volatility_window=int(config.jump_volatility_window),
            jump_min_absolute_return=float(config.jump_min_absolute_return),
            jump_terminal_atr_period=int(config.jump_terminal_atr_period),
            jump_stop_atr_multiple=float(config.jump_stop_atr_multiple),
            jump_min_stop_fraction=float(config.jump_min_stop_fraction),
            jump_emergency_target_fraction=float(config.jump_emergency_target_fraction),
            jump_stop_mode=str(config.jump_stop_mode),
            jump_selection_mode="source",
            jump_confirmation_minutes=int(config.jump_confirmation_minutes),
            jump_confirmation_bucket_minutes=int(
                config.jump_confirmation_bucket_minutes
            ),
        )
        jump_required = int(config.jump_timeframe_minutes) * (
            int(config.jump_volatility_window) + 4
        ) + 400
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=max(8_000, jump_required))
            for symbol in SYMBOLS
        }
        self._ichi_history_minutes = 1_000
        self.used_episode_keys: set[tuple[str, str, int]] = set()
        self._jump_favorable_net_r_peak = -math.inf
        self.diagnostics.update(
            {
                "candidate": "candidate-57-jump-ichi-n1-v1",
                "integration_mode": mode,
                "arbitration_policy": "JUMP_4H_PRIORITY_ELSE_ICHI_SHORT_LEVEL",
                "source_logic_changed": 0,
                "outcome_derived_arbitration": 0,
                "family_source_signals": {"jump": 0, "ichi": 0},
                "family_actionable_boundaries": {"jump": 0, "ichi": 0},
                "family_selected_entries": {"jump": 0, "ichi": 0},
                "family_shadow_signals_while_slot_busy": {"jump": 0, "ichi": 0},
                "family_shadow_boundaries_while_slot_busy": {"jump": 0, "ichi": 0},
                "dual_family_boundaries": 0,
                "jump_priority_boundaries": 0,
                "jump_competing_candidates": 0,
                "jump_least_z_arbitration_used": 1,
                "jump_taker_filter_used": 0,
                "jump_protection_activations": 0,
                "jump_protection_exit_requests": 0,
                "jump_protection_escape_events": 0,
                "jump_protection_disarms": 0,
                "jump_source_horizon_exits": 0,
                "family_route_counts": {"jump": {}, "ichi": {}},
                "family_unresolved_reasons": {"jump": {}, "ichi": {}},
                "signal_start_ns": int(config.signal_start_ns),
                "signal_end_ns": int(config.signal_end_ns),
                "indicator_history_max_completed_minutes": 1000,
                "alpha_rule_changed_by_finite_history": 0,
            }
        )

    def _ichi_bars(self, symbol: str) -> tuple[Any, ...]:
        return tuple(list(self.bars[symbol])[-self._ichi_history_minutes :])

    def _source_entry_signal_active(self) -> bool:
        if self.current_symbol is None or self.current_scenario is None:
            return False
        candles = router._aggregate_complete(
            self._ichi_bars(self.current_symbol),
            int(self.ichi_route_config.picasso_bucket_minutes),
        )
        required = max(
            100,
            int(self.config.ichi_lagging_span_period)
            + int(self.config.ichi_displacement)
            + 2,
        )
        if len(candles) < required:
            return False
        arrays = router._source_arrays(candles, self.ichi_route_config)
        long_level, short_level, _ = router._signal_at(
            arrays, len(candles) - 1, self.ichi_route_config
        )
        side = int(self.current_scenario.get("side", 0))
        return bool(long_level if side > 0 else short_level if side < 0 else False)

    def _source_exit_signal(self) -> tuple[bool, dict[str, float | int | str]]:
        if self.current_symbol is None or self.current_scenario is None:
            return False, {}
        candles = router._aggregate_complete(
            self._ichi_bars(self.current_symbol),
            int(self.ichi_route_config.picasso_bucket_minutes),
        )
        required = max(
            100,
            int(self.config.ichi_lagging_span_period)
            + int(self.config.ichi_displacement)
            + 2,
        )
        if len(candles) < required:
            return False, {
                "source_exit_ready": 0,
                "source_exit_completed_5m_candles": len(candles),
            }
        arrays = router._source_arrays(candles, self.ichi_route_config)
        source = arrays[str(self.config.ichi_exit_indicator)]
        trend = arrays["trend_close_5m"]
        if len(source) < 2 or len(trend) < 2:
            return False, {"source_exit_ready": 0}
        values = (
            float(trend[-1]),
            float(trend[-2]),
            float(source[-1]),
            float(source[-2]),
        )
        if not all(math.isfinite(value) for value in values):
            return False, {"source_exit_ready": 0}
        current_trend, previous_trend, current_source, previous_source = values
        side = int(self.current_scenario.get("side", 0))
        if side > 0:
            crossed = current_trend < current_source and previous_trend >= previous_source
        elif side < 0:
            crossed = current_trend > current_source and previous_trend <= previous_source
        else:
            return False, {"source_exit_ready": 0}
        return bool(crossed), {
            "source_exit_ready": 1,
            "source_exit_side": side,
            "source_exit_indicator": str(self.config.ichi_exit_indicator),
            "source_exit_current_trend": current_trend,
            "source_exit_previous_trend": previous_trend,
            "source_exit_current_indicator": current_source,
            "source_exit_previous_indicator": previous_source,
            "source_exit_crossed": int(crossed),
        }

    @staticmethod
    def _ordered(items: list[Any]) -> list[Any]:
        return sorted(
            items,
            key=lambda item: (
                -float(item.score),
                _PRIORITY.get(item.symbol, 99),
                int(item.episode_ts),
            ),
        )

    def _record_family_decisions(
        self,
        family: str,
        decisions: dict[str, Any],
        *,
        slot_busy: bool,
    ) -> list[Any]:
        route_counts = self.diagnostics["family_route_counts"][family]
        reasons = self.diagnostics["family_unresolved_reasons"][family]
        actionable: list[Any] = []
        for decision in decisions.values():
            route_counts[decision.state] = int(route_counts.get(decision.state, 0)) + 1
            if decision.actionable:
                actionable.append(decision)
            else:
                for reason in decision.reasons:
                    reasons[reason] = int(reasons.get(reason, 0)) + 1
        self.diagnostics["family_source_signals"][family] += len(actionable)
        if actionable:
            self.diagnostics["family_actionable_boundaries"][family] += 1
        if slot_busy and actionable:
            self.diagnostics["family_shadow_signals_while_slot_busy"][family] += len(
                actionable
            )
            self.diagnostics["family_shadow_boundaries_while_slot_busy"][family] += 1
        return self._ordered(actionable)

    def _route_families(
        self,
        ts_event: int,
        *,
        slot_busy: bool,
    ) -> tuple[list[Any], list[Any]]:
        minute_ordinal = int(ts_event // _MINUTE_NS)
        five_boundary = minute_ordinal % 5 == 4
        jump_boundary = (
            minute_ordinal % int(self.config.jump_timeframe_minutes)
            == int(self.config.jump_timeframe_minutes) - 1
        )
        if not five_boundary:
            return [], []
        features = {
            symbol: FeatureObservation(int(self.bars[symbol][-1].ts_event), ready=True)
            for symbol in SYMBOLS
        }
        ichi_actionable: list[Any] = []
        jump_actionable: list[Any] = []
        if self.integration_mode in {"ichi_only", "integrated"}:
            minimum_ichi = (
                int(self.config.ichi_lagging_span_period)
                + int(self.config.ichi_displacement)
                + 4
            ) * 5
            if all(len(self.bars[symbol]) >= minimum_ichi for symbol in SYMBOLS):
                _, decisions = router.ichi_route_universe(
                    bars_by_symbol={
                        symbol: self._ichi_bars(symbol) for symbol in SYMBOLS
                    },
                    features_by_symbol=features,
                    config=self.ichi_route_config,
                )
                ichi_actionable = self._record_family_decisions(
                    "ichi", decisions, slot_busy=slot_busy
                )
        if jump_boundary and self.integration_mode in {"jump_only", "integrated"}:
            minimum_jump = int(self.config.jump_timeframe_minutes) * (
                int(self.config.jump_volatility_window) + 2
            )
            if all(len(self.bars[symbol]) >= minimum_jump for symbol in SYMBOLS):
                _, decisions = router.jump_route_universe(
                    bars_by_symbol={
                        symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS
                    },
                    features_by_symbol=features,
                    config=self.jump_route_config,
                )
                jump_actionable = self._record_family_decisions(
                    "jump", decisions, slot_busy=slot_busy
                )
                if len(jump_actionable) > 1:
                    self.diagnostics["jump_competing_candidates"] += len(
                        jump_actionable
                    ) - 1
        return jump_actionable, ichi_actionable

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

        in_signal_window = (
            int(self.config.signal_start_ns)
            <= int(ts_event)
            <= int(self.config.signal_end_ns)
        )
        if open_symbols:
            self.current_symbol = open_symbols[0]
            if in_signal_window:
                self._route_families(ts_event, slot_busy=True)
            self._manage_open_position(ts_event)
            return
        if self.entry_pending:
            if in_signal_window:
                self._route_families(ts_event, slot_busy=True)
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
        if not in_signal_window:
            return

        jump_actionable, ichi_actionable = self._route_families(
            ts_event, slot_busy=False
        )
        if jump_actionable and ichi_actionable:
            self.diagnostics["dual_family_boundaries"] += 1

        family: str | None = None
        candidate = None
        if self.integration_mode == "jump_only":
            family = "jump" if jump_actionable else None
            candidate = jump_actionable[0] if jump_actionable else None
        elif self.integration_mode == "ichi_only":
            family = "ichi" if ichi_actionable else None
            candidate = ichi_actionable[0] if ichi_actionable else None
        else:
            if jump_actionable:
                family = "jump"
                candidate = jump_actionable[0]
                self.diagnostics["jump_priority_boundaries"] += 1
            elif ichi_actionable:
                family = "ichi"
                candidate = ichi_actionable[0]

        if candidate is None or family is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        key = (family, candidate.symbol, int(candidate.episode_ts))
        if key in self.used_episode_keys:
            self.diagnostics["used_episode_rejections"] += 1
            return
        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return
        if self.minute_index - self.last_entry_minute < int(self.config.cooldown_minutes):
            self.diagnostics["cooldown_rejections"] += 1
            return

        self.used_episode_keys.add(key)
        self._trail_active = False
        self._trail_best = None
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(candidate, ts_event)
        if (
            int(self.diagnostics["entry_submissions"]) > before
            and self.current_scenario is not None
        ):
            self.diagnostics["family_selected_entries"][family] += 1
            self.current_scenario.update(
                {
                    "candidate": "candidate-57-jump-ichi-n1-v1",
                    "scenario_family": family,
                    "integration_mode": self.integration_mode,
                    "arbitration_policy": self.diagnostics["arbitration_policy"],
                    "dual_family_boundary": int(
                        bool(jump_actionable and ichi_actionable)
                    ),
                    "jump_actionable_candidates": len(jump_actionable),
                    "ichi_actionable_candidates": len(ichi_actionable),
                    "management_exit_reason": None,
                    "management_exit_requested": False,
                }
            )
            if family == "jump":
                self.current_scenario.update(
                    {
                        "source_holding_minutes": int(
                            self.config.jump_timeframe_minutes
                        ),
                        "source_exit_minute_index": self.minute_index
                        + int(self.config.jump_timeframe_minutes),
                        "risk_geometry": str(self.config.jump_stop_mode),
                        "selection_mode": "least_qualifying_z",
                        "confirmation_minutes": 0,
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
                    }
                )

    def _manage_open_position(self, ts_event: int) -> None:
        scenario = self.current_scenario or {}
        family = str(scenario.get("scenario_family", ""))
        if family == "jump" or scenario.get("state") == JUMP_REVERSION_STATE:
            _JumpStrategy._manage_open_position(self, ts_event)
            return
        if family == "ichi" or scenario.get("state") == ICHI_STATE:
            super()._manage_open_position(ts_event)
            return
        _ExecutionShell._manage_open_position(self, ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
