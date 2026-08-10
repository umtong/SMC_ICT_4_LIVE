"""Immediate causal basis-state repair for the V15 short family.

The V15 source entry, stop and trailing policy are unchanged.  The only entry
repair rejects a source short when the completed five-minute perpetual premium
has already collapsed more than a small predeclared amount.  Such entries are
usually derivative-led/crowded breakdowns rather than spot-led acceptance and
were the dominant gross-loss state in the known tapes.

The threshold is expressed in the native premium-index fraction.  ``-0.00005``
means the premium may contract by at most 0.5 basis points over five minutes.
An optional DI-only failed-auction exit is kept separate so entry-state repair
and management repair can be diagnosed independently.
"""
from __future__ import annotations

from collections import deque
import importlib.util
import math
from pathlib import Path
import sys
from typing import NamedTuple

import numpy as np
import pandas as pd


_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v15_short.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v15_basis_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load V15 short execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    v15_basis_mode: str = "source"
    v15_min_premium_change_5m: float = -0.00005
    v15_failure_min_age_minutes: int = 20
    v15_failure_adverse_fraction: float = 0.002
    v15_failure_mfe_cap_fraction: float = 0.003
    v15_failure_window_minutes: int = 10
    v15_failure_flow_3m_min: float = 0.0
    v15_failure_return_bps_min: float = 0.0


class _BasisObservation(NamedTuple):
    observed_time_ns: int
    ready: bool
    premium_change_5m: float
    flow_3m: float
    ret_60s_bps: float


class _BasisFeatureStore:
    """Causal complete-minute view for entry basis and failed-auction state."""

    def __init__(self, path: Path) -> None:
        frame = pd.read_csv(
            path,
            compression="infer",
            usecols=[
                "observed_time_ns",
                "feature_ready",
                "premium_change_5m",
                "flow_3m",
                "ret_60s_bps",
            ],
        )
        self.times = (
            pd.to_numeric(frame["observed_time_ns"], errors="raise")
            .astype("int64")
            .to_numpy(copy=True)
        )
        if self.times.size == 0 or np.any(np.diff(self.times) <= 0):
            raise RuntimeError(f"invalid basis feature clock: {path}")
        self.ready = (
            frame["feature_ready"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"true", "1", "yes"})
            .to_numpy(dtype=np.bool_, copy=True)
        )
        self.premium_change_5m = pd.to_numeric(
            frame["premium_change_5m"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)
        self.flow_3m = pd.to_numeric(
            frame["flow_3m"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)
        self.ret_60s_bps = pd.to_numeric(
            frame["ret_60s_bps"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)

    def observation(
        self, ts_event: int, max_age_seconds: float
    ) -> _BasisObservation:
        index = int(np.searchsorted(self.times, ts_event, side="right") - 1)
        if index < 0:
            return _BasisObservation(0, False, math.nan, math.nan, math.nan)
        observed = int(self.times[index])
        age = (int(ts_event) - observed) / 1_000_000_000.0
        if age < -1e-9:
            raise RuntimeError("future basis feature reached Candidate 55")
        premium = float(self.premium_change_5m[index])
        flow = float(self.flow_3m[index])
        ret = float(self.ret_60s_bps[index])
        ready = (
            age <= float(max_age_seconds)
            and bool(self.ready[index])
            and math.isfinite(premium)
            and math.isfinite(flow)
            and math.isfinite(ret)
        )
        return _BasisObservation(observed, ready, premium, flow, ret)


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """V15 short with immediate basis-state gating and optional DI invalidation."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        mode = str(config.v15_basis_mode).strip().lower()
        if mode not in {"source", "basis", "basis_di_fail"}:
            raise ValueError(f"unsupported V15 basis mode: {mode}")
        self._basis_mode = mode
        self._basis_features: dict[str, _BasisFeatureStore] = {}
        self._basis_flow_window: deque[float] = deque(
            maxlen=max(60, int(config.v15_failure_window_minutes))
        )
        self._basis_return_window: deque[float] = deque(
            maxlen=max(60, int(config.v15_failure_window_minutes))
        )
        self._basis_best_favourable: float | None = None
        self.diagnostics.update(
            {
                "candidate55_research_question": (
                    "PRESERVE_V15_GROSS_PROFIT_WHILE_REJECTING_DERIVATIVE_LED_"
                    "PREMIUM_COLLAPSE_BREAKDOWNS"
                ),
                "v15_basis_mode": mode,
                "v15_min_premium_change_5m": float(
                    config.v15_min_premium_change_5m
                ),
                "basis_entry_checks": 0,
                "basis_entry_accepts": 0,
                "basis_entry_rejections": 0,
                "basis_feature_stale": 0,
                "basis_failed_auction_checks": 0,
                "basis_failed_auction_exits": 0,
                "entry_policy_source_thresholds_changed": 0,
                "source_stop_changed": 0,
                "source_trailing_changed": 0,
                "basis_uses_complete_feature_minutes_only": 1,
            }
        )

    @property
    def _basis_enabled(self) -> bool:
        return self._basis_mode != "source"

    @property
    def _basis_failure_exit_enabled(self) -> bool:
        return self._basis_mode == "basis_di_fail"

    def on_start(self) -> None:
        super().on_start()
        self._basis_features = {
            symbol: _BasisFeatureStore(path)
            for symbol, path in self.feature_paths.items()
        }

    def _submit_decision(self, decision, ts_event: int) -> None:
        if not self._basis_enabled:
            super()._submit_decision(decision, ts_event)
            return
        observation = self._basis_features[decision.symbol].observation(
            ts_event, self.config.feature_max_age_seconds
        )
        self.diagnostics["basis_entry_checks"] += 1
        if not observation.ready:
            self.diagnostics["basis_feature_stale"] += 1
            self._event(
                "V15_BASIS_FEATURE_STALE",
                ts_event,
                symbol=decision.symbol,
                observed_time_ns=observation.observed_time_ns,
            )
            return
        threshold = float(self.config.v15_min_premium_change_5m)
        if float(observation.premium_change_5m) < threshold:
            self.diagnostics["basis_entry_rejections"] += 1
            self._event(
                "V15_PREMIUM_COLLAPSE_ENTRY_REJECTED",
                ts_event,
                symbol=decision.symbol,
                premium_change_5m=float(observation.premium_change_5m),
                minimum_premium_change_5m=threshold,
            )
            return
        before = int(self.diagnostics.get("entry_submissions", 0))
        super()._submit_decision(decision, ts_event)
        after = int(self.diagnostics.get("entry_submissions", 0))
        if after > before:
            self.diagnostics["basis_entry_accepts"] += 1
            if self.current_scenario is not None:
                self.current_scenario.update(
                    {
                        "v15_basis_mode": self._basis_mode,
                        "signal_premium_change_5m": float(
                            observation.premium_change_5m
                        ),
                        "basis_observed_time_ns": int(
                            observation.observed_time_ns
                        ),
                    }
                )

    def on_position_opened(self, event) -> None:
        super().on_position_opened(event)
        scenario = self.current_scenario or {}
        entry = float(scenario.get("entry_reference", math.nan))
        self._basis_best_favourable = (
            entry if math.isfinite(entry) and entry > 0.0 else None
        )
        self._basis_flow_window.clear()
        self._basis_return_window.clear()

    def _manage_open_position(self, ts_event: int) -> None:
        if (
            self.current_symbol is not None
            and self._basis_failure_exit_enabled
            and not bool(getattr(self, "_trail_active", False))
        ):
            scenario = self.current_scenario or {}
            diagnostics = scenario.get("diagnostics", {})
            di_only = (
                int(diagnostics.get("used_di_component", 0)) == 1
                and int(diagnostics.get("used_bb_component", 0)) == 0
            )
            if di_only:
                symbol = self.current_symbol
                bar = self.bars[symbol][-1]
                entry = float(scenario.get("entry_reference", math.nan))
                if math.isfinite(entry) and entry > 0.0:
                    favourable = float(bar.low)
                    self._basis_best_favourable = (
                        favourable
                        if self._basis_best_favourable is None
                        else min(self._basis_best_favourable, favourable)
                    )
                    observation = self._basis_features[symbol].observation(
                        ts_event, self.config.feature_max_age_seconds
                    )
                    if observation.ready:
                        self._basis_flow_window.append(float(observation.flow_3m))
                        self._basis_return_window.append(
                            float(observation.ret_60s_bps)
                        )
                    age = (
                        int(self.minute_index) - int(self.position_open_minute)
                        if self.position_open_minute >= 0
                        else -1
                    )
                    window = int(self.config.v15_failure_window_minutes)
                    if (
                        observation.ready
                        and age >= int(self.config.v15_failure_min_age_minutes)
                        and len(self._basis_flow_window) >= window
                        and len(self._basis_return_window) >= window
                    ):
                        self.diagnostics["basis_failed_auction_checks"] += 1
                        best = float(self._basis_best_favourable)
                        mfe = max(0.0, (entry - best) / entry)
                        adverse = max(0.0, (float(bar.close) - entry) / entry)
                        mean_flow = float(
                            np.mean(list(self._basis_flow_window)[-window:])
                        )
                        mean_return = float(
                            np.mean(list(self._basis_return_window)[-window:])
                        )
                        failed = (
                            adverse
                            >= float(self.config.v15_failure_adverse_fraction)
                            and mfe
                            < float(self.config.v15_failure_mfe_cap_fraction)
                            and mean_flow
                            > float(self.config.v15_failure_flow_3m_min)
                            and mean_return
                            > float(self.config.v15_failure_return_bps_min)
                        )
                        if failed:
                            instrument_id = self.instrument_ids[symbol]
                            self.cancel_all_orders(instrument_id)
                            self.close_all_positions(instrument_id)
                            self.diagnostics["basis_failed_auction_exits"] += 1
                            self._event(
                                "V15_BASIS_DI_FAILED_AUCTION_EXIT",
                                ts_event,
                                age_minutes=age,
                                adverse_fraction=adverse,
                                mfe_fraction=mfe,
                                rolling_flow_3m=mean_flow,
                                rolling_return_bps=mean_return,
                                window_minutes=window,
                            )
                            return
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._basis_flow_window.clear()
        self._basis_return_window.clear()
        self._basis_best_favourable = None


__all__ = ["Candidate35Config", "Candidate35Strategy"]
