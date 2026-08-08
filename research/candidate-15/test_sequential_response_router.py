from __future__ import annotations

from dataclasses import dataclass
import unittest

from sequential_response_router import (
    AuctionResolution,
    SequentialAuctionRouter,
)


MINUTE_NS = 60_000_000_000


@dataclass(frozen=True)
class FakeBar:
    ts_ns: int
    close: float
    volume: float
    signed_flow: float


def calibration_history(count: int = 140) -> list[FakeBar]:
    bars: list[FakeBar] = []
    price = 100.0
    for index in range(count):
        pressure = 0.18 if index % 2 == 0 else -0.16
        price *= 1.0 + pressure * 0.00012
        bars.append(
            FakeBar(
                ts_ns=index * MINUTE_NS,
                close=price,
                volume=100.0 + float(index % 5),
                signed_flow=pressure,
            ),
        )
    return bars


class SequentialResponseRouterTests(unittest.TestCase):
    def _start(self, *, side: str = "HIGH") -> tuple[list[FakeBar], int, SequentialAuctionRouter]:
        bars = calibration_history()
        sweep_ts = len(bars) * MINUTE_NS
        sweep_close = 100.5 if side == "HIGH" else 99.5
        sweep_flow = 0.9 if side == "HIGH" else -0.9
        bars.append(FakeBar(sweep_ts, sweep_close, 180.0, sweep_flow))
        return bars, sweep_ts, SequentialAuctionRouter()

    @staticmethod
    def _observe(
        router: SequentialAuctionRouter,
        bars: list[FakeBar],
        sweep_ts: int,
        side: str,
        boundary: float,
    ):
        return router.observe(
            scenario_id="episode",
            sweep_ts_ns=sweep_ts,
            swept_side=side,
            boundary=boundary,
            atr=1.0,
            bars=bars,
            current_index=len(bars) - 1,
        )

    def test_consistent_price_flow_conversion_resolves_acceptance(self) -> None:
        bars, sweep_ts, router = self._start(side="HIGH")
        result = None
        for step in range(1, 7):
            bars.append(
                FakeBar(
                    sweep_ts + step * MINUTE_NS,
                    100.5 + 0.16 * step,
                    150.0,
                    0.8,
                ),
            )
            result = self._observe(router, bars, sweep_ts, "HIGH", 100.0)
            if result.snapshot.state is AuctionResolution.ACCEPTANCE:
                break
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.snapshot.state, AuctionResolution.ACCEPTANCE)
        self.assertGreaterEqual(result.snapshot.observations, 3)
        self.assertGreaterEqual(
            result.snapshot.evidence,
            result.snapshot.decision_boundary,
        )

    def test_absorbed_sweep_side_aggressors_resolve_failure(self) -> None:
        bars, sweep_ts, router = self._start(side="HIGH")
        result = None
        for step in range(1, 8):
            bars.append(
                FakeBar(
                    sweep_ts + step * MINUTE_NS,
                    100.5 - 0.20 * step,
                    165.0,
                    0.85,
                ),
            )
            result = self._observe(router, bars, sweep_ts, "HIGH", 100.0)
            if result.snapshot.state is AuctionResolution.FAILURE:
                break
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.snapshot.state, AuctionResolution.FAILURE)
        self.assertLessEqual(
            result.snapshot.evidence,
            -result.snapshot.decision_boundary,
        )
        self.assertLess(result.snapshot.flow_channel, 0.0)

    def test_alternating_response_remains_unresolved(self) -> None:
        bars, sweep_ts, router = self._start(side="HIGH")
        result = None
        close = 100.35
        for step in range(1, 9):
            close += 0.09 if step % 2 else -0.09
            bars.append(
                FakeBar(
                    sweep_ts + step * MINUTE_NS,
                    close,
                    120.0,
                    0.35 if step % 2 else -0.35,
                ),
            )
            result = self._observe(router, bars, sweep_ts, "HIGH", 100.0)
        assert result is not None
        self.assertEqual(result.snapshot.state, AuctionResolution.UNRESOLVED)
        self.assertLess(
            abs(result.snapshot.evidence),
            result.snapshot.decision_boundary,
        )

    def test_new_sweep_extreme_resets_causal_episode(self) -> None:
        bars, sweep_ts, router = self._start(side="HIGH")
        bars.append(FakeBar(sweep_ts + MINUTE_NS, 100.7, 150.0, 0.7))
        first = self._observe(router, bars, sweep_ts, "HIGH", 100.0)
        self.assertEqual(first.snapshot.observations, 1)

        new_sweep_ts = sweep_ts + 2 * MINUTE_NS
        bars.append(FakeBar(new_sweep_ts, 101.0, 190.0, 0.9))
        reset = self._observe(router, bars, new_sweep_ts, "HIGH", 100.4)
        self.assertTrue(reset.reset)
        self.assertEqual(reset.snapshot.sweep_ts_ns, new_sweep_ts)
        self.assertEqual(reset.snapshot.observations, 0)
        self.assertEqual(reset.snapshot.state, AuctionResolution.UNRESOLVED)

    def test_duplicate_completed_bar_is_not_counted_twice(self) -> None:
        bars, sweep_ts, router = self._start(side="LOW")
        bars.append(FakeBar(sweep_ts + MINUTE_NS, 99.2, 150.0, -0.8))
        first = self._observe(router, bars, sweep_ts, "LOW", 100.0)
        duplicate = self._observe(router, bars, sweep_ts, "LOW", 100.0)
        self.assertEqual(first.snapshot.observations, duplicate.snapshot.observations)
        self.assertEqual(first.snapshot.evidence, duplicate.snapshot.evidence)


if __name__ == "__main__":
    unittest.main()
