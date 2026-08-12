"""Causal auction-state gate for the source-faithful DRPT daily-low reclaim.

The public/source decision mechanism remains unchanged:

    15m 1-2% dump -> prior seven-day low break -> source trend filters
    -> completed 1m close above dump high + 0.5 ATR

This adapter asks one additional, economically distinct question before the
NautilusTrader execution shell may use that reclaim:

    is price reclaiming while residual sell pressure is being absorbed,
    rather than because buyers are chasing an already-efficient rebound?

The gate is self-normalized.  It compares the current symbol's absorption to
its strictly-prior 240 completed one-minute observations, requires the symbol
to rank in the top two of the four-asset universe at the same completed minute,
and requires at least one of the current 10s/60s/3m aggressor-flow measures to
remain negative.  No result, trade ID, symbol allowlist or period label is read.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from strategy_base import SYMBOLS
from strategy_drpt_source_selector import Candidate35Config as _SourceConfig
from strategy_drpt_source_selector import Candidate35Strategy as _SourceStrategy


_SYMBOL_PRIORITY = {symbol: index for index, symbol in enumerate(SYMBOLS)}
STATE_REASON = "RESIDUAL_SELL_PRESSURE_ABSORBED_IN_TOP_TWO_UNIVERSE"


@dataclass(frozen=True, slots=True)
class _AuctionObservation:
    observed_time_ns: int
    ready: bool
    absorption_60s: float = math.nan
    prior_absorption_median: float = math.nan
    prior_valid_count: int = 0
    flow_open_10s: float = math.nan
    flow_60s: float = math.nan
    flow_3m: float = math.nan


class _AuctionFeatureStore:
    """Minimal causal feature view for the frozen DRPT auction-state gate."""

    _COLUMNS = (
        "observed_time_ns",
        "feature_ready",
        "absorption_60s",
        "flow_open_10s",
        "flow_60s",
        "flow_3m",
    )

    def __init__(self, path: Path) -> None:
        header = list(pd.read_csv(path, compression="infer", nrows=0).columns)
        missing = sorted(set(self._COLUMNS) - set(header))
        if missing:
            raise RuntimeError(f"DRPT absorption gate missing features {missing}: {path}")
        frame = pd.read_csv(path, compression="infer", usecols=list(self._COLUMNS))
        self.times = pd.to_numeric(frame["observed_time_ns"], errors="raise").astype("int64").to_numpy(copy=True)
        if self.times.size == 0 or np.any(np.diff(self.times) <= 0):
            raise RuntimeError(f"feature times must be non-empty, unique and monotonic: {path}")
        self.ready = (
            frame["feature_ready"].astype(str).str.strip().str.lower().isin({"true", "1", "yes"})
        ).to_numpy(dtype=np.bool_, copy=True)
        self.values = {
            name: pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=np.float64, copy=True)
            for name in self._COLUMNS
            if name not in {"observed_time_ns", "feature_ready"}
        }

    def observation(
        self,
        ts_event: int,
        *,
        baseline_minutes: int,
        minimum_valid: int,
        max_age_seconds: float,
    ) -> _AuctionObservation:
        index = int(np.searchsorted(self.times, int(ts_event), side="right") - 1)
        if index < 0:
            return _AuctionObservation(0, False)
        observed = int(self.times[index])
        age = (int(ts_event) - observed) / 1_000_000_000
        if age < -1e-9:
            raise RuntimeError("future feature observation reached DRPT absorption gate")
        if age > float(max_age_seconds) or not bool(self.ready[index]):
            return _AuctionObservation(observed, False)

        absorption = float(self.values["absorption_60s"][index])
        start = max(0, index - int(baseline_minutes))
        prior = self.values["absorption_60s"][start:index]
        clean = prior[np.isfinite(prior)]
        if not math.isfinite(absorption) or clean.size < int(minimum_valid):
            return _AuctionObservation(
                observed,
                False,
                absorption_60s=absorption,
                prior_valid_count=int(clean.size),
            )

        def value(name: str) -> float:
            number = float(self.values[name][index])
            return number if math.isfinite(number) else math.nan

        return _AuctionObservation(
            observed_time_ns=observed,
            ready=True,
            absorption_60s=absorption,
            prior_absorption_median=float(np.median(clean)),
            prior_valid_count=int(clean.size),
            flow_open_10s=value("flow_open_10s"),
            flow_60s=value("flow_60s"),
            flow_3m=value("flow_3m"),
        )


class Candidate35Config(_SourceConfig, frozen=True):
    drpt_absorption_baseline_minutes: int = 240
    drpt_absorption_minimum_valid: int = 120
    drpt_absorption_top_universe_rank: int = 2
    drpt_absorption_require_residual_sell: bool = True


class Candidate35Strategy(_SourceStrategy):
    """Source DRPT policy plus a causal passive-transfer auction-state gate."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        frozen: dict[str, int | bool] = {
            "drpt_absorption_baseline_minutes": 240,
            "drpt_absorption_minimum_valid": 120,
            "drpt_absorption_top_universe_rank": 2,
            "drpt_absorption_require_residual_sell": True,
        }
        for key, expected in frozen.items():
            actual = getattr(config, key)
            if isinstance(expected, bool):
                if bool(actual) is not expected:
                    raise ValueError(f"frozen {key} must be {expected}, received {actual}")
            elif int(actual) != expected:
                raise ValueError(f"frozen {key} must be {expected}, received {actual}")
        self.auction_features: dict[str, _AuctionFeatureStore] = {}
        self._auction_first_passed_episodes: set[tuple[str, int]] = set()
        self.diagnostics.update(
            {
                "candidate": "candidate-60-drpt-absorption-gate-v1",
                "source_policy_sha256": "ef738712337eea113a47bb2b3ce1e3b402a00d203111633d6ac036685822ff23",
                "auction_gate": {
                    "baseline_minutes": 240,
                    "minimum_valid": 120,
                    "minimum_absorption": "strictly-prior rolling median",
                    "maximum_cross_asset_rank": 2,
                    "residual_pressure": "any of flow_open_10s, flow_60s, flow_3m < 0",
                },
                "auction_gate_evaluations": 0,
                "auction_gate_passes": 0,
                "auction_gate_first_episode_passes": 0,
                "auction_gate_feature_rejections": 0,
                "auction_gate_absorption_rejections": 0,
                "auction_gate_rank_rejections": 0,
                "auction_gate_pressure_rejections": 0,
                "auction_gate_passes_by_symbol": {},
            }
        )

    def on_start(self) -> None:
        super().on_start()
        self.auction_features = {
            symbol: _AuctionFeatureStore(self.feature_paths[symbol])
            for symbol in SYMBOLS
        }

    @staticmethod
    def _residual_sell(observation: _AuctionObservation) -> bool:
        values = (
            observation.flow_open_10s,
            observation.flow_60s,
            observation.flow_3m,
        )
        return any(math.isfinite(value) and value < 0.0 for value in values)

    def _auction_observations(self, ts_event: int) -> dict[str, _AuctionObservation]:
        return {
            symbol: self.auction_features[symbol].observation(
                ts_event,
                baseline_minutes=int(self.config.drpt_absorption_baseline_minutes),
                minimum_valid=int(self.config.drpt_absorption_minimum_valid),
                max_age_seconds=float(self.config.feature_max_age_seconds),
            )
            for symbol in SYMBOLS
        }

    def _reclaim_candidates(self, ts_event: int):
        source_candidates = super()._reclaim_candidates(ts_event)
        if not source_candidates:
            return []

        observations = self._auction_observations(ts_event)
        finite_universe = [
            (symbol, observation.absorption_60s)
            for symbol, observation in observations.items()
            if observation.ready and math.isfinite(observation.absorption_60s)
        ]
        ranks: dict[str, int] = {}
        if len(finite_universe) == len(SYMBOLS):
            ordered = sorted(
                finite_universe,
                key=lambda item: (-float(item[1]), _SYMBOL_PRIORITY[item[0]]),
            )
            ranks = {symbol: rank for rank, (symbol, _) in enumerate(ordered, start=1)}

        accepted = []
        for decision, arm, target_diagnostics in source_candidates:
            self.diagnostics["auction_gate_evaluations"] += 1
            observation = observations.get(decision.symbol, _AuctionObservation(0, False))
            if not observation.ready or decision.symbol not in ranks:
                self.diagnostics["auction_gate_feature_rejections"] += 1
                continue

            absorption_pass = (
                observation.absorption_60s >= observation.prior_absorption_median
            )
            if not absorption_pass:
                self.diagnostics["auction_gate_absorption_rejections"] += 1
                continue

            rank = int(ranks[decision.symbol])
            if rank > int(self.config.drpt_absorption_top_universe_rank):
                self.diagnostics["auction_gate_rank_rejections"] += 1
                continue

            residual_sell = self._residual_sell(observation)
            if bool(self.config.drpt_absorption_require_residual_sell) and not residual_sell:
                self.diagnostics["auction_gate_pressure_rejections"] += 1
                continue

            self.diagnostics["auction_gate_passes"] += 1
            key = (decision.symbol, int(decision.episode_ts))
            if key not in self._auction_first_passed_episodes:
                self._auction_first_passed_episodes.add(key)
                self.diagnostics["auction_gate_first_episode_passes"] += 1
            counts = self.diagnostics["auction_gate_passes_by_symbol"]
            counts[decision.symbol] = int(counts.get(decision.symbol, 0)) + 1

            diagnostics: dict[str, Any] = dict(decision.diagnostics)
            diagnostics.update(
                {
                    "auction_absorption_60s": observation.absorption_60s,
                    "auction_prior_absorption_median_240m": observation.prior_absorption_median,
                    "auction_prior_valid_count": observation.prior_valid_count,
                    "auction_absorption_ratio_to_median": (
                        observation.absorption_60s
                        / max(observation.prior_absorption_median, 1e-12)
                    ),
                    "auction_cross_asset_absorption_rank": rank,
                    "auction_flow_open_10s": observation.flow_open_10s,
                    "auction_flow_60s": observation.flow_60s,
                    "auction_flow_3m": observation.flow_3m,
                    "auction_residual_sell_pressure": int(residual_sell),
                }
            )
            accepted.append(
                (
                    replace(
                        decision,
                        score=float(decision.score)
                        + min(
                            2.0,
                            observation.absorption_60s
                            / max(observation.prior_absorption_median, 1e-12),
                        )
                        + (len(SYMBOLS) - rank + 1) / len(SYMBOLS),
                        reasons=tuple(decision.reasons) + (STATE_REASON,),
                        diagnostics=diagnostics,
                    ),
                    arm,
                    target_diagnostics,
                )
            )
        return accepted


__all__ = ["Candidate35Config", "Candidate35Strategy", "_AuctionFeatureStore"]
