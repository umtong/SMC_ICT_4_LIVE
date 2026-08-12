from __future__ import annotations

import unittest

from domain import Side
from easychart_zones import PriceZone, ZoneKind, ZoneSide
from mtf_strategy_exit_v9 import OPPOSING_OB_EXIT_PROVENANCE, is_opposing_order_block


def zone(kind: ZoneKind, side: ZoneSide) -> PriceZone:
    return PriceZone(
        zone_id=f"TEST:{kind.value}:{side.value}",
        kind=kind,
        side=side,
        timeframe_minutes=15,
        lower=100.0,
        upper=101.0,
        invalidation=99.0 if side is ZoneSide.SUPPORT else 102.0,
        impulse_extreme=103.0 if side is ZoneSide.SUPPORT else 98.0,
        formed_index=2,
        formed_time_ns=2,
        observed_time_ns=3,
        formation_indices=(1, 2),
        strength_ratio=2.0,
    )


class OpposingOrderBlockExitTests(unittest.TestCase):
    def test_long_exits_only_for_resistance_order_block(self) -> None:
        self.assertTrue(
            is_opposing_order_block(Side.LONG, zone(ZoneKind.ORDER_BLOCK, ZoneSide.RESISTANCE)),
        )
        self.assertFalse(
            is_opposing_order_block(Side.LONG, zone(ZoneKind.ORDER_BLOCK, ZoneSide.SUPPORT)),
        )
        self.assertFalse(
            is_opposing_order_block(Side.LONG, zone(ZoneKind.FVG, ZoneSide.RESISTANCE)),
        )

    def test_short_exits_only_for_support_order_block(self) -> None:
        self.assertTrue(
            is_opposing_order_block(Side.SHORT, zone(ZoneKind.ORDER_BLOCK, ZoneSide.SUPPORT)),
        )
        self.assertFalse(
            is_opposing_order_block(Side.SHORT, zone(ZoneKind.ORDER_BLOCK, ZoneSide.RESISTANCE)),
        )
        self.assertFalse(
            is_opposing_order_block(Side.SHORT, zone(ZoneKind.FVG, ZoneSide.SUPPORT)),
        )

    def test_policy_is_source_option_not_an_outcome_score(self) -> None:
        self.assertTrue(OPPOSING_OB_EXIT_PROVENANCE.startswith("SOURCE_EXPLICIT_OPTION:"))
        self.assertIn("FULLY_EXITS", OPPOSING_OB_EXIT_PROVENANCE)


if __name__ == "__main__":
    unittest.main()
