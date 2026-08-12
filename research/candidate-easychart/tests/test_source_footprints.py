from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from domain_v3 import Candle, Side
from source_footprints import detect_fvgs, detect_order_blocks


NS = 60_000_000_000


def bar(index, open_, high, low, close, minutes=5):
    start = index * minutes * NS
    return Candle(start, start + minutes * NS - 1, open_, high, low, close, 1.0)


class TestSourceOrderBlocks(unittest.TestCase):
    def test_two_candle_zone_is_engulfed_body_and_stop_uses_both_wicks(self):
        candles = [
            bar(0, 101.0, 101.5, 98.5, 99.5),
            bar(1, 99.0, 102.5, 98.0, 102.0),
        ]
        blocks = detect_order_blocks("BTCUSDT", candles, 5)
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual(block.side, Side.LONG)
        self.assertEqual(block.zone_low, 99.5)
        self.assertEqual(block.zone_high, 101.0)
        self.assertEqual(block.invalidation, 98.0)
        self.assertEqual(block.pattern, "TWO_CANDLE_BODY_ENGULF")

    def test_three_candle_double_engulf_uses_middle_body_and_all_extremes(self):
        candles = [
            bar(0, 101.0, 101.5, 99.5, 100.0),
            bar(1, 99.5, 103.0, 99.0, 102.0),
            bar(2, 102.5, 103.5, 98.0, 98.5),
        ]
        blocks = detect_order_blocks("BTCUSDT", candles, 5)
        # At candle two the bullish two-candle OB was genuinely known.  The
        # final bearish engulf then invalidates that earlier premise and creates
        # the stronger three-candle bearish OB. A batch detector must not erase
        # information that was causally available one candle earlier.
        self.assertEqual(len(blocks), 2)
        prior, block = blocks
        self.assertEqual(prior.side, Side.LONG)
        self.assertEqual(prior.pattern, "TWO_CANDLE_BODY_ENGULF")
        self.assertEqual(block.side, Side.SHORT)
        self.assertEqual(block.pattern, "THREE_CANDLE_DOUBLE_ENGULF_MIDDLE_BODY")
        self.assertEqual(block.zone_low, 99.5)
        self.assertEqual(block.zone_high, 102.0)
        self.assertEqual(block.invalidation, 103.5)

    def test_body_ratio_is_quality_attribute_not_mandatory_gate(self):
        candles = [
            bar(0, 101.0, 101.5, 99.0, 100.0),
            bar(1, 99.8, 101.3, 99.5, 101.2),
        ]
        block = detect_order_blocks("BTCUSDT", candles, 5)[0]
        self.assertLess(block.body_ratio, 2.0)
        self.assertFalse(block.source_two_x_quality)
        self.assertEqual(
            block.numeric_doji_boundary_status,
            "SOURCE_GIVES_NO_NUMERIC_NEAR_DOJI_BOUNDARY",
        )

    def test_exact_doji_does_not_create_nonfinite_ratio_evidence(self):
        candles = [
            bar(0, 100.0, 101.0, 99.0, 100.0),
            bar(1, 99.5, 102.0, 99.2, 101.0),
        ]
        # The doji itself has no direction, so it is not an OB formation. The
        # test protects the evidence model directly through a later directional
        # pair whose engulfed body is finite.
        self.assertEqual(detect_order_blocks("BTCUSDT", candles, 5), [])


class TestSourceFVG(unittest.TestCase):
    def test_bullish_wick_gap_and_large_middle_body(self):
        candles = [
            bar(0, 100.0, 101.0, 99.5, 100.5),
            bar(1, 100.4, 104.5, 100.2, 104.0),
            bar(2, 102.0, 105.0, 101.5, 102.5),
        ]
        gaps = detect_fvgs("BTCUSDT", candles, 5)
        self.assertEqual(len(gaps), 1)
        gap = gaps[0]
        self.assertEqual(gap.side, Side.LONG)
        self.assertEqual(gap.zone_low, 101.0)
        self.assertEqual(gap.zone_high, 101.5)
        self.assertTrue(gap.source_two_x_quality)

    def test_gap_with_similar_candles_is_enumerated_but_not_strict_quality(self):
        candles = [
            bar(0, 100.0, 101.0, 99.5, 100.8),
            bar(1, 100.8, 102.5, 100.7, 101.8),
            bar(2, 101.5, 103.0, 101.2, 102.2),
        ]
        gap = detect_fvgs("BTCUSDT", candles, 5)[0]
        self.assertFalse(gap.source_two_x_quality)

    def test_middle_candle_direction_is_required(self):
        candles = [
            bar(0, 100.0, 101.0, 99.5, 100.5),
            bar(1, 102.0, 102.2, 100.5, 101.0),
            bar(2, 101.5, 103.0, 101.2, 102.0),
        ]
        self.assertEqual(detect_fvgs("BTCUSDT", candles, 5), [])


if __name__ == "__main__":
    unittest.main()
