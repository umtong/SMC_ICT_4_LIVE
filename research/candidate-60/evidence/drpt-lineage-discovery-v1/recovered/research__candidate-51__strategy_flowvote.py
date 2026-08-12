"""NautilusTrader adapter for the public order-book/delta vote strategy."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
import math
from pathlib import Path

import numpy as np
import pandas as pd

from router import FeatureObservation, classify_symbol, route_universe
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class _FlowFeatureStore:
    """Causal view over checksum-verified aggTrades and bookDepth features."""

    def __init__(self, path: Path) -> None:
        required = {
            "observed_time_ns", "feature_ready", "flow_60s", "flow_3m",
            "notional_60s", "efficiency_60s", "depth_imbalance_1",
            "depth_snapshot_age_seconds",
        }
        frame = pd.read_csv(path, compression="infer")
        missing = required.difference(frame.columns)
        if missing:
            raise RuntimeError(f"flow-vote feature schema missing {sorted(missing)} in {path}")
        self.times = pd.to_numeric(frame["observed_time_ns"], errors="raise").astype("int64").to_numpy(copy=True)
        if self.times.size == 0 or np.any(np.diff(self.times) <= 0):
            raise RuntimeError(f"flow-vote times must be unique and monotonic: {path}")
        self.ready = frame["feature_ready"].astype(str).str.lower().isin({"true", "1", "yes"}).to_numpy(dtype=np.bool_)
        self.flow_60s = pd.to_numeric(frame["flow_60s"], errors="coerce").to_numpy(dtype=np.float64)
        self.flow_3m = pd.to_numeric(frame["flow_3m"], errors="coerce").to_numpy(dtype=np.float64)
        self.depth = pd.to_numeric(frame["depth_imbalance_1"], errors="coerce").to_numpy(dtype=np.float64)
        self.efficiency = pd.to_numeric(frame["efficiency_60s"], errors="coerce").to_numpy(dtype=np.float64)
        self.depth_age = pd.to_numeric(frame["depth_snapshot_age_seconds"], errors="coerce").to_numpy(dtype=np.float64)
        notional = pd.to_numeric(frame["notional_60s"], errors="coerce")
        mean = notional.rolling(20, min_periods=20).mean()
        self.volume_ratio = (notional / mean.replace(0.0, np.nan)).to_numpy(dtype=np.float64)

    def observation(self, ts_event: int, max_age_seconds: float) -> FeatureObservation:
        index = int(np.searchsorted(self.times, ts_event, side="right") - 1)
        if index < 0:
            return FeatureObservation(0, ready=False)
        observed = int(self.times[index])
        age = (ts_event - observed) / 1_000_000_000
        if age < -1e-9:
            raise RuntimeError("future flow-vote feature reached strategy")
        values = (self.flow_60s[index], self.flow_3m[index], self.depth[index],
                  self.volume_ratio[index], self.efficiency[index], self.depth_age[index])
        ready = (age <= max_age_seconds and bool(self.ready[index])
                 and all(math.isfinite(float(value)) for value in values))
        return FeatureObservation(
            observed_time_ns=observed, ready=ready,
            flow_60s=float(self.flow_60s[index]), flow_3m=float(self.flow_3m[index]),
            depth_imbalance_1=float(self.depth[index]),
            notional_volume_ratio_20=float(self.volume_ratio[index]),
            efficiency_60s=float(self.efficiency[index]),
            depth_snapshot_age_seconds=float(self.depth_age[index]),
        )


class Candidate35Config(_ExecutionConfig, frozen=True):
    flowvote_bucket_minutes: int = 1
    flowvote_ema_period: int = 50
    flowvote_atr_period: int = 14
    flowvote_imbalance_threshold: float = 0.12
    flowvote_delta_threshold: float = 0.15
    flowvote_min_volume_ratio: float = 1.20
    flowvote_min_votes: int = 3
    flowvote_min_efficiency: float = 0.0
    flowvote_stop_atr_multiple: float = 1.50
    flowvote_min_stop_fraction: float = 0.003
    flowvote_target_r: float = 2.0


class Candidate35Strategy(_ExecutionShell):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.bars = {symbol: deque(self.bars[symbol], maxlen=2_000) for symbol in SYMBOLS}
        self.route_config = replace(
            self.route_config,
            flowvote_bucket_minutes=int(config.flowvote_bucket_minutes),
            flowvote_ema_period=int(config.flowvote_ema_period),
            flowvote_atr_period=int(config.flowvote_atr_period),
            flowvote_imbalance_threshold=float(config.flowvote_imbalance_threshold),
            flowvote_delta_threshold=float(config.flowvote_delta_threshold),
            flowvote_min_volume_ratio=float(config.flowvote_min_volume_ratio),
            flowvote_min_votes=int(config.flowvote_min_votes),
            flowvote_min_efficiency=float(config.flowvote_min_efficiency),
            flowvote_stop_atr_multiple=float(config.flowvote_stop_atr_multiple),
            flowvote_min_stop_fraction=float(config.flowvote_min_stop_fraction),
            flowvote_target_r=float(config.flowvote_target_r),
        )
        self.last_source_side = {symbol: 0 for symbol in SYMBOLS}
        self.diagnostics.update({
            "external_source": "vortex-systems-tech/Crypto-Strategy-Order-Book-Delta-Volume",
            "flowvote_bucket_minutes": int(config.flowvote_bucket_minutes),
            "flowvote_source_edges": 0,
            "flowvote_opposite_vote_exits": 0,
            "feature_stale_by_symbol": {},
            "unresolved_reason_counts": {},
            "actionable_family_counts": {},
        })

    def on_start(self) -> None:
        super().on_start()
        for symbol in SYMBOLS:
            self.features[symbol] = _FlowFeatureStore(self.feature_paths[symbol])

    def _observation(self, symbol: str, ts_event: int) -> FeatureObservation:
        return self.features[symbol].observation(ts_event, self.config.feature_max_age_seconds)

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is not None and self.current_scenario is not None:
            observation = self._observation(self.current_symbol, ts_event)
            decision = classify_symbol(
                self.current_symbol, tuple(self.bars[self.current_symbol]), observation,
                self.route_config)
            position_side = int(self.current_scenario.get("side", 0))
            if decision.actionable and int(decision.side) == -position_side:
                instrument_id = self.instrument_ids[self.current_symbol]
                self.cancel_all_orders(instrument_id)
                self.close_all_positions(instrument_id)
                self.diagnostics["flowvote_opposite_vote_exits"] += 1
                self._event("PUBLIC_FLOWVOTE_OPPOSITE_EXIT", ts_event,
                    symbol=self.current_symbol, position_side=position_side,
                    opposite_score=decision.score, diagnostics=dict(decision.diagnostics))
                return
        super()._manage_open_position(ts_event)

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)
        open_symbols = [symbol for symbol in SYMBOLS
                        if not self.portfolio.is_flat(self.instrument_ids[symbol])]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]), len(open_symbols))
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
                int(self.diagnostics["max_simultaneous_entry_intents"]), 1)
            if self.minute_index - self.entry_pending_minute > 2:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event("ENTRY_EXPIRED", ts_event,
                            reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES")
                self._clear_trade_state()
            return
        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        bucket = int(self.route_config.flowvote_bucket_minutes)
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % bucket != bucket - 1:
            return
        if any(len(self.bars[symbol]) < bucket * (self.route_config.flowvote_ema_period + 5)
               for symbol in SYMBOLS):
            return
        observations = {symbol: self._observation(symbol, ts_event) for symbol in SYMBOLS}
        stale = self.diagnostics["feature_stale_by_symbol"]
        for symbol, observation in observations.items():
            if not observation.ready:
                stale[symbol] = int(stale.get(symbol, 0)) + 1
        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(
            {symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS}, observations,
            self.route_config)
        edges = []
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
        for symbol, decision in decisions.items():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            current_side = int(decision.side) if decision.actionable else 0
            previous_side = int(self.last_source_side[symbol])
            if decision.actionable:
                family_counts[decision.state] = int(family_counts.get(decision.state, 0)) + 1
                if current_side != previous_side:
                    edges.append(decision)
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1
            self.last_source_side[symbol] = current_side
        if not edges:
            self.diagnostics["unresolved_episodes"] += 1
            return
        self.diagnostics["flowvote_source_edges"] += len(edges)
        edges.sort(key=lambda item: (-item.score, item.symbol))
        winner = edges[0]
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if int(self.diagnostics["entry_submissions"]) > before and self.current_scenario is not None:
            self.current_scenario.update({
                "candidate": "candidate-51-public-flowvote",
                "source_bucket_minutes": bucket,
                "source_vote_threshold": int(self.route_config.flowvote_min_votes),
                "risk_geometry": "max-1.5atr-or-0.3pct-real-stop",
                "management": "2R-bracket-opposite-vote-timeout",
            })


__all__ = ["Candidate35Config", "Candidate35Strategy"]
