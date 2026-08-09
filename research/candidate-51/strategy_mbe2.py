"""NautilusTrader execution adapter for public myshortingstrategiembe2."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import math

from router import FeatureObservation, MBE2_STATE, route_universe
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    mbe_rsi_period: int = 14
    mbe_tema_period: int = 9
    mbe_bb_period: int = 20
    mbe_long_rsi_cross: float = 30.0
    mbe_short_rsi_cross: float = 70.0
    mbe_source_effective_leverage: float = 6.46
    mbe_source_stoploss: float = 0.22
    mbe_trailing_positive: float = 0.015
    mbe_trailing_offset: float = 0.025
    mbe_emergency_target_fraction: float = 0.10
    mbe_roi_0: float = 0.079
    mbe_roi_15: float = 0.047
    mbe_roi_41: float = 0.032
    mbe_roi_114: float = 0.11
    mbe_roi_180: float = 0.007
    mbe_roi_420: float = 0.001


class Candidate35Strategy(_ExecutionShell):
    """One global slot, exact crossing episodes, source ROI and trailing."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            mbe_rsi_period=int(config.mbe_rsi_period),
            mbe_tema_period=int(config.mbe_tema_period),
            mbe_bb_period=int(config.mbe_bb_period),
            mbe_long_rsi_cross=float(config.mbe_long_rsi_cross),
            mbe_short_rsi_cross=float(config.mbe_short_rsi_cross),
            mbe_source_effective_leverage=float(config.mbe_source_effective_leverage),
            mbe_source_stoploss=float(config.mbe_source_stoploss),
            mbe_trailing_positive=float(config.mbe_trailing_positive),
            mbe_trailing_offset=float(config.mbe_trailing_offset),
            mbe_emergency_target_fraction=float(config.mbe_emergency_target_fraction),
        )
        self._roi_schedule = (
            (0, float(config.mbe_roi_0)),
            (15, float(config.mbe_roi_15)),
            (41, float(config.mbe_roi_41)),
            (114, float(config.mbe_roi_114)),
            (180, float(config.mbe_roi_180)),
            (420, float(config.mbe_roi_420)),
        )
        self.diagnostics.setdefault("source_signals_before_execution_filters", 0)
        self.diagnostics.setdefault("funding_runway_rejections", 0)
        self.diagnostics.setdefault("cooldown_rejections", 0)
        self.diagnostics.setdefault("used_episode_rejections", 0)
        self.diagnostics.setdefault("mbe_trailing_activations", 0)
        self.diagnostics.setdefault("mbe_trailing_exits", 0)
        self.diagnostics.setdefault("mbe_roi_exits", 0)
        self.used_episode_keys: set[tuple[str, str, int]] = set()
        self._trail_active = False
        self._trail_best: float | None = None

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)
        open_symbols = [
            symbol for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]), len(open_symbols),
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
                int(self.diagnostics["max_simultaneous_entry_intents"]), 1,
            )
            if self.minute_index - self.entry_pending_minute > 2:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event("ENTRY_EXPIRED", ts_event, reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES")
                self._clear_trade_state()
            return
        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % self.route_config.bucket_minutes != self.route_config.bucket_minutes - 1:
            return
        required_bars = self.route_config.bucket_minutes * max(
            self.route_config.mbe_rsi_period + 5,
            self.route_config.mbe_bb_period + 5,
            self.route_config.mbe_tema_period * 3 + 8,
        )
        if any(len(self.bars[symbol]) < required_bars for symbol in SYMBOLS):
            return

        features = {
            symbol: FeatureObservation(int(self.bars[symbol][-1].ts_event), ready=True)
            for symbol in SYMBOLS
        }
        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(
            bars_by_symbol={symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
            features_by_symbol=features,
            config=self.route_config,
        )
        reason_counts = self.diagnostics.setdefault("unresolved_reason_counts", {})
        family_counts = self.diagnostics.setdefault("actionable_family_counts", {})
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if decision.actionable:
                family_counts[decision.state] = int(family_counts.get(decision.state, 0)) + 1
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1

        actionable = [decision for decision in decisions.values() if decision.actionable]
        self.diagnostics["source_signals_before_execution_filters"] += len(actionable)
        unused = []
        for decision in actionable:
            key = (decision.symbol, decision.state, int(decision.episode_ts))
            if key in self.used_episode_keys:
                self.diagnostics["used_episode_rejections"] += 1
            else:
                unused.append(decision)
        unused.sort(key=lambda item: (-item.score, item.symbol))
        winner = unused[0] if unused else None
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            self.diagnostics["cooldown_rejections"] += 1
            return
        self.used_episode_keys.add((winner.symbol, winner.state, int(winner.episode_ts)))
        self._trail_active = False
        self._trail_best = None
        self._submit_decision(winner, ts_event)

    def _roi_profit_ratio(self, elapsed_minutes: int) -> float:
        result = self._roi_schedule[0][1]
        for minute, value in self._roi_schedule:
            if elapsed_minutes >= minute:
                result = value
            else:
                break
        return result

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        if scenario.get("state") == MBE2_STATE:
            side = int(scenario.get("side", 0))
            entry = float(scenario.get("entry_reference", 0.0))
            bar = self.bars[self.current_symbol][-1]
            leverage = max(self.route_config.mbe_source_effective_leverage, 1e-12)
            if side in (-1, 1) and math.isfinite(entry) and entry > 0.0:
                trail_activation = self.route_config.mbe_trailing_offset / leverage
                trail_distance = self.route_config.mbe_trailing_positive / leverage
                if self._trail_active and self._trail_best is not None:
                    if side > 0:
                        trailing_stop = self._trail_best * (1.0 - trail_distance)
                        trailing_hit = float(bar.low) <= trailing_stop
                    else:
                        trailing_stop = self._trail_best * (1.0 + trail_distance)
                        trailing_hit = float(bar.high) >= trailing_stop
                    if trailing_hit:
                        instrument_id = self.instrument_ids[self.current_symbol]
                        self.cancel_all_orders(instrument_id)
                        self.close_all_positions(instrument_id)
                        self.diagnostics["mbe_trailing_exits"] += 1
                        self._event(
                            "PUBLIC_MBE2_TRAILING_EXIT", ts_event,
                            trailing_stop=trailing_stop,
                            best_price=self._trail_best,
                            activation_fraction=trail_activation,
                            trail_fraction=trail_distance,
                        )
                        return

                elapsed = max(0, self.minute_index - self.position_open_minute)
                roi_profit_ratio = self._roi_profit_ratio(elapsed)
                roi_fraction = roi_profit_ratio / leverage
                roi_target = entry * (1.0 + side * roi_fraction)
                roi_hit = (
                    float(bar.high) >= roi_target if side > 0
                    else float(bar.low) <= roi_target
                )
                if roi_hit:
                    instrument_id = self.instrument_ids[self.current_symbol]
                    self.cancel_all_orders(instrument_id)
                    self.close_all_positions(instrument_id)
                    self.diagnostics["mbe_roi_exits"] += 1
                    self._event(
                        "PUBLIC_MBE2_ROI_EXIT", ts_event,
                        elapsed_minutes=elapsed,
                        source_roi_profit_ratio=roi_profit_ratio,
                        underlying_roi_fraction=roi_fraction,
                        roi_target=roi_target,
                    )
                    return

                favourable = float(bar.high) if side > 0 else float(bar.low)
                move = side * (favourable - entry) / entry
                if not self._trail_active and move >= trail_activation:
                    self._trail_active = True
                    self._trail_best = favourable
                    self.diagnostics["mbe_trailing_activations"] += 1
                    self._event(
                        "PUBLIC_MBE2_TRAILING_ACTIVATED", ts_event,
                        favourable_price=favourable,
                        activation_fraction=trail_activation,
                    )
                elif self._trail_active:
                    assert self._trail_best is not None
                    self._trail_best = (
                        max(self._trail_best, favourable) if side > 0
                        else min(self._trail_best, favourable)
                    )
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._trail_active = False
        self._trail_best = None


__all__ = ["Candidate35Config", "Candidate35Strategy"]
