"""NautilusTrader adapter for the Candidate 51 external-source tournament."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import math

from router import (
    EDGE_MR_STATE,
    WINNER_STATE,
    FeatureObservation,
    route_universe,
)
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    external_family_mode: str = "winner"

    winner_bucket_minutes: int = 15
    winner_ema_fast: int = 10
    winner_ema_slow: int = 30
    winner_macd_fast: int = 12
    winner_macd_slow: int = 26
    winner_macd_signal: int = 9
    winner_roc_period: int = 3
    winner_roc_threshold: float = 0.10
    winner_adx_period: int = 14
    winner_adx_threshold: float = 18.0
    winner_volume_period: int = 20
    winner_volume_ratio: float = 1.0
    winner_stop_fraction: float = 0.025
    winner_initial_target_fraction: float = 0.080
    winner_trailing_positive: float = 0.005
    winner_trailing_offset: float = 0.018
    winner_roi_0: float = 0.080
    winner_roi_480: float = 0.050
    winner_roi_1440: float = 0.030
    winner_roi_4320: float = 0.0

    edge_bucket_minutes: int = 15
    edge_vwap_period: int = 20
    edge_entry_z: float = 4.0
    edge_stop_z: float = 6.0
    edge_min_sigma_fraction: float = 0.00075
    edge_min_reward_r: float = 1.25


class Candidate35Strategy(_ExecutionShell):
    """One account slot, source entries, cost-aware 3% risk, source management."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            external_family_mode=str(config.external_family_mode),
            winner_bucket_minutes=int(config.winner_bucket_minutes),
            winner_ema_fast=int(config.winner_ema_fast),
            winner_ema_slow=int(config.winner_ema_slow),
            winner_macd_fast=int(config.winner_macd_fast),
            winner_macd_slow=int(config.winner_macd_slow),
            winner_macd_signal=int(config.winner_macd_signal),
            winner_roc_period=int(config.winner_roc_period),
            winner_roc_threshold=float(config.winner_roc_threshold),
            winner_adx_period=int(config.winner_adx_period),
            winner_adx_threshold=float(config.winner_adx_threshold),
            winner_volume_period=int(config.winner_volume_period),
            winner_volume_ratio=float(config.winner_volume_ratio),
            winner_stop_fraction=float(config.winner_stop_fraction),
            winner_initial_target_fraction=float(
                config.winner_initial_target_fraction
            ),
            edge_bucket_minutes=int(config.edge_bucket_minutes),
            edge_vwap_period=int(config.edge_vwap_period),
            edge_entry_z=float(config.edge_entry_z),
            edge_stop_z=float(config.edge_stop_z),
            edge_min_sigma_fraction=float(config.edge_min_sigma_fraction),
            edge_min_reward_r=float(config.edge_min_reward_r),
        )
        self._winner_roi_schedule = (
            (0, float(config.winner_roi_0)),
            (480, float(config.winner_roi_480)),
            (1440, float(config.winner_roi_1440)),
            (4320, float(config.winner_roi_4320)),
        )
        self._winner_trailing_positive = float(
            config.winner_trailing_positive
        )
        self._winner_trailing_offset = float(
            config.winner_trailing_offset
        )
        self.used_episode_keys: set[tuple[str, str, int]] = set()
        self._trail_active = False
        self._trail_best: float | None = None
        for key in (
            "source_signals_before_execution_filters",
            "funding_runway_rejections",
            "cooldown_rejections",
            "used_episode_rejections",
            "winner_trailing_activations",
            "winner_trailing_exits",
            "winner_roi_exits",
            "winner_entries",
            "edge_mr_entries",
        ):
            self.diagnostics.setdefault(key, 0)

    def _decision_boundary(self, moment: datetime) -> bool:
        mode = self.route_config.external_family_mode.strip().lower()
        buckets: tuple[int, ...]
        if mode == "winner":
            buckets = (self.route_config.winner_bucket_minutes,)
        elif mode == "edge":
            buckets = (self.route_config.edge_bucket_minutes,)
        else:
            buckets = (
                self.route_config.winner_bucket_minutes,
                self.route_config.edge_bucket_minutes,
            )
        return any(
            bucket > 0 and moment.minute % bucket == bucket - 1
            for bucket in buckets
        )

    def _required_minute_bars(self) -> int:
        winner_candles = max(
            self.route_config.winner_ema_slow + 4,
            self.route_config.winner_macd_slow
            + self.route_config.winner_macd_signal
            + 4,
            self.route_config.winner_adx_period * 2 + 4,
            self.route_config.winner_volume_period + 4,
        )
        winner_minutes = (
            winner_candles * self.route_config.winner_bucket_minutes
        )
        edge_minutes = (
            (self.route_config.edge_vwap_period + 4)
            * self.route_config.edge_bucket_minutes
        )
        mode = self.route_config.external_family_mode.strip().lower()
        if mode == "winner":
            return winner_minutes
        if mode == "edge":
            return edge_minutes
        return max(winner_minutes, edge_minutes)

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
        if not self._decision_boundary(moment):
            return
        if any(
            len(self.bars[symbol]) < self._required_minute_bars()
            for symbol in SYMBOLS
        ):
            return

        features = {
            symbol: FeatureObservation(
                int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(
            bars_by_symbol={
                symbol: tuple(self.bars[symbol])
                for symbol in SYMBOLS
            },
            features_by_symbol=features,
            config=self.route_config,
        )
        reason_counts = self.diagnostics.setdefault(
            "unresolved_reason_counts",
            {},
        )
        family_counts = self.diagnostics.setdefault(
            "actionable_family_counts",
            {},
        )
        for decision in decisions.values():
            route_counts = self.diagnostics["route_counts"]
            route_counts[decision.state] = (
                int(route_counts.get(decision.state, 0)) + 1
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
                item.state,
            )
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
            < self.config.cooldown_minutes
        ):
            self.diagnostics["cooldown_rejections"] += 1
            return

        self.used_episode_keys.add(
            (
                winner.symbol,
                winner.state,
                int(winner.episode_ts),
            )
        )
        self._trail_active = False
        self._trail_best = None
        if winner.state == WINNER_STATE:
            self.diagnostics["winner_entries"] += 1
        elif winner.state == EDGE_MR_STATE:
            self.diagnostics["edge_mr_entries"] += 1
        self._submit_decision(winner, ts_event)

    def _winner_roi(self, elapsed_minutes: int) -> float:
        value = self._winner_roi_schedule[0][1]
        for minute, threshold in self._winner_roi_schedule:
            if elapsed_minutes >= minute:
                value = threshold
            else:
                break
        return value

    def _manage_winner(self, ts_event: int) -> bool:
        if self.current_symbol is None:
            return False
        scenario = self.current_scenario or {}
        if scenario.get("state") != WINNER_STATE:
            return False
        side = int(scenario.get("side", 0))
        entry = float(scenario.get("entry_reference", math.nan))
        if side not in (-1, 1) or not math.isfinite(entry) or entry <= 0:
            return False
        bar = self.bars[self.current_symbol][-1]

        if self._trail_active and self._trail_best is not None:
            trailing_stop = self._trail_best * (
                1.0 - side * self._winner_trailing_positive
            )
            trailing_hit = (
                float(bar.low) <= trailing_stop
                if side > 0
                else float(bar.high) >= trailing_stop
            )
            if trailing_hit:
                instrument_id = self.instrument_ids[self.current_symbol]
                self.cancel_all_orders(instrument_id)
                self.close_all_positions(instrument_id)
                self.diagnostics["winner_trailing_exits"] += 1
                self._event(
                    "PUBLIC_BTCQUANT_WINNER_TRAILING_EXIT",
                    ts_event,
                    trailing_stop=trailing_stop,
                    best_price=self._trail_best,
                    trail_fraction=self._winner_trailing_positive,
                )
                return True

        elapsed = max(
            0,
            self.minute_index - self.position_open_minute,
        )
        roi_fraction = self._winner_roi(elapsed)
        roi_target = entry * (1.0 + side * roi_fraction)
        roi_hit = (
            float(bar.high) >= roi_target
            if side > 0
            else float(bar.low) <= roi_target
        )
        if roi_hit:
            instrument_id = self.instrument_ids[self.current_symbol]
            self.cancel_all_orders(instrument_id)
            self.close_all_positions(instrument_id)
            self.diagnostics["winner_roi_exits"] += 1
            self._event(
                "PUBLIC_BTCQUANT_WINNER_ROI_EXIT",
                ts_event,
                elapsed_minutes=elapsed,
                roi_fraction=roi_fraction,
                roi_target=roi_target,
            )
            return True

        favourable = float(bar.high) if side > 0 else float(bar.low)
        move = side * (favourable - entry) / entry
        if (
            not self._trail_active
            and move >= self._winner_trailing_offset
        ):
            self._trail_active = True
            self._trail_best = favourable
            self.diagnostics["winner_trailing_activations"] += 1
            self._event(
                "PUBLIC_BTCQUANT_WINNER_TRAILING_ACTIVATED",
                ts_event,
                favourable_price=favourable,
                activation_fraction=self._winner_trailing_offset,
            )
        elif self._trail_active:
            assert self._trail_best is not None
            self._trail_best = (
                max(self._trail_best, favourable)
                if side > 0
                else min(self._trail_best, favourable)
            )
        return False

    def _manage_open_position(self, ts_event: int) -> None:
        if self._manage_winner(ts_event):
            return
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._trail_active = False
        self._trail_best = None


__all__ = ["Candidate35Config", "Candidate35Strategy"]
