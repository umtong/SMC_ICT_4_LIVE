"""Candidate 53 sparse auction policy over the exercised Nautilus shell."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from router import FeatureObservation, RouteConfig, RouteDecision, route_universe, thesis_invalidated
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
            arr = self.values.get(name)
            if arr is None:
                return default
            x = float(arr[index])
            return x if math.isfinite(x) else default

        oi_change = math.nan
        oi = self.values.get("open_interest")
        if oi is not None and index >= 15:
            cur, prev = float(oi[index]), float(oi[index - 15])
            if math.isfinite(cur) and math.isfinite(prev) and prev > 0:
                oi_change = cur / prev - 1.0
        premium_z = math.nan
        premium = self.values.get("premium")
        if premium is not None:
            clean = premium[max(0, index - 95):index + 1]
            clean = clean[np.isfinite(clean)]
            cur = float(premium[index])
            if clean.size >= 24 and math.isfinite(cur):
                std = float(clean.std(ddof=0))
                if std > 1e-12:
                    premium_z = (cur - float(clean.mean())) / std
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
    FAMILY_REENTRY_MINUTES = 35

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        # Base shell constructs RouteConfig before Candidate53-specific cost fields
        # exist. Replace it with the sparse cost-aware contract.
        self.route_config = RouteConfig(
            atr_period=config.atr_period,
            min_impulse_atr_continuation=config.min_impulse_atr_continuation,
            min_impulse_atr_reversal=config.min_impulse_atr_reversal,
            min_response_atr=config.min_response_atr,
            min_participation_ratio=config.min_participation_ratio,
            min_route_score=config.min_route_score,
            ambiguity_score_gap=config.ambiguity_score_gap,
            continuation_target_r=config.continuation_target_r,
            reversal_target_r=config.reversal_target_r,
            fee_rate_each_side=config.all_in_cost_bps_each_side / 10_000.0,
            slippage_rate_each_side=config.adverse_slippage_bps_each_side / 10_000.0,
            funding_reserve_rate=config.funding_reserve_bps / 10_000.0,
        )
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
        open_symbols = [s for s in SYMBOLS if not self.portfolio.is_flat(self.instrument_ids[s])]
        self.diagnostics["max_open_positions_observed"] = max(int(self.diagnostics["max_open_positions_observed"]), len(open_symbols))
        if len(open_symbols) > 1:
            self.diagnostics["global_position_violations"] += 1
            for symbol in open_symbols:
                self.cancel_all_orders(self.instrument_ids[symbol]); self.close_all_positions(self.instrument_ids[symbol])
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
        if any(len(self.bars[s]) < 110 for s in SYMBOLS):
            return
        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            self.diagnostics["cooldown_rejections"] += 1
            return
        features = {s: self.features[s].observation(ts_event, self.config.feature_max_age_seconds) for s in SYMBOLS}
        if not all(x.ready for x in features.values()):
            self.diagnostics["feature_stale_episodes"] += 1
            return
        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(bars_by_symbol={s: tuple(self.bars[s]) for s in SYMBOLS}, features_by_symbol=features, config=self.route_config)
        reasons = self.diagnostics["unresolved_reason_counts"]
        families = self.diagnostics["actionable_family_counts"]
        actionable: list[RouteDecision] = []
        for d in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[d.state] = int(counts.get(d.state, 0)) + 1
            if d.actionable:
                families[d.state] = int(families.get(d.state, 0)) + 1
                actionable.append(d)
            else:
                for reason in d.reasons:
                    reasons[reason] = int(reasons.get(reason, 0)) + 1
        self.diagnostics["source_signals_before_execution_filters"] += len(actionable)
        available: list[RouteDecision] = []
        for d in actionable:
            exact = (d.symbol, d.state, d.side, int(d.episode_ts))
            family = (d.symbol, d.state, d.side)
            if exact in self.used_episode_keys:
                self.diagnostics["used_episode_rejections"] += 1
                continue
            if self.minute_index - self.last_family_entry_minute.get(family, -10**12) < self.FAMILY_REENTRY_MINUTES:
                self.diagnostics["family_reentry_rejections"] += 1
                continue
            available.append(d)
        available.sort(key=lambda x: (-x.score, x.symbol, x.state))
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
        # Do not repeat v1's 5-minute micro-flow exits.  The bracket owns normal
        # resolution; only a completed hard rejection of the external release can
        # terminate early after the trade has had time to develop.
        if elapsed >= 10:
            feature = self.features[self.current_symbol].observation(ts_event, self.config.feature_max_age_seconds)
            if feature.ready:
                invalid, reason = thesis_invalidated(state=str(scenario.get("state", "")), side=int(scenario.get("side", 0)), bars=tuple(self.bars[self.current_symbol]), feature=feature, diagnostics=scenario.get("diagnostics", {}), config=self.route_config)
                if invalid:
                    instrument_id = self.instrument_ids[self.current_symbol]
                    self.cancel_all_orders(instrument_id); self.close_all_positions(instrument_id)
                    self.diagnostics["prospective_exit_submissions"] += 1
                    self._event("PROSPECTIVE_THESIS_EXIT", ts_event, reason=reason, elapsed_minutes=elapsed)
                    return
        super()._manage_open_position(ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
