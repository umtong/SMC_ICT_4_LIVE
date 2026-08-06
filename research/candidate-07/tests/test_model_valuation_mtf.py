from __future__ import annotations

import unittest

from model import Direction
from model_valuation_mtf import (
    MTFDislocationKind,
    MTFValuationDislocationRouter,
    MTFValuationLogicConfig,
    ValuationMinuteBar,
    ValuationSignalBar,
)


NS_PER_MINUTE = 60_000_000_000


def config(*, use_open_interest: bool = True) -> MTFValuationLogicConfig:
    return MTFValuationLogicConfig(
        signal_flow_period=6,
        oi_period=6,
        basis_period=20,
        min_signal_history=20,
        minute_atr_period=20,
        minute_flow_period=20,
        min_minute_history=20,
        confirmation_minutes=5,
        rearm_minutes=2,
        use_open_interest=use_open_interest,
    )


def minute_bar(
    index: int,
    *,
    index_price: float = 100.0,
    basis: float = 0.0,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    buy: float = 50.0,
) -> ValuationMinuteBar:
    close = index_price * (1.0 + basis)
    if open_ is None:
        open_ = close
    if high is None:
        high = max(open_, close) + 0.02
    if low is None:
        low = min(open_, close) - 0.02
    return ValuationMinuteBar(
        ts_event_ns=(index + 1) * NS_PER_MINUTE,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        taker_buy_volume=buy,
        index_open=index_price,
        index_high=index_price + 0.01,
        index_low=index_price - 0.01,
        index_close=index_price,
    )


def signal_bar(
    index: int,
    *,
    index_price: float = 100.0,
    basis: float = 0.0,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    buy: float = 50.0,
    oi: float = 1000.0,
) -> ValuationSignalBar:
    close = index_price * (1.0 + basis)
    if open_ is None:
        open_ = close
    if high is None:
        high = max(open_, close) + 0.05
    if low is None:
        low = min(open_, close) - 0.05
    return ValuationSignalBar(
        ts_event_ns=(index + 1) * 5 * NS_PER_MINUTE,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        taker_buy_volume=buy,
        index_close=index_price,
        open_interest=oi,
        open_interest_value=oi * index_price,
    )


def warmed(
    *,
    use_open_interest: bool = True,
    normalize: bool = True,
) -> tuple[MTFValuationDislocationRouter, int, int, float]:
    router = MTFValuationDislocationRouter(config(use_open_interest=use_open_interest))
    patterns = (0.00002, -0.00001, 0.0, 0.00001)
    for minute_index in range(110):
        basis = patterns[minute_index % len(patterns)]
        close = 100.0 * (1.0 + basis)
        router.observe_minute(
            minute_bar(
                minute_index,
                basis=basis,
                open_=close + (0.005 if minute_index % 2 == 0 else -0.005),
                buy=49.0 if minute_index % 2 == 0 else 51.0,
            ),
            minute_index,
            eligible=False,
        )

    oi = 1000.0
    signal_patterns = (0.00003, -0.00002, 0.00001, 0.0)
    for signal_index in range(20):
        oi += 0.05 if signal_index % 2 == 0 else -0.03
        basis = signal_patterns[signal_index % len(signal_patterns)]
        close = 100.0 * (1.0 + basis)
        router.observe_signal(
            signal_bar(
                signal_index,
                basis=basis,
                open_=close + 0.01,
                buy=49.0 if signal_index % 2 == 0 else 51.0,
                oi=oi,
            ),
            signal_index,
            99,
            eligible=False,
        )

    signal_index = 20
    if normalize:
        oi += 0.02
        router.observe_signal(
            signal_bar(
                signal_index,
                basis=0.0,
                open_=100.01,
                high=100.03,
                low=99.98,
                buy=50.0,
                oi=oi,
            ),
            signal_index,
            104,
            eligible=True,
        )
        signal_index += 1
    return router, signal_index, 109, oi


class MTFValuationTests(unittest.TestCase):
    def test_positive_tail_routes_short_on_next_minute_counterflow(self) -> None:
        router, signal_index, minute_index, oi = warmed()
        contact = router.observe_signal(
            signal_bar(
                signal_index,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=oi + 20.0,
            ),
            signal_index,
            minute_index,
        )
        self.assertTrue(contact.transitions)
        confirmed = router.observe_minute(
            minute_bar(
                minute_index + 1,
                basis=0.001,
                open_=100.115,
                high=100.118,
                low=100.095,
                buy=25.0,
            ),
            minute_index + 1,
        )
        self.assertIsNotNone(confirmed.plan)
        assert confirmed.plan is not None
        self.assertEqual(confirmed.plan.direction, Direction.SHORT)
        self.assertGreaterEqual(confirmed.plan.expected_rr, 1.25)
        self.assertEqual(confirmed.plan.details["execution_clock"], "ONE_MINUTE")

    def test_negative_tail_routes_long(self) -> None:
        router, signal_index, minute_index, oi = warmed()
        contact = router.observe_signal(
            signal_bar(
                signal_index,
                basis=-0.002,
                open_=99.90,
                high=99.95,
                low=99.75,
                buy=18.0,
                oi=oi - 20.0,
            ),
            signal_index,
            minute_index,
        )
        self.assertEqual(
            contact.transitions[0].details["dislocation_kind"],
            MTFDislocationKind.INVENTORY_RELEASE.value,
        )
        confirmed = router.observe_minute(
            minute_bar(
                minute_index + 1,
                basis=-0.001,
                open_=99.885,
                high=99.905,
                low=99.882,
                buy=75.0,
            ),
            minute_index + 1,
        )
        self.assertIsNotNone(confirmed.plan)
        assert confirmed.plan is not None
        self.assertEqual(confirmed.plan.direction, Direction.LONG)

    def test_fair_value_first_terminates_without_trade(self) -> None:
        router, signal_index, minute_index, oi = warmed()
        router.observe_signal(
            signal_bar(
                signal_index,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=oi + 20.0,
            ),
            signal_index,
            minute_index,
        )
        reached = router.observe_minute(
            minute_bar(
                minute_index + 1,
                basis=-0.0001,
                open_=100.02,
                high=100.03,
                low=99.98,
                buy=25.0,
            ),
            minute_index + 1,
        )
        self.assertIsNone(reached.plan)
        self.assertTrue(
            any(
                item.reason_code == "FAIR_VALUE_REACHED_BEFORE_ENTRY"
                for item in reached.transitions
            )
        )

    def test_bad_first_geometry_can_wait_for_better_counterflow(self) -> None:
        router, signal_index, minute_index, oi = warmed()
        router.observe_signal(
            signal_bar(
                signal_index,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=oi + 20.0,
            ),
            signal_index,
            minute_index,
        )
        wide = router.observe_minute(
            minute_bar(
                minute_index + 1,
                basis=0.0014,
                open_=100.18,
                high=100.30,
                low=100.12,
                buy=25.0,
            ),
            minute_index + 1,
        )
        self.assertIsNone(wide.plan)
        self.assertEqual(
            wide.diagnostics.get("geometry_reason"),
            "REMAINING_RR_BELOW_MINIMUM",
        )
        self.assertIsNotNone(router.active_scenario_id)

        better = router.observe_minute(
            minute_bar(
                minute_index + 2,
                basis=0.001,
                open_=100.115,
                high=100.118,
                low=100.095,
                buy=25.0,
            ),
            minute_index + 2,
        )
        self.assertIsNotNone(better.plan)

    def test_neutral_oi_blocks_but_ablation_allows(self) -> None:
        router, signal_index, minute_index, oi = warmed()
        blocked = router.observe_signal(
            signal_bar(
                signal_index,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=oi + 0.001,
            ),
            signal_index,
            minute_index,
        )
        self.assertFalse(blocked.transitions)

        ablation, signal_index, minute_index, oi = warmed(use_open_interest=False)
        allowed = ablation.observe_signal(
            signal_bar(
                signal_index,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=oi + 0.001,
            ),
            signal_index,
            minute_index,
        )
        self.assertTrue(allowed.transitions)
        self.assertEqual(
            allowed.transitions[0].details["dislocation_kind"],
            MTFDislocationKind.OI_ABLATION.value,
        )

    def test_stale_tail_requires_normalization(self) -> None:
        router, signal_index, minute_index, oi = warmed(normalize=False)
        stale = router.observe_signal(
            signal_bar(
                signal_index,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=oi + 20.0,
            ),
            signal_index,
            minute_index,
        )
        self.assertFalse(stale.transitions)

    def test_data_gap_terminates_active_episode(self) -> None:
        router, signal_index, minute_index, oi = warmed()
        router.observe_signal(
            signal_bar(
                signal_index,
                basis=0.002,
                open_=100.10,
                high=100.25,
                low=100.05,
                buy=82.0,
                oi=oi + 20.0,
            ),
            signal_index,
            minute_index,
        )
        transitions = router.invalidate_data_gap(
            minute_index=minute_index + 1,
            event_time_ns=(minute_index + 2) * NS_PER_MINUTE,
            reference_price=100.15,
            reason_code="INDEX_PRICE_DATA_GAP",
        )
        self.assertEqual(len(transitions), 1)
        self.assertFalse(transitions[0].details["synthetic_index_used"])
        self.assertIsNone(router.active_scenario_id)


if __name__ == "__main__":
    unittest.main()
