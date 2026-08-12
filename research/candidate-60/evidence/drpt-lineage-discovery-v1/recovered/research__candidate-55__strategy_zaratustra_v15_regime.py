"""One-slot V15 Bollinger-short execution with a fixed trend-quality router.

The source entry, source stop, source trailing, fee/slippage model, continuous
NAV accounting and exact 3% planned-loss sizing are unchanged.  The only
research intervention is upstream state ownership: the clean variant accepts a
Bollinger short episode only when the latest completed higher-timeframe auction
is ``trending_down_clean`` under the shared ATR/ADX/Kaufman classifier.

Every source candidate, rejection and winner is emitted before the global slot
is consumed so false negatives and replacement trades remain auditable.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys

from router import FeatureObservation, route_universe
from strategy_base import SYMBOLS
from v15_regime_state import (
    RegimeThresholds,
    TRENDING_DOWN_CLEAN,
    latest_regime_from_minutes,
)


_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v15.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_v15_regime_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load V15 execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    v15_regime_mode: str = "source_bb_short"
    v15_regime_bucket_minutes: int = 30
    v15_regime_period: int = 21
    v15_regime_return_eff_threshold: float = 0.05
    v15_regime_range_eff_threshold: float = 0.03
    v15_regime_adx_threshold: float = 25.0
    v15_regime_efficiency_threshold: float = 0.50


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """V15 BB short family with result-predicted clean-auction ownership."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        mode = str(config.v15_regime_mode).strip().lower()
        if mode not in {"source_bb_short", "clean_down_bb_short"}:
            raise ValueError(f"unsupported V15 regime mode: {mode}")
        if int(config.v15_regime_bucket_minutes) <= 0:
            raise ValueError("regime bucket must be positive")
        if int(config.v15_regime_period) < 2:
            raise ValueError("regime period must be at least two")
        self._regime_mode = mode
        self._regime_thresholds = RegimeThresholds(
            return_eff=float(config.v15_regime_return_eff_threshold),
            range_eff=float(config.v15_regime_range_eff_threshold),
            adx=float(config.v15_regime_adx_threshold),
            efficiency=float(config.v15_regime_efficiency_threshold),
        )
        self.diagnostics.update(
            {
                "candidate55_research_question": (
                    "DO_V15_BB_SHORT_PROFITS_CONCENTRATE_IN_CLEAN_DOWNSIDE_PRICE_DISCOVERY"
                ),
                "v15_regime_mode": mode,
                "v15_regime_bucket_minutes": int(config.v15_regime_bucket_minutes),
                "v15_regime_period": int(config.v15_regime_period),
                "v15_regime_fixed_thresholds": {
                    "return_eff": self._regime_thresholds.return_eff,
                    "range_eff": self._regime_thresholds.range_eff,
                    "adx": self._regime_thresholds.adx,
                    "efficiency": self._regime_thresholds.efficiency,
                },
                "regime_raw_actionable": 0,
                "regime_short_candidates": 0,
                "regime_long_rejections": 0,
                "regime_stale_rejections": 0,
                "regime_state_rejections": 0,
                "regime_clean_eligible": 0,
                "regime_selected": 0,
                "regime_alternative_symbol_selected": 0,
                "regime_label_counts": {},
                "source_entry_changed": 0,
                "source_stop_changed": 0,
                "source_trailing_changed": 0,
                "complete_higher_timeframe_bars_only": 1,
                "one_global_slot": 1,
            }
        )

    def _snapshot(self, symbol: str):
        return latest_regime_from_minutes(
            tuple(self.bars[symbol]),
            bucket_minutes=int(self.config.v15_regime_bucket_minutes),
            period=int(self.config.v15_regime_period),
            thresholds=self._regime_thresholds,
        )

    def _regime_decisions(self, decisions, ts_event: int):
        actionable = [item for item in decisions.values() if item.actionable]
        self.diagnostics["regime_raw_actionable"] += len(actionable)
        eligible = []
        for decision in actionable:
            if int(decision.side) != -1:
                self.diagnostics["regime_long_rejections"] += 1
                self._event(
                    "V15_REGIME_CANDIDATE",
                    ts_event,
                    symbol=decision.symbol,
                    episode_ts=int(decision.episode_ts),
                    source_score=float(decision.score),
                    source_side=int(decision.side),
                    eligible=0,
                    rejection="LONG_NOT_OWNED_BY_SHORT_FAMILY",
                )
                continue
            self.diagnostics["regime_short_candidates"] += 1
            snapshot = self._snapshot(decision.symbol)
            label_counts = self.diagnostics["regime_label_counts"]
            label_counts[snapshot.label] = int(label_counts.get(snapshot.label, 0)) + 1
            diagnostics = dict(decision.diagnostics)
            diagnostics.update(
                {
                    "regime_ready": int(snapshot.ready),
                    "regime_label": snapshot.label,
                    "regime_observed_time_ns": int(snapshot.observed_time_ns),
                    "regime_return_eff": snapshot.return_eff,
                    "regime_range_eff": snapshot.range_eff,
                    "regime_efficiency": snapshot.efficiency,
                    "regime_adx": snapshot.adx,
                    "regime_plus_di": snapshot.plus_di,
                    "regime_minus_di": snapshot.minus_di,
                    "regime_atr_fraction": snapshot.atr_fraction,
                    "regime_window_net_fraction": snapshot.window_net_fraction,
                    "v15_regime_mode": self._regime_mode,
                }
            )
            accepted = self._regime_mode == "source_bb_short"
            rejection = ""
            if self._regime_mode == "clean_down_bb_short":
                if not snapshot.ready:
                    self.diagnostics["regime_stale_rejections"] += 1
                    rejection = "REGIME_NOT_READY"
                elif snapshot.label != TRENDING_DOWN_CLEAN:
                    self.diagnostics["regime_state_rejections"] += 1
                    rejection = "NOT_TRENDING_DOWN_CLEAN"
                else:
                    accepted = True
                    self.diagnostics["regime_clean_eligible"] += 1
            self._event(
                "V15_REGIME_CANDIDATE",
                ts_event,
                symbol=decision.symbol,
                episode_ts=int(decision.episode_ts),
                source_score=float(decision.score),
                source_side=int(decision.side),
                eligible=int(accepted),
                rejection=rejection,
                **snapshot.diagnostics(),
            )
            if accepted:
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
        required_minutes = max(
            int(self.config.zaratustra_startup_30m_candles) * 30,
            int(self.config.v15_regime_bucket_minutes)
            * (int(self.config.v15_regime_period) * 2 + 4),
        )
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
        filtered = self._regime_decisions(decisions, ts_event)
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
            self.diagnostics["regime_alternative_symbol_selected"] += 1
        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            self.diagnostics["cooldown_rejections"] += 1
            return

        self.used_episode_keys.add((winner.symbol, winner.state, int(winner.episode_ts)))
        self._trail_active = False
        self._trail_best = None
        self.diagnostics["regime_selected"] += 1
        self._event(
            "V15_REGIME_WINNER",
            ts_event,
            symbol=winner.symbol,
            episode_ts=int(winner.episode_ts),
            source_score=float(winner.score),
            raw_short_winner=(raw_short[0].symbol if raw_short else ""),
            regime_mode=self._regime_mode,
            **{
                key: value
                for key, value in winner.diagnostics.items()
                if str(key).startswith("regime_")
            },
        )
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if int(self.diagnostics["entry_submissions"]) > before and self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-55-v15-regime",
                    "source_variant": str(self.route_config.picasso_precedence_mode),
                    "source_timeframes_minutes": [5],
                    "v15_regime_mode": self._regime_mode,
                    "source_entry_changed": False,
                    "source_stop_changed": False,
                    "source_trailing_changed": False,
                    "valid_real_ohlc_execution": True,
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
