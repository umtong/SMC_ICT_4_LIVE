from __future__ import annotations

import unittest

from model import Direction, ScenarioKind
from model_positioning import PositioningSignalBar
from model_valuation_dislocation import (
    DislocationKind,
    ValuationDislocationRouter,
    ValuationLogicConfig,
)


FIVE_MINUTES_NS = 300_000_000_000


def config(*, use_open_interest: bool = True) -> ValuationLogicConfig:
    return ValuationLogicConfig(
        atr_period=6,
        flow_period=6,
        oi_period=6,
        basis_period=20,
        target_lookback=20,
        target_pivot_radius=1,
        min_history=20,
        confirmation_bars=4,
        rearm_bars=2,
        use_open_interest=use_open_interest,
    )


def make_bar(
    index: int,
    *,
    anchor: float = 100.0,
    basis: float = 0.0,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    buy: float = 50.0,
    oi: float = 1000.0,
) -> PositioningSignalBar:
    close = anchor * (1.0 + basis)
    if open_ is None:
        open_ = close
    if high is None:
        high = max(open_, close) + 0.08
    if low is None:
        low = min(open_, close) - 0.08
    return PositioningSignalBar(
        ts_event_ns=(index + 1) * FIVE_MINUTES_NS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        taker_buy_volume=buy,
        open_interest=oi,
        open_interest_value=anchor * oi,
        top_trader_account_ratio=1.2,
        top_trader_position_ratio=1.1,
        global_long_short_ratio=1.0,
        taker_long_short_ratio=1.0,
    )


def warmed(
    *,
    use_open_interest: bool = True,
    normalize: bool = True,
) -> tuple[ValuationDislocationRouter, int, float]:
    router = ValuationDislocationRouter(config(use_open_interest=use_open_interest))
    oi = 1000.0
    patterns = (0.00003, -0.00002, 0.00001, 0.0)
    for index in range(20):
        oi += 0.05 if index % 2 == 0 else -0.03
        basis = patterns[index % len(patterns)]
        close = 100.0 * (1.0 + basis)
        router.observe(
            make_bar(
                index,
                basis=basis,
                open_=close + (0.02 if index % 2 == 0 else -0.02),
                buy=49.0 if index % 2 == 0 else 51.0,
                oi=oi,
            ),
            index,
            eligible=False,
        )
    index = 20
    if normalize:
        oi += 0.02
        router.observe(
            make_bar(
                index,
                basis=0.0,
                open_=100.01,
                high=100.03,
                low=99.98,
                buy=50.0,
                oi=oi,
            ),
            index,
        )
        index += 1
    return router, index, oi


class ValuationDislocationTests(unittest.TestCase):
    def test_positive_dislocation_contraction_routes_short(self) -> None:
        router, index, oi = warmed()
        contact = router.observe(
            make_bar(
                index,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=oi + 20.0,
            ),
            index,
        )
        self.assertTrue(
            any(
                item.reason_code == "PERPETUAL_VALUATION_TAIL_DISLOCATION"
                for item in contact.transitions
            )
        )
        confirmed = router.observe(
            make_bar(
                index + 1,
                basis=0.001,
                open_=100.116,
                high=100.118,
                low=100.095,
                buy=25.0,
                oi=oi + 19.5,
            ),
            index + 1,
        )
        self.assertIsNotNone(confirmed.plan)
        assert confirmed.plan is not None
        self.assertEqual(confirmed.plan.direction, Direction.SHORT)
        self.assertEqual(confirmed.plan.kind, ScenarioKind.ABSORPTION_RECLAIM)
        self.assertEqual(
            confirmed.plan.details["dislocation_kind"],
            DislocationKind.INVENTORY_BUILD.value,
        )
        self.assertGreaterEqual(
            confirmed.plan.expected_rr,
            router.config.minimum_rr,
        )

    def test_negative_dislocation_contraction_routes_long(self) -> None:
        router, index, oi = warmed()
        contact = router.observe(
            make_bar(
                index,
                basis=-0.002,
                open_=99.90,
                high=99.95,
                low=99.75,
                buy=18.0,
                oi=oi - 20.0,
            ),
            index,
        )
        self.assertEqual(
            contact.transitions[0].details["dislocation_kind"],
            DislocationKind.INVENTORY_RELEASE.value,
        )
        confirmed = router.observe(
            make_bar(
                index + 1,
                basis=-0.001,
                open_=99.884,
                high=99.905,
                low=99.882,
                buy=75.0,
                oi=oi - 19.5,
            ),
            index + 1,
        )
        self.assertIsNotNone(confirmed.plan)
        assert confirmed.plan is not None
        self.assertEqual(confirmed.plan.direction, Direction.LONG)
        self.assertGreater(confirmed.plan.target_price, confirmed.plan.entry_reference)

    def test_oi_sign_classifies_but_does_not_set_direction(self) -> None:
        router, index, oi = warmed()
        contact = router.observe(
            make_bar(
                index,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=oi - 20.0,
            ),
            index,
        )
        self.assertEqual(
            contact.transitions[0].details["direction"],
            Direction.SHORT.value,
        )
        self.assertEqual(
            contact.transitions[0].details["dislocation_kind"],
            DislocationKind.INVENTORY_RELEASE.value,
        )

    def test_neutral_oi_blocks_event_but_ablation_does_not(self) -> None:
        router, index, oi = warmed()
        blocked = router.observe(
            make_bar(
                index,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=oi + 0.001,
            ),
            index,
        )
        self.assertFalse(blocked.transitions)

        ablation, ablation_index, ablation_oi = warmed(use_open_interest=False)
        allowed = ablation.observe(
            make_bar(
                ablation_index,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=ablation_oi + 0.001,
            ),
            ablation_index,
        )
        self.assertTrue(allowed.transitions)
        self.assertEqual(
            allowed.transitions[0].details["dislocation_kind"],
            DislocationKind.OI_ABLATION.value,
        )

    def test_stale_tail_requires_normalization_inside_trade_window(self) -> None:
        router, index, oi = warmed(normalize=False)
        stale = router.observe(
            make_bar(
                index,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=oi + 20.0,
            ),
            index,
        )
        self.assertFalse(stale.transitions)

        router.observe(
            make_bar(
                index + 1,
                basis=0.0,
                open_=100.01,
                high=100.03,
                low=99.98,
                buy=50.0,
                oi=oi + 20.1,
            ),
            index + 1,
        )
        fresh = router.observe(
            make_bar(
                index + 2,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=oi + 40.0,
            ),
            index + 2,
        )
        self.assertTrue(fresh.transitions)

    def test_data_gap_terminates_episode_and_requires_normalization(self) -> None:
        router, index, oi = warmed()
        router.observe(
            make_bar(
                index,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=oi + 20.0,
            ),
            index,
        )
        transitions = router.invalidate_data_gap(
            index=index + 1,
            event_time_ns=(index + 2) * FIVE_MINUTES_NS,
            reference_price=100.15,
            reason_code="POSITIONING_DATA_GAP",
        )
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].reason_code, "POSITIONING_DATA_GAP")
        self.assertFalse(transitions[0].details["synthetic_positioning_used"])
        self.assertIsNone(router.active_scenario_id)

    def test_normalization_without_counterflow_invalidates(self) -> None:
        router, index, oi = warmed()
        router.observe(
            make_bar(
                index,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=oi + 20.0,
            ),
            index,
        )
        normalized = router.observe(
            make_bar(
                index + 1,
                basis=0.0,
                open_=99.99,
                high=100.03,
                low=99.98,
                buy=55.0,
                oi=oi + 20.1,
            ),
            index + 1,
        )
        self.assertIsNone(normalized.plan)
        self.assertTrue(
            any(
                item.reason_code == "DISLOCATION_NORMALIZED_WITHOUT_OPPOSITE_FLOW"
                for item in normalized.transitions
            )
        )


if __name__ == "__main__":
    unittest.main()
