from __future__ import annotations

from dataclasses import dataclass
import unittest

from sequential_response_router import AuctionResolution, SequentialAuctionRouter


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
        bars.append(FakeBar(index * MINUTE_NS, price, 100.0 + index % 5, pressure))
    return bars


class SequentialResponseRouterTests(unittest.TestCase):
    def _start(self, side: str = "HIGH"):
        bars = calibration_history()
        sweep_ts = len(bars) * MINUTE_NS
        bars.append(
            FakeBar(
                sweep_ts,
                100.5 if side == "HIGH" else 99.5,
                180.0,
                0.9 if side == "HIGH" else -0.9,
            ),
        )
        return bars, sweep_ts, SequentialAuctionRouter()

    @staticmethod
    def _observe(router, bars, sweep_ts, side, boundary):
        return router.observe(
            scenario_id="episode",
            sweep_ts_ns=sweep_ts,
            swept_side=side,
            boundary=boundary,
            atr=1.0,
            bars=bars,
            current_index=len(bars) - 1,
        )

    def _resolve_acceptance(self):
        bars, sweep_ts, router = self._start("HIGH")
        result = None
        for step in range(1, 8):
            bars.append(FakeBar(sweep_ts + step * MINUTE_NS, 100.5 + 0.16 * step, 150.0, 0.8))
            result = self._observe(router, bars, sweep_ts, "HIGH", 100.0)
            if result.snapshot.state is AuctionResolution.ACCEPTANCE:
                break
        assert result is not None
        return bars, sweep_ts, router, result

    def test_consistent_conversion_resolves_acceptance(self) -> None:
        _, _, _, result = self._resolve_acceptance()
        self.assertEqual(result.snapshot.state, AuctionResolution.ACCEPTANCE)
        self.assertTrue(result.snapshot.fresh_for_entry)
        self.assertEqual(result.snapshot.resolution_age_bars, 0)

    def test_absorbed_aggressors_resolve_failure(self) -> None:
        bars, sweep_ts, router = self._start("HIGH")
        result = None
        for step in range(1, 8):
            bars.append(FakeBar(sweep_ts + step * MINUTE_NS, 100.5 - 0.20 * step, 165.0, 0.85))
            result = self._observe(router, bars, sweep_ts, "HIGH", 100.0)
            if result.snapshot.state is AuctionResolution.FAILURE:
                break
        assert result is not None
        self.assertEqual(result.snapshot.state, AuctionResolution.FAILURE)
        self.assertTrue(result.snapshot.fresh_for_entry)
        self.assertLess(result.snapshot.flow_channel, 0.0)

    def test_alternating_response_remains_unresolved(self) -> None:
        bars, sweep_ts, router = self._start("HIGH")
        close = 100.35
        result = None
        for step in range(1, 9):
            close += 0.09 if step % 2 else -0.09
            bars.append(FakeBar(sweep_ts + step * MINUTE_NS, close, 120.0, 0.35 if step % 2 else -0.35))
            result = self._observe(router, bars, sweep_ts, "HIGH", 100.0)
        assert result is not None
        self.assertEqual(result.snapshot.state, AuctionResolution.UNRESOLVED)
        self.assertFalse(result.snapshot.fresh_for_entry)

    def test_decision_lease_expires_after_following_bar(self) -> None:
        bars, sweep_ts, router, _ = self._resolve_acceptance()
        step = (bars[-1].ts_ns - sweep_ts) // MINUTE_NS + 1
        bars.append(FakeBar(sweep_ts + step * MINUTE_NS, bars[-1].close + 0.01, 100.0, 0.0))
        next_bar = self._observe(router, bars, sweep_ts, "HIGH", 100.0)
        self.assertEqual(next_bar.snapshot.resolution_age_bars, 1)
        self.assertTrue(next_bar.snapshot.fresh_for_entry)
        step += 1
        bars.append(FakeBar(sweep_ts + step * MINUTE_NS, bars[-1].close + 0.01, 100.0, 0.0))
        stale = self._observe(router, bars, sweep_ts, "HIGH", 100.0)
        self.assertEqual(stale.snapshot.state, AuctionResolution.STALE)
        self.assertFalse(stale.snapshot.fresh_for_entry)
        self.assertTrue(stale.expired_now)

    def test_new_sweep_resets_stale_episode(self) -> None:
        bars, sweep_ts, router, _ = self._resolve_acceptance()
        for _ in range(2):
            bars.append(FakeBar(bars[-1].ts_ns + MINUTE_NS, bars[-1].close, 100.0, 0.0))
            stale = self._observe(router, bars, sweep_ts, "HIGH", 100.0)
        self.assertEqual(stale.snapshot.state, AuctionResolution.STALE)
        new_sweep_ts = bars[-1].ts_ns + MINUTE_NS
        bars.append(FakeBar(new_sweep_ts, bars[-1].close + 0.2, 190.0, 0.9))
        reset = self._observe(router, bars, new_sweep_ts, "HIGH", 100.4)
        self.assertTrue(reset.reset)
        self.assertEqual(reset.snapshot.state, AuctionResolution.UNRESOLVED)
        self.assertEqual(reset.snapshot.observations, 0)

    def test_duplicate_bar_is_not_counted_twice(self) -> None:
        bars, sweep_ts, router = self._start("LOW")
        bars.append(FakeBar(sweep_ts + MINUTE_NS, 99.2, 150.0, -0.8))
        first = self._observe(router, bars, sweep_ts, "LOW", 100.0)
        duplicate = self._observe(router, bars, sweep_ts, "LOW", 100.0)
        self.assertEqual(first.snapshot.observations, duplicate.snapshot.observations)
        self.assertEqual(first.snapshot.evidence, duplicate.snapshot.evidence)


if __name__ == "__main__":
    unittest.main()
