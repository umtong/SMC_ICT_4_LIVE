from __future__ import annotations

from pathlib import Path
import unittest

import pandas as pd

import filter_candidate_signals as routes
import volume_clock_impact_residual_compiler as v37
import volume_clock_imbalance_gap_compiler as v38


ROOT = Path(__file__).resolve().parents[1]


class ContractFixture(unittest.TestCase):
    def bucket(self, bucket_id: int, **updates) -> v37.VolumeBucket:
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
        return v37.VolumeBucket(**values)


class CompletedBucketTests(ContractFixture):
    def test_only_frozen_target_completed_buckets_are_emitted(self) -> None:
        rows = v37.TARGET_MINIMUM_MINUTES + 30
        data = pd.DataFrame(
            {
                "open": [100.0] * rows,
                "high": [101.0] * rows,
                "low": [99.0] * rows,
                "close": [100.0] * rows,
                "notional_60s": [100.0] * rows,
                "flow_60s": [0.5] * rows,
                "metric_sum_open_interest": [1000.0] * rows,
                "trade_index_basis_bps": [0.0] * rows,
            }
        )
        # The final state cannot receive its frozen five-minute information
        # amount, so it must not appear as a partial event-time bucket.
        data.loc[rows - 3 :, "notional_60s"] = 0.0
        buckets = v37.build_volume_buckets(data, {})
        self.assertTrue(buckets)
        self.assertTrue(
            all(bucket.notional >= bucket.target_notional for bucket in buckets)
        )


class AuctionMeaningTests(ContractFixture):
    def test_same_direction_pause_is_not_counter_auction(self) -> None:
        shock = self.bucket(1, start_price=100.0, close=102.0)
        pause = self.bucket(
            2,
            start_price=102.0,
            close=102.1,
            side=1,
            imbalance=0.10,
        )
        self.assertFalse(v37.weak_counter_flow(shock, pause))
        self.assertFalse(v37.pullback_retains_displacement(shock, pause))

    def test_created_inventory_is_measured_relative_to_event_baseline(self) -> None:
        shock = self.bucket(1, oi_before=1000.0, oi_end=1010.0)
        retained = self.bucket(2, oi_end=1008.0)
        lost = self.bucket(2, oi_end=1007.9)
        self.assertTrue(v37.oi_creation_retained(shock, retained))
        self.assertFalse(v37.oi_creation_retained(shock, lost))

    def test_new_inventory_and_liquidation_have_different_resolution(self) -> None:
        new_inventory = self.bucket(1, oi_before=1000.0, oi_end=1010.0)
        half_unwound = self.bucket(2, oi_end=1005.0)
        insufficient = self.bucket(2, oi_end=1006.0)
        self.assertTrue(
            v37.route_inventory_resolved(
                "NEW_INVENTORY", new_inventory, half_unwound
            )
        )
        self.assertFalse(
            v37.route_inventory_resolved(
                "NEW_INVENTORY", new_inventory, insufficient
            )
        )
        liquidation = self.bucket(1, oi_before=1000.0, oi_end=980.0)
        small_rebuild = self.bucket(2, oi_end=984.0)
        large_rebuild = self.bucket(2, oi_end=985.0)
        self.assertTrue(
            v37.route_inventory_resolved(
                "LIQUIDATION", liquidation, small_rebuild
            )
        )
        self.assertFalse(
            v37.route_inventory_resolved(
                "LIQUIDATION", liquidation, large_rebuild
            )
        )

    def test_v38_retrace_requires_counter_flow_and_counter_return(self) -> None:
        gap = v38.ImbalanceGap(
            side=1,
            lower=100.0,
            upper=100.5,
            midpoint=100.25,
            first_position=0,
            middle_position=1,
            final_position=2,
            start_index=0,
            end_index=14,
            formation_low=99.0,
            formation_high=102.0,
        )
        state = v38.GapState(
            gap=gap,
            thresholds=v37.BucketThresholds(
                imbalance_q75=0.50,
                imbalance_q50=0.25,
                absolute_return_q65=10.0,
                efficiency_q60=0.60,
                impact_q25=20.0,
                impact_q60=50.0,
                positive_oi_median=0.005,
            ),
            state="INFORMED_GAP",
            inventory_route="NEW_INVENTORY",
            source_oi_before=1000.0,
            source_oi_end=1010.0,
        )
        same_price_direction = self.bucket(
            3,
            side=-1,
            imbalance=-0.20,
            return_bps=10.0,
        )
        actual_retrace = self.bucket(
            3,
            side=-1,
            imbalance=-0.20,
            return_bps=-10.0,
        )
        self.assertFalse(v38.weak_retrace(same_price_direction, state))
        self.assertTrue(v38.weak_retrace(actual_retrace, state))


class RoutingTests(unittest.TestCase):
    def test_repaired_candidate_families_have_controlled_routes(self) -> None:
        for family in ("v37", "v38", "v41"):
            self.assertIn(family, routes.ROUTES)
            self.assertEqual(
                set(routes.ROUTES[family]),
                {"full", "continuation", "reversal"},
            )

    def test_v41_route_ablation_filters_declared_scenarios_only(self) -> None:
        rows = [
            {"scenario": "DEPTH_NORMALIZED_POSITIVE_INNOVATION_PULLBACK_CONTINUATION"},
            {"scenario": "EXTERNAL_POOL_NEGATIVE_INNOVATION_TRAPPED_REVERSAL"},
            {"scenario": "EXTERNAL_POOL_NEGATIVE_INNOVATION_LIQUIDATION_REVERSAL"},
        ]
        self.assertEqual(
            len(routes.filter_rows(rows, "v41", "continuation")), 1
        )
        self.assertEqual(
            len(routes.filter_rows(rows, "v41", "reversal")), 2
        )


class TargetContractTests(unittest.TestCase):
    def test_submit_path_has_no_measured_move_fallback(self) -> None:
        text = (ROOT / "nt_rich_signal_strategy.py").read_text(
            encoding="utf-8"
        )
        submit = text.split("    def _submit_signal(", 1)[1]
        self.assertIn("projection_fallback_disabled", submit)
        self.assertNotIn("self._projection_target(", submit)
        self.assertIn("pre_existing_external_liquidity_only", submit)


if __name__ == "__main__":
    unittest.main()
