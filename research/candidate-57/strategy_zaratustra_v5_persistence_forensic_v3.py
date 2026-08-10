"""Persistent source-thesis instrumentation for public ZaratustraV5.

This extends the behaviour-identical v2 lifecycle wrapper.  It does not submit,
modify, cancel, or close any order.  It records component-level and duration
structure of same-side source invalidation so a later policy can be predicted
before it is tested.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from router_zaratustra_impl import _level
from strategy_zaratustra_lifecycle_base import (
    Candidate35Config as _BaseConfig,
    Candidate35Strategy as _BaseStrategy,
)


class Candidate35Config(_BaseConfig, frozen=True):
    """No policy parameter is added by the persistence diagnostic."""


class Candidate35Strategy(_BaseStrategy):
    _STREAK_THRESHOLDS = (2, 3, 6)

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate57_zara_persistence_forensic_v3": 1,
                "zara_persistence_policy_changed": 0,
                "zara_persistence_checks": 0,
                "zara_persistence_invalidation_episodes": 0,
                "zara_persistence_recoveries": 0,
            }
        )

    @staticmethod
    def _mark_r(
        side: int, entry: float, stop: float, close: float
    ) -> float:
        risk_fraction = abs(entry - stop) / entry
        if risk_fraction <= 1e-12:
            return math.nan
        return side * (close - entry) / entry / risk_fraction

    def _record_persistence(self, ts_event: int) -> None:
        diagnostics = self._forensic_diagnostics()
        if diagnostics is None or self.current_symbol is None:
            return
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 != 4:
            return
        scenario = self.current_scenario or {}
        side = int(scenario.get("side", 0))
        entry = float(scenario.get("entry_reference", 0.0))
        stop = float(scenario.get("stop", 0.0))
        if side not in (-1, 1) or not math.isfinite(entry) or entry <= 0.0:
            return

        _, _, live = _level(
            tuple(self.bars[self.current_symbol]), self.route_config
        )
        if not live:
            return
        close = float(self.bars[self.current_symbol][-1].close)
        mark_r = self._mark_r(side, entry, stop, close)
        elapsed = max(0, self.minute_index - self.position_open_minute)
        rsi_threshold = float(self.route_config.zara_rsi_threshold)
        di_threshold = float(self.route_config.zara_di_threshold)

        failed_components = 0
        failed_timeframes = 0
        timeframe_ok: dict[str, bool] = {}
        for label in ("5m", "15m", "30m"):
            rsi = float(live[f"rsi_{label}"])
            plus_di = float(live[f"plus_di_{label}"])
            minus_di = float(live[f"minus_di_{label}"])
            tf_close = float(live[f"close_{label}"])
            middle = float(live[f"bb_middle_{label}"])
            if side > 0:
                components = {
                    "rsi": rsi > rsi_threshold,
                    "di": plus_di > di_threshold,
                    "bb": tf_close > middle,
                }
            else:
                components = {
                    "rsi": rsi < rsi_threshold,
                    "di": minus_di > di_threshold,
                    "bb": tf_close < middle,
                }
            tf_ok = all(components.values())
            timeframe_ok[label] = tf_ok
            failed_components += sum(not value for value in components.values())
            failed_timeframes += int(not tf_ok)
            for component, value in components.items():
                diagnostics[f"forensic_latest_{label}_{component}_ok"] = int(value)
            diagnostics[f"forensic_latest_{label}_context_ok"] = int(tf_ok)

        same_side = failed_components == 0
        diagnostics["forensic_latest_failed_components"] = failed_components
        diagnostics["forensic_latest_failed_timeframes"] = failed_timeframes
        diagnostics["forensic_max_failed_components"] = max(
            int(diagnostics.get("forensic_max_failed_components", 0)),
            failed_components,
        )
        diagnostics["forensic_max_failed_timeframes"] = max(
            int(diagnostics.get("forensic_max_failed_timeframes", 0)),
            failed_timeframes,
        )
        self.diagnostics["zara_persistence_checks"] += 1

        prior_streak = int(
            diagnostics.get("forensic_current_invalidation_streak_checks", 0)
        )
        if same_side:
            if prior_streak > 0:
                self.diagnostics["zara_persistence_recoveries"] += 1
                diagnostics["forensic_recovery_count"] = int(
                    diagnostics.get("forensic_recovery_count", 0)
                ) + 1
                if "forensic_first_recovery_after_invalidation_minute" not in diagnostics:
                    diagnostics[
                        "forensic_first_recovery_after_invalidation_minute"
                    ] = elapsed
                    diagnostics[
                        "forensic_first_recovered_streak_checks"
                    ] = prior_streak
                    start = int(
                        diagnostics.get(
                            "forensic_current_invalidation_start_minute", elapsed
                        )
                    )
                    diagnostics[
                        "forensic_first_invalidation_episode_minutes"
                    ] = max(0, elapsed - start)
            diagnostics["forensic_current_invalidation_streak_checks"] = 0
            diagnostics.pop("forensic_current_invalidation_start_minute", None)
        else:
            streak = prior_streak + 1
            diagnostics["forensic_current_invalidation_streak_checks"] = streak
            if prior_streak == 0:
                self.diagnostics["zara_persistence_invalidation_episodes"] += 1
                diagnostics["forensic_invalidation_episode_count"] = int(
                    diagnostics.get("forensic_invalidation_episode_count", 0)
                ) + 1
                diagnostics["forensic_current_invalidation_start_minute"] = elapsed
            diagnostics["forensic_max_invalidation_streak_checks"] = max(
                int(diagnostics.get("forensic_max_invalidation_streak_checks", 0)),
                streak,
            )
            for threshold in self._STREAK_THRESHOLDS:
                key = f"forensic_first_invalidation_streak_{threshold}_minute"
                if streak >= threshold and key not in diagnostics:
                    diagnostics[key] = elapsed
                    diagnostics[
                        f"forensic_mark_r_at_first_invalidation_streak_{threshold}"
                    ] = mark_r
                    diagnostics[
                        f"forensic_failed_components_at_first_invalidation_streak_{threshold}"
                    ] = failed_components
                    diagnostics[
                        f"forensic_failed_timeframes_at_first_invalidation_streak_{threshold}"
                    ] = failed_timeframes

        event_map = {
            "15m_context_failure": not timeframe_ok["15m"],
            "30m_context_failure": not timeframe_ok["30m"],
            "two_timeframe_failure": failed_timeframes >= 2,
            "all_timeframe_failure": failed_timeframes == 3,
        }
        for name, active in event_map.items():
            key = f"forensic_first_{name}_minute"
            if active and key not in diagnostics:
                diagnostics[key] = elapsed
                diagnostics[f"forensic_mark_r_at_first_{name}"] = mark_r
                diagnostics[f"forensic_failed_components_at_first_{name}"] = (
                    failed_components
                )

        diagnostics["forensic_last_persistence_check_minute"] = elapsed
        diagnostics["forensic_last_persistence_mark_r"] = mark_r

    def _manage_open_position(self, ts_event: int) -> None:
        super()._manage_open_position(ts_event)
        # The source management above can submit an exit, but the scenario remains
        # available until the fill.  Recording after it does not alter that order.
        self._record_persistence(ts_event)

    def on_position_closed(self, event: Any) -> None:
        diagnostics = self._forensic_diagnostics()
        if diagnostics is not None:
            diagnostics["forensic_closing_invalidation_streak_checks"] = int(
                diagnostics.get("forensic_current_invalidation_streak_checks", 0)
            )
        super().on_position_closed(event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
