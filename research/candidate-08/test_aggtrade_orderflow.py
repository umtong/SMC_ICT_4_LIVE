from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from aggtrade_orderflow_probe import (
    _aggregate_chunk,
    _crossed_boundary,
    _net,
    _select_target,
)
from range_fvg_logic import ExternalLevel, LevelKind, LevelSource


def level(
    level_id: str,
    kind: LevelKind,
    value: float,
    source: LevelSource = LevelSource.FOUR_HOUR,
) -> ExternalLevel:
    return ExternalLevel(
        level_id=level_id,
        kind=kind,
        source=source,
        level=value,
        formed_index=1,
        formed_time_ns=1,
        period_key="p0",
    )


class AggTradeOrderFlowTests(unittest.TestCase):
    def test_buyer_maker_flag_maps_to_aggressive_sell_volume(self) -> None:
        chunk = pd.DataFrame(
            [
                [1, 100.0, 2.0, 10, 10, 1_712_534_400_001, False],
                [2, 100.1, 1.0, 11, 11, 1_712_534_400_002, True],
            ]
        )
        result = _aggregate_chunk(chunk)
        self.assertEqual(len(result.index), 1)
        row = result.iloc[0]
        self.assertAlmostEqual(float(row["volume"]), 3.0)
        self.assertAlmostEqual(float(row["signed_volume"]), 1.0)
        self.assertAlmostEqual(float(row["trade_count"]), 2.0)

    def test_completed_high_cross_is_detected_without_future_level(self) -> None:
        high = level("h4-high", LevelKind.HIGH, 100.0)
        low = level("h4-low", LevelKind.LOW, 95.0)
        crossed = _crossed_boundary(
            (high, low),
            previous_close=99.9,
            high=100.3,
            low=99.7,
            atr=1.0,
            consumed=set(),
        )
        self.assertIsNotNone(crossed)
        assert crossed is not None
        self.assertEqual(crossed[0].level_id, "h4-high")
        self.assertEqual(crossed[1], 1)
        self.assertIsNone(
            _crossed_boundary(
                (high, low),
                previous_close=99.9,
                high=100.3,
                low=99.7,
                atr=1.0,
                consumed={"h4-high"},
            )
        )

    def test_target_is_nearest_active_completed_level_not_r_projection(self) -> None:
        levels = (
            level("h-near", LevelKind.HIGH, 102.0),
            level("h-far", LevelKind.HIGH, 105.0, LevelSource.DAY),
            level("l", LevelKind.LOW, 97.0),
        )
        target = _select_target(
            levels,
            direction=1,
            entry=100.0,
            excluded_level_id="none",
        )
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.level_id, "h-near")

    def test_cost_function_charges_both_fills_and_two_ticks(self) -> None:
        gain = _net(1, 100.0, 102.0, 0.1)
        self.assertAlmostEqual(gain, 2.0 - 0.0006 * 202.0 - 0.2)
        loss = _net(1, 100.0, 99.0, 0.1)
        self.assertAlmostEqual(loss, -1.0 - 0.0006 * 199.0 - 0.2)


if __name__ == "__main__":
    unittest.main()
