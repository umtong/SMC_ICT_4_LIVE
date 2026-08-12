"""Behaviour-preserving continuous-episode tagging for ZaratustraV5.

The wrapper computes the source-state episode clock independently of the account
and attaches that clock to actual submitted scenarios after the unchanged source
strategy acts. It never changes a decision or order.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from router import FeatureObservation, route_universe
from strategy_base import SYMBOLS
from strategy_zaratustra_lifecycle_base import (
    Candidate35Config as _BaseConfig,
    Candidate35Strategy as _BaseStrategy,
)


class Candidate35Config(_BaseConfig, frozen=True):
    """No policy parameters are added."""


class Candidate35Strategy(_BaseStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self._episode_last_side = {symbol: 0 for symbol in SYMBOLS}
        self._episode_id = {symbol: 0 for symbol in SYMBOLS}
        self._episode_start_ts = {symbol: 0 for symbol in SYMBOLS}
        self._episode_entry_ordinal: dict[tuple[str, int, int], int] = {}
        self._episode_global_counter = 0
        self.diagnostics.update(
            {
                "candidate57_zara_reentry_forensic_v5": 1,
                "zara_reentry_policy_changed": 0,
                "zara_source_continuous_episodes_observed": 0,
                "zara_actual_first_entries_in_episode": 0,
                "zara_actual_reentries_in_episode": 0,
            }
        )

    def _update_source_episode_clock(self, ts_event: int) -> None:
        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        bucket_minutes = int(self.route_config.picasso_bucket_minutes)
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % bucket_minutes != bucket_minutes - 1:
            return
        if any(len(self.bars[symbol]) < bucket_minutes * 65 for symbol in SYMBOLS):
            return
        features = {
            symbol: FeatureObservation(int(self.bars[symbol][-1].ts_event), ready=True)
            for symbol in SYMBOLS
        }
        _, decisions = route_universe(
            bars_by_symbol={symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
            features_by_symbol=features,
            config=self.route_config,
        )
        for symbol in SYMBOLS:
            decision = decisions.get(symbol)
            side = int(decision.side) if decision is not None and decision.actionable else 0
            previous = int(self._episode_last_side[symbol])
            if side != previous:
                if side != 0:
                    self._episode_global_counter += 1
                    self._episode_id[symbol] = self._episode_global_counter
                    self._episode_start_ts[symbol] = int(ts_event)
                    self.diagnostics[
                        "zara_source_continuous_episodes_observed"
                    ] += 1
                else:
                    self._episode_id[symbol] = 0
                    self._episode_start_ts[symbol] = 0
            self._episode_last_side[symbol] = side

    def _tag_new_account_entry(self, ts_event: int, before: int) -> None:
        if int(self.diagnostics.get("entry_submissions", 0)) <= before:
            return
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is None or symbol is None:
            return
        side = int(scenario.get("side", 0))
        episode_id = int(self._episode_id.get(symbol, 0))
        episode_start_ts = int(self._episode_start_ts.get(symbol, 0))
        if episode_id <= 0 or side != int(self._episode_last_side.get(symbol, 0)):
            # This should not occur for a submitted source decision, but preserving
            # the account takes priority over diagnostic completeness.
            scenario["forensic_continuous_episode_missing"] = True
            return
        key = (symbol, side, episode_id)
        ordinal = int(self._episode_entry_ordinal.get(key, 0)) + 1
        self._episode_entry_ordinal[key] = ordinal
        age_minutes = max(0, (int(ts_event) - episode_start_ts) // 60_000_000_000)
        diagnostics = scenario.get("diagnostics")
        if not isinstance(diagnostics, dict):
            diagnostics = {}
            scenario["diagnostics"] = diagnostics
        diagnostics.update(
            {
                "forensic_continuous_episode_id": episode_id,
                "forensic_continuous_episode_start_ts": episode_start_ts,
                "forensic_entry_ordinal_in_continuous_episode": ordinal,
                "forensic_continuous_episode_age_at_entry_minutes": age_minutes,
                "forensic_is_reentry_in_continuous_episode": int(ordinal >= 2),
            }
        )
        scenario.update(
            {
                "forensic_continuous_episode_id": episode_id,
                "forensic_entry_ordinal_in_continuous_episode": ordinal,
            }
        )
        if ordinal == 1:
            self.diagnostics["zara_actual_first_entries_in_episode"] += 1
        else:
            self.diagnostics["zara_actual_reentries_in_episode"] += 1

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self._update_source_episode_clock(ts_event)
        before = int(self.diagnostics.get("entry_submissions", 0))
        super()._on_complete_universe_minute(ts_event)
        self._tag_new_account_entry(ts_event, before)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
