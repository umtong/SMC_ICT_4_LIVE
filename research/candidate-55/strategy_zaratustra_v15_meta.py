"""Meta-labeling repair for the V15 accepted-auction family.

The primary V15 side and geometry are not relearned.  This module reuses the
meta-labeling idea: the source/relative/basis policy proposes a short, while a
small frozen causal model predicts whether that particular episode should be
acted on.  Coefficients, normalization and threshold are supplied by the
research runner after period-blocked development and are then frozen for new
NautilusTrader replays.
"""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import NamedTuple

import numpy as np
import pandas as pd


_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v15_accepted.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v15_meta_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load accepted-auction execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    v15_meta_feature_names_json: str = "[]"
    v15_meta_means_json: str = "[]"
    v15_meta_scales_json: str = "[]"
    v15_meta_coefficients_json: str = "[]"
    v15_meta_intercept: float = 0.0
    v15_meta_threshold: float = 0.0


class _MetaObservation(NamedTuple):
    observed_time_ns: int
    ready: bool
    flow_3m: float
    ret_60s_bps: float
    efficiency_60s: float
    absorption_60s: float
    notional_burst: float
    trade_count_burst: float
    oi_change_15m: float
    premium_change_5m: float


class _MetaFeatureStore:
    _COLUMNS = (
        "flow_3m",
        "ret_60s_bps",
        "efficiency_60s",
        "absorption_60s",
        "notional_burst",
        "trade_count_burst",
        "oi_change_15m",
        "premium_change_5m",
    )

    def __init__(self, path: Path) -> None:
        header = list(pd.read_csv(path, compression="infer", nrows=0).columns)
        missing = [name for name in self._COLUMNS if name not in header]
        if missing:
            raise RuntimeError(f"missing meta features {missing}: {path}")
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
            raise RuntimeError(f"invalid meta feature clock: {path}")
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

    def observation(self, ts_event: int, max_age_seconds: float) -> _MetaObservation:
        index = int(np.searchsorted(self.times, ts_event, side="right") - 1)
        if index < 0:
            return _MetaObservation(0, False, *(math.nan for _ in self._COLUMNS))
        observed = int(self.times[index])
        age = (int(ts_event) - observed) / 1_000_000_000.0
        if age < -1e-9:
            raise RuntimeError("future meta feature reached Candidate 55")
        numbers = [float(self.values[name][index]) for name in self._COLUMNS]
        ready = (
            age <= float(max_age_seconds)
            and bool(self.ready[index])
            and all(math.isfinite(value) for value in numbers)
        )
        return _MetaObservation(observed, ready, *numbers)


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """Frozen expected-R meta-label and arbitration over accepted V15 shorts."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        names = list(json.loads(config.v15_meta_feature_names_json))
        means = np.asarray(json.loads(config.v15_meta_means_json), dtype=float)
        scales = np.asarray(json.loads(config.v15_meta_scales_json), dtype=float)
        coefficients = np.asarray(
            json.loads(config.v15_meta_coefficients_json), dtype=float
        )
        if not names or not (
            len(names) == means.size == scales.size == coefficients.size
        ):
            raise ValueError("invalid frozen V15 meta model dimensions")
        if np.any(~np.isfinite(means)) or np.any(~np.isfinite(scales)):
            raise ValueError("non-finite V15 meta normalization")
        if np.any(scales <= 0.0) or np.any(~np.isfinite(coefficients)):
            raise ValueError("invalid V15 meta scales or coefficients")
        self._meta_names = tuple(str(name) for name in names)
        self._meta_means = means
        self._meta_scales = scales
        self._meta_coefficients = coefficients
        self._meta_intercept = float(config.v15_meta_intercept)
        self._meta_threshold = float(config.v15_meta_threshold)
        self._meta_features: dict[str, _MetaFeatureStore] = {}
        self.diagnostics.update(
            {
                "candidate55_research_question": (
                    "CAN_A_FROZEN_CAUSAL_META_LABEL_PRESERVE_V15_GROSS_PROFIT_"
                    "WHILE_ROUTING_OUT_FALSE_ACCEPTANCE"
                ),
                "v15_meta_features": list(self._meta_names),
                "v15_meta_intercept": self._meta_intercept,
                "v15_meta_threshold": self._meta_threshold,
                "meta_checks": 0,
                "meta_feature_stale": 0,
                "meta_rejections": 0,
                "meta_eligible": 0,
                "meta_alternative_symbol_selected": 0,
                "source_side_relearned": 0,
                "source_stop_changed": 0,
                "source_trailing_changed": 0,
                "one_global_slot": 1,
                "complete_feature_minutes_only": 1,
            }
        )

    def on_start(self) -> None:
        super().on_start()
        self._meta_features = {
            symbol: _MetaFeatureStore(path)
            for symbol, path in self.feature_paths.items()
        }

    def _feature_vector(self, decision, observation: _MetaObservation) -> np.ndarray:
        diagnostics = decision.diagnostics
        values = {
            "relative_aligned": float(
                diagnostics.get("side_aligned_relative_fraction", math.nan)
            ),
            "premium_change_5m": float(observation.premium_change_5m),
            "efficiency_60s": float(observation.efficiency_60s),
            "absorption_60s": float(observation.absorption_60s),
            "aligned_flow_3m": -float(observation.flow_3m),
            "aligned_ret_60s_bps": -float(observation.ret_60s_bps),
            "log_notional_burst": math.log1p(max(0.0, float(observation.notional_burst))),
            "log_trade_count_burst": math.log1p(max(0.0, float(observation.trade_count_burst))),
            "oi_change_15m": float(observation.oi_change_15m),
            "source_score": float(decision.score),
            "breakout_bps": float(diagnostics.get("breakout_bps", 0.0)),
        }
        vector = np.asarray([values[name] for name in self._meta_names], dtype=float)
        if np.any(~np.isfinite(vector)):
            raise RuntimeError(f"non-finite V15 meta vector: {values}")
        return vector

    def _relative_decisions(self, decisions, ts_event: int):
        # The parent is configured as relative+basis short.  It preserves the
        # source side/geometry and performs upstream per-symbol filtering.
        base = super()._relative_decisions(decisions, ts_event)
        if not base:
            return []
        eligible = []
        raw_best = max(base, key=lambda item: (float(item.score), item.symbol))
        for decision in base:
            observation = self._meta_features[decision.symbol].observation(
                ts_event, self.config.feature_max_age_seconds
            )
            self.diagnostics["meta_checks"] += 1
            if not observation.ready:
                self.diagnostics["meta_feature_stale"] += 1
                continue
            vector = self._feature_vector(decision, observation)
            standardized = (vector - self._meta_means) / self._meta_scales
            expected_r = self._meta_intercept + float(
                standardized @ self._meta_coefficients
            )
            if expected_r < self._meta_threshold:
                self.diagnostics["meta_rejections"] += 1
                continue
            diagnostics = dict(decision.diagnostics)
            diagnostics.update(
                {
                    "meta_expected_r": expected_r,
                    "meta_threshold": self._meta_threshold,
                    "meta_observed_time_ns": int(observation.observed_time_ns),
                    "meta_standardized_features": standardized.tolist(),
                }
            )
            # Expected R, not source pattern strength, owns arbitration among
            # simultaneously eligible symbols.
            eligible.append(
                replace(decision, score=expected_r, diagnostics=diagnostics)
            )
        self.diagnostics["meta_eligible"] += len(eligible)
        if eligible:
            selected = max(eligible, key=lambda item: (float(item.score), item.symbol))
            if selected.symbol != raw_best.symbol:
                self.diagnostics["meta_alternative_symbol_selected"] += 1
        return eligible


__all__ = ["Candidate35Config", "Candidate35Strategy"]
