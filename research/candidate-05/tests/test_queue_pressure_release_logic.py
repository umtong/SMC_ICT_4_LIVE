from __future__ import annotations

import math
import unittest

from queue_pressure_release_logic import CompressionRange
from queue_pressure_release_logic import boundary_retest_invalidated
from queue_pressure_release_logic import compressed_range
from queue_pressure_release_logic import compression_structural_stop
from queue_pressure_release_logic import first_boundary_retest_response
from queue_pressure_release_logic import persistent_queue_pressure
from queue_pressure_release_logic import pressure_release_breakout


class QueuePressureReleaseLogicTest(unittest.TestCase):
    def compression(self) -> CompressionRange:
        value = compressed_range(
            highs=[100.2, 100.3, 100.1],
            lows=[99.8, 99.9, 99.7],
            atr=2.0,
            maximum_width_atr=0.5,
        )
        self.assertIsNotNone(value)
        assert value is not None
        return value

    def test_persistent_pressure_is_mirror_symmetric(self) -> None:
        self.assertEqual(persistent_queue_pressure([0.40, 0.45, 0.35]), 1)
        self.assertEqual(persistent_queue_pressure([-0.40, -0.45, -0.35]), -1)
        self.assertEqual(persistent_queue_pressure([0.40, -0.45, 0.35]), 0)
        self.assertEqual(persistent_queue_pressure([0.40, math.nan, 0.35]), 0)

    def test_compression_rejects_wide_or_nonfinite_ranges(self) -> None:
        self.assertIsNotNone(self.compression())
        self.assertIsNone(
            compressed_range(
                highs=[101.5, 102.0, 101.7],
                lows=[99.0, 99.2, 99.1],
                atr=2.0,
                maximum_width_atr=0.5,
            ),
        )
        self.assertIsNone(
            compressed_range(
                highs=[100.2, math.nan, 100.1],
                lows=[99.8, 99.9, 99.7],
                atr=2.0,
                maximum_width_atr=0.5,
            ),
        )

    def test_pressure_release_breakout_is_mirror_symmetric(self) -> None:
        compression = self.compression()
        long_ready = pressure_release_breakout(
            side=1,
            compression=compression,
            open_price=100.0,
            high=101.6,
            low=99.9,
            close=101.5,
            atr=2.0,
            flow_60s=0.20,
            efficiency_60s=0.60,
            notional_burst=1.20,
            bid_depth_change_1m=0.02,
            ask_depth_change_1m=-0.05,
            minimum_break_distance_atr=0.05,
            minimum_flow=0.10,
            minimum_efficiency=0.45,
            minimum_notional_burst=1.05,
            minimum_depth_withdrawal=0.01,
            minimum_close_location=0.62,
        )
        anchor = 100.0
        mirror = CompressionRange(
            lower=2 * anchor - compression.upper,
            upper=2 * anchor - compression.lower,
            midpoint=2 * anchor - compression.midpoint,
        )
        short_ready = pressure_release_breakout(
            side=-1,
            compression=mirror,
            open_price=2 * anchor - 100.0,
            high=2 * anchor - 99.9,
            low=2 * anchor - 101.6,
            close=2 * anchor - 101.5,
            atr=2.0,
            flow_60s=-0.20,
            efficiency_60s=0.60,
            notional_burst=1.20,
            bid_depth_change_1m=-0.05,
            ask_depth_change_1m=0.02,
            minimum_break_distance_atr=0.05,
            minimum_flow=0.10,
            minimum_efficiency=0.45,
            minimum_notional_burst=1.05,
            minimum_depth_withdrawal=0.01,
            minimum_close_location=0.62,
        )
        self.assertTrue(long_ready)
        self.assertEqual(long_ready, short_ready)

    def test_breakout_requires_flow_efficiency_notional_and_withdrawal(self) -> None:
        compression = self.compression()
        base = dict(
            side=1,
            compression=compression,
            open_price=100.0,
            high=101.6,
            low=99.9,
            close=101.5,
            atr=2.0,
            flow_60s=0.20,
            efficiency_60s=0.60,
            notional_burst=1.20,
            bid_depth_change_1m=0.02,
            ask_depth_change_1m=-0.05,
            minimum_break_distance_atr=0.05,
            minimum_flow=0.10,
            minimum_efficiency=0.45,
            minimum_notional_burst=1.05,
            minimum_depth_withdrawal=0.01,
            minimum_close_location=0.62,
        )
        self.assertTrue(pressure_release_breakout(**base))
        self.assertFalse(pressure_release_breakout(**(base | {"flow_60s": 0.05})))
        self.assertFalse(pressure_release_breakout(**(base | {"efficiency_60s": 0.30})))
        self.assertFalse(pressure_release_breakout(**(base | {"notional_burst": 1.00})))
        self.assertFalse(pressure_release_breakout(**(base | {"ask_depth_change_1m": 0.0})))

    def test_boundary_retest_and_stop_are_mirror_symmetric(self) -> None:
        compression = self.compression()
        long_ready = first_boundary_retest_response(
            side=1,
            boundary=compression.upper,
            high=100.9,
            low=100.2,
            close=100.7,
            flow_15s=0.02,
            depth_imbalance=0.2,
            maximum_counterflow=0.08,
        )
        anchor = 100.0
        mirror = CompressionRange(
            lower=2 * anchor - compression.upper,
            upper=2 * anchor - compression.lower,
            midpoint=2 * anchor - compression.midpoint,
        )
        short_ready = first_boundary_retest_response(
            side=-1,
            boundary=mirror.lower,
            high=2 * anchor - 100.2,
            low=2 * anchor - 100.9,
            close=2 * anchor - 100.7,
            flow_15s=-0.02,
            depth_imbalance=-0.2,
            maximum_counterflow=0.08,
        )
        self.assertTrue(long_ready)
        self.assertEqual(long_ready, short_ready)

        long_stop = compression_structural_stop(
            side=1,
            compression=compression,
            atr=2.0,
            stop_buffer_atr=0.08,
        )
        short_stop = compression_structural_stop(
            side=-1,
            compression=mirror,
            atr=2.0,
            stop_buffer_atr=0.08,
        )
        self.assertAlmostEqual(short_stop, 2 * anchor - long_stop)
        self.assertEqual(
            boundary_retest_invalidated(side=1, compression=compression, close=99.6),
            boundary_retest_invalidated(side=-1, compression=mirror, close=2 * anchor - 99.6),
        )


if __name__ == "__main__":
    unittest.main()
