from __future__ import annotations

import unittest

from objective_lifecycle_core import (
    ControlAuction,
    ControlThresholds,
    DirectionalLeg,
    ObjectiveKey,
    ObjectiveLedger,
    classify_control_auction,
)


class ObjectiveLedgerTests(unittest.TestCase):
    def test_objective_is_invisible_to_touch_on_its_confirmation_bar(self):
        ledger = ObjectiveLedger()
        key = ObjectiveKey("POOL", "UPPER", "pool-1")
        ledger.register(
            key,
            level=110.0,
            reason="CONFIRMED_LTF_BUYSIDE_LIQUIDITY",
            confirmed_index=10,
            confirmed_ts_ns=10,
        )
        self.assertFalse(
            ledger.observe_completed_bar(index=10, high=111.0, low=100.0),
        )
        self.assertTrue(ledger.get(key).available)  # type: ignore[union-attr]
        touched = ledger.observe_completed_bar(index=11, high=110.0, low=105.0)
        self.assertEqual(len(touched), 1)
        self.assertFalse(ledger.get(key).available)  # type: ignore[union-attr]

    def test_side_filter_preserves_sweep_side_until_end_of_bar(self):
        ledger = ObjectiveLedger()
        upper = ObjectiveKey("POOL", "UPPER", "upper")
        lower = ObjectiveKey("POOL", "LOWER", "lower")
        ledger.register(upper, level=105.0, reason="UPPER", confirmed_index=1, confirmed_ts_ns=1)
        ledger.register(lower, level=95.0, reason="LOWER", confirmed_index=1, confirmed_ts_ns=1)
        touched = ledger.observe_completed_bar(
            index=2,
            high=106.0,
            low=94.0,
            sides={"UPPER"},
        )
        self.assertEqual([value.key for value in touched], [upper])
        self.assertTrue(ledger.get(lower).available)  # type: ignore[union-attr]
        ledger.observe_completed_bar(index=2, high=106.0, low=94.0, sides={"LOWER"})
        self.assertFalse(ledger.get(lower).available)  # type: ignore[union-attr]

    def test_reserved_objective_cannot_be_reused(self):
        ledger = ObjectiveLedger()
        key = ObjectiveKey("ACTIVE_LEG_EXTREME", "LOWER", "ctx:1")
        ledger.register(
            key,
            level=90.0,
            reason="ACTIVE_DIRECTIONAL_LEG_EXTREME",
            confirmed_index=5,
            confirmed_ts_ns=5,
        )
        self.assertTrue(ledger.reserve(key, index=6))
        self.assertFalse(ledger.reserve(key, index=7))
        self.assertEqual(
            ledger.available_for_direction(direction="SHORT", entry=100.0),
            [],
        )

    def test_available_objectives_are_directional_and_nearest_first(self):
        ledger = ObjectiveLedger()
        for source, level in (("a", 105.0), ("b", 103.0), ("c", 95.0)):
            ledger.register(
                ObjectiveKey("POOL", "UPPER" if level > 100.0 else "LOWER", source),
                level=level,
                reason="POOL",
                confirmed_index=1,
                confirmed_ts_ns=1,
            )
        values = ledger.available_for_direction(direction="LONG", entry=100.0)
        self.assertEqual([value.level for value in values], [103.0, 105.0])

    def test_directional_leg_reserves_exactly_one_scenario(self):
        key = ObjectiveKey("ACTIVE_LEG_EXTREME", "UPPER", "ctx:1")
        leg = DirectionalLeg("ctx", 1, "LONG", 100.0, 110.0, 5, 5, key)
        self.assertTrue(leg.reserve_entry(scenario_id="s1", index=6))
        self.assertTrue(leg.reserved_for("s1"))
        self.assertFalse(leg.reserve_entry(scenario_id="s2", index=7))


class ControlAuctionTests(unittest.TestCase):
    def setUp(self):
        self.thresholds = ControlThresholds(use_flow=True)

    def classify(self, *, direction, auction, prior_high, prior_low, atr, leg_origin):
        return classify_control_auction(
            direction=direction,
            auction=auction,
            prior_high=prior_high,
            prior_low=prior_low,
            atr=atr,
            baseline_volume=100.0,
            leg_origin=leg_origin,
            thresholds=self.thresholds,
        )

    def test_same_direction_long_acceptance_renews_leg(self):
        decision = self.classify(
            direction="LONG",
            auction=ControlAuction(101.0, 106.0, 100.5, 105.5, 120.0, 90.0, 15),
            prior_high=104.0,
            prior_low=98.0,
            atr=4.0,
            leg_origin=100.0,
        )
        self.assertTrue(decision.same_direction_renewal)
        self.assertFalse(decision.opposing_acceptance)

    def test_opposing_acceptance_has_priority(self):
        decision = self.classify(
            direction="LONG",
            auction=ControlAuction(100.0, 100.5, 93.0, 93.5, 120.0, 24.0, 30),
            prior_high=106.0,
            prior_low=95.0,
            atr=5.0,
            leg_origin=98.0,
        )
        self.assertTrue(decision.opposing_acceptance)
        self.assertEqual(decision.classification, "OPPOSING_CONTROL_AUCTION_ACCEPTED")

    def test_origin_loss_suspends_leg_without_full_opposing_break(self):
        decision = self.classify(
            direction="LONG",
            auction=ControlAuction(102.0, 102.2, 98.0, 98.4, 120.0, 24.0, 45),
            prior_high=108.0,
            prior_low=95.0,
            atr=5.0,
            leg_origin=100.0,
        )
        self.assertTrue(decision.leg_origin_lost)
        self.assertFalse(decision.opposing_acceptance)

    def test_short_path_is_symmetric(self):
        decision = self.classify(
            direction="SHORT",
            auction=ControlAuction(99.0, 99.5, 93.0, 93.4, 120.0, 24.0, 60),
            prior_high=104.0,
            prior_low=95.0,
            atr=4.0,
            leg_origin=100.0,
        )
        self.assertTrue(decision.same_direction_renewal)

    def test_low_relative_volume_cannot_create_acceptance(self):
        decision = classify_control_auction(
            direction="LONG",
            auction=ControlAuction(101.0, 106.0, 100.5, 105.5, 50.0, 40.0, 75),
            prior_high=104.0,
            prior_low=98.0,
            atr=4.0,
            baseline_volume=100.0,
            leg_origin=100.0,
            thresholds=self.thresholds,
        )
        self.assertEqual(decision.classification, "NO_CONTROL_STATE_CHANGE")


if __name__ == "__main__":
    unittest.main()
