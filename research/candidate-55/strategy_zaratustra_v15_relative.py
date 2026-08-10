"""Cross-sectional auction-state router for the high-capacity V15 family.

The source V15 entry detector is retained, but DI-only states are not promoted
into this family.  Bollinger-origin signals are accepted only when the target
has moved moderately, not extremely, relative to the other liquid crypto
contracts over the preceding completed hour:

* short: target underperformed the peer median by 0.10% to 0.40%;
* long: target outperformed the peer median by 0.10% to 0.40%.

The lower bound rejects names that have not become relative followers.  The
upper bound rejects exhausted chases.  A basis-aware variant additionally
rejects derivative-led moves when the perpetual premium is accelerating in the
trade direction.  All eligible symbols and both directions are arbitrated
before one order is selected, preserving the four-asset single-slot contract.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
import math
from pathlib import Path
import sys
from typing import NamedTuple

import numpy as np
import pandas as pd

from router import FeatureObservation, route_universe
from strategy_base import SYMBOLS


_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v15.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v15_relative_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load V15 execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    v15_relative_mode: str = "source_both"
    v15_relative_lookback_minutes: int = 60
    v15_relative_min_fraction: float = 0.001
    v15_relative_max_fraction: float = 0.004
    v15_short_min_premium_change_5m: float = -0.00005
    v15_long_max_premium_change_5m: float = 0.00005


class _RelativeObservation(NamedTuple):
    observed_time_ns: int
    ready: bool
    return_fraction: float
    premium_change_5m: float


class _RelativeFeatureStore:
    """Causal completed-minute price and premium history."""

    def __init__(self, path: Path) -> None:
        frame = pd.read_csv(
            path,
            compression="infer",
            usecols=[
                "observed_time_ns",
                "feature_ready",
                "trade_vwap_60s",
                "premium_change_5m",
            ],
        )
        self.times = (
            pd.to_numeric(frame["observed_time_ns"], errors="raise")
            .astype("int64")
            .to_numpy(copy=True)
        )
        if self.times.size == 0 or np.any(np.diff(self.times) <= 0):
            raise RuntimeError(f"invalid relative feature clock: {path}")
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
        self.premium_change_5m = pd.to_numeric(
            frame["premium_change_5m"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)

    def observation(
        self,
        ts_event: int,
        lookback_minutes: int,
        max_age_seconds: float,
    ) -> _RelativeObservation:
        index = int(np.searchsorted(self.times, ts_event, side="right") - 1)
        lag = index - int(lookback_minutes)
        if index < 0 or lag < 0:
            return _RelativeObservation(0, False, math.nan, math.nan)
        observed = int(self.times[index])
        age = (int(ts_event) - observed) / 1_000_000_000.0
        if age < -1e-9:
            raise RuntimeError("future relative feature reached Candidate 55")
        current = float(self.price[index])
        previous = float(self.price[lag])
        premium = float(self.premium_change_5m[index])
        ready = (
            age <= float(max_age_seconds)
            and bool(self.ready[index])
            and bool(self.ready[lag])
            and math.isfinite(current)
            and math.isfinite(previous)
            and previous > 0.0
            and math.isfinite(premium)
        )
        return _RelativeObservation(
            observed,
            ready,
            current / previous - 1.0 if ready else math.nan,
            premium,
        )


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """V15 BB-origin relative follower/leader router over one account slot."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        mode = str(config.v15_relative_mode).strip().lower()
        if mode not in {
            "source_both",
            "bb_relative_short",
            "bb_relative_both",
            "bb_relative_basis_both",
        }:
            raise ValueError(f"unsupported V15 relative mode: {mode}")
        if not (
            0.0
            < float(config.v15_relative_min_fraction)
            < float(config.v15_relative_max_fraction)
        ):
            raise ValueError("relative band must be positive and ordered")
        self._relative_mode = mode
        self._relative_features: dict[str, _RelativeFeatureStore] = {}
        self.diagnostics.update(
            {
                "candidate": "candidate-55",
                "candidate55_research_question": (
                    "PRESERVE_V15_BOLLINGER_GROSS_PROFIT_WHILE_REMOVING_DI_"
                    "LOSS_AND_RELATIVE_EXHAUSTION"
                ),
                "v15_relative_mode": mode,
                "v15_relative_lookback_minutes": int(
                    config.v15_relative_lookback_minutes
                ),
                "v15_relative_min_fraction": float(
                    config.v15_relative_min_fraction
                ),
                "v15_relative_max_fraction": float(
                    config.v15_relative_max_fraction
                ),
                "relative_source_actionable": 0,
                "relative_component_rejections": 0,
                "relative_direction_rejections": 0,
                "relative_feature_stale": 0,
                "relative_band_rejections": 0,
                "relative_basis_rejections": 0,
                "relative_eligible": 0,
                "relative_alternative_symbol_selected": 0,
                "entry_policy_source_thresholds_changed": 0,
                "source_stop_changed": 0,
                "source_trailing_changed": 0,
                "one_global_slot": 1,
                "complete_feature_minutes_only": 1,
            }
        )

    def on_start(self) -> None:
        super().on_start()
        self._relative_features = {
            symbol: _RelativeFeatureStore(path)
            for symbol, path in self.feature_paths.items()
        }

    def _relative_decisions(self, decisions, ts_event: int):
        actionable = [item for item in decisions.values() if item.actionable]
        self.diagnostics["relative_source_actionable"] += len(actionable)
        if self._relative_mode == "source_both":
            return actionable

        observations = {
            symbol: self._relative_features[symbol].observation(
                ts_event,
                int(self.config.v15_relative_lookback_minutes),
                self.config.feature_max_age_seconds,
            )
            for symbol in SYMBOLS
        }
        if not all(item.ready for item in observations.values()):
            self.diagnostics["relative_feature_stale"] += len(actionable)
            return []

        minimum = float(self.config.v15_relative_min_fraction)
        maximum = float(self.config.v15_relative_max_fraction)
        eligible = []
        for decision in actionable:
            diagnostics = dict(decision.diagnostics)
            if int(diagnostics.get("used_bb_component", 0)) != 1:
                self.diagnostics["relative_component_rejections"] += 1
                continue
            side = int(decision.side)
            if self._relative_mode == "bb_relative_short" and side != -1:
                self.diagnostics["relative_direction_rejections"] += 1
                continue
            target_return = float(
                observations[decision.symbol].return_fraction
            )
            peer_returns = [
                float(observations[symbol].return_fraction)
                for symbol in SYMBOLS
                if symbol != decision.symbol
            ]
            peer_median = float(np.median(peer_returns))
            residual = target_return - peer_median
            aligned = side * residual
            if not (minimum <= aligned <= maximum):
                self.diagnostics["relative_band_rejections"] += 1
                continue
            premium = float(
                observations[decision.symbol].premium_change_5m
            )
            if self._relative_mode == "bb_relative_basis_both":
                if side < 0 and premium < float(
                    self.config.v15_short_min_premium_change_5m
                ):
                    self.diagnostics["relative_basis_rejections"] += 1
                    continue
                if side > 0 and premium > float(
                    self.config.v15_long_max_premium_change_5m
                ):
                    self.diagnostics["relative_basis_rejections"] += 1
                    continue
            diagnostics.update(
                {
                    "relative_lookback_minutes": int(
                        self.config.v15_relative_lookback_minutes
                    ),
                    "target_return_fraction": target_return,
                    "peer_median_return_fraction": peer_median,
                    "relative_residual_fraction": residual,
                    "side_aligned_relative_fraction": aligned,
                    "signal_premium_change_5m": premium,
                    "used_relative_component_filter": 1,
                }
            )
            eligible.append(replace(decision, diagnostics=diagnostics))
        self.diagnostics["relative_eligible"] += len(eligible)
        return eligible

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)
        open_symbols = [
            symbol
            for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]),
            len(open_symbols),
        )
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
                int(self.diagnostics["max_simultaneous_entry_intents"]), 1
            )
            if self.minute_index - self.entry_pending_minute > 2:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES",
                )
                self._clear_trade_state()
            return
        if not (
            self.config.evaluation_start_ns
            <= ts_event
            <= self.config.evaluation_end_ns
        ):
            return
        moment = datetime.fromtimestamp(
            ts_event / 1_000_000_000, tz=timezone.utc
        )
        if moment.minute % 5 != 4:
            return
        required_minutes = int(
            self.config.zaratustra_startup_30m_candles
        ) * 30
        if any(len(self.bars[symbol]) < required_minutes for symbol in SYMBOLS):
            return

        source_features = {
            symbol: FeatureObservation(
                int(self.bars[symbol][-1].ts_event), ready=True
            )
            for symbol in SYMBOLS
        }
        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(
            bars_by_symbol={
                symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS
            },
            features_by_symbol=source_features,
            config=self.route_config,
        )
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if decision.actionable:
                family_counts[decision.state] = int(
                    family_counts.get(decision.state, 0)
                ) + 1
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = int(
                        reason_counts.get(reason, 0)
                    ) + 1

        filtered = self._relative_decisions(decisions, ts_event)
        unused = []
        for decision in filtered:
            key = (decision.symbol, decision.state, int(decision.episode_ts))
            if key in self.used_episode_keys:
                self.diagnostics["used_episode_rejections"] += 1
            else:
                unused.append(decision)
        unused.sort(
            key=lambda item: (
                -float(item.score),
                item.symbol,
                int(item.episode_ts),
            )
        )
        winner = unused[0] if unused else None
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return
        if (
            self.minute_index - self.last_entry_minute
            < self.config.cooldown_minutes
        ):
            self.diagnostics["cooldown_rejections"] += 1
            return

        raw_actionable = [item for item in decisions.values() if item.actionable]
        if raw_actionable and winner.symbol != sorted(
            raw_actionable,
            key=lambda item: (-float(item.score), item.symbol),
        )[0].symbol:
            self.diagnostics["relative_alternative_symbol_selected"] += 1
        self.used_episode_keys.add(
            (winner.symbol, winner.state, int(winner.episode_ts))
        )
        self._trail_active = False
        self._trail_best = None
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if (
            int(self.diagnostics["entry_submissions"]) > before
            and self.current_scenario is not None
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-55-v15-relative",
                    "v15_relative_mode": self._relative_mode,
                    "source_variant": str(
                        self.route_config.picasso_precedence_mode
                    ),
                    "source_timeframes_minutes": [5],
                    "source_entry_is_level": False,
                    "source_has_no_roi_or_exit_signal": True,
                    "valid_real_ohlc_execution": True,
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
