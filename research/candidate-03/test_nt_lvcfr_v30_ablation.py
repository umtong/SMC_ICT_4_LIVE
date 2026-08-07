from __future__ import annotations

import unittest

from derive_nt_lvcfr_v23_signals import BasisBar, FiveBar
from derive_nt_lvcfr_v30_signals import find_failure


def five(minute: int, open_: float, high: float, low: float, close: float, flow: float) -> FiveBar:
    notional = 1_000_000.0
    return FiveBar(
        end_minute=minute,
        open=open_,
        high=high,
        low=low,
        close=close,
        notional=notional,
        signed_notional=flow * notional,
    )


def basis(minute: int, futures: FiveBar, spot: FiveBar, basis_bp: float) -> BasisBar:
    return BasisBar(end_minute=minute, futures=futures, spot=spot, basis_bp=basis_bp)


class FailureExtremeAblationTests(unittest.TestCase):
    def test_basis_rebreak_can_precede_original_price_extreme(self) -> None:
        bars = [
            basis(
                0,
                five(0, 100.0, 100.2, 99.8, 100.0, 0.0),
                five(0, 100.0, 100.2, 99.8, 100.0, 0.0),
                0.0,
            ),
            basis(
                5,
                # Basis and both flows re-expand, but price has not yet crossed
                # the original 101.0 event extreme. That is deferred to retest.
                five(5, 100.0, 100.9, 99.9, 100.8, 0.35),
                five(5, 100.0, 100.5, 99.9, 100.4, 0.20),
                5.0,
            ),
        ]
        index, found = find_failure(
            bars,
            start_index=0,
            original_direction=1,
            lower_fence=-2.0,
            upper_fence=2.0,
            event_extreme=101.0,
        )
        self.assertEqual(index, 1)
        self.assertIsNotNone(found)


if __name__ == "__main__":
    unittest.main(verbosity=2)
