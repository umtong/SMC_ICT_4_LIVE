from __future__ import annotations

from types import SimpleNamespace
import unittest

import pandas as pd

import volume_clock_impact_residual_compiler as candidate


class VolumeClockTests(unittest.TestCase):
    def test_current_notional_outlier_cannot_change_its_target(self) -> None:
        values = [100.0] * (candidate.TARGET_MINIMUM_MINUTES + 5)
        values[-1] = 1_000_000.0
        data = pd.DataFrame({"notional_60s": values})
        targets = candidate.past_only_notional_target(data)
        self.assertAlmostEqual(float(targets.iloc[-1]), 500.0)

    def test_bucket_target_is_frozen_at_start(self) -> None:
        rows = candidate.TARGET_MINIMUM_MINUTES + 20
        data = pd.DataFrame(
            {
                "open": [100.0] * rows,
                "high": [101.0] * rows,
                "low": [99.0] * rows,
                "close": [100.0 + 0.01 * index for index in range(rows)],
                "notional_60s": [100.0] * rows,
                "flow_60s": [0.5] * rows,
                "metric_sum_open_interest": [1000.0 + index for index in range(rows)],
                "trade_index_basis_bps": [0.01 * index for index in range(rows)],
            }
        )
        buckets = candidate.build_volume_buckets(data, {})
        self.assertTrue(buckets)
        first = buckets[0]
        self.assertAlmostEqual(first.target_notional, 500.0)
        self.assertGreaterEqual(first.notional, first.target_notional)
        self.assertLessEqual(
            first.end_index - first.start_index + 1,
            candidate.MAX_BUCKET_BARS,
        )


class ThresholdTests(unittest.TestCase):
    def _bucket(self, bucket_id: int, imbalance: float) -> candidate.VolumeBucket:
        return candidate.VolumeBucket(
            bucket_id=bucket_id,
            start_index=bucket_id * 5,
            end_index=bucket_id * 5 + 4,
            target_notional=500.0,
            notional=500.0,
            signed_effort=imbalance * 500.0,
            imbalance=imbalance,
            side=1 if imbalance > 0 else -1,
            start_price=100.0,
            close=101.0,
            high=101.5,
            low=99.5,
            return_bps=100.0,
            directional_return_bps=100.0,
            path_bps=150.0,
            efficiency=2.0 / 3.0,
            impact_ratio=100.0 / abs(imbalance),
            oi_before=1000.0,
            oi_end=1010.0,
            oi_change=0.01,
            basis_before=0.0,
            basis_end=1.0,
            directional_basis_change_bps=1.0,
            external_takes=(),
        )

    def test_current_bucket_is_not_in_its_threshold_history(self) -> None:
        history = [
            self._bucket(index, 0.10 + index * 0.001)
            for index in range(candidate.MIN_HISTORY_BUCKETS)
        ]
        threshold = candidate.bucket_thresholds(history)
        self.assertIsNotNone(threshold)
        assert threshold is not None
        outlier = self._bucket(999, 0.99)
        unchanged = candidate.bucket_thresholds(history)
        with_outlier = candidate.bucket_thresholds([*history, outlier])
        self.assertEqual(threshold, unchanged)
        self.assertGreaterEqual(
            with_outlier.imbalance_q75,
            threshold.imbalance_q75,
        )


class ClassificationTests(unittest.TestCase):
    def _thresholds(self) -> candidate.BucketThresholds:
        return candidate.BucketThresholds(
            imbalance_q75=0.50,
            imbalance_q50=0.25,
            absolute_return_q65=10.0,
            efficiency_q60=0.60,
            impact_q25=20.0,
            impact_q60=50.0,
            positive_oi_median=0.005,
        )

    def _bucket(self, **updates) -> candidate.VolumeBucket:
        values = dict(
            bucket_id=1,
            start_index=10,
            end_index=14,
            target_notional=500.0,
            notional=500.0,
            signed_effort=300.0,
            imbalance=0.60,
            side=1,
            start_price=100.0,
            close=101.0,
            high=101.5,
            low=99.5,
            return_bps=100.0,
            directional_return_bps=100.0,
            path_bps=120.0,
            efficiency=0.80,
            impact_ratio=166.0,
            oi_before=1000.0,
            oi_end=1010.0,
            oi_change=0.01,
            basis_before=0.0,
            basis_end=1.0,
            directional_basis_change_bps=1.0,
            external_takes=(),
        )
        values.update(updates)
        return candidate.VolumeBucket(**values)

    def test_informed_state_requires_material_new_inventory(self) -> None:
        state = candidate.classify_bucket(
            self._bucket(),
            self._thresholds(),
        )
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.state, "INFORMED_NEW_INVENTORY")
        self.assertEqual(state.inventory_route, "NEW_INVENTORY")
        self.assertIsNone(
            candidate.classify_bucket(
                self._bucket(oi_change=0.001),
                self._thresholds(),
            )
        )

    def test_low_impact_external_take_routes_absorption(self) -> None:
        take = candidate.v24.PoolTake(
            shock_index=12,
            pool_id=7,
            pool_side=1,
            trade_side=-1,
            level=101.0,
            extreme=101.5,
            penetration_atr=0.2,
            age_bars=30,
            prominence_atr=0.3,
            touches=2,
        )
        bucket = self._bucket(
            return_bps=5.0,
            directional_return_bps=5.0,
            efficiency=0.10,
            impact_ratio=10.0,
            oi_end=990.0,
            oi_change=-0.01,
            external_takes=(take,),
        )
        state = candidate.classify_bucket(bucket, self._thresholds())
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.state, "EXTERNAL_POOL_ABSORPTION")
        self.assertEqual(state.inventory_route, "LIQUIDATION")
        self.assertEqual(state.pool_take, take)


class ResolutionTests(unittest.TestCase):
    def _bucket(self, bucket_id: int, **updates) -> candidate.VolumeBucket:
        values = dict(
            bucket_id=bucket_id,
            start_index=bucket_id * 5,
            end_index=bucket_id * 5 + 4,
            target_notional=500.0,
            notional=500.0,
            signed_effort=300.0,
            imbalance=0.60,
            side=1,
            start_price=100.0,
            close=101.0,
            high=101.5,
            low=99.5,
            return_bps=100.0,
            directional_return_bps=100.0,
            path_bps=120.0,
            efficiency=0.80,
            impact_ratio=166.0,
            oi_before=1000.0,
            oi_end=1010.0,
            oi_change=0.01,
            basis_before=0.0,
            basis_end=1.0,
            directional_basis_change_bps=1.0,
            external_takes=(),
        )
        values.update(updates)
        return candidate.VolumeBucket(**values)

    def test_pullback_and_resumption_are_separate_completed_buckets(self) -> None:
        shock = self._bucket(1)
        pullback = self._bucket(
            2,
            imbalance=-0.20,
            side=-1,
            start_price=101.0,
            close=100.7,
            high=101.1,
            low=100.5,
            return_bps=-30.0,
            directional_return_bps=30.0,
            oi_before=1010.0,
            oi_end=1009.5,
            oi_change=-0.0005,
            basis_before=1.0,
            basis_end=0.8,
            directional_basis_change_bps=0.2,
        )
        resume = self._bucket(
            3,
            start_price=100.7,
            close=101.3,
            high=101.4,
            low=100.6,
            return_bps=60.0,
            directional_return_bps=60.0,
            oi_before=1009.5,
            oi_end=1011.0,
            oi_change=0.0015,
            basis_before=0.8,
            basis_end=1.2,
            directional_basis_change_bps=0.4,
        )
        state = candidate.BucketState(
            bucket=shock,
            thresholds=candidate.BucketThresholds(
                imbalance_q75=0.5,
                imbalance_q50=0.25,
                absolute_return_q65=10.0,
                efficiency_q60=0.6,
                impact_q25=20.0,
                impact_q60=50.0,
                positive_oi_median=0.005,
            ),
            state="INFORMED_NEW_INVENTORY",
            inventory_route="NEW_INVENTORY",
        )
        rows = 25
        data = pd.DataFrame(
            {
                "high": [102.0] * rows,
                "low": [99.0] * rows,
                "atr": [1.0] * rows,
            },
            index=pd.date_range("2025-01-01", periods=rows, freq="min", tz="UTC"),
        )
        intent, resolved = candidate.resolve_informed_continuation(
            data,
            [shock, pullback, resume],
            0,
            state,
            data.index[-1],
            SimpleNamespace(stop_buffer_atr=0.1),
        )
        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(resolved, 2)
        self.assertEqual(intent.signal_index, resume.end_index)
        self.assertGreater(intent.signal_index, pullback.end_index)

    def test_inventory_resolution_routes_are_cause_specific(self) -> None:
        shock = self._bucket(1)
        lower_oi = self._bucket(2, oi_end=1005.0)
        rebuilt = self._bucket(2, oi_end=1012.0)
        self.assertTrue(
            candidate.route_inventory_resolved(
                "NEW_INVENTORY", shock, lower_oi
            )
        )
        self.assertFalse(
            candidate.route_inventory_resolved(
                "NEW_INVENTORY", shock, rebuilt
            )
        )
        liquidation_shock = self._bucket(1, oi_end=990.0, oi_change=-0.01)
        depleted = self._bucket(2, oi_end=990.5)
        self.assertTrue(
            candidate.route_inventory_resolved(
                "LIQUIDATION", liquidation_shock, depleted
            )
        )


if __name__ == "__main__":
    unittest.main()
