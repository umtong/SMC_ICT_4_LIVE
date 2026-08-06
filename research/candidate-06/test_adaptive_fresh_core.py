from __future__ import annotations

import unittest

from adaptive_fresh_core import (
    DirectionalFreshnessClock,
    assess_prior_only_quality,
    empirical_percentile,
    linear_quantile,
)


class AdaptiveFreshCoreTests(unittest.TestCase):
    def test_linear_quantile_is_deterministic(self) -> None:
        self.assertEqual(linear_quantile([1.0, 2.0, 3.0, 4.0], 0.75), 3.25)
        self.assertEqual(linear_quantile([5.0], 0.75), 5.0)

    def test_current_auction_is_not_inserted_into_its_own_reference(self) -> None:
        prior_ranges = [1.0, 1.1, 1.2, 1.3]
        prior_volumes = [100.0, 110.0, 120.0, 130.0]
        result = assess_prior_only_quality(
            prior_ranges=prior_ranges,
            prior_volumes=prior_volumes,
            current_range=10.0,
            current_volume=1_000.0,
            current_body_fraction=0.8,
            enabled=True,
            lookback=4,
            minimum_history=4,
            quantile=0.75,
            body_floor=0.65,
        )
        self.assertAlmostEqual(result.range_threshold, 1.225, places=12)
        self.assertAlmostEqual(result.volume_threshold, 122.5, places=12)
        self.assertEqual(prior_ranges, [1.0, 1.1, 1.2, 1.3])
        self.assertEqual(prior_volumes, [100.0, 110.0, 120.0, 130.0])
        self.assertTrue(result.passed)

    def test_weak_range_or_volume_or_body_rejects_without_or_search(self) -> None:
        common = dict(
            prior_ranges=[1.0, 1.1, 1.2, 1.3],
            prior_volumes=[100.0, 110.0, 120.0, 130.0],
            enabled=True,
            lookback=4,
            minimum_history=4,
            quantile=0.75,
            body_floor=0.65,
        )
        self.assertFalse(assess_prior_only_quality(**common, current_range=1.0, current_volume=200.0, current_body_fraction=0.9).passed)
        self.assertFalse(assess_prior_only_quality(**common, current_range=2.0, current_volume=100.0, current_body_fraction=0.9).passed)
        self.assertFalse(assess_prior_only_quality(**common, current_range=2.0, current_volume=200.0, current_body_fraction=0.5).passed)

    def test_disabled_quality_is_a_true_single_variable_ablation(self) -> None:
        result = assess_prior_only_quality(
            prior_ranges=[10.0] * 12,
            prior_volumes=[100.0] * 12,
            current_range=1.0,
            current_volume=1.0,
            current_body_fraction=0.01,
            enabled=False,
            lookback=12,
            minimum_history=12,
            quantile=0.75,
            body_floor=0.65,
        )
        self.assertTrue(result.passed)
        self.assertFalse(result.enabled)

    def test_percentile_uses_mid_rank(self) -> None:
        self.assertEqual(empirical_percentile([1.0, 2.0, 2.0, 4.0], 2.0), 0.5)

    def test_long_freshness_resets_only_on_new_completed_close_extreme(self) -> None:
        clock = DirectionalFreshnessClock("LONG", 100.0, 10)
        self.assertFalse(clock.observe(close=99.0, index=11))
        self.assertFalse(clock.observe(close=100.0, index=12))
        self.assertEqual(clock.age(12), 2)
        self.assertTrue(clock.observe(close=100.1, index=13))
        self.assertEqual(clock.age(13), 0)

    def test_short_freshness_and_staleness_are_symmetric(self) -> None:
        clock = DirectionalFreshnessClock("SHORT", 100.0, 20)
        self.assertFalse(clock.observe(close=101.0, index=21))
        self.assertTrue(clock.observe(close=99.5, index=22))
        self.assertFalse(clock.is_stale(index=26, maximum_age_bars=4))
        self.assertTrue(clock.is_stale(index=27, maximum_age_bars=4))


if __name__ == "__main__":
    unittest.main()
