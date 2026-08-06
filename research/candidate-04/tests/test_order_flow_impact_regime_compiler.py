from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

import order_flow_impact_regime_compiler as candidate


class OrderFlowImpactRegimeTests(unittest.TestCase):
    def test_shifted_quantile_excludes_current_outlier(self) -> None:
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 100.0])
        result = candidate.shifted_quantile(series, 0.50, 4, 4)
        self.assertTrue(pd.isna(result.iloc[3]))
        self.assertAlmostEqual(float(result.iloc[4]), 2.5)

    def test_depth_replenishment_is_directional(self) -> None:
        values = {}
        for band in candidate.DEPTH_BANDS:
            values[f"bid_chg_{band}_60s"] = 0.40
            values[f"ask_chg_{band}_60s"] = 0.10
        row = pd.Series(values)
        self.assertAlmostEqual(
            candidate.directional_depth_replenishment(row, 1),
            0.30,
        )
        self.assertAlmostEqual(
            candidate.directional_depth_replenishment(row, -1),
            -0.30,
        )

    def test_flow_regime_requires_material_oi_creation(self) -> None:
        index = pd.date_range("2025-01-01", periods=20, freq="1min", tz="UTC")
        data = pd.DataFrame(
            {
                "flow_300s": 0.50,
                "ret_300s_bps": 10.0,
                "flow_sign_persistence_300s": 0.90,
                "eff_300s": 0.80,
                "notional_burst_60s": 2.0,
                "basis_change_15m": 1.0,
                "metric_sum_open_interest": [100.0] * 4 + [101.0] * 16,
            },
            index=index,
        )
        constant = pd.Series(0.0, index=index)
        thresholds = candidate.PastOnlyThresholds(
            flow_300s_q80=pd.Series(0.40, index=index),
            abs_return_300s_q70=pd.Series(8.0, index=index),
            persistence_300s_q65=pd.Series(0.70, index=index),
            efficiency_300s_q65=pd.Series(0.60, index=index),
            notional_burst_60s_q70=pd.Series(1.5, index=index),
            flow_60s_q80=constant,
            abs_return_60s_q80=constant,
            absorption_60s_q80=constant,
            efficiency_60s_q35=constant,
            positive_oi_step_median=pd.Series(0.005, index=index),
        )
        result = candidate.flow_regime_state(data, 19, thresholds)
        self.assertIsNotNone(result)
        assert result is not None
        side, details = result
        self.assertEqual(side, 1)
        self.assertGreaterEqual(details["open_interest_change_15m"], 0.005)

        data.loc[index[19], "metric_sum_open_interest"] = 100.1
        self.assertIsNone(candidate.flow_regime_state(data, 19, thresholds))

    def test_continuation_waits_for_pullback_and_structure_resumption(self) -> None:
        index = pd.date_range("2025-01-01", periods=40, freq="1min", tz="UTC")
        rows = []
        for _ in index:
            rows.append(
                {
                    "open": 100.0,
                    "high": 100.2,
                    "low": 99.8,
                    "close": 100.0,
                    "atr": 1.0,
                    "flow_60s": 0.0,
                    "ret_60s_bps": 0.0,
                    "basis_change_5m": 0.0,
                    "absorption_60s": 0.20,
                    "eff_60s": 0.40,
                    "flow_accel_15_vs_prior45": 0.0,
                    "metric_sum_open_interest": 100.0,
                }
            )
        rows[5]["close"] = 100.0
        rows[10].update(
            {"close": 102.0, "high": 102.2, "metric_sum_open_interest": 101.0}
        )
        rows[12].update(
            {
                "close": 101.0,
                "high": 101.2,
                "low": 100.8,
                "flow_60s": -0.3,
                "ret_60s_bps": -2.0,
                "metric_sum_open_interest": 101.0,
            }
        )
        rows[13].update(
            {
                "close": 103.0,
                "high": 103.2,
                "low": 100.9,
                "flow_60s": 0.5,
                "ret_60s_bps": 5.0,
                "basis_change_5m": 0.5,
                "absorption_60s": 0.30,
                "eff_60s": 0.40,
                "flow_accel_15_vs_prior45": 0.0,
                "metric_sum_open_interest": 101.2,
            }
        )
        data = pd.DataFrame(rows, index=index)
        zero = pd.Series(0.0, index=index)
        thresholds = candidate.PastOnlyThresholds(
            flow_300s_q80=zero,
            abs_return_300s_q70=zero,
            persistence_300s_q65=zero,
            efficiency_300s_q65=zero,
            notional_burst_60s_q70=zero,
            flow_60s_q80=pd.Series(0.4, index=index),
            abs_return_60s_q80=zero,
            absorption_60s_q80=zero,
            efficiency_60s_q35=zero,
            positive_oi_step_median=zero,
        )
        config = SimpleNamespace(
            trend_absorption_60s_min=0.15,
            trend_efficiency_60s_max=0.60,
            trend_flow_acceleration_max=0.50,
        )
        impact = SimpleNamespace(stop_buffer_atr=0.10)

        def fake_state(_data, position, _thresholds):
            if position == 10:
                return 1, {"regime": "mock"}
            return None

        with patch.object(candidate, "flow_regime_state", side_effect=fake_state):
            intents, counts = candidate.detect_informed_flow_regime_intents(
                data,
                index[0],
                index[-1],
                config,
                impact,
                thresholds,
            )
        self.assertEqual(counts["confirmed_resumption"], 1)
        self.assertEqual(len(intents), 1)
        intent = intents[0]
        self.assertEqual(intent.scenario, candidate.CONTINUATION_SCENARIO)
        self.assertEqual(intent.side, 1)
        self.assertEqual(intent.signal_index, 13)
        self.assertLess(intent.stop_level, float(data["close"].iloc[13]))

    def test_absorbed_external_flow_requires_exact_reclaim(self) -> None:
        index = pd.date_range("2025-01-01", periods=30, freq="1min", tz="UTC")
        rows = []
        for _ in index:
            row = {
                "open": 100.0,
                "high": 100.5,
                "low": 99.5,
                "close": 100.0,
                "atr": 1.0,
                "flow_60s": 0.0,
                "ret_60s_bps": 0.0,
                "notional_burst_60s": 1.0,
                "absorption_60s": 0.1,
                "eff_60s": 0.5,
                "basis_change_5m": 0.0,
                "metric_sum_open_interest": 100.0,
                "depth_snapshot_age_seconds": 10.0,
            }
            for band in candidate.DEPTH_BANDS:
                row[f"bid_chg_{band}_60s"] = 0.0
                row[f"ask_chg_{band}_60s"] = 0.0
            rows.append(row)
        rows[10].update(
            {
                "high": 102.0,
                "close": 101.5,
                "flow_60s": 0.8,
                "ret_60s_bps": 8.0,
                "notional_burst_60s": 3.0,
                "absorption_60s": 0.9,
                "eff_60s": 0.1,
                "metric_sum_open_interest": 99.0,
            }
        )
        for band in candidate.DEPTH_BANDS:
            rows[10][f"bid_chg_{band}_60s"] = 0.1
            rows[10][f"ask_chg_{band}_60s"] = 0.5
        rows[11].update(
            {
                "close": 99.5,
                "high": 101.8,
                "flow_60s": -0.6,
                "ret_60s_bps": -5.0,
                "basis_change_5m": -0.5,
                "metric_sum_open_interest": 98.8,
            }
        )
        data = pd.DataFrame(rows, index=index)
        zero = pd.Series(0.0, index=index)
        thresholds = candidate.PastOnlyThresholds(
            flow_300s_q80=zero,
            abs_return_300s_q70=zero,
            persistence_300s_q65=zero,
            efficiency_300s_q65=zero,
            notional_burst_60s_q70=pd.Series(2.0, index=index),
            flow_60s_q80=pd.Series(0.5, index=index),
            abs_return_60s_q80=zero,
            absorption_60s_q80=pd.Series(0.7, index=index),
            efficiency_60s_q35=pd.Series(0.2, index=index),
            positive_oi_step_median=zero,
        )
        take = candidate.v24.PoolTake(
            shock_index=10,
            pool_id=1,
            pool_side=1,
            trade_side=-1,
            level=100.0,
            extreme=102.0,
            penetration_atr=2.0,
            age_bars=60,
            prominence_atr=1.0,
            touches=2,
        )
        config = SimpleNamespace()
        impact = SimpleNamespace(stop_buffer_atr=0.10)
        with patch.object(
            candidate.v24,
            "detect_external_pool_takes",
            return_value={10: [take]},
        ):
            intents, counts = candidate.detect_absorbed_flow_reversal_intents(
                data,
                index[0],
                index[-1],
                config,
                impact,
                thresholds,
            )
        self.assertEqual(counts["confirmed_reversal"], 1)
        self.assertEqual(len(intents), 1)
        intent = intents[0]
        self.assertEqual(intent.scenario, candidate.REVERSAL_SCENARIO)
        self.assertEqual(intent.side, -1)
        self.assertEqual(intent.signal_index, 11)
        self.assertGreater(intent.stop_level, float(data["close"].iloc[11]))


if __name__ == "__main__":
    unittest.main()
