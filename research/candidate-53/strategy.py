"""Candidate 53 multi-mechanism policy over the reused Nautilus execution shell."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from router import FeatureObservation, RouteDecision, route_universe, thesis_invalidated
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config
from strategy_base import Candidate35Strategy as _ExecutionShell


def _bool_array(values: pd.Series) -> np.ndarray:
    if values.dtype == bool:
        return values.to_numpy(dtype=np.bool_, copy=True)
    return values.astype(str).str.strip().str.lower().isin({"true", "1", "yes"}).to_numpy(dtype=np.bool_, copy=True)


class _RichFeatureStore:
    _ALIASES = {
        "flow_open_10s": ("flow_open_10s", "flow_15s", "flow_60s"),
        "notional_open_10s_burst": ("notional_open_10s_burst", "notional_burst"),
        "flow_60s": ("flow_60s", "flow_15s"),
        "efficiency_60s": ("efficiency_60s",),
        "flow_15s": ("flow_15s", "flow_60s"),
        "flow_3m": ("flow_3m", "flow_60s"),
        "notional_burst": ("notional_burst",),
        "trade_count_burst": ("trade_count_burst",),
        "absorption_60s": ("absorption_60s",),
        "depth_imbalance_1": ("depth_imbalance_1",),
        "depth_imbalance_2": ("depth_imbalance_2",),
        "bid_depth_change_1_1m": ("bid_depth_change_1_1m",),
        "ask_depth_change_1_1m": ("ask_depth_change_1_1m",),
        "bid_depth_change_1_5m": ("bid_depth_change_1_5m",),
        "ask_depth_change_1_5m": ("ask_depth_change_1_5m",),
        "ret_60s_bps": ("ret_60s_bps",),
        "open_interest": ("sum_open_interest", "open_interest", "sum_open_interest_value"),
        "premium": ("premium_index", "mark_index_basis", "basis"),
    }

    def __init__(self, path: Path) -> None:
        header = list(pd.read_csv(path, compression="infer", nrows=0).columns)
        if not {"observed_time_ns", "feature_ready"}.issubset(header):
            raise RuntimeError(f"invalid feature schema in {path}")
        selected: dict[str, str] = {}
        for logical, aliases in self._ALIASES.items():
            source = next((name for name in aliases if name in header), None)
            if source is not None:
                selected[logical] = source
        usecols = ["observed_time_ns", "feature_ready", *sorted(set(selected.values()))]
        frame = pd.read_csv(path, compression="infer", usecols=usecols)
        self.times = pd.to_numeric(frame["observed_time_ns"], errors="raise").astype("int64").to_numpy(copy=True)
        if self.times.size == 0 or np.any(np.diff(self.times) <= 0):
            raise RuntimeError(f"non-monotone feature times: {path}")
        self.ready = _bool_array(frame["feature_ready"])
        self.values = {logical: pd.to_numeric(frame[source], errors="coerce").to_numpy(dtype=np.float64, copy=True) for logical, source in selected.items()}

    def _index(self, ts_event: int) -> int:
        return int(np.searchsorted(self.times, ts_event, side="right") - 1)

    def observation(self, ts_event: int, max_age_seconds: float) -> FeatureObservation:
        index = self._index(ts_event)
        if index < 0:
            return FeatureObservation(0, ready=False)
        observed = int(self.times[index])
        age = (ts_event - observed) / 1_000_000_000
        if age < -1e-9:
            raise RuntimeError("future feature observation reached Candidate 53")
        if age > max_age_seconds or not bool(self.ready[index]):
            return FeatureObservation(observed, ready=False)

        def value(name: str, default: float = math.nan) -> float:
            array = self.values.get(name)
            if array is None:
                return default
            number = float(array[index])
            return number if math.isfinite(number) else default

        oi_change = math.nan
        oi = self.values.get("open_interest")
        if oi is not None and index >= 15:
            current, previous = float(oi[index]), float(oi[index - 15])
            if math.isfinite(current) and math.isfinite(previous) and previous > 0.0:
                oi_change = current / previous - 1.0
        premium_z = math.nan
        premium = self.values.get("premium")
        if premium is not None:
            start = max(0, index - 95)
            clean = premium[start:index + 1]
            clean = clean[np.isfinite(clean)]
            current = float(premium[index])
            if clean.size >= 24 and math.isfinite(current):
                std = float(clean.std(ddof=0))
                if std > 1e-12:
                    premium_z = (current - float(clean.mean())) / std
        return FeatureObservation(
            observed_time_ns=observed, ready=True,
            flow_open_10s=value("flow_open_10s"), notional_open_10s_burst=value("notional_open_10s_burst"),
            flow_60s=value("flow_60s"), efficiency_60s=value("efficiency_60s"),
            oi_change_15m=oi_change, premium_z=premium_z,
            flow_15s=value("flow_15s"), flow_3m=value("flow_3m"),
            notional_burst=value("notional_burst"), trade_count_burst=value("trade_count_burst"),
            absorption_60s=value("absorption_60s"), depth_imbalance_1=value("depth_imbalance_1"),
            depth_imbalance_2=value("depth_imbalance_2"), bid_depth_change_1_1m=value("bid_depth_change_1_1m"),
            ask_depth_change_1_1m=value("ask_depth_change_1_1m"), bid_depth_change_1_5m=value("bid_depth_change_1_5m"),
            ask_depth_change_1_5m=value("ask_depth_change_1_5m"), ret_60s_bps=value("ret_60s_bps"),
        )


class Candidate35Strategy(_ExecutionShell):
    FAMILY_REENTRY_MINUTES = 20

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.used_episode_keys: set[tuple[str, str, int, int]] = set()
        self.last_family_entry_minute: dict[tuple[str, str, int], int] = {}
        for key in ("source_signals_before_execution_filters", "used_episode_rejections", "family_reentry_rejections", "prospective_exit_submissions", "funding_runway_rejections", "cooldown_rejections"):
            self.diagnostics.setdefault(key, 0)
        self.diagnostics.setdefault("actionable_family_counts", {})
        self.diagnostics.setdefault("unresolved_reason_counts", {})

    def on_start(self) -> None:
        super().on_start()
        self.features = {symbol: _RichFeatureStore(self.feature_paths[symbol]) for symbol in SYMBOLS}

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)
        open_symbols = [symbol for symbol in SYMBOLS if not self.portfolio.is_flat(self.instrument_ids[symbol])]
        self.diagnostics["max_open_positions_observed"] = max(int(self.diagnostics["max_open_positions_observed"]), len(open_symbols))
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
            self.diagnostics["max_simultaneous_entry_intents"] = max(int(self.diagnostics["max_simultaneous_entry_intents"]), 1)
            if self.minute_index - self.entry_pending_minute > 2:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event("ENTRY_EXPIRED", ts_event, reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES")
                self._clear_trade_state()
            return
        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        if any(len(self.bars[symbol]) < 70 for symbol in SYMBOLS):
            return
        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            self.diagnostics["cooldown_rejections"] += 1
            return
        features = {symbol: self.features[symbol].observation(ts_event, self.config.feature_max_age_seconds) for symbol in SYMBOLS}
        if not all(item.ready for item in features.values()):
            self.diagnostics["feature_stale_episodes"] += 1
            return
        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(bars_by_symbol={symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS}, features_by_symbol=features, config=self.route_config)
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
        actionable: list[RouteDecision] = []
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if decision.actionable:
                family_counts[decision.state] = int(family_counts.get(decision.state, 0)) + 1
                actionable.append(decision)
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1
        self.diagnostics["source_signals_before_execution_filters"] += len(actionable)
        available: list[RouteDecision] = []
        for decision in actionable:
            exact = (decision.symbol, decision.state, decision.side, int(decision.episode_ts))
            family = (decision.symbol, decision.state, decision.side)
            if exact in self.used_episode_keys:
                self.diagnostics["used_episode_rejections"] += 1
                continue
            last = self.last_family_entry_minute.get(family, -10**12)
            if self.minute_index - last < self.FAMILY_REENTRY_MINUTES:
                self.diagnostics["family_reentry_rejections"] += 1
                continue
            available.append(decision)
        available.sort(key=lambda item: (-item.score, item.symbol, item.state))
        if not available:
            self.diagnostics["unresolved_episodes"] += 1
            return
        winner = available[0]
        self.used_episode_keys.add((winner.symbol, winner.state, winner.side, int(winner.episode_ts)))
        self.last_family_entry_minute[(winner.symbol, winner.state, winner.side)] = self.minute_index
        self._submit_decision(winner, ts_event)

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        elapsed = self.minute_index - self.position_open_minute if self.position_open_minute >= 0 else 0
        if elapsed >= 4:
            feature = self.features[self.current_symbol].observation(ts_event, self.config.feature_max_age_seconds)
            if feature.ready:
                invalid, reason = thesis_invalidated(state=str(scenario.get("state", "")), side=int(scenario.get("side", 0)), bars=tuple(self.bars[self.current_symbol]), feature=feature, diagnostics=scenario.get("diagnostics", {}), config=self.route_config)
                if invalid:
                    instrument_id = self.instrument_ids[self.current_symbol]
                    self.cancel_all_orders(instrument_id)
                    self.close_all_positions(instrument_id)
                    self.diagnostics["prospective_exit_submissions"] += 1
                    self._event("PROSPECTIVE_THESIS_EXIT", ts_event, reason=reason, elapsed_minutes=elapsed)
                    return
                side = int(scenario.get("side", 0))
                entry = float(scenario.get("entry_reference", math.nan))
                stop = float(scenario.get("stop", math.nan))
                latest = self.bars[self.current_symbol][-1].close
                risk = side * (entry - stop) if side else math.nan
                if math.isfinite(risk) and risk > 0.0:
                    gain_r = side * (latest - entry) / risk
                    flow = feature.flow_3m if math.isfinite(feature.flow_3m) else feature.flow_60s
                    if gain_r >= 0.65:
                        scenario["armed_profit_protection"] = True
                    if scenario.get("armed_profit_protection") and gain_r <= 0.18 and math.isfinite(flow) and side * flow < -0.08:
                        instrument_id = self.instrument_ids[self.current_symbol]
                        self.cancel_all_orders(instrument_id)
                        self.close_all_positions(instrument_id)
                        self.diagnostics["prospective_exit_submissions"] += 1
                        self._event("PROSPECTIVE_PROFIT_PROTECTION", ts_event, gain_r=gain_r, flow_side=side * flow)
                        return
        super()._manage_open_position(ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
