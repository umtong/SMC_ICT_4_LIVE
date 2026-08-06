from __future__ import annotations

import unittest

from derive_nt_lvcfr_v4_signals import (
    EXTERNAL_ACCEPTANCE_CONTINUATION,
    EXTERNAL_RECLAIM_REVERSAL,
    VALUE_EDGE_CONTINUATION,
    route_v4_state,
)
from nt_lvcfr_strategy import signal_entry_kind


BASE = {
    "origin_alignment": 0.40,
    "first_acceptance_fraction": 0.60,
    "minimum_origin_alignment": 2.0 / 3.0,
    "minimum_acceptance_fraction": 0.50,
    "directional_external_swept": True,
    "all_closes_beyond_directional_external": False,
    "all_closes_inside_prior_range": False,
    "directional_spot_flow": 0.10,
    "minimum_directional_spot_flow": 0.0,
    "total_oi_drop_bp": 15.0,
    "maximum_acceptance_oi_drop_bp": 20.0,
    "directional_price_change_bp": 1.0,
}


class StateRouterTests(unittest.TestCase):
    def route(self, **overrides):
        values = dict(BASE)
        values.update(overrides)
        return route_v4_state(**values)

    def test_value_edge_continuation_has_priority(self) -> None:
        state, _ = self.route(
            origin_alignment=0.80,
            all_closes_beyond_directional_external=True,
        )
        self.assertEqual(state, VALUE_EDGE_CONTINUATION)

    def test_moderate_spot_supported_external_acceptance(self) -> None:
        state, _ = self.route(all_closes_beyond_directional_external=True)
        self.assertEqual(state, EXTERNAL_ACCEPTANCE_CONTINUATION)

    def test_extreme_deleveraging_is_not_called_acceptance(self) -> None:
        state, reason = self.route(
            all_closes_beyond_directional_external=True,
            total_oi_drop_bp=20.0,
        )
        self.assertIsNone(state)
        self.assertEqual(reason, "EXTREME_DELEVERAGING_EXHAUSTION_RISK")

    def test_external_reclaim_requires_opposite_displacement(self) -> None:
        state, _ = self.route(
            all_closes_inside_prior_range=True,
            directional_price_change_bp=-1.0,
        )
        self.assertEqual(state, EXTERNAL_RECLAIM_REVERSAL)

    def test_range_reentry_without_opposite_displacement_is_no_trade(self) -> None:
        state, reason = self.route(
            all_closes_inside_prior_range=True,
            directional_price_change_bp=1.0,
        )
        self.assertIsNone(state)
        self.assertEqual(reason, "RANGE_REENTRY_WITHOUT_OPPOSITE_DISPLACEMENT")


class ExplicitEntryKindTests(unittest.TestCase):
    def test_legacy_schedule_defaults_to_continuation(self) -> None:
        self.assertEqual(signal_entry_kind({}), "CONTINUATION")

    def test_explicit_reversal_is_supported(self) -> None:
        self.assertEqual(signal_entry_kind({"entry_kind": "reversal"}), "REVERSAL")

    def test_unknown_entry_kind_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            signal_entry_kind({"entry_kind": "UNKNOWN"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
