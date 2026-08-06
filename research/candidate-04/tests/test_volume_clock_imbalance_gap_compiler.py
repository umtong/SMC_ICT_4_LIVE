from __future__ import annotations

from types import SimpleNamespace
import unittest

import pandas as pd

import volume_clock_imbalance_gap_compiler as candidate


class GapFixture(unittest.TestCase):
    def bucket(self, bucket_id: int, **updates) -> candidate.v37.VolumeBucket:
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
            high=101.2,
            low=100.2,
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

    def thresholds(self) -> candidate.v37.BucketThresholds:
        return candidate.v37.BucketThresholds(
            imbalance_q75=0.50,
            imbalance_q50=0.25,
            absolute_return_q65=10.0,
            efficiency_q60=0.60,
            impact_q25=20.0,
            impact_q60=50.0,
            positive_oi_median=0.005,
        )

    def data(self, rows: int = 40) -> pd.DataFrame:
        return pd.DataFrame(
            {
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


class GapFormationTests(GapFixture):
    def test_three_completed_buckets_form_bullish_gap(self) -> None:
        buckets = [
            self.bucket(0, high=100.0, low=99.0, close=99.8),
            self.bucket(1, high=101.0, low=99.8, close=100.8),
            self.bucket(2, high=102.0, low=100.2, close=101.8),
        ]
        gap = candidate.form_gap(buckets, 2, self.data())
        self.assertIsNotNone(gap)
        assert gap is not None
        self.assertEqual(gap.side, 1)
        self.assertAlmostEqual(gap.lower, 100.0)
        self.assertAlmostEqual(gap.upper, 100.2)
        self.assertEqual(gap.first_position, 0)
        self.assertEqual(gap.final_position, 2)

    def test_overlapping_ranges_do_not_form_gap(self) -> None:
        buckets = [
            self.bucket(0, high=100.5, low=99.0),
            self.bucket(1),
            self.bucket(2, low=100.4),
        ]
        self.assertIsNone(candidate.form_gap(buckets, 2, self.data()))

    def test_middle_and_final_buckets_must_share_gap_direction(self) -> None:
        buckets = [
            self.bucket(0, high=100.0, low=99.0),
            self.bucket(1, side=-1, imbalance=-0.6),
            self.bucket(2, low=100.2),
        ]
        self.assertIsNone(candidate.form_gap(buckets, 2, self.data()))


class GapStateTests(GapFixture):
    def bullish_gap(self) -> tuple[list[candidate.v37.VolumeBucket], candidate.ImbalanceGap]:
        buckets = [
            self.bucket(
                0,
                high=100.0,
                low=99.0,
                close=99.8,
                oi_before=1000.0,
                oi_end=1001.0,
                basis_before=0.0,
                basis_end=0.1,
            ),
            self.bucket(
                1,
                high=101.0,
                low=99.8,
                close=100.8,
                oi_before=1001.0,
                oi_end=1007.0,
                basis_before=0.1,
                basis_end=0.8,
            ),
            self.bucket(
                2,
                low=100.2,
                high=102.0,
                close=101.8,
                oi_before=1007.0,
                oi_end=1012.0,
                basis_before=0.8,
                basis_end=1.2,
            ),
        ]
        gap = candidate.form_gap(buckets, 2, self.data())
        assert gap is not None
        return buckets, gap

    def test_informed_gap_requires_material_total_oi_creation(self) -> None:
        buckets, gap = self.bullish_gap()
        state = candidate.informed_gap_state(
            gap,
            buckets,
            self.thresholds(),
        )
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.state, "INFORMED_GAP")
        weak = list(buckets)
        weak[-1] = self.bucket(
            2,
            low=100.2,
            high=102.0,
            close=101.8,
            oi_before=1007.0,
            oi_end=1002.0,
            oi_change=-0.005,
            basis_before=0.8,
            basis_end=1.2,
        )
        self.assertIsNone(
            candidate.informed_gap_state(
                gap,
                weak,
                self.thresholds(),
            )
        )

    def test_inverse_gap_requires_exact_reclaimed_external_pool(self) -> None:
        take = candidate.v24.PoolTake(
            shock_index=7,
            pool_id=9,
            pool_side=1,
            trade_side=-1,
            level=101.0,
            extreme=101.5,
            penetration_atr=0.2,
            age_bars=30,
            prominence_atr=0.3,
            touches=2,
        )
        buckets = [
            self.bucket(
                0,
                side=-1,
                imbalance=-0.3,
                high=102.0,
                low=101.2,
                close=101.5,
            ),
            self.bucket(
                1,
                side=-1,
                imbalance=-0.6,
                high=101.6,
                low=100.5,
                close=100.8,
                oi_before=1000.0,
                oi_end=1010.0,
                oi_change=0.01,
                external_takes=(take,),
            ),
            self.bucket(
                2,
                side=-1,
                imbalance=-0.4,
                high=101.0,
                low=99.5,
                close=100.0,
                return_bps=-80.0,
                directional_return_bps=80.0,
                basis_before=0.5,
                basis_end=0.0,
                directional_basis_change_bps=0.5,
            ),
        ]
        gap = candidate.form_gap(buckets, 2, self.data())
        self.assertIsNotNone(gap)
        assert gap is not None
        state = candidate.inverse_gap_state(
            gap,
            buckets,
            self.thresholds(),
        )
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.inventory_route, "NEW_INVENTORY")
        unreclaimed = list(buckets)
        unreclaimed[-1] = self.bucket(
            2,
            side=-1,
            imbalance=-0.4,
            high=101.0,
            low=99.5,
            close=101.2,
            return_bps=-80.0,
            directional_return_bps=80.0,
            basis_before=0.5,
            basis_end=0.0,
            directional_basis_change_bps=0.5,
        )
        self.assertIsNone(
            candidate.inverse_gap_state(
                gap,
                unreclaimed,
                self.thresholds(),
            )
        )


class ResolutionTests(GapFixture):
    def test_retrace_and_resumption_are_separate_buckets(self) -> None:
        formation, gap = GapStateTests.bullish_gap(self)
        state = candidate.informed_gap_state(
            gap,
            formation,
            self.thresholds(),
        )
        assert state is not None
        retrace = self.bucket(
            3,
            side=-1,
            imbalance=-0.20,
            start_price=101.8,
            close=100.15,
            high=101.9,
            low=100.05,
            return_bps=-160.0,
            directional_return_bps=160.0,
            oi_before=1012.0,
            oi_end=1010.0,
            oi_change=-0.002,
            basis_before=1.2,
            basis_end=1.0,
            directional_basis_change_bps=0.2,
        )
        resume = self.bucket(
            4,
            side=1,
            imbalance=0.4,
            start_price=100.15,
            close=102.0,
            high=102.1,
            low=100.1,
            return_bps=180.0,
            directional_return_bps=180.0,
            oi_before=1010.0,
            oi_end=1011.0,
            oi_change=0.001,
            basis_before=1.0,
            basis_end=1.4,
            directional_basis_change_bps=0.4,
        )
        data = self.data(rows=50)
        intent, resolved = candidate.resolve_gap(
            data,
            [*formation, retrace, resume],
            state,
            data.index[-1],
            SimpleNamespace(stop_buffer_atr=0.1),
        )
        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(resolved, 4)
        self.assertEqual(intent.signal_index, resume.end_index)
        self.assertGreater(intent.signal_index, retrace.end_index)
        self.assertEqual(
            intent.scenario,
            candidate.INFORMED_GAP_CONTINUATION,
        )
        self.assertLess(intent.stop_level, resume.close)

    def test_gap_midpoint_must_be_accepted_before_resumption(self) -> None:
        formation, gap = GapStateTests.bullish_gap(self)
        state = candidate.informed_gap_state(
            gap,
            formation,
            self.thresholds(),
        )
        assert state is not None
        rejected = self.bucket(
            3,
            side=-1,
            imbalance=-0.2,
            close=gap.midpoint - 0.01,
            high=gap.upper,
            low=gap.lower - 0.1,
            oi_before=1012.0,
            oi_end=1010.0,
        )
        self.assertFalse(candidate.midpoint_accepted(rejected, gap))


if __name__ == "__main__":
    unittest.main()
