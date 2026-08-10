"""NautilusTrader execution adapter for the public ``myshortingstrategiembe2`` strategy.

The inherited shell owns the continuous four-symbol account, exchange
contracts, matching, latency, fees, adverse slippage, liquidation, one global
entry/position slot, and exact current-NAV 3% planned-loss sizing.  This module
adds only the source's 5-minute signal cadence, source ROI semantics, and
causal one-minute trailing detail.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
import math

from router import (
    MBE_STATE,
    FeatureObservation,
    route_universe,
)
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    mbe_variant: str = "both_avg646"
    mbe_evaluation_start_ns: int = 0
    mbe_evaluation_end_ns: int = 0
    mbe_startup_5m_candles: int = 140
    mbe_tema_period: int = 9
    mbe_bb_period: int = 20
    mbe_rsi_period: int = 14
    mbe_source_leverage: float = 6.46
    mbe_source_stoploss: float = 0.22
    mbe_trailing_positive: float = 0.015
    mbe_trailing_offset: float = 0.025
    mbe_roi_0: float = 0.079
    mbe_roi_15: float = 0.047
    mbe_roi_41: float = 0.032
    mbe_roi_114: float = 0.11
    mbe_roi_180: float = 0.007
    mbe_roi_420: float = 0.001
    mbe_emergency_target_fraction: float = 0.50
    mbe_management_mode: str = "source"


class Candidate35Strategy(_ExecutionShell):
    """One-account source-faithful ``myshortingstrategiembe2`` policy with independent episodes."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        required_minutes = int(config.mbe_startup_5m_candles) * 5 + 60
        self.bars = {
            symbol: deque(
                self.bars[symbol], maxlen=max(12_500, required_minutes)
            )
            for symbol in SYMBOLS
        }
        self.route_config = replace(
            self.route_config,
            picasso_bucket_minutes=5,
            picasso_precedence_mode=str(config.mbe_variant),
            picasso_rsi_long_period=int(config.mbe_rsi_period),
            picasso_bb_long_period=int(config.mbe_tema_period),
            picasso_bb_short_period=int(config.mbe_bb_period),
            picasso_source_effective_leverage=float(
                config.mbe_source_leverage
            ),
            picasso_source_stoploss=float(config.mbe_source_stoploss),
            picasso_trailing_positive=float(
                config.mbe_trailing_positive
            ),
            picasso_trailing_offset=float(config.mbe_trailing_offset),
            picasso_emergency_target_fraction=float(
                config.mbe_emergency_target_fraction
            ),
        )
        self._entry_gate_start_ns = (
            int(config.mbe_evaluation_start_ns)
            if int(config.mbe_evaluation_start_ns) > 0
            else int(config.evaluation_start_ns)
        )
        self._entry_gate_end_ns = (
            int(config.mbe_evaluation_end_ns)
            if int(config.mbe_evaluation_end_ns) > 0
            else int(config.evaluation_end_ns)
        )
        self.used_episode_keys: set[tuple[str, str, int]] = set()
        self._trail_active = False
        self._trail_best: float | None = None
        self._mbe_mfe_fraction = 0.0
        self._mbe_mae_fraction = 0.0
        self.diagnostics.update(
            {
                "candidate": "candidate-57",
                "external_source": (
                    "remiotore/ccxt-freqtrade:strategies/myshortingstrategiembe2.py"
                ),
                "external_source_blob": (
                    "d312e07abc99ffd5631a992fc67a4e97a8768c0a"
                ),
                "mbe_variant": str(config.mbe_variant),
                "source_timeframe_minutes": 5,
                "source_startup_5m_candles": int(
                    config.mbe_startup_5m_candles
                ),
                "source_signals_before_execution_filters": 0,
                "used_episode_rejections": 0,
                "cooldown_rejections": 0,
                "funding_runway_rejections": 0,
                "mbe_trailing_activations": 0,
                "mbe_trailing_exits": 0,
                "mbe_roi_exits": 0,
                "mbe_management_mode": str(config.mbe_management_mode),
                "mbe_collision_minutes": 0,
                "mbe_competing_candidates": 0,
                "mbe_collision_rejected_symbols": {},
                "mbe_collision_score_gap_count": 0,
                "mbe_collision_score_gap_sum": 0.0,
                "mbe_collision_score_gap_min": None,
                "mbe_collision_score_gap_max": None,
                "unresolved_reason_counts": {},
                "actionable_family_counts": {},
                "complete_5m_candles_only": 1,
                "one_minute_trailing_detail": 1,
                "same_minute_trail_activation_and_hit_allowed": 0,
                "real_binance_ohlc_execution": 1,
                "project_independent_episode_mode": 1,
                "warmup_trade_block_start_ns": self._entry_gate_start_ns,
                "forced_flat_cutoff_ns": self._entry_gate_end_ns,
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
                int(
                    self.diagnostics[
                        "max_simultaneous_entry_intents"
                    ]
                ),
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
            self._entry_gate_start_ns
            <= ts_event
            <= self._entry_gate_end_ns
        ):
            return

        moment = datetime.fromtimestamp(
            ts_event / 1_000_000_000, tz=timezone.utc
        )
        if moment.minute % 5 != 4:
            return
        required_minutes = int(self.config.mbe_startup_5m_candles) * 5
        if any(
            len(self.bars[symbol]) < required_minutes
            for symbol in SYMBOLS
        ):
            return

        features = {
            symbol: FeatureObservation(
                int(self.bars[symbol][-1].ts_event), ready=True
            )
            for symbol in SYMBOLS
        }
        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(
            bars_by_symbol={
                symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS
            },
            features_by_symbol=features,
            config=self.route_config,
        )
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = (
                int(counts.get(decision.state, 0)) + 1
            )
            if decision.actionable:
                family_counts[decision.state] = (
                    int(family_counts.get(decision.state, 0)) + 1
                )
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = (
                        int(reason_counts.get(reason, 0)) + 1
                    )

        actionable = [
            decision
            for decision in decisions.values()
            if decision.actionable
        ]
        self.diagnostics[
            "source_signals_before_execution_filters"
        ] += len(actionable)
        unused = []
        for decision in actionable:
            key = (
                decision.symbol,
                decision.state,
                int(decision.episode_ts),
            )
            if key in self.used_episode_keys:
                self.diagnostics["used_episode_rejections"] += 1
            else:
                unused.append(decision)
        unused.sort(
            key=lambda item: (
                -float(item.score),
                item.symbol,
                int(item.episode_ts),
            )
        )
        collision_count = max(0, len(unused) - 1)
        score_gap = None
        if collision_count > 0:
            self.diagnostics["mbe_collision_minutes"] += 1
            self.diagnostics["mbe_competing_candidates"] += collision_count
            rejected = self.diagnostics["mbe_collision_rejected_symbols"]
            for item in unused[1:]:
                rejected[item.symbol] = int(rejected.get(item.symbol, 0)) + 1
            score_gap = max(0.0, float(unused[0].score) - float(unused[1].score))
            self.diagnostics["mbe_collision_score_gap_count"] += 1
            self.diagnostics["mbe_collision_score_gap_sum"] += score_gap
            minimum = self.diagnostics["mbe_collision_score_gap_min"]
            maximum = self.diagnostics["mbe_collision_score_gap_max"]
            self.diagnostics["mbe_collision_score_gap_min"] = (
                score_gap if minimum is None else min(float(minimum), score_gap)
            )
            self.diagnostics["mbe_collision_score_gap_max"] = (
                score_gap if maximum is None else max(float(maximum), score_gap)
            )
        winner = unused[0] if unused else None
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
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

        self.used_episode_keys.add(
            (winner.symbol, winner.state, int(winner.episode_ts))
        )
        self._trail_active = False
        self._trail_best = None
        self._mbe_mfe_fraction = 0.0
        self._mbe_mae_fraction = 0.0
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if (
            int(self.diagnostics["entry_submissions"]) > before
            and self.current_scenario is not None
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-57-public-mbe2",
                    "source_variant": str(
                        self.route_config.picasso_precedence_mode
                    ),
                    "source_timeframe_minutes": 5,
                    "source_entry_is_level": False,
                    "source_ignore_roi_if_entry_signal": False,
                    "valid_real_ohlc_execution": True,
                    "mbe_management_mode": str(self.config.mbe_management_mode),
                    "mbe_collision_competitors": collision_count,
                    "mbe_winner_score_gap": score_gap,
                    "mbe_mfe_underlying_fraction": 0.0,
                    "mbe_mae_underlying_fraction": 0.0,
                }
            )

    def _close_source_position(
        self, event_type: str, ts_event: int, **details: float | int | str
    ) -> None:
        if self.current_symbol is None:
            return
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event(event_type, ts_event, **details)

    def _roi_threshold(self, age_minutes: int) -> float:
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


    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        if ts_event >= self._entry_gate_end_ns:
            self._close_source_position(
                "CANDIDATE57_EVALUATION_FLATTEN",
                ts_event,
                cutoff_ns=self._entry_gate_end_ns,
            )
            return
        scenario = self.current_scenario or {}
        if scenario.get("state") == MBE_STATE:
            side = int(scenario.get("side", 0))
            entry = float(scenario.get("entry_reference", 0.0))
            bar = self.bars[self.current_symbol][-1]
            leverage = max(
                float(self.config.mbe_source_leverage), 1e-12
            )
            if side in (-1, 1) and math.isfinite(entry) and entry > 0.0:
                favourable = float(bar.high) if side > 0 else float(bar.low)
                adverse = float(bar.low) if side > 0 else float(bar.high)
                favourable_move = side * (favourable - entry) / entry
                adverse_move = side * (adverse - entry) / entry
                self._mbe_mfe_fraction = max(self._mbe_mfe_fraction, favourable_move)
                self._mbe_mae_fraction = min(self._mbe_mae_fraction, adverse_move)
                if self.current_scenario is not None:
                    self.current_scenario["mbe_mfe_underlying_fraction"] = self._mbe_mfe_fraction
                    self.current_scenario["mbe_mae_underlying_fraction"] = self._mbe_mae_fraction
                    self.current_scenario["mbe_mfe_source_profit_ratio"] = self._mbe_mfe_fraction * leverage
                    self.current_scenario["mbe_mae_source_profit_ratio"] = self._mbe_mae_fraction * leverage
                activation = (
                    float(self.config.mbe_trailing_offset) / leverage
                )
                distance = (
                    float(self.config.mbe_trailing_positive) / leverage
                )
                # A trail active before this completed minute may be hit
                # during it.  A trail first activated by this minute may not
                # also use an earlier intra-minute path to exit.
                management_mode = str(self.config.mbe_management_mode).strip().lower()
                trailing_enabled = management_mode in {"source", "trail_only"}
                roi_enabled = management_mode in {"source", "roi_only"}
                if trailing_enabled and self._trail_active and self._trail_best is not None:
                    if side > 0:
                        trailing_stop = self._trail_best * (
                            1.0 - distance
                        )
                        hit = float(bar.low) <= trailing_stop
                    else:
                        trailing_stop = self._trail_best * (
                            1.0 + distance
                        )
                        hit = float(bar.high) >= trailing_stop
                    if hit:
                        self._close_source_position(
                            "PUBLIC_MBE2_TRAILING_EXIT",
                            ts_event,
                            trailing_stop=trailing_stop,
                            best_price=self._trail_best,
                            source_leverage=leverage,
                            activation_fraction=activation,
                            trail_fraction=distance,
                        )
                        self.diagnostics["mbe_trailing_exits"] += 1
                        return

                move = favourable_move
                if trailing_enabled and not self._trail_active and move >= activation:
                    self._trail_active = True
                    self._trail_best = favourable
                    self.diagnostics["mbe_trailing_activations"] += 1
                    self._event(
                        "PUBLIC_MBE2_TRAILING_ACTIVATED",
                        ts_event,
                        favourable_price=favourable,
                        source_leverage=leverage,
                        activation_fraction=activation,
                    )
                elif trailing_enabled and self._trail_active:
                    assert self._trail_best is not None
                    self._trail_best = (
                        max(self._trail_best, favourable)
                        if side > 0
                        else min(self._trail_best, favourable)
                    )

                moment = datetime.fromtimestamp(
                    ts_event / 1_000_000_000, tz=timezone.utc
                )
                if moment.minute % 5 == 4:
                    age = max(
                        0,
                        self.minute_index - self.position_open_minute,
                    )
                    profit_ratio = (
                        side
                        * (float(bar.close) - entry)
                        / entry
                        * leverage
                    )
                    roi = self._roi_threshold(age)
                    if roi_enabled and profit_ratio >= roi:
                        self._close_source_position(
                            "PUBLIC_MBE2_ROI_EXIT",
                            ts_event,
                            age_minutes=age,
                            source_profit_ratio=profit_ratio,
                            roi_threshold=roi,
                            source_leverage=leverage,
                        )
                        self.diagnostics["mbe_roi_exits"] += 1
                        return
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._trail_active = False
        self._trail_best = None
        self._mbe_mfe_fraction = 0.0
        self._mbe_mae_fraction = 0.0


__all__ = ["Candidate35Config", "Candidate35Strategy"]
