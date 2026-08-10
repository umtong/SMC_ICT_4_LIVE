"""Accepted-auction repair for the V15 short opportunity engine.

This module keeps the source V15 entry, stop, trailing and 3% risk sizing, but
routes only short Bollinger-origin episodes that satisfy three independent
state requirements before the global winner is selected:

1. moderate one-hour underperformance versus the peer median (follower state),
2. no sharp five-minute perpetual-premium collapse (not derivative-led),
3. efficient price discovery or low absorption on the completed signal minute.

The filter is applied to every symbol before one-slot arbitration.  Removing a
loser therefore exposes the next eligible causal opportunity rather than merely
editing an already selected trade log.
"""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import math
from pathlib import Path
import sys
from typing import NamedTuple

import numpy as np
import pandas as pd

from strategy_base import SYMBOLS


_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v15_relative.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v15_accepted_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load V15 relative execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    v15_accepted_mode: str = "source_short"
    v15_acceptance_efficiency_min: float = 0.007
    v15_acceptance_absorption_max: float = 0.37


class _AcceptedObservation(NamedTuple):
    observed_time_ns: int
    ready: bool
    return_fraction: float
    premium_change_5m: float
    efficiency_60s: float
    absorption_60s: float


class _AcceptedFeatureStore:
    def __init__(self, path: Path) -> None:
        frame = pd.read_csv(
            path,
            compression="infer",
            usecols=[
                "observed_time_ns",
                "feature_ready",
                "trade_vwap_60s",
                "premium_change_5m",
                "efficiency_60s",
                "absorption_60s",
            ],
        )
        self.times = (
            pd.to_numeric(frame["observed_time_ns"], errors="raise")
            .astype("int64")
            .to_numpy(copy=True)
        )
        if self.times.size == 0 or np.any(np.diff(self.times) <= 0):
            raise RuntimeError(f"invalid accepted-auction feature clock: {path}")
        self.ready = (
            frame["feature_ready"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"true", "1", "yes"})
            .to_numpy(dtype=np.bool_, copy=True)
        )
        self.price = pd.to_numeric(
            frame["trade_vwap_60s"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)
        self.premium = pd.to_numeric(
            frame["premium_change_5m"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)
        self.efficiency = pd.to_numeric(
            frame["efficiency_60s"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)
        self.absorption = pd.to_numeric(
            frame["absorption_60s"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)

    def observation(
        self,
        ts_event: int,
        lookback_minutes: int,
        max_age_seconds: float,
    ) -> _AcceptedObservation:
        index = int(np.searchsorted(self.times, ts_event, side="right") - 1)
        lag = index - int(lookback_minutes)
        if index < 0 or lag < 0:
            return _AcceptedObservation(
                0, False, math.nan, math.nan, math.nan, math.nan
            )
        observed = int(self.times[index])
        age = (int(ts_event) - observed) / 1_000_000_000.0
        if age < -1e-9:
            raise RuntimeError("future accepted-auction feature reached Candidate 55")
        current = float(self.price[index])
        previous = float(self.price[lag])
        premium = float(self.premium[index])
        efficiency = float(self.efficiency[index])
        absorption = float(self.absorption[index])
        ready = (
            age <= float(max_age_seconds)
            and bool(self.ready[index])
            and bool(self.ready[lag])
            and math.isfinite(current)
            and math.isfinite(previous)
            and previous > 0.0
            and math.isfinite(premium)
            and math.isfinite(efficiency)
            and math.isfinite(absorption)
        )
        return _AcceptedObservation(
            observed,
            ready,
            current / previous - 1.0 if ready else math.nan,
            premium,
            efficiency,
            absorption,
        )


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """V15 short router with causal relative/basis/acceptance state."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        mode = str(config.v15_accepted_mode).strip().lower()
        if mode not in {
            "source_short",
            "relative_basis_short",
            "accepted_efficiency",
            "accepted_absorption",
        }:
            raise ValueError(f"unsupported V15 accepted mode: {mode}")
        self._accepted_mode = mode
        self._accepted_features: dict[str, _AcceptedFeatureStore] = {}
        self.diagnostics.update(
            {
                "candidate55_research_question": (
                    "CAN_V15_BB_GROSS_PROFIT_SURVIVE_AFTER_DI_LOSS_RELATIVE_"
                    "EXHAUSTION_DERIVATIVE_LEAD_AND_ABSORPTION_ARE_REMOVED"
                ),
                "v15_accepted_mode": mode,
                "accepted_source_actionable": 0,
                "accepted_direction_rejections": 0,
                "accepted_component_rejections": 0,
                "accepted_feature_stale": 0,
                "accepted_relative_rejections": 0,
                "accepted_basis_rejections": 0,
                "accepted_efficiency_rejections": 0,
                "accepted_absorption_rejections": 0,
                "accepted_eligible": 0,
                "source_stop_changed": 0,
                "source_trailing_changed": 0,
                "one_global_slot": 1,
                "complete_feature_minutes_only": 1,
            }
        )

    def on_start(self) -> None:
        super().on_start()
        self._accepted_features = {
            symbol: _AcceptedFeatureStore(path)
            for symbol, path in self.feature_paths.items()
        }

    def _relative_decisions(self, decisions, ts_event: int):
        actionable = [item for item in decisions.values() if item.actionable]
        self.diagnostics["accepted_source_actionable"] += len(actionable)
        short_actionable = []
        for decision in actionable:
            if int(decision.side) != -1:
                self.diagnostics["accepted_direction_rejections"] += 1
                continue
            if self._accepted_mode == "source_short":
                short_actionable.append(decision)
                continue
            diagnostics = dict(decision.diagnostics)
            if int(diagnostics.get("used_bb_component", 0)) != 1:
                self.diagnostics["accepted_component_rejections"] += 1
                continue
            short_actionable.append(decision)
        if self._accepted_mode == "source_short":
            return short_actionable

        observations = {
            symbol: self._accepted_features[symbol].observation(
                ts_event,
                int(self.config.v15_relative_lookback_minutes),
                self.config.feature_max_age_seconds,
            )
            for symbol in SYMBOLS
        }
        if not all(item.ready for item in observations.values()):
            self.diagnostics["accepted_feature_stale"] += len(short_actionable)
            return []

        minimum = float(self.config.v15_relative_min_fraction)
        maximum = float(self.config.v15_relative_max_fraction)
        basis_minimum = float(self.config.v15_short_min_premium_change_5m)
        eligible = []
        for decision in short_actionable:
            observation = observations[decision.symbol]
            peers = [
                observations[symbol].return_fraction
                for symbol in SYMBOLS
                if symbol != decision.symbol
            ]
            peer_median = float(np.median(peers))
            residual = float(observation.return_fraction) - peer_median
            aligned = -residual
            if not (minimum <= aligned <= maximum):
                self.diagnostics["accepted_relative_rejections"] += 1
                continue
            if float(observation.premium_change_5m) < basis_minimum:
                self.diagnostics["accepted_basis_rejections"] += 1
                continue
            if (
                self._accepted_mode == "accepted_efficiency"
                and float(observation.efficiency_60s)
                < float(self.config.v15_acceptance_efficiency_min)
            ):
                self.diagnostics["accepted_efficiency_rejections"] += 1
                continue
            if (
                self._accepted_mode == "accepted_absorption"
                and float(observation.absorption_60s)
                > float(self.config.v15_acceptance_absorption_max)
            ):
                self.diagnostics["accepted_absorption_rejections"] += 1
                continue
            diagnostics = dict(decision.diagnostics)
            diagnostics.update(
                {
                    "relative_lookback_minutes": int(
                        self.config.v15_relative_lookback_minutes
                    ),
                    "target_return_fraction": float(
                        observation.return_fraction
                    ),
                    "peer_median_return_fraction": peer_median,
                    "relative_residual_fraction": residual,
                    "side_aligned_relative_fraction": aligned,
                    "signal_premium_change_5m": float(
                        observation.premium_change_5m
                    ),
                    "signal_efficiency_60s": float(
                        observation.efficiency_60s
                    ),
                    "signal_absorption_60s": float(
                        observation.absorption_60s
                    ),
                    "accepted_auction_state": 1,
                }
            )
            eligible.append(replace(decision, diagnostics=diagnostics))
        self.diagnostics["accepted_eligible"] += len(eligible)
        return eligible


__all__ = ["Candidate35Config", "Candidate35Strategy"]
