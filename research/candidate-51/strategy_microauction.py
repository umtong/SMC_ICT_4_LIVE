"""NautilusTrader adapter for the real-data micro-auction router."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import math
from pathlib import Path

import numpy as np
import pandas as pd

from router import (
    ABSORPTION_STATE,
    CONTINUATION_STATE,
    FeatureObservation,
    classify_symbol,
    route_universe,
)
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class _MicroAuctionFeatureStore:
    """Causal columnar view over real Binance aggTrades and bookDepth features."""

    _COLUMNS = (
        "flow_15s",
        "flow_60s",
        "flow_3m",
        "notional_burst",
        "trade_count_burst",
        "ret_60s_bps",
        "path_60s_bps",
        "efficiency_60s",
        "flow_price_alignment_60s",
        "absorption_60s",
        "depth_imbalance_1",
        "depth_imbalance_2",
        "bid_depth_change_1_1m",
        "ask_depth_change_1_1m",
        "bid_depth_change_1_5m",
        "ask_depth_change_1_5m",
        "depth_snapshot_age_seconds",
    )

    def __init__(self, path: Path) -> None:
        header = set(pd.read_csv(path, compression="infer", nrows=0).columns)
        required = {"observed_time_ns", "feature_ready", *self._COLUMNS}
        missing = required.difference(header)
        if missing:
            raise RuntimeError(
                f"micro-auction feature schema missing {sorted(missing)} in {path}"
            )
        frame = pd.read_csv(
            path,
            compression="infer",
            usecols=["observed_time_ns", "feature_ready", *self._COLUMNS],
        )
        self.times = (
            pd.to_numeric(frame["observed_time_ns"], errors="raise")
            .astype("int64")
            .to_numpy(copy=True)
        )
        if self.times.size == 0 or np.any(np.diff(self.times) <= 0):
            raise RuntimeError(
                f"micro-auction times must be non-empty, unique and monotonic: {path}"
            )
        self.ready = (
            frame["feature_ready"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"true", "1", "yes"})
            .to_numpy(dtype=np.bool_, copy=True)
        )
        self.values = {
            name: pd.to_numeric(frame[name], errors="coerce").to_numpy(
                dtype=np.float64,
                copy=True,
            )
            for name in self._COLUMNS
        }

    def observation(self, ts_event: int, max_age_seconds: float) -> FeatureObservation:
        index = int(np.searchsorted(self.times, ts_event, side="right") - 1)
        if index < 0:
            return FeatureObservation(0, ready=False)
        observed = int(self.times[index])
        age = (int(ts_event) - observed) / 1_000_000_000
        if age < -1e-9:
            raise RuntimeError("future micro-auction feature reached strategy")
        numbers = {
            name: float(values[index])
            for name, values in self.values.items()
        }
        ready = (
            age <= float(max_age_seconds)
            and bool(self.ready[index])
            and all(math.isfinite(value) for value in numbers.values())
        )
        return FeatureObservation(
            observed_time_ns=observed,
            ready=ready,
            **numbers,
        )


class Candidate35Config(_ExecutionConfig, frozen=True):
    microauction_mode: str = "continuation"
    microauction_atr_period: int = 30
    microauction_balance_lookback: int = 10
    microauction_stop_buffer_atr: float = 0.15
    microauction_min_reward_r: float = 1.25

    continuation_flow_min: float = 0.20
    continuation_flow_3m_min: float = 0.10
    continuation_notional_burst_min: float = 1.50
    continuation_trade_burst_min: float = 1.20
    continuation_efficiency_min: float = 0.35
    continuation_displacement_atr_min: float = 0.20
    continuation_depth_support_min: float = -0.10
    continuation_opposite_depth_thinning_min: float = 0.05
    continuation_same_depth_growth_min: float = 0.05
    continuation_measured_move_multiple: float = 1.00

    absorption_flow_min: float = 0.25
    absorption_flow_15s_min: float = 0.15
    absorption_flow_3m_min: float = 0.05
    absorption_notional_burst_min: float = 1.50
    absorption_trade_burst_min: float = 1.20
    absorption_efficiency_max: float = 0.25
    absorption_strength_min: float = 0.15
    absorption_alignment_max_bps: float = 0.50
    absorption_sweep_buffer_atr: float = 0.05
    absorption_depth_defense_min: float = 0.00
    absorption_same_depth_growth_min: float = 0.00
    microauction_exit_on_opposite_state: bool = True


class Candidate35Strategy(_ExecutionShell):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=2_000)
            for symbol in SYMBOLS
        }
        self.route_config = replace(
            self.route_config,
            microauction_mode=str(config.microauction_mode),
            microauction_atr_period=int(config.microauction_atr_period),
            microauction_balance_lookback=int(config.microauction_balance_lookback),
            microauction_stop_buffer_atr=float(config.microauction_stop_buffer_atr),
            microauction_min_reward_r=float(config.microauction_min_reward_r),
            continuation_flow_min=float(config.continuation_flow_min),
            continuation_flow_3m_min=float(config.continuation_flow_3m_min),
            continuation_notional_burst_min=float(
                config.continuation_notional_burst_min
            ),
            continuation_trade_burst_min=float(config.continuation_trade_burst_min),
            continuation_efficiency_min=float(config.continuation_efficiency_min),
            continuation_displacement_atr_min=float(
                config.continuation_displacement_atr_min
            ),
            continuation_depth_support_min=float(
                config.continuation_depth_support_min
            ),
            continuation_opposite_depth_thinning_min=float(
                config.continuation_opposite_depth_thinning_min
            ),
            continuation_same_depth_growth_min=float(
                config.continuation_same_depth_growth_min
            ),
            continuation_measured_move_multiple=float(
                config.continuation_measured_move_multiple
            ),
            absorption_flow_min=float(config.absorption_flow_min),
            absorption_flow_15s_min=float(config.absorption_flow_15s_min),
            absorption_flow_3m_min=float(config.absorption_flow_3m_min),
            absorption_notional_burst_min=float(
                config.absorption_notional_burst_min
            ),
            absorption_trade_burst_min=float(config.absorption_trade_burst_min),
            absorption_efficiency_max=float(config.absorption_efficiency_max),
            absorption_strength_min=float(config.absorption_strength_min),
            absorption_alignment_max_bps=float(
                config.absorption_alignment_max_bps
            ),
            absorption_sweep_buffer_atr=float(
                config.absorption_sweep_buffer_atr
            ),
            absorption_depth_defense_min=float(
                config.absorption_depth_defense_min
            ),
            absorption_same_depth_growth_min=float(
                config.absorption_same_depth_growth_min
            ),
        )
        self.last_episode_signature: dict[str, tuple[str, int] | None] = {
            symbol: None for symbol in SYMBOLS
        }
        self.diagnostics.update({
            "external_source": "azseza/smallfish_",
            "source_performance_used_as_evidence": False,
            "source_backtest_problem": (
                "synthetic order book and synthetic trade tape are derived from "
                "the completed OHLCV candle"
            ),
            "microauction_mode": str(config.microauction_mode),
            "microauction_edges": 0,
            "microauction_opposite_state_exits": 0,
            "feature_stale_by_symbol": {},
            "unresolved_reason_counts": {},
            "actionable_family_counts": {},
        })

    def on_start(self) -> None:
        super().on_start()
        for symbol in SYMBOLS:
            self.features[symbol] = _MicroAuctionFeatureStore(
                self.feature_paths[symbol]
            )

    def _observation(self, symbol: str, ts_event: int) -> FeatureObservation:
        return self.features[symbol].observation(
            ts_event,
            self.config.feature_max_age_seconds,
        )

    def _manage_open_position(self, ts_event: int) -> None:
        if (
            bool(self.config.microauction_exit_on_opposite_state)
            and self.current_symbol is not None
            and self.current_scenario is not None
        ):
            observation = self._observation(self.current_symbol, ts_event)
            decision = classify_symbol(
                self.current_symbol,
                tuple(self.bars[self.current_symbol]),
                observation,
                self.route_config,
            )
            position_side = int(self.current_scenario.get("side", 0))
            if decision.actionable and int(decision.side) == -position_side:
                instrument_id = self.instrument_ids[self.current_symbol]
                self.cancel_all_orders(instrument_id)
                self.close_all_positions(instrument_id)
                self.diagnostics["microauction_opposite_state_exits"] += 1
                self._event(
                    "MICROAUCTION_OPPOSITE_STATE_EXIT",
                    ts_event,
                    symbol=self.current_symbol,
                    position_side=position_side,
                    opposite_state=decision.state,
                    opposite_score=decision.score,
                    diagnostics=dict(decision.diagnostics),
                )
                return
        super()._manage_open_position(ts_event)

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
        minimum = max(
            int(self.route_config.microauction_atr_period) + 2,
            int(self.route_config.microauction_balance_lookback) + 1,
        )
        if any(len(self.bars[symbol]) < minimum for symbol in SYMBOLS):
            return

        observations = {
            symbol: self._observation(symbol, ts_event)
            for symbol in SYMBOLS
        }
        stale = self.diagnostics["feature_stale_by_symbol"]
        for symbol, observation in observations.items():
            if not observation.ready:
                stale[symbol] = int(stale.get(symbol, 0)) + 1

        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(
            {
                symbol: tuple(self.bars[symbol])
                for symbol in SYMBOLS
            },
            observations,
            self.route_config,
        )

        edges = []
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
        route_counts = self.diagnostics["route_counts"]
        for symbol, decision in decisions.items():
            route_counts[decision.state] = int(
                route_counts.get(decision.state, 0)
            ) + 1
            if decision.actionable:
                family_counts[decision.state] = int(
                    family_counts.get(decision.state, 0)
                ) + 1
                signature = (decision.state, int(decision.side))
                if signature != self.last_episode_signature[symbol]:
                    edges.append(decision)
                self.last_episode_signature[symbol] = signature
            else:
                self.last_episode_signature[symbol] = None
                for reason in decision.reasons:
                    reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1

        if not edges:
            self.diagnostics["unresolved_episodes"] += 1
            return
        self.diagnostics["microauction_edges"] += len(edges)
        edges.sort(key=lambda item: (
            0 if item.state == ABSORPTION_STATE else 1,
            -float(item.score),
            item.symbol,
            int(item.episode_ts),
        ))
        winner = edges[0]
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return

        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if (
            int(self.diagnostics["entry_submissions"]) > before
            and self.current_scenario is not None
        ):
            management = (
                "balance-midpoint-bracket-opposite-state-timeout"
                if winner.state == ABSORPTION_STATE
                else "measured-move-bracket-opposite-state-timeout"
            )
            self.current_scenario.update({
                "candidate": "candidate-51-real-microauction",
                "external_source": "azseza/smallfish_",
                "external_source_performance_used": False,
                "state_family": winner.state,
                "risk_geometry": "event-extreme-plus-atr-buffer",
                "management": management,
            })


__all__ = ["Candidate35Config", "Candidate35Strategy"]
