"""Causal loss-engine repair for the high-capacity V15 short family.

The source entry, stop and trailing policy remain intact.  Two changes solve
separate observed failure modes without using future information:

1. A source edge is held for one completed minute and entered only when the
   observed three-minute aggressor flow still points down.  The confirmation
   minute is complete before the market order is submitted.
2. DI-only positions that never obtain meaningful favourable excursion and
   then show persistent opposite flow plus positive one-minute returns are
   closed as failed auctions before the source's full stop is consumed.

Both behaviours are switchable through the strategy config so the unchanged
source family, confirmation-only family and repaired family can be compared in
the same NautilusTrader account.
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
    "candidate55_zaratustra_v15_repair_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load V15 short execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    v15_repair_mode: str = "source"
    v15_confirmation_delay_minutes: int = 1
    v15_entry_flow_3m_max: float = 0.0
    v15_failure_min_age_minutes: int = 20
    v15_failure_adverse_fraction: float = 0.002
    v15_failure_mfe_cap_fraction: float = 0.003
    v15_failure_window_minutes: int = 10
    v15_failure_flow_3m_min: float = 0.0
    v15_failure_return_bps_min: float = 0.0


class _RepairObservation(NamedTuple):
    observed_time_ns: int
    ready: bool
    flow_3m: float
    ret_60s_bps: float


class _RepairFeatureStore:
    """Minimal causal view over the two fields used by the repair."""

    def __init__(self, path: Path) -> None:
        frame = pd.read_csv(
            path,
            compression="infer",
            usecols=[
                "observed_time_ns",
                "feature_ready",
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
            raise RuntimeError(f"invalid repair feature clock: {path}")
        self.ready = (
            frame["feature_ready"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"true", "1", "yes"})
            .to_numpy(dtype=np.bool_, copy=True)
        )
        self.flow_3m = pd.to_numeric(
            frame["flow_3m"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)
        self.ret_60s_bps = pd.to_numeric(
            frame["ret_60s_bps"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)

    def observation(
        self, ts_event: int, max_age_seconds: float
    ) -> _RepairObservation:
        index = int(np.searchsorted(self.times, ts_event, side="right") - 1)
        if index < 0:
            return _RepairObservation(0, False, math.nan, math.nan)
        observed = int(self.times[index])
        age = (int(ts_event) - observed) / 1_000_000_000.0
        if age < -1e-9:
            raise RuntimeError("future repair feature reached Candidate 55")
        flow = float(self.flow_3m[index])
        ret = float(self.ret_60s_bps[index])
        ready = (
            age <= float(max_age_seconds)
            and bool(self.ready[index])
            and math.isfinite(flow)
            and math.isfinite(ret)
        )
        return _RepairObservation(observed, ready, flow, ret)


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """V15 short with delayed flow confirmation and DI failed-auction exit."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        mode = str(config.v15_repair_mode).strip().lower()
        if mode not in {
            "source",
            "confirm",
            "confirm_di_fail20",
            "confirm_di_fail30",
        }:
            raise ValueError(f"unsupported V15 repair mode: {mode}")
        if int(config.v15_confirmation_delay_minutes) != 1:
            raise ValueError("V15 confirmation is intentionally fixed at one minute")
        if int(config.v15_failure_window_minutes) <= 0:
            raise ValueError("failure window must be positive")

        self._repair_mode = mode
        self._repair_features: dict[str, _RepairFeatureStore] = {}
        self._confirmation_decision = None
        self._confirmation_signal_ts: int | None = None
        self._confirmation_signal_minute = -1
        self._repair_flow_window: deque[float] = deque(
            maxlen=max(60, int(config.v15_failure_window_minutes))
        )
        self._repair_return_window: deque[float] = deque(
            maxlen=max(60, int(config.v15_failure_window_minutes))
        )
        self._repair_best_favourable: float | None = None

        self.diagnostics.update(
            {
                "candidate55_research_question": (
                    "PRESERVE_V15_GROSS_PROFIT_AND_REPLACE_CONTRADICTED_ENTRY_"
                    "PLUS_DI_FAILED_AUCTION_LOSS_ENGINE"
                ),
                "v15_repair_mode": mode,
                "confirmation_candidates": 0,
                "confirmation_entries": 0,
                "confirmation_rejections": 0,
                "confirmation_stale": 0,
                "confirmation_geometry_rejections": 0,
                "failed_auction_checks": 0,
                "failed_auction_exits": 0,
                "failed_auction_di_only": 1,
                "entry_policy_source_thresholds_changed": 0,
                "source_stop_changed": 0,
                "source_trailing_changed": 0,
                "repair_uses_complete_feature_minutes_only": 1,
            }
        )

    @property
    def _confirmation_enabled(self) -> bool:
        return self._repair_mode != "source"

    @property
    def _failure_exit_enabled(self) -> bool:
        return self._repair_mode in {
            "confirm_di_fail20",
            "confirm_di_fail30",
        }

    def on_start(self) -> None:
        super().on_start()
        self._repair_features = {
            symbol: _RepairFeatureStore(path)
            for symbol, path in self.feature_paths.items()
        }

    def _submit_decision(self, decision, ts_event: int) -> None:
        if not self._confirmation_enabled:
            super()._submit_decision(decision, ts_event)
            return
        if self._confirmation_decision is not None:
            self.diagnostics["confirmation_rejections"] += 1
            self._event(
                "V15_CONFIRMATION_COLLISION_REJECTED",
                ts_event,
                pending_symbol=self._confirmation_decision.symbol,
                new_symbol=decision.symbol,
            )
            return
        self._confirmation_decision = decision
        self._confirmation_signal_ts = int(ts_event)
        self._confirmation_signal_minute = int(self.minute_index)
        self.diagnostics["confirmation_candidates"] += 1
        self._event(
            "V15_FLOW_CONFIRMATION_PENDING",
            ts_event,
            symbol=decision.symbol,
            side=int(decision.side),
            source_episode_ts=int(decision.episode_ts),
            confirmation_delay_minutes=1,
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        super()._on_complete_universe_minute(ts_event)
        decision = self._confirmation_decision
        signal_ts = self._confirmation_signal_ts
        if (
            decision is None
            or signal_ts is None
            or int(ts_event) <= int(signal_ts)
        ):
            return
        if self.entry_pending or not self._global_flat():
            return

        elapsed = int(self.minute_index) - int(self._confirmation_signal_minute)
        if elapsed < int(self.config.v15_confirmation_delay_minutes):
            return
        if elapsed > int(self.config.v15_confirmation_delay_minutes):
            self.diagnostics["confirmation_rejections"] += 1
            self._event(
                "V15_FLOW_CONFIRMATION_EXPIRED",
                ts_event,
                symbol=decision.symbol,
                elapsed_minutes=elapsed,
            )
            self._clear_confirmation_state()
            return

        observation = self._repair_features[decision.symbol].observation(
            ts_event, self.config.feature_max_age_seconds
        )
        if not observation.ready:
            self.diagnostics["confirmation_stale"] += 1
            self._event(
                "V15_FLOW_CONFIRMATION_STALE",
                ts_event,
                symbol=decision.symbol,
                observed_time_ns=observation.observed_time_ns,
            )
            self._clear_confirmation_state()
            return
        if float(observation.flow_3m) >= float(
            self.config.v15_entry_flow_3m_max
        ):
            self.diagnostics["confirmation_rejections"] += 1
            self._event(
                "V15_FLOW_CONFIRMATION_REJECTED",
                ts_event,
                symbol=decision.symbol,
                flow_3m=float(observation.flow_3m),
                required_max=float(self.config.v15_entry_flow_3m_max),
            )
            self._clear_confirmation_state()
            return

        self._clear_confirmation_state()
        before = int(self.diagnostics.get("entry_submissions", 0))
        super()._submit_decision(decision, ts_event)
        after = int(self.diagnostics.get("entry_submissions", 0))
        if after > before:
            self.diagnostics["confirmation_entries"] += 1
            if self.current_scenario is not None:
                self.current_scenario.update(
                    {
                        "v15_repair_mode": self._repair_mode,
                        "flow_confirmation_delay_minutes": 1,
                        "confirmation_flow_3m": float(observation.flow_3m),
                        "confirmation_observed_time_ns": int(
                            observation.observed_time_ns
                        ),
                    }
                )
        else:
            self.diagnostics["confirmation_geometry_rejections"] += 1

    def on_position_opened(self, event) -> None:
        super().on_position_opened(event)
        scenario = self.current_scenario or {}
        entry = float(scenario.get("entry_reference", math.nan))
        self._repair_best_favourable = (
            entry if math.isfinite(entry) and entry > 0.0 else None
        )
        self._repair_flow_window.clear()
        self._repair_return_window.clear()

    def _manage_open_position(self, ts_event: int) -> None:
        if (
            self.current_symbol is not None
            and self._failure_exit_enabled
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
                    self._repair_best_favourable = (
                        favourable
                        if self._repair_best_favourable is None
                        else min(self._repair_best_favourable, favourable)
                    )
                    observation = self._repair_features[symbol].observation(
                        ts_event, self.config.feature_max_age_seconds
                    )
                    if observation.ready:
                        self._repair_flow_window.append(
                            float(observation.flow_3m)
                        )
                        self._repair_return_window.append(
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
                        and len(self._repair_flow_window) >= window
                        and len(self._repair_return_window) >= window
                    ):
                        self.diagnostics["failed_auction_checks"] += 1
                        best = float(self._repair_best_favourable)
                        mfe = max(0.0, (entry - best) / entry)
                        adverse = max(0.0, (float(bar.close) - entry) / entry)
                        mean_flow = float(
                            np.mean(list(self._repair_flow_window)[-window:])
                        )
                        mean_return = float(
                            np.mean(list(self._repair_return_window)[-window:])
                        )
                        failed = (
                            adverse
                            >= float(
                                self.config.v15_failure_adverse_fraction
                            )
                            and mfe
                            < float(
                                self.config.v15_failure_mfe_cap_fraction
                            )
                            and mean_flow
                            > float(
                                self.config.v15_failure_flow_3m_min
                            )
                            and mean_return
                            > float(
                                self.config.v15_failure_return_bps_min
                            )
                        )
                        if failed:
                            instrument_id = self.instrument_ids[symbol]
                            self.cancel_all_orders(instrument_id)
                            self.close_all_positions(instrument_id)
                            self.diagnostics["failed_auction_exits"] += 1
                            self._event(
                                "V15_DI_FAILED_AUCTION_EXIT",
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

    def _clear_confirmation_state(self) -> None:
        self._confirmation_decision = None
        self._confirmation_signal_ts = None
        self._confirmation_signal_minute = -1

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._clear_confirmation_state()
        self._repair_flow_window.clear()
        self._repair_return_window.clear()
        self._repair_best_favourable = None


__all__ = ["Candidate35Config", "Candidate35Strategy"]
