from __future__ import annotations

from types import SimpleNamespace
import unittest

import pandas as pd

import causal_target_registry_enricher as candidate


class RegistryFixture(unittest.TestCase):
    def config(self):
        return SimpleNamespace(
            pivot_left=3,
            pivot_right=3,
            pool_merge_atr=0.18,
            sweep_min_atr=0.03,
            pool_min_age_minutes=15,
            pool_min_prominence_atr=0.1,
        )

    def data(self, rows: int = 3000) -> pd.DataFrame:
        index = pd.date_range(
            "2025-01-01",
            periods=rows,
            freq="min",
            tz="UTC",
        )
        return pd.DataFrame(
            {
                "open": [100.0] * rows,
                "high": [100.4] * rows,
                "low": [99.6] * rows,
                "close": [100.0] * rows,
                "atr": [1.0] * rows,
            },
            index=index,
        )


class CalendarRegistryTests(RegistryFixture):
    def test_completed_day_level_is_not_visible_before_completion(self) -> None:
        data = self.data(rows=1600)
        data.iloc[100, data.columns.get_loc("high")] = 130.0
        snapshots = candidate.active_calendar_liquidity_snapshots(data)
        self.assertFalse(
            any(
                level.source.startswith("completed_previous_day")
                for level in snapshots[1439]
            )
        )
        self.assertTrue(
            any(
                level.source.startswith("completed_previous_day")
                and level.side == 1
                and level.price == 130.0
                for level in snapshots[1440]
            )
        )

    def test_completed_level_is_removed_on_first_later_touch(self) -> None:
        data = self.data(rows=1600)
        data.iloc[100, data.columns.get_loc("high")] = 130.0
        data.iloc[1500, data.columns.get_loc("high")] = 130.0
        snapshots = candidate.active_calendar_liquidity_snapshots(data)
        self.assertTrue(
            any(level.price == 130.0 for level in snapshots[1499])
        )
        self.assertFalse(
            any(level.price == 130.0 for level in snapshots[1500])
        )


class TargetSelectionTests(RegistryFixture):
    def test_nearest_economic_level_is_selected_without_measured_move(self) -> None:
        levels = [
            candidate.RegistryLevel(1, 101.0, "completed_previous_day_near_high", 10),
            candidate.RegistryLevel(1, 105.0, "completed_previous_day_far_high", 10),
            candidate.RegistryLevel(1, 110.0, "completed_previous_week_farther_high", 5),
        ]
        target = candidate.choose_registry_target(
            levels,
            entry=100.0,
            stop=99.0,
            side=1,
            cost_rate=0.00075,
            minimum_net_r=1.20,
        )
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.price, 105.0)
        self.assertTrue(target.source.startswith("completed_previous_day"))

    def test_wrong_direction_and_uneconomic_levels_are_rejected(self) -> None:
        levels = [
            candidate.RegistryLevel(-1, 95.0, "completed_previous_day_low", 10),
            candidate.RegistryLevel(1, 100.5, "completed_previous_day_high", 10),
        ]
        self.assertIsNone(
            candidate.choose_registry_target(
                levels,
                entry=100.0,
                stop=99.0,
                side=1,
                cost_rate=0.00075,
                minimum_net_r=1.20,
            )
        )


class EnrichmentTests(RegistryFixture):
    def test_existing_execution_target_keeps_signal_undeclared(self) -> None:
        data = self.data(rows=1000)
        data.iloc[900:999, data.columns.get_loc("high")] = 110.0
        signal_index = 999
        row = {
            "scenario": "TEST",
            "side": 1,
            "signal_index": signal_index,
            "signal_time": data.index[signal_index].isoformat(),
            "observe_time": data.index[signal_index].isoformat(),
            "observe_time_ns": int(data.index[signal_index].value),
            "stop_level": 99.0,
            "event_indices": [signal_index],
            "details": {},
        }
        enriched, summary = candidate.enrich_signals(
            [row],
            data,
            self.config(),
        )
        self.assertEqual(summary["counts"]["existing_registry_target"], 1)
        self.assertNotIn(
            "causal_target_reference",
            enriched[0]["details"],
        )

    def test_old_untouched_completed_day_high_enriches_missing_target(self) -> None:
        data = self.data(rows=3000)
        data.iloc[100, data.columns.get_loc("high")] = 130.0
        signal_index = 2500
        row = {
            "scenario": "TEST",
            "side": 1,
            "signal_index": signal_index,
            "signal_time": data.index[signal_index].isoformat(),
            "observe_time": data.index[signal_index].isoformat(),
            "observe_time_ns": int(data.index[signal_index].value),
            "stop_level": 99.0,
            "event_indices": [signal_index],
            "details": {},
        }
        enriched, summary = candidate.enrich_signals(
            [row],
            data,
            self.config(),
        )
        details = enriched[0]["details"]
        self.assertEqual(summary["counts"]["new_declared_target"], 1)
        self.assertEqual(details["causal_target_reference"], 130.0)
        self.assertTrue(
            details["causal_target_source"].startswith(
                "completed_previous_day_"
            )
        )
        self.assertLess(
            details["causal_target_observed_index"],
            signal_index,
        )
        self.assertFalse(details["target_enrichment_changed_entry_logic"])


if __name__ == "__main__":
    unittest.main()
