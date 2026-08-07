from __future__ import annotations

from decimal import Decimal
import unittest
from unittest.mock import patch

import pandas as pd

from diagnose_value_normalized_mss_fvg import (
    ValueNormalizedMSSFVGLogic,
    _cost_adjusted_terminal_r,
    events_from_detector,
    find_value_normalization,
)
from execution_cost_geometry import adverse_execution_geometry


class ValueNormalizedStateTests(unittest.TestCase):
    def _bars(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "timestamp_ns": [
                    1_000_000_000,
                    2_000_000_000,
                    3_000_000_000,
                    4_000_000_000,
                ],
                "open": [100.0, 100.0, 99.5, 98.9],
                "high": [100.5, 100.2, 99.7, 99.0],
                "low": [99.8, 99.4, 98.7, 98.5],
                "close": [100.0, 99.5, 98.9, 98.7],
            }
        )

    def _detector(self) -> dict:
        return {
            "scenarios": [
                {
                    "scenario_id": "detector-1",
                    "outcome": "EVENT_ACCEPTED",
                    "direction": "SHORT",
                    "pool_id": "5MH-source",
                    "liquidity_level": 100.0,
                    "event_extreme": 101.5,
                    "contact": {"timestamp_ns": 1_000_000_000},
                    "recovery_terminal": {
                        "timestamp_ns": 1_000_000_000,
                        "close": 100.0,
                    },
                }
            ]
        }

    def test_value_normalization_uses_completed_close(self) -> None:
        result = find_value_normalization(
            self._bars(),
            recovery_index=0,
            direction="SHORT",
            value=99.0,
            event_extreme=101.5,
            maximum_seconds=3,
        )
        self.assertEqual(result["outcome"], "VALUE_NORMALIZED")
        self.assertEqual(result["timestamp_ns"], 3_000_000_000)

    def test_source_invalidation_precedes_later_value(self) -> None:
        bars = self._bars()
        bars.loc[1, "high"] = 101.6
        result = find_value_normalization(
            bars,
            recovery_index=0,
            direction="SHORT",
            value=99.0,
            event_extreme=101.5,
            maximum_seconds=3,
        )
        self.assertEqual(
            result["outcome"],
            "SOURCE_INVALIDATED_BEFORE_VALUE_NORMALIZATION",
        )
        self.assertEqual(result["timestamp_ns"], 2_000_000_000)

    def test_ablation_changes_only_mss_search_start(self) -> None:
        bars = self._bars()
        with patch(
            "diagnose_value_normalized_mss_fvg._pre_attack_value",
            return_value=(99.0, {"target_statistic": "vwap"}),
        ):
            baseline, baseline_details, baseline_diagnostics = (
                events_from_detector(
                    bars,
                    detector_report=self._detector(),
                    logic=ValueNormalizedMSSFVGLogic(),
                    require_value_normalization=True,
                )
            )
            ablation, ablation_details, ablation_diagnostics = (
                events_from_detector(
                    bars,
                    detector_report=self._detector(),
                    logic=ValueNormalizedMSSFVGLogic(),
                    require_value_normalization=False,
                )
            )
        self.assertEqual(baseline_diagnostics, [])
        self.assertEqual(ablation_diagnostics, [])
        self.assertEqual(len(baseline), 1)
        self.assertEqual(len(ablation), 1)
        self.assertEqual(baseline[0].event_end_ns, 3_000_000_000)
        self.assertEqual(ablation[0].event_end_ns, 1_000_000_000)
        baseline_source = next(iter(baseline_details.values()))
        ablation_source = next(iter(ablation_details.values()))
        for key in (
            "source_scenario_id",
            "direction",
            "source_pool_id",
            "source_level",
            "event_extreme",
            "pre_attack_value",
        ):
            self.assertEqual(baseline_source[key], ablation_source[key])


class CostAdjustedTerminalTests(unittest.TestCase):
    def test_flat_timeout_is_negative_after_execution_costs(self) -> None:
        geometry = adverse_execution_geometry(
            direction="LONG",
            entry_reference=Decimal("100"),
            stop_price=Decimal("99"),
            target_price=Decimal("102"),
            price_increment=Decimal("0.1"),
            taker_fee_rate=Decimal("0.001"),
            funding_reserve_bps=Decimal("1"),
        )
        result = _cost_adjusted_terminal_r(
            path={"outcome": "TIMEOUT", "terminal_close_r": 0.0},
            direction="LONG",
            entry=Decimal("100"),
            stop=Decimal("99"),
            geometry=geometry,
            price_increment=Decimal("0.1"),
            taker_fee_rate=Decimal("0.001"),
        )
        self.assertLess(result, 0)

    def test_target_and_stop_use_declared_cost_budget(self) -> None:
        geometry = adverse_execution_geometry(
            direction="SHORT",
            entry_reference=Decimal("100"),
            stop_price=Decimal("101"),
            target_price=Decimal("97"),
            price_increment=Decimal("0.1"),
            taker_fee_rate=Decimal("0.001"),
            funding_reserve_bps=Decimal("1"),
        )
        target = _cost_adjusted_terminal_r(
            path={"outcome": "TARGET", "terminal_close_r": 0.0},
            direction="SHORT",
            entry=Decimal("100"),
            stop=Decimal("101"),
            geometry=geometry,
            price_increment=Decimal("0.1"),
            taker_fee_rate=Decimal("0.001"),
        )
        stop = _cost_adjusted_terminal_r(
            path={"outcome": "STOP", "terminal_close_r": 0.0},
            direction="SHORT",
            entry=Decimal("100"),
            stop=Decimal("101"),
            geometry=geometry,
            price_increment=Decimal("0.1"),
            taker_fee_rate=Decimal("0.001"),
        )
        self.assertEqual(target, geometry.cost_adjusted_target_r)
        self.assertEqual(stop, Decimal("-1"))


if __name__ == "__main__":
    unittest.main()
