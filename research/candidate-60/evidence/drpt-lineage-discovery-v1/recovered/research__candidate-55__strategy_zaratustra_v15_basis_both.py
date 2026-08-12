"""Two-sided V15 router with causal derivative-lead rejection.

A directional source edge is accepted only when the completed five-minute
perpetual premium is not accelerating in the same direction as the trade:

* short: reject sharp premium compression below ``short_min``;
* long: reject sharp premium expansion above ``long_max``.

This preserves the source's price/DI/Bollinger opportunity detector while
removing crowded derivative-led moves.  Long and short signals still compete
inside one NautilusTrader process and one global account slot.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import NamedTuple

import numpy as np
import pandas as pd


_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v15.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v15_basis_both_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load V15 execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    v15_basis_both_mode: str = "source"
    v15_short_min_premium_change_5m: float = -0.00005
    v15_long_max_premium_change_5m: float = 0.0


class _PremiumObservation(NamedTuple):
    observed_time_ns: int
    ready: bool
    premium_change_5m: float


class _PremiumFeatureStore:
    def __init__(self, path: Path) -> None:
        frame = pd.read_csv(
            path,
            compression="infer",
            usecols=[
                "observed_time_ns",
                "feature_ready",
                "premium_change_5m",
            ],
        )
        self.times = (
            pd.to_numeric(frame["observed_time_ns"], errors="raise")
            .astype("int64")
            .to_numpy(copy=True)
        )
        if self.times.size == 0 or np.any(np.diff(self.times) <= 0):
            raise RuntimeError(f"invalid premium feature clock: {path}")
        self.ready = (
            frame["feature_ready"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"true", "1", "yes"})
            .to_numpy(dtype=np.bool_, copy=True)
        )
        self.change = pd.to_numeric(
            frame["premium_change_5m"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)

    def observation(
        self, ts_event: int, max_age_seconds: float
    ) -> _PremiumObservation:
        index = int(np.searchsorted(self.times, ts_event, side="right") - 1)
        if index < 0:
            return _PremiumObservation(0, False, math.nan)
        observed = int(self.times[index])
        age = (int(ts_event) - observed) / 1_000_000_000.0
        if age < -1e-9:
            raise RuntimeError("future premium observation reached Candidate 55")
        value = float(self.change[index])
        ready = (
            age <= float(max_age_seconds)
            and bool(self.ready[index])
            and math.isfinite(value)
        )
        return _PremiumObservation(observed, ready, value)


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """One-account two-sided V15 source with causal basis-state routing."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        mode = str(config.v15_basis_both_mode).strip().lower()
        if mode not in {"source", "basis"}:
            raise ValueError(f"unsupported V15 basis-both mode: {mode}")
        self._basis_both_mode = mode
        self._premium_features: dict[str, _PremiumFeatureStore] = {}
        self.diagnostics.update(
            {
                "candidate": "candidate-55",
                "candidate55_research_question": (
                    "CAN_SPOT_LED_V15_LONG_AND_SHORT_FAMILIES_SHARE_ONE_SLOT_"
                    "AFTER_DERIVATIVE_LEAD_REJECTION"
                ),
                "v15_basis_both_mode": mode,
                "v15_short_min_premium_change_5m": float(
                    config.v15_short_min_premium_change_5m
                ),
                "v15_long_max_premium_change_5m": float(
                    config.v15_long_max_premium_change_5m
                ),
                "basis_both_checks": 0,
                "basis_both_accepts": 0,
                "basis_both_short_rejections": 0,
                "basis_both_long_rejections": 0,
                "basis_both_feature_stale": 0,
                "source_entry_thresholds_changed": 0,
                "source_stop_changed": 0,
                "source_trailing_changed": 0,
                "one_global_slot": 1,
                "complete_feature_minutes_only": 1,
            }
        )

    def on_start(self) -> None:
        super().on_start()
        self._premium_features = {
            symbol: _PremiumFeatureStore(path)
            for symbol, path in self.feature_paths.items()
        }

    def _submit_decision(self, decision, ts_event: int) -> None:
        if self._basis_both_mode == "source":
            super()._submit_decision(decision, ts_event)
            return
        observation = self._premium_features[decision.symbol].observation(
            ts_event, self.config.feature_max_age_seconds
        )
        self.diagnostics["basis_both_checks"] += 1
        if not observation.ready:
            self.diagnostics["basis_both_feature_stale"] += 1
            self._event(
                "V15_BASIS_BOTH_FEATURE_STALE",
                ts_event,
                symbol=decision.symbol,
                side=int(decision.side),
                observed_time_ns=observation.observed_time_ns,
            )
            return
        change = float(observation.premium_change_5m)
        if int(decision.side) < 0 and change < float(
            self.config.v15_short_min_premium_change_5m
        ):
            self.diagnostics["basis_both_short_rejections"] += 1
            self._event(
                "V15_DERIVATIVE_LED_SHORT_REJECTED",
                ts_event,
                symbol=decision.symbol,
                premium_change_5m=change,
            )
            return
        if int(decision.side) > 0 and change > float(
            self.config.v15_long_max_premium_change_5m
        ):
            self.diagnostics["basis_both_long_rejections"] += 1
            self._event(
                "V15_DERIVATIVE_LED_LONG_REJECTED",
                ts_event,
                symbol=decision.symbol,
                premium_change_5m=change,
            )
            return
        before = int(self.diagnostics.get("entry_submissions", 0))
        super()._submit_decision(decision, ts_event)
        if int(self.diagnostics.get("entry_submissions", 0)) > before:
            self.diagnostics["basis_both_accepts"] += 1
            if self.current_scenario is not None:
                self.current_scenario.update(
                    {
                        "v15_basis_both_mode": self._basis_both_mode,
                        "signal_premium_change_5m": change,
                        "premium_observed_time_ns": int(
                            observation.observed_time_ns
                        ),
                    }
                )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
