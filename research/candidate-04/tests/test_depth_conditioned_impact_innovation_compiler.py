from __future__ import annotations

from dataclasses import replace
import unittest

import pandas as pd

import depth_conditioned_impact_innovation_compiler as candidate


class DepthImpactFixture(unittest.TestCase):
    def bucket(self, bucket_id: int, **updates) -> candidate.v37.VolumeBucket:
        values = dict(
            bucket_id=bucket_id,
            start_index=10 + bucket_id * 5,
            end_index=14 + bucket_id * 5,
            target_notional=500.0,
            notional=500.0,
            signed_effort=300.0,
            imbalance=0.60,
            side=1,
            start_price=100.0,
            close=101.0,
            high=101.2,
            low=99.8,
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
        return candidate.v37.VolumeBucket(**values)

    def data(self, rows: int = 300, *, ask_depth: float = 1000.0) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ask_depth_1": [ask_depth] * rows,
                "bid_depth_1": [ask_depth] * rows,
                "depth_snapshot_age_seconds": [5.0] * rows,
                "high": [105.0] * rows,
                "low": [95.0] * rows,
                "atr": [1.0] * rows,
            },
            index=pd.date_range(
                "2025-01-01",
                periods=rows,
                freq="min",
                tz="UTC",
            ),
        )


class PressureTests(DepthImpactFixture):
    def test_shallower_opposing_depth_increases_normalized_pressure(self) -> None:
        bucket = self.bucket(0)
        deep = candidate.signed_depth_pressure(
            self.data(ask_depth=2000.0), bucket
        )
        shallow = candidate.signed_depth_pressure(
            self.data(ask_depth=500.0), bucket
        )
        self.assertGreater(shallow, deep)
        self.assertAlmostEqual(shallow / deep, 4.0)

    def test_post_event_depth_cannot_change_current_pressure(self) -> None:
        bucket = self.bucket(0)
        baseline = self.data()
        changed = baseline.copy()
        changed.loc[changed.index[bucket.start_index :], "ask_depth_1"] = 1.0
        self.assertAlmostEqual(
            candidate.signed_depth_pressure(baseline, bucket),
            candidate.signed_depth_pressure(changed, bucket),
        )

    def test_stale_depth_rejects_event_state(self) -> None:
        bucket = self.bucket(0)
        data = self.data()
        data.loc[
            data.index[
                bucket.start_index - candidate.DEPTH_LOOKBACK_MINUTES :
                bucket.start_index
            ],
            "depth_snapshot_age_seconds",
        ] = candidate.MAX_DEPTH_AGE_SECONDS + 1.0
        self.assertTrue(
            pd.isna(candidate.signed_depth_pressure(data, bucket))
        )


class ModelTests(DepthImpactFixture):
    def test_current_bucket_is_excluded_from_its_impact_model(self) -> None:
        data = self.data()
        history = [
            self.bucket(
                index,
                signed_effort=100.0 + index,
                return_bps=5.0 + 0.1 * index,
            )
            for index in range(candidate.MIN_MODEL_BUCKETS)
        ]
        model = candidate.robust_impact_model(data, history)
        self.assertIsNotNone(model)
        assert model is not None
        outlier = self.bucket(
            99,
            signed_effort=10_000.0,
            return_bps=10_000.0,
        )
        unchanged = candidate.robust_impact_model(data, history)
        contaminated = candidate.robust_impact_model(data, [*history, outlier])
        self.assertEqual(model, unchanged)
        self.assertNotEqual(model, contaminated)

    def test_absorbed_and_adverse_responses_remain_in_expected_impact_sample(self) -> None:
        data = self.data(rows=500)
        history = []
        for index in range(candidate.MIN_MODEL_BUCKETS):
            side = 1 if index % 2 == 0 else -1
            response = 8.0 if index % 4 < 2 else -2.0
            history.append(
                self.bucket(
                    index,
                    side=side,
                    signed_effort=side * 250.0,
                    imbalance=side * 0.5,
                    return_bps=side * response,
                    directional_return_bps=response,
                )
            )
        model = candidate.robust_impact_model(data, history)
        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual(model.sample_size, len(history))
        self.assertGreater(model.beta, 0.0)
        self.assertGreaterEqual(model.residual_scale, candidate.MIN_MAD_BPS)


class PullbackTests(DepthImpactFixture):
    def test_same_direction_pause_is_not_a_pullback(self) -> None:
        shock = self.bucket(0, close=102.0)
        pause = self.bucket(
            1,
            start_price=102.0,
            close=102.1,
            side=1,
            imbalance=0.10,
        )
        valid, _ = candidate.actual_counter_pullback(shock, pause)
        self.assertFalse(valid)

    def test_true_counter_price_and_counter_flow_pullback_is_accepted(self) -> None:
        shock = self.bucket(0, start_price=100.0, close=102.0)
        pullback = self.bucket(
            1,
            start_price=102.0,
            close=101.2,
            side=-1,
            imbalance=-0.20,
        )
        valid, fraction = candidate.actual_counter_pullback(shock, pullback)
        self.assertTrue(valid)
        self.assertAlmostEqual(fraction, 0.4)


if __name__ == "__main__":
    unittest.main()
