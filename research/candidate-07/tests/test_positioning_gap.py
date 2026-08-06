from __future__ import annotations

import unittest

from model import ScenarioState
from model_positioning import InventoryState, PositioningLogicConfig, PositioningSignalBar
from model_positioning_gap_safe import GapSafePositioningAuctionRouter


FIVE_MINUTES_NS = 300_000_000_000


def bar(
    index: int,
    *,
    high: float = 95.5,
    low: float = 94.5,
    open_: float = 95.0,
    close: float = 95.0,
    buy: float = 50.0,
    oi: float = 1000.0,
) -> PositioningSignalBar:
    return PositioningSignalBar(
        ts_event_ns=(index + 1) * FIVE_MINUTES_NS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0 if buy <= 100.0 else 220.0,
        taker_buy_volume=buy,
        open_interest=oi,
        open_interest_value=oi * close,
    )


def warmed() -> tuple[GapSafePositioningAuctionRouter, int, float]:
    config = PositioningLogicConfig()
    router = GapSafePositioningAuctionRouter(config)
    oi = 1000.0
    for index in range(300):
        oi += 0.2 if index % 2 == 0 else -0.1
        high = 100.0 if index == 270 else 95.5
        low = 90.0 if index == 280 else 94.5
        router.observe(
            bar(index, high=high, low=low, oi=oi),
            index,
            eligible=False,
        )
    return router, 300, oi


class GapSafePositioningTests(unittest.TestCase):
    def test_non_contiguous_oi_change_is_neutral(self) -> None:
        router, index, oi = warmed()
        observed = router.observe(
            bar(
                index + 1,
                high=100.55,
                low=99.2,
                open_=99.8,
                close=99.45,
                buy=170.0,
                oi=oi - 20.0,
            ),
            index + 1,
        )
        self.assertEqual(
            observed.diagnostics["inventory_state"],
            InventoryState.NEUTRAL.value,
        )
        self.assertEqual(observed.diagnostics["oi_impulse_rank"], 0.0)
        self.assertFalse(observed.transitions)

    def test_gap_terminates_active_episode_without_synthetic_bar(self) -> None:
        router, index, oi = warmed()
        contact = router.observe(
            bar(
                index,
                high=100.55,
                low=99.2,
                open_=99.8,
                close=99.45,
                buy=170.0,
                oi=oi - 20.0,
            ),
            index,
        )
        self.assertTrue(contact.transitions)
        transitions = router.invalidate_data_gap(
            index=index + 1,
            event_time_ns=(index + 2) * FIVE_MINUTES_NS,
            reference_price=99.4,
            reason_code="POSITIONING_DATA_GAP",
        )
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].next_state, ScenarioState.INVALIDATED.value)
        self.assertEqual(transitions[0].reason_code, "POSITIONING_DATA_GAP")
        self.assertFalse(transitions[0].details["synthetic_positioning_used"])
        self.assertIsNone(router.active_scenario_id)


if __name__ == "__main__":
    unittest.main()
