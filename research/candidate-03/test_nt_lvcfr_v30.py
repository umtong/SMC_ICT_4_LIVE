from __future__ import annotations

import unittest

from derive_nt_lvcfr_v23_signals import BasisBar, FiveBar
from derive_nt_lvcfr_v30_signals import find_failure, find_retest


def five(
    minute: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    flow: float,
) -> FiveBar:
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


def basis(
    minute: int,
    *,
    futures: tuple[float, float, float, float, float],
    spot: tuple[float, float, float, float, float],
    basis_bp: float,
) -> BasisBar:
    return BasisBar(
        end_minute=minute,
        futures=five(
            minute,
            open_=futures[0], high=futures[1], low=futures[2],
            close=futures[3], flow=futures[4],
        ),
        spot=five(
            minute,
            open_=spot[0], high=spot[1], low=spot[2],
            close=spot[3], flow=spot[4],
        ),
        basis_bp=basis_bp,
    )


class FailedBasisReversionTests(unittest.TestCase):
    def test_premium_rebreak_requires_spot_and_futures_alignment(self) -> None:
        bars = [
            basis(
                0,
                futures=(100.0, 100.2, 99.8, 100.0, 0.0),
                spot=(100.0, 100.2, 99.8, 100.0, 0.0),
                basis_bp=0.0,
            ),
            basis(
                5,
                futures=(100.0, 102.0, 99.9, 101.8, 0.4),
                spot=(100.0, 101.5, 99.9, 101.3, -0.1),
                basis_bp=6.0,
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
        self.assertIsNone(index)
        self.assertIsNone(found)

        aligned = [bars[0], basis(
            5,
            futures=(100.0, 102.0, 99.9, 101.8, 0.4),
            spot=(100.0, 101.5, 99.9, 101.3, 0.2),
            basis_bp=6.0,
        )]
        index, found = find_failure(
            aligned,
            start_index=0,
            original_direction=1,
            lower_fence=-2.0,
            upper_fence=2.0,
            event_extreme=101.0,
        )
        self.assertEqual(index, 1)
        self.assertIsNotNone(found)

    def test_long_retest_must_touch_and_defend_failed_boundary(self) -> None:
        bars = [
            basis(
                0,
                futures=(101.5, 102.0, 101.2, 101.8, 0.4),
                spot=(101.0, 101.7, 100.9, 101.5, 0.2),
                basis_bp=4.0,
            ),
            basis(
                5,
                futures=(101.7, 102.0, 101.1, 101.6, 0.3),
                spot=(101.4, 101.8, 101.0, 101.5, 0.15),
                basis_bp=3.0,
            ),
        ]
        index, found = find_retest(
            bars,
            start_index=0,
            original_direction=1,
            event_extreme=101.0,
            atr=1.0,
        )
        self.assertEqual(index, 1)
        self.assertIsNotNone(found)

    def test_short_retest_is_symmetric(self) -> None:
        bars = [
            basis(
                0,
                futures=(99.0, 99.2, 98.0, 98.2, -0.4),
                spot=(99.2, 99.3, 98.3, 98.5, -0.2),
                basis_bp=-4.0,
            ),
            basis(
                5,
                futures=(98.3, 98.9, 98.0, 98.2, -0.3),
                spot=(98.6, 98.9, 98.3, 98.4, -0.15),
                basis_bp=-3.0,
            ),
        ]
        index, found = find_retest(
            bars,
            start_index=0,
            original_direction=-1,
            event_extreme=99.0,
            atr=1.0,
        )
        self.assertEqual(index, 1)
        self.assertIsNotNone(found)


if __name__ == "__main__":
    unittest.main(verbosity=2)
