"""One-account causal integration of replicated ichiV2 and MBE2 components.

The policy is frozen before the fresh interval:
- report-inferred ichiV2 short level handles persistent multi-horizon downtrend;
- exact MBE2 short ROI-only signals are actionable only when at least two of
  BTC/ETH/SOL/XRP signal on the same completed five-minute boundary;
- a qualified MBE2 collision has priority over ichi on that boundary because it
  is the more specific synchronized exhaustion state; otherwise ichi acts;
- within each family the source score and BTC/ETH/SOL/XRP priority are unchanged.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
import math
from typing import Any

import router
from router import FeatureObservation, ICHI_STATE, MBE_STATE
from strategy_base import SYMBOLS
from strategy_base import Candidate35Strategy as _ExecutionShell
from strategy_ichi_impl import (
    Candidate35Config as _IchiConfig,
    Candidate35Strategy as _IchiStrategy,
)

_PRIORITY = {symbol: index for index, symbol in enumerate(SYMBOLS)}


class Candidate35Config(_IchiConfig, frozen=True):
    integration_mode: str = "integrated"
    ichi_family_max_hold_minutes: int = 480
    mbe_min_actionable_candidates: int = 2
    mbe_startup_5m_candles: int = 140
    mbe_variant: str = "short_avg646"
    mbe_source_leverage: float = 6.46
    mbe_source_stoploss: float = 0.22
    mbe_tema_period: int = 9
    mbe_bb_period: int = 20
    mbe_rsi_period: int = 14
    mbe_roi_0: float = 0.079
    mbe_roi_15: float = 0.047
    mbe_roi_41: float = 0.032
    mbe_roi_114: float = 0.11
    mbe_roi_180: float = 0.007
    mbe_roi_420: float = 0.001
    mbe_emergency_target_fraction: float = 0.50


class Candidate35Strategy(_IchiStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        mode = str(config.integration_mode).strip().lower()
        if mode not in {"ichi_only", "mbe_only", "integrated"}:
            raise ValueError(f"unsupported integration_mode={mode!r}")
        if int(config.mbe_min_actionable_candidates) != 2:
            raise ValueError("the frozen MBE2 cross-asset relation is exactly >=2")
        if str(config.ichi_side_mode).strip().lower() != "short":
            raise ValueError("the replicated ichi component is report_short_level")
        if str(config.ichi_trigger_mode).strip().lower() != "level":
            raise ValueError("the replicated ichi component uses level triggering")
        super().__init__(config)
        self.integration_mode = mode
        self.bars = {
            symbol: deque(
                self.bars[symbol],
                maxlen=max(
                    12_500,
                    int(config.mbe_startup_5m_candles) * 5 + 60,
                ),
            )
            for symbol in SYMBOLS
        }
        self.ichi_route_config = self.route_config
        self.mbe_route_config = replace(
            self.route_config,
            picasso_bucket_minutes=5,
            picasso_precedence_mode=str(config.mbe_variant),
            picasso_rsi_long_period=int(config.mbe_rsi_period),
            picasso_bb_long_period=int(config.mbe_tema_period),
            picasso_bb_short_period=int(config.mbe_bb_period),
            picasso_source_effective_leverage=float(config.mbe_source_leverage),
            picasso_source_stoploss=float(config.mbe_source_stoploss),
            picasso_emergency_target_fraction=float(
                config.mbe_emergency_target_fraction
            ),
        )
        self._mbe_mfe_fraction = 0.0
        self._mbe_mae_fraction = 0.0
        self.diagnostics.update(
            {
                "candidate": "candidate-57-ichi-mbe-n1-v1",
                "integration_mode": mode,
                "arbitration_policy": (
                    "MBE_COLLISION_GE2_PRIORITY_ELSE_ICHI_SHORT_LEVEL"
                ),
                "source_logic_changed": 0,
                "outcome_derived_arbitration": 0,
                "family_source_signals": {"ichi": 0, "mbe": 0},
                "family_actionable_boundaries": {
                    "ichi": 0,
                    "mbe_collision": 0,
                },
                "family_selected_entries": {"ichi": 0, "mbe": 0},
                "dual_family_boundaries": 0,
                "mbe_raw_collision_boundaries": 0,
                "mbe_singleton_rejections": 0,
                "mbe_collision_competing_candidates": 0,
                "mbe_roi_exits": 0,
                "ichi_family_horizon_exits": 0,
                "integrated_unresolved_boundaries": 0,
                "family_route_counts": {"ichi": {}, "mbe": {}},
                "family_unresolved_reasons": {"ichi": {}, "mbe": {}},
            }
        )

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
    ) -> list[Any]:
        route_counts = self.diagnostics["family_route_counts"][family]
        reasons = self.diagnostics["family_unresolved_reasons"][family]
        actionable = []
        for decision in decisions.values():
            route_counts[decision.state] = (
                int(route_counts.get(decision.state, 0)) + 1
            )
            if decision.actionable:
                actionable.append(decision)
            else:
                for reason in decision.reasons:
                    reasons[reason] = int(reasons.get(reason, 0)) + 1
        self.diagnostics["family_source_signals"][family] += len(actionable)
        return self._ordered(actionable)

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
                int(self.diagnostics["max_simultaneous_entry_intents"]),
                1,
            )
            if self.minute_index - self.entry_pending_minute > 2:
                assert self.current_symbol is not None
                self.cancel_all_orders(
                    self.instrument_ids[self.current_symbol]
                )
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
        moment = datetime.fromtimestamp(
            ts_event / 1_000_000_000,
            tz=timezone.utc,
        )
        if moment.minute % 5 != 4:
            return
        required_minutes = max(
            int(self.config.mbe_startup_5m_candles) * 5,
            (
                int(self.config.ichi_lagging_span_period)
                + int(self.config.ichi_displacement)
                + 4
            )
            * 5,
        )
        if any(
            len(self.bars[symbol]) < required_minutes for symbol in SYMBOLS
        ):
            return
        features = {
            symbol: FeatureObservation(
                int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        bars_by_symbol = {
            symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS
        }
        ichi_actionable: list[Any] = []
        mbe_actionable: list[Any] = []
        if self.integration_mode in {"ichi_only", "integrated"}:
            _, ichi_decisions = router.ichi_route_universe(
                bars_by_symbol=bars_by_symbol,
                features_by_symbol=features,
                config=self.ichi_route_config,
            )
            ichi_actionable = self._record_family_decisions(
                "ichi",
                ichi_decisions,
            )
            if ichi_actionable:
                self.diagnostics["family_actionable_boundaries"]["ichi"] += 1
        if self.integration_mode in {"mbe_only", "integrated"}:
            _, mbe_decisions = router.mbe_route_universe(
                bars_by_symbol=bars_by_symbol,
                features_by_symbol=features,
                config=self.mbe_route_config,
            )
            mbe_actionable = self._record_family_decisions(
                "mbe",
                mbe_decisions,
            )
        mbe_collision = len(mbe_actionable) >= int(
            self.config.mbe_min_actionable_candidates
        )
        if mbe_collision:
            self.diagnostics["family_actionable_boundaries"][
                "mbe_collision"
            ] += 1
            self.diagnostics["mbe_raw_collision_boundaries"] += 1
            self.diagnostics["mbe_collision_competing_candidates"] += (
                len(mbe_actionable) - 1
            )
        elif mbe_actionable:
            self.diagnostics["mbe_singleton_rejections"] += len(
                mbe_actionable
            )
        if ichi_actionable and mbe_collision:
            self.diagnostics["dual_family_boundaries"] += 1

        candidate_family: str | None = None
        candidate = None
        if self.integration_mode == "ichi_only":
            candidate_family = "ichi" if ichi_actionable else None
            candidate = ichi_actionable[0] if ichi_actionable else None
        elif self.integration_mode == "mbe_only":
            candidate_family = "mbe" if mbe_collision else None
            candidate = mbe_actionable[0] if mbe_collision else None
        else:
            if mbe_collision:
                candidate_family = "mbe"
                candidate = mbe_actionable[0]
            elif ichi_actionable:
                candidate_family = "ichi"
                candidate = ichi_actionable[0]

        if candidate is None or candidate_family is None:
            self.diagnostics["unresolved_episodes"] += 1
            self.diagnostics["integrated_unresolved_boundaries"] += 1
            return
        key = (
            candidate_family,
            candidate.symbol,
            candidate.state,
            int(candidate.episode_ts),
        )
        if key in self.used_episode_keys:
            self.diagnostics["used_episode_rejections"] += 1
            return
        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return
        if (
            self.minute_index - self.last_entry_minute
            < int(self.config.cooldown_minutes)
        ):
            self.diagnostics["cooldown_rejections"] += 1
            return

        self.used_episode_keys.add(key)
        self._trail_active = False
        self._trail_best = None
        self._mbe_mfe_fraction = 0.0
        self._mbe_mae_fraction = 0.0
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(candidate, ts_event)
        if (
            int(self.diagnostics["entry_submissions"]) > before
            and self.current_scenario is not None
        ):
            self.diagnostics["family_selected_entries"][candidate_family] += 1
            self.current_scenario.update(
                {
                    "candidate": "candidate-57-ichi-mbe-n1-v1",
                    "scenario_family": candidate_family,
                    "integration_mode": self.integration_mode,
                    "arbitration_policy": self.diagnostics[
                        "arbitration_policy"
                    ],
                    "mbe_actionable_candidates": len(mbe_actionable),
                    "ichi_actionable_candidates": len(ichi_actionable),
                    "dual_family_boundary": int(
                        bool(ichi_actionable and mbe_collision)
                    ),
                    "mbe_mfe_underlying_fraction": 0.0,
                    "mbe_mae_underlying_fraction": 0.0,
                }
            )

    def _mbe_roi_threshold(self, age_minutes: int) -> float:
        if age_minutes >= 420:
            return float(self.config.mbe_roi_420)
        if age_minutes >= 180:
            return float(self.config.mbe_roi_180)
        if age_minutes >= 114:
            return float(self.config.mbe_roi_114)
        if age_minutes >= 41:
            return float(self.config.mbe_roi_41)
        if age_minutes >= 15:
            return float(self.config.mbe_roi_15)
        return float(self.config.mbe_roi_0)

    def _close_family_position(
        self,
        event_type: str,
        ts_event: int,
        **details: Any,
    ) -> None:
        if self.current_symbol is None:
            return
        if self.current_scenario is not None:
            self.current_scenario["management_exit_reason"] = event_type
            self.current_scenario["management_exit_signal_ts"] = int(ts_event)
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event(event_type, ts_event, **details)

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        family = str(scenario.get("scenario_family", ""))
        state = scenario.get("state")
        age = max(0, self.minute_index - self.position_open_minute)
        if state == MBE_STATE or family == "mbe":
            side = int(scenario.get("side", 0))
            entry = float(scenario.get("entry_reference", 0.0))
            bar = self.bars[self.current_symbol][-1]
            leverage = max(float(self.config.mbe_source_leverage), 1e-12)
            if side in (-1, 1) and math.isfinite(entry) and entry > 0.0:
                favourable = (
                    float(bar.high) if side > 0 else float(bar.low)
                )
                adverse = float(bar.low) if side > 0 else float(bar.high)
                favourable_move = side * (favourable - entry) / entry
                adverse_move = side * (adverse - entry) / entry
                self._mbe_mfe_fraction = max(
                    self._mbe_mfe_fraction,
                    favourable_move,
                )
                self._mbe_mae_fraction = min(
                    self._mbe_mae_fraction,
                    adverse_move,
                )
                if self.current_scenario is not None:
                    self.current_scenario[
                        "mbe_mfe_underlying_fraction"
                    ] = self._mbe_mfe_fraction
                    self.current_scenario[
                        "mbe_mae_underlying_fraction"
                    ] = self._mbe_mae_fraction
                    self.current_scenario[
                        "mbe_mfe_source_profit_ratio"
                    ] = self._mbe_mfe_fraction * leverage
                    self.current_scenario[
                        "mbe_mae_source_profit_ratio"
                    ] = self._mbe_mae_fraction * leverage
                moment = datetime.fromtimestamp(
                    ts_event / 1_000_000_000,
                    tz=timezone.utc,
                )
                if moment.minute % 5 == 4:
                    profit_ratio = (
                        side * (float(bar.close) - entry) / entry * leverage
                    )
                    roi = self._mbe_roi_threshold(age)
                    if profit_ratio >= roi:
                        self._close_family_position(
                            "PUBLIC_MBE2_ROI_EXIT",
                            ts_event,
                            age_minutes=age,
                            source_profit_ratio=profit_ratio,
                            roi_threshold=roi,
                            source_leverage=leverage,
                        )
                        self.diagnostics["mbe_roi_exits"] += 1
                        return
            _ExecutionShell._manage_open_position(self, ts_event)
            return
        if state == ICHI_STATE or family == "ichi":
            if age >= int(self.config.ichi_family_max_hold_minutes):
                self._close_family_position(
                    "PUBLIC_ICHI_FAMILY_HORIZON_EXIT",
                    ts_event,
                    age_minutes=age,
                    horizon_minutes=int(
                        self.config.ichi_family_max_hold_minutes
                    ),
                )
                self.diagnostics["ichi_family_horizon_exits"] += 1
                return
            _IchiStrategy._manage_open_position(self, ts_event)
            return
        _ExecutionShell._manage_open_position(self, ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._mbe_mfe_fraction = 0.0
        self._mbe_mae_fraction = 0.0


__all__ = ["Candidate35Config", "Candidate35Strategy"]
