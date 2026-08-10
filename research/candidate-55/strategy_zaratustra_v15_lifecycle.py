"""Early auction-acceptance lifecycle for the high-capacity V15 BB short family.

The source V15 Bollinger edge, one-account arbitration, entry submission,
1.5% underlying stop, 1.07% trailing activation and 0.12% trail distance remain
unchanged.  This module changes only the lifecycle of a newly opened short:
within a small predeclared number of completed minutes, the broken Bollinger
boundary must become an accepted auction.

Acceptance is causal and mirrors the existing Candidate-05 failed-inventory
acceptance logic.  Price must remain below the signal boundary, the target must
remain a relative follower versus the other three liquid contracts, and current
aggressor flow must remain sell-aligned.  The depth variants additionally
require withdrawal of best-level bid liquidity.  Failure to obtain acceptance
closes the position before the source full stop consumes the entire risk budget.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import NamedTuple

import numpy as np
import pandas as pd

from strategy_base import SYMBOLS


_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v15_accepted.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v15_lifecycle_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load accepted V15 execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    v15_lifecycle_mode: str = "source"
    v15_acceptance_deadline_minutes: int = 3


class _LifecycleObservation(NamedTuple):
    observed_time_ns: int
    ready: bool
    return_since_signal: float
    flow_15s: float
    flow_60s: float
    efficiency_60s: float
    absorption_60s: float
    bid_depth_change_1_1m: float
    depth_imbalance_2: float


class _LifecycleFeatureStore:
    _COLUMNS = (
        "trade_vwap_60s",
        "flow_15s",
        "flow_60s",
        "efficiency_60s",
        "absorption_60s",
        "bid_depth_change_1_1m",
        "depth_imbalance_2",
    )

    def __init__(self, path: Path) -> None:
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
            raise RuntimeError(f"invalid lifecycle feature clock: {path}")
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
                dtype=np.float64, copy=True
            )
            for name in self._COLUMNS
        }

    def observation(
        self,
        ts_event: int,
        signal_ts: int,
        max_age_seconds: float,
    ) -> _LifecycleObservation:
        current_index = int(np.searchsorted(self.times, ts_event, side="right") - 1)
        signal_index = int(np.searchsorted(self.times, signal_ts, side="right") - 1)
        if current_index < 0 or signal_index < 0 or current_index < signal_index:
            return _LifecycleObservation(
                0, False, *(math.nan for _ in range(len(self._COLUMNS) - 1)), math.nan
            )
        observed = int(self.times[current_index])
        age_seconds = (int(ts_event) - observed) / 1_000_000_000.0
        if age_seconds < -1e-9:
            raise RuntimeError("future lifecycle feature reached Candidate 55")
        current_price = float(self.values["trade_vwap_60s"][current_index])
        signal_price = float(self.values["trade_vwap_60s"][signal_index])
        numbers = {
            name: float(self.values[name][current_index])
            for name in self._COLUMNS
            if name != "trade_vwap_60s"
        }
        ready = (
            age_seconds <= float(max_age_seconds)
            and bool(self.ready[current_index])
            and bool(self.ready[signal_index])
            and math.isfinite(current_price)
            and math.isfinite(signal_price)
            and signal_price > 0.0
            and all(math.isfinite(value) for value in numbers.values())
        )
        return _LifecycleObservation(
            observed,
            ready,
            current_price / signal_price - 1.0 if ready else math.nan,
            numbers["flow_15s"],
            numbers["flow_60s"],
            numbers["efficiency_60s"],
            numbers["absorption_60s"],
            numbers["bid_depth_change_1_1m"],
            numbers["depth_imbalance_2"],
        )


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """V15 relative-basis shorts with a causal early acceptance lifecycle."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        mode = str(config.v15_lifecycle_mode).strip().lower()
        if mode not in {
            "source",
            "accept_flow_1",
            "accept_depth_3",
            "accept_strict_3",
        }:
            raise ValueError(f"unsupported V15 lifecycle mode: {mode}")
        expected_deadline = 1 if mode == "accept_flow_1" else 3
        if mode != "source" and int(config.v15_acceptance_deadline_minutes) != expected_deadline:
            raise ValueError(
                f"{mode} requires deadline {expected_deadline}, got "
                f"{config.v15_acceptance_deadline_minutes}"
            )
        self._lifecycle_mode = mode
        self._lifecycle_features: dict[str, _LifecycleFeatureStore] = {}
        self._acceptance_bound = False
        self._acceptance_satisfied = False
        self._acceptance_signal_ts: int | None = None
        self._acceptance_level: float | None = None
        self._acceptance_last_age = -1
        self.diagnostics.update(
            {
                "candidate55_research_question": (
                    "CAN_THE_V15_BB_GROSS_PROFIT_ENGINE_BE_PRESERVED_WHILE_"
                    "FULL_STOPS_ARE_REPLACED_BY_A_CAUSAL_ACCEPTANCE_LIFECYCLE"
                ),
                "v15_lifecycle_mode": mode,
                "v15_acceptance_deadline_minutes": int(
                    config.v15_acceptance_deadline_minutes
                ),
                "acceptance_positions_bound": 0,
                "acceptance_checks": 0,
                "acceptance_feature_stale": 0,
                "acceptance_satisfied": 0,
                "acceptance_failed_exits": 0,
                "acceptance_price_failures": 0,
                "acceptance_relative_failures": 0,
                "acceptance_flow_failures": 0,
                "acceptance_depth_failures": 0,
                "acceptance_strict_failures": 0,
                "source_side_relearned": 0,
                "source_entry_thresholds_changed": 0,
                "source_stop_changed": 0,
                "source_trailing_changed": 0,
                "one_global_slot": 1,
                "complete_feature_minutes_only": 1,
            }
        )

    @property
    def _lifecycle_enabled(self) -> bool:
        return self._lifecycle_mode != "source"

    def on_start(self) -> None:
        super().on_start()
        self._lifecycle_features = {
            symbol: _LifecycleFeatureStore(path)
            for symbol, path in self.feature_paths.items()
        }

    def on_position_opened(self, event) -> None:
        super().on_position_opened(event)
        if not self._lifecycle_enabled:
            return
        scenario = self.current_scenario or {}
        diagnostics = scenario.get("diagnostics", {})
        signal_ts = int(scenario.get("episode_ts") or 0)
        level = float(diagnostics.get("lower", math.nan))
        side = int(scenario.get("side", 0))
        is_bb = int(diagnostics.get("used_bb_component", 0)) == 1
        if side != -1 or not is_bb or signal_ts <= 0 or not math.isfinite(level):
            self._acceptance_bound = False
            return
        self._acceptance_bound = True
        self._acceptance_satisfied = False
        self._acceptance_signal_ts = signal_ts
        self._acceptance_level = level
        self._acceptance_last_age = -1
        self.diagnostics["acceptance_positions_bound"] += 1
        self._event(
            "V15_ACCEPTANCE_LIFECYCLE_BOUND",
            int(getattr(event, "ts_event", signal_ts)),
            symbol=self.current_symbol,
            signal_ts=signal_ts,
            accepted_level=level,
            deadline_minutes=int(self.config.v15_acceptance_deadline_minutes),
            lifecycle_mode=self._lifecycle_mode,
        )

    def _acceptance_state(self, ts_event: int) -> tuple[bool, dict[str, float | int | bool]]:
        if self.current_symbol is None or self._acceptance_signal_ts is None:
            return False, {"ready": False}
        observations = {
            symbol: self._lifecycle_features[symbol].observation(
                ts_event,
                self._acceptance_signal_ts,
                self.config.feature_max_age_seconds,
            )
            for symbol in SYMBOLS
        }
        if not all(item.ready for item in observations.values()):
            return False, {"ready": False}
        symbol = self.current_symbol
        target = observations[symbol]
        peer_returns = [
            item.return_since_signal
            for peer, item in observations.items()
            if peer != symbol
        ]
        peer_median = float(np.median(peer_returns))
        aligned_relative = -(float(target.return_since_signal) - peer_median)
        market_returns = [float(item.return_since_signal) for item in observations.values()]
        market_median = float(np.median(market_returns))
        sell_breadth = float(np.mean([value < 0.0 for value in market_returns]))
        bar = self.bars[symbol][-1]
        level = float(self._acceptance_level)
        price_accepted = float(bar.close) < level
        relative_accepted = aligned_relative > 0.0
        flow_accepted = float(target.flow_60s) < 0.0
        depth_accepted = float(target.bid_depth_change_1_1m) < 0.0
        span = max(float(bar.high) - float(bar.low), 1e-12)
        close_location = (float(bar.high) - float(bar.close)) / span
        strict_accepted = (
            float(target.flow_15s) < 0.0
            and float(bar.close) < float(bar.open)
            and close_location >= 0.5
        )
        accepted = price_accepted and relative_accepted and flow_accepted
        if self._lifecycle_mode in {"accept_depth_3", "accept_strict_3"}:
            accepted = accepted and depth_accepted
        if self._lifecycle_mode == "accept_strict_3":
            accepted = accepted and strict_accepted
        return bool(accepted), {
            "ready": True,
            "price_accepted": price_accepted,
            "relative_accepted": relative_accepted,
            "flow_accepted": flow_accepted,
            "depth_accepted": depth_accepted,
            "strict_accepted": strict_accepted,
            "accepted_level": level,
            "bar_open": float(bar.open),
            "bar_high": float(bar.high),
            "bar_low": float(bar.low),
            "bar_close": float(bar.close),
            "close_location": close_location,
            "target_return_since_signal": float(target.return_since_signal),
            "peer_median_return_since_signal": peer_median,
            "aligned_relative_since_signal": aligned_relative,
            "market_median_return_since_signal": market_median,
            "sell_breadth_since_signal": sell_breadth,
            "flow_15s": float(target.flow_15s),
            "flow_60s": float(target.flow_60s),
            "efficiency_60s": float(target.efficiency_60s),
            "absorption_60s": float(target.absorption_60s),
            "bid_depth_change_1_1m": float(target.bid_depth_change_1_1m),
            "depth_imbalance_2": float(target.depth_imbalance_2),
            "observed_time_ns": int(target.observed_time_ns),
        }

    def _manage_open_position(self, ts_event: int) -> None:
        if (
            self._lifecycle_enabled
            and self._acceptance_bound
            and not self._acceptance_satisfied
            and self.current_symbol is not None
            and self.position_open_minute >= 0
        ):
            age = int(self.minute_index) - int(self.position_open_minute)
            deadline = int(self.config.v15_acceptance_deadline_minutes)
            if age >= 1 and age != self._acceptance_last_age:
                self._acceptance_last_age = age
                self.diagnostics["acceptance_checks"] += 1
                accepted, details = self._acceptance_state(ts_event)
                if not bool(details.get("ready")):
                    self.diagnostics["acceptance_feature_stale"] += 1
                elif accepted:
                    self._acceptance_satisfied = True
                    self.diagnostics["acceptance_satisfied"] += 1
                    if self.current_scenario is not None:
                        self.current_scenario.update(
                            {
                                "v15_lifecycle_mode": self._lifecycle_mode,
                                "acceptance_age_minutes": age,
                                "acceptance_details": details,
                            }
                        )
                    self._event(
                        "V15_ACCEPTANCE_SATISFIED",
                        ts_event,
                        age_minutes=age,
                        **details,
                    )
                elif age >= deadline:
                    if not bool(details.get("price_accepted")):
                        self.diagnostics["acceptance_price_failures"] += 1
                    if not bool(details.get("relative_accepted")):
                        self.diagnostics["acceptance_relative_failures"] += 1
                    if not bool(details.get("flow_accepted")):
                        self.diagnostics["acceptance_flow_failures"] += 1
                    if self._lifecycle_mode in {"accept_depth_3", "accept_strict_3"} and not bool(details.get("depth_accepted")):
                        self.diagnostics["acceptance_depth_failures"] += 1
                    if self._lifecycle_mode == "accept_strict_3" and not bool(details.get("strict_accepted")):
                        self.diagnostics["acceptance_strict_failures"] += 1
                    instrument_id = self.instrument_ids[self.current_symbol]
                    self.cancel_all_orders(instrument_id)
                    self.close_all_positions(instrument_id)
                    self.diagnostics["acceptance_failed_exits"] += 1
                    self._event(
                        "V15_ACCEPTANCE_FAILED_EXIT",
                        ts_event,
                        age_minutes=age,
                        lifecycle_mode=self._lifecycle_mode,
                        **details,
                    )
                    return
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._acceptance_bound = False
        self._acceptance_satisfied = False
        self._acceptance_signal_ts = None
        self._acceptance_level = None
        self._acceptance_last_age = -1


__all__ = ["Candidate35Config", "Candidate35Strategy"]
