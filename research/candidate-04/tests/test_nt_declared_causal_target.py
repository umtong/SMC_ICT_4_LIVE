from __future__ import annotations

import unittest

from nt_declared_causal_target import choose_declared_causal_target


class DeclaredTargetTests(unittest.TestCase):
    def signal(self, **details):
        return {
            "signal_index": 100,
            "details": {
                "causal_target_reference": 90.0,
                "causal_target_source": "causal_pivot_pool_17_low",
                "causal_target_observed_index": 50,
                **details,
            },
        }

    def test_valid_pre_signal_directional_target_is_accepted(self) -> None:
        target, error = choose_declared_causal_target(
            self.signal(),
            entry=100.0,
            stop=104.0,
            side=-1,
            cost_rate=0.00075,
            minimum_net_r=1.2,
        )
        self.assertIsNone(error)
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.price, 90.0)

    def test_same_bar_or_future_target_is_rejected(self) -> None:
        target, error = choose_declared_causal_target(
            self.signal(causal_target_observed_index=100),
            entry=100.0,
            stop=104.0,
            side=-1,
            cost_rate=0.00075,
            minimum_net_r=1.2,
        )
        self.assertIsNone(target)
        self.assertEqual(error, "compiler_target_not_observed_before_signal")

    def test_wrong_direction_does_not_fall_back(self) -> None:
        target, error = choose_declared_causal_target(
            self.signal(causal_target_reference=110.0),
            entry=100.0,
            stop=104.0,
            side=-1,
            cost_rate=0.00075,
            minimum_net_r=1.2,
        )
        self.assertIsNone(target)
        self.assertEqual(error, "compiler_target_wrong_direction")

    def test_undeclared_target_allows_execution_registry_fallback(self) -> None:
        target, error = choose_declared_causal_target(
            {"signal_index": 100, "details": {}},
            entry=100.0,
            stop=104.0,
            side=-1,
            cost_rate=0.00075,
            minimum_net_r=1.2,
        )
        self.assertIsNone(target)
        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
