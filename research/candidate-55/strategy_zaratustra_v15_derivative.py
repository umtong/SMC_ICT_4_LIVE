"""V15 lower-band short router for derivative-led sell sponsorship.

Development episode forensics across four separated account windows reversed an
earlier interpretation of perpetual premium.  V15 lower-band shorts performed
poorly when premium was stable/rising during the price break, but retained a
gross edge when the perpetual contract led spot downward and completed-minute
aggressor flow remained sell-side.  OI sign does not become a threshold; it
routes the same policy into two causal substates:

* OI build: new-short sponsored downside continuation;
* OI release: sell-side liquidation cascade continuation.

The source V15 Bollinger interaction, stop, trailing, costs, 3% planned-loss
sizing and one global slot are unchanged.  The filter is applied to every
symbol before arbitration, and every rejected/selected state is logged so a
removed loser cannot be credited without accounting for its replacement trade.
"""
from __future__ import annotations

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
    "candidate55_v15_derivative_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load V15 execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    v15_derivative_mode: str = "source_bb_short"


class _DerivativeObservation(NamedTuple):
    observed_time_ns: int
    ready: bool
    premium_change_5m: float
    flow_60s: float
    oi_change_15m: float


class _DerivativeFeatureStore:
    def __init__(self, path: Path) -> None:
        frame = pd.read_csv(
            path,
            compression="infer",
            usecols=[
                "observed_time_ns",
                "feature_ready",
                "premium_change_5m",
                "flow_60s",
                "oi_change_15m",
            ],
        )
        self.times = (
            pd.to_numeric(frame["observed_time_ns"], errors="raise")
            .astype("int64")
            .to_numpy(copy=True)
        )
        if self.times.size == 0 or np.any(np.diff(self.times) <= 0):
            raise RuntimeError(f"invalid derivative-state feature clock: {path}")
        self.ready = (
            frame["feature_ready"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"true", "1", "yes"})
            .to_numpy(dtype=np.bool_, copy=True)
        )
        self.premium = pd.to_numeric(
            frame["premium_change_5m"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)
        self.flow = pd.to_numeric(
            frame["flow_60s"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)
        self.oi = pd.to_numeric(
            frame["oi_change_15m"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)

    def observation(
        self,
        ts_event: int,
        max_age_seconds: float,
    ) -> _DerivativeObservation:
        index = int(np.searchsorted(self.times, ts_event, side="right") - 1)
        if index < 0:
            return _DerivativeObservation(0, False, math.nan, math.nan, math.nan)
        observed = int(self.times[index])
        age = (int(ts_event) - observed) / 1_000_000_000.0
        if age < -1e-9:
            raise RuntimeError("future derivative-state feature reached Candidate 55")
        premium = float(self.premium[index])
        flow = float(self.flow[index])
        oi = float(self.oi[index])
        ready = (
            age <= float(max_age_seconds)
            and bool(self.ready[index])
            and math.isfinite(premium)
            and math.isfinite(flow)
            and math.isfinite(oi)
        )
        return _DerivativeObservation(
            observed,
            ready,
            premium if ready else math.nan,
            flow if ready else math.nan,
            oi if ready else math.nan,
        )


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """Source BB short versus one predeclared derivative-led sell policy."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        mode = str(config.v15_derivative_mode).strip().lower()
        if mode not in {"source_bb_short", "derivative_sell_short"}:
            raise ValueError(f"unsupported V15 derivative mode: {mode}")
        self._derivative_mode = mode
        self._derivative_features: dict[str, _DerivativeFeatureStore] = {}
        self.diagnostics.update(
            {
                "candidate55_research_question": (
                    "DOES_DERIVATIVE_LEAD_PLUS_SELL_AGGRESSION_OWN_V15_BB_SHORT_EDGE"
                ),
                "v15_derivative_mode": mode,
                "derivative_raw_actionable": 0,
                "derivative_short_candidates": 0,
                "derivative_long_rejections": 0,
                "derivative_component_rejections": 0,
                "derivative_feature_stale": 0,
                "derivative_premium_rejections": 0,
                "derivative_flow_rejections": 0,
                "derivative_new_short_eligible": 0,
                "derivative_liquidation_eligible": 0,
                "derivative_selected": 0,
                "derivative_alternative_symbol_selected": 0,
                "source_entry_changed": 0,
                "source_stop_changed": 0,
                "source_trailing_changed": 0,
                "zero_is_economic_boundary_not_fitted_threshold": 1,
                "complete_feature_minutes_only": 1,
                "one_global_slot": 1,
            }
        )

    def on_start(self) -> None:
        super().on_start()
        self._derivative_features = {
            symbol: _DerivativeFeatureStore(path)
            for symbol, path in self.feature_paths.items()
        }

    def _filter_decisions(self, decisions, ts_event: int):
        actionable = [item for item in decisions.values() if item.actionable]
        self.diagnostics["derivative_raw_actionable"] += len(actionable)
        eligible = []
        for decision in actionable:
            if int(decision.side) != -1:
                self.diagnostics["derivative_long_rejections"] += 1
                continue
            diagnostics = dict(decision.diagnostics)
            if int(diagnostics.get("used_bb_component", 0)) != 1:
                self.diagnostics["derivative_component_rejections"] += 1
                continue
            self.diagnostics["derivative_short_candidates"] += 1
            if self._derivative_mode == "source_bb_short":
                eligible.append(decision)
                continue

            observation = self._derivative_features[decision.symbol].observation(
                ts_event,
                self.config.feature_max_age_seconds,
            )
            rejection = ""
            state = "UNRESOLVED"
            accepted = False
            if not observation.ready:
                self.diagnostics["derivative_feature_stale"] += 1
                rejection = "FEATURE_NOT_READY"
            elif float(observation.premium_change_5m) >= 0.0:
                self.diagnostics["derivative_premium_rejections"] += 1
                rejection = "PERPETUAL_DID_NOT_LEAD_SPOT_DOWN"
            elif float(observation.flow_60s) >= 0.0:
                self.diagnostics["derivative_flow_rejections"] += 1
                rejection = "SELL_AGGRESSION_NOT_PRESENT"
            else:
                accepted = True
                if float(observation.oi_change_15m) >= 0.0:
                    state = "NEW_SHORT_SPONSORED_DOWNSIDE"
                    self.diagnostics["derivative_new_short_eligible"] += 1
                else:
                    state = "SELL_SIDE_LIQUIDATION_CASCADE"
                    self.diagnostics["derivative_liquidation_eligible"] += 1

            self._event(
                "V15_DERIVATIVE_CANDIDATE",
                ts_event,
                symbol=decision.symbol,
                episode_ts=int(decision.episode_ts),
                source_score=float(decision.score),
                eligible=int(accepted),
                derivative_state=state,
                rejection=rejection,
                observed_time_ns=int(observation.observed_time_ns),
                feature_ready=int(observation.ready),
                premium_change_5m=observation.premium_change_5m,
                flow_60s=observation.flow_60s,
                oi_change_15m=observation.oi_change_15m,
            )
            if not accepted:
                continue
            diagnostics.update(
                {
                    "derivative_state": state,
                    "derivative_observed_time_ns": int(observation.observed_time_ns),
                    "signal_premium_change_5m": float(observation.premium_change_5m),
                    "signal_flow_60s": float(observation.flow_60s),
                    "signal_oi_change_15m": float(observation.oi_change_15m),
                    "v15_derivative_mode": self._derivative_mode,
                }
            )
            eligible.append(replace(decision, diagnostics=diagnostics))
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
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 != 4:
            return
        required_minutes = int(self.config.zaratustra_startup_30m_candles) * 30
        if any(len(self.bars[symbol]) < required_minutes for symbol in SYMBOLS):
            return

        features = {
            symbol: FeatureObservation(int(self.bars[symbol][-1].ts_event), ready=True)
            for symbol in SYMBOLS
        }
        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(
            bars_by_symbol={symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
            features_by_symbol=features,
            config=self.route_config,
        )
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if decision.actionable:
                family_counts[decision.state] = int(family_counts.get(decision.state, 0)) + 1
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1

        raw_short = sorted(
            [item for item in decisions.values() if item.actionable and int(item.side) == -1],
            key=lambda item: (-float(item.score), item.symbol, int(item.episode_ts)),
        )
        filtered = self._filter_decisions(decisions, ts_event)
        unused = []
        for decision in filtered:
            key = (decision.symbol, decision.state, int(decision.episode_ts))
            if key in self.used_episode_keys:
                self.diagnostics["used_episode_rejections"] += 1
            else:
                unused.append(decision)
        unused.sort(
            key=lambda item: (-float(item.score), item.symbol, int(item.episode_ts))
        )
        winner = unused[0] if unused else None
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        if raw_short and winner.symbol != raw_short[0].symbol:
            self.diagnostics["derivative_alternative_symbol_selected"] += 1
        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            self.diagnostics["cooldown_rejections"] += 1
            return

        self.used_episode_keys.add((winner.symbol, winner.state, int(winner.episode_ts)))
        self._trail_active = False
        self._trail_best = None
        self.diagnostics["derivative_selected"] += 1
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if int(self.diagnostics["entry_submissions"]) > before and self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-55-v15-derivative",
                    "source_variant": str(self.route_config.picasso_precedence_mode),
                    "source_timeframes_minutes": [5],
                    "v15_derivative_mode": self._derivative_mode,
                    "source_entry_changed": False,
                    "source_stop_changed": False,
                    "source_trailing_changed": False,
                    "valid_real_ohlc_execution": True,
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
