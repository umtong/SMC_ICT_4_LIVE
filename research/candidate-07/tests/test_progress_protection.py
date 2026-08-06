from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from model import Direction  # noqa: E402
from strategy_progress import (  # noqa: E402
    ProgressDeliveryBlock,
    cost_floor_trigger_price,
    favorable_progress_r,
)


class ProgressProtectionGeometryTests(unittest.TestCase):
    def test_long_completed_close_progress_is_measured_in_initial_r(self) -> None:
        self.assertEqual(
            favorable_progress_r(
                direction=Direction.LONG,
                entry_price=Decimal("100"),
                initial_stop=Decimal("98"),
                close_price=Decimal("106"),
            ),
            Decimal("3"),
        )

    def test_short_completed_close_progress_is_symmetric(self) -> None:
        self.assertEqual(
            favorable_progress_r(
                direction=Direction.SHORT,
                entry_price=Decimal("100"),
                initial_stop=Decimal("102"),
                close_price=Decimal("94"),
            ),
            Decimal("3"),
        )

    def test_long_floor_covers_fees_funding_and_adverse_stop_tick(self) -> None:
        entry = Decimal("100")
        fee = Decimal("0.001")
        funding_bps = Decimal("2")
        tick = Decimal("0.1")
        trigger = cost_floor_trigger_price(
            direction=Direction.LONG,
            entry_price=entry,
            taker_fee_rate=fee,
            funding_reserve_bps=funding_bps,
            price_increment=tick,
        )
        stop_fill = trigger - tick
        funding = entry * funding_bps / Decimal("10000")
        net = stop_fill - entry - entry * fee - stop_fill * fee - funding
        self.assertGreaterEqual(net, Decimal("0"))
        self.assertEqual(trigger % tick, Decimal("0"))

    def test_short_floor_covers_fees_funding_and_adverse_stop_tick(self) -> None:
        entry = Decimal("100")
        fee = Decimal("0.001")
        funding_bps = Decimal("2")
        tick = Decimal("0.1")
        trigger = cost_floor_trigger_price(
            direction=Direction.SHORT,
            entry_price=entry,
            taker_fee_rate=fee,
            funding_reserve_bps=funding_bps,
            price_increment=tick,
        )
        stop_fill = trigger + tick
        funding = entry * funding_bps / Decimal("10000")
        net = entry - stop_fill - entry * fee - stop_fill * fee - funding
        self.assertGreaterEqual(net, Decimal("0"))
        self.assertEqual(trigger % tick, Decimal("0"))

    def test_wrong_side_initial_stop_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            favorable_progress_r(
                direction=Direction.LONG,
                entry_price=Decimal("100"),
                initial_stop=Decimal("101"),
                close_price=Decimal("106"),
            )

    def test_long_progress_lock_releases_only_at_declared_target(self) -> None:
        block = ProgressDeliveryBlock(
            direction=Direction.LONG,
            reset_price=105.0,
            source_scenario_id="long-progress",
            blocked_at_ns=10,
        )
        self.assertFalse(block.reset_reached(104.9))
        self.assertTrue(block.reset_reached(105.0))

    def test_short_progress_lock_is_symmetric(self) -> None:
        block = ProgressDeliveryBlock(
            direction=Direction.SHORT,
            reset_price=95.0,
            source_scenario_id="short-progress",
            blocked_at_ns=10,
        )
        self.assertFalse(block.reset_reached(95.1))
        self.assertTrue(block.reset_reached(95.0))

    def test_negative_cost_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            cost_floor_trigger_price(
                direction=Direction.LONG,
                entry_price=Decimal("100"),
                taker_fee_rate=Decimal("-0.001"),
                funding_reserve_bps=Decimal("2"),
                price_increment=Decimal("0.1"),
            )


if __name__ == "__main__":
    unittest.main()
