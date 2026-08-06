from __future__ import annotations

from pathlib import Path
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from model import Direction  # noqa: E402
from model_flow import (  # noqa: E402
    CausalAggressorFlowRouter,
    FlowLogicConfig,
    FlowSignalBar,
)


def make_config(**overrides) -> FlowLogicConfig:
    values = {
        "signal_minutes": 1,
        "atr_period": 4,
        "flow_period": 4,
        "external_lookback": 5,
        "internal_lookback": 2,
        "min_history": 5,
        "sweep_min_atr": 0.01,
        "sweep_max_atr": 2.0,
        "sweep_wick_fraction": 0.20,
        "reclaim_buffer_atr": 0.0,
        "absorption_min_imbalance": 0.10,
        "absorption_flow_z": 0.10,
        "reversal_efficiency_max": 0.95,
        "confirmation_bars": 2,
        "confirmation_body_atr": 0.05,
        "confirmation_min_imbalance": 0.01,
        "stop_buffer_atr": 0.05,
        "minimum_stop_atr": 0.20,
        "maximum_stop_atr": 3.0,
        "minimum_rr": 0.20,
        "maximum_target_rr": 3.0,
        "episode_cooldown_bars": 0,
    }
    values.update(overrides)
    return FlowLogicConfig.from_mapping(values)


def history_bars() -> list[FlowSignalBar]:
    closes = [100.0, 100.1, 99.9, 100.0, 100.0]
    buys = [50.0, 55.0, 45.0, 60.0, 40.0]
    highs = [101.0, 100.8, 100.7, 100.9, 100.6]
    lows = [93.0, 94.0, 95.0, 94.5, 94.0]
    return [
        FlowSignalBar(
            ts_event_ns=index + 1,
            open=100.0,
            high=highs[index],
            low=lows[index],
            close=closes[index],
            volume=100.0,
            taker_buy_volume=buys[index],
        )
        for index in range(5)
    ]


def warmed_router(config: FlowLogicConfig | None = None) -> CausalAggressorFlowRouter:
    router = CausalAggressorFlowRouter(config or make_config())
    for index, bar in enumerate(history_bars()):
        result = router.observe(bar, index)
        if result.plan is not None:
            raise AssertionError("warmup must not emit a plan")
    return router


class AggressorFlowRouterTests(unittest.TestCase):
    def test_upper_pool_buy_aggression_failure_routes_short(self) -> None:
        router = warmed_router()
        contact_bar = FlowSignalBar(
            ts_event_ns=6,
            open=100.5,
            high=101.8,
            low=99.8,
            close=100.4,
            volume=100.0,
            taker_buy_volume=75.0,
        )
        contact = router.observe(contact_bar, 5)
        self.assertIsNone(contact.plan)
        self.assertEqual(
            contact.transitions[0].reason_code,
            "UPPER_POOL_BUY_AGGRESSION_ABSORBED",
        )

        confirmation_bar = FlowSignalBar(
            ts_event_ns=7,
            open=100.4,
            high=100.5,
            low=97.8,
            close=98.0,
            volume=100.0,
            taker_buy_volume=35.0,
        )
        confirmed = router.observe(confirmation_bar, 6)
        self.assertIsNotNone(confirmed.plan)
        assert confirmed.plan is not None
        self.assertEqual(confirmed.plan.direction, Direction.SHORT)
        self.assertIn(
            "OPPOSITE_AGGRESSOR_DISPLACEMENT",
            [item.reason_code for item in confirmed.transitions],
        )

    def test_lower_pool_sell_aggression_failure_routes_long(self) -> None:
        router = warmed_router()
        contact_bar = FlowSignalBar(
            ts_event_ns=6,
            open=93.7,
            high=94.2,
            low=92.2,
            close=93.8,
            volume=100.0,
            taker_buy_volume=25.0,
        )
        contact = router.observe(contact_bar, 5)
        self.assertEqual(
            contact.transitions[0].reason_code,
            "LOWER_POOL_SELL_AGGRESSION_ABSORBED",
        )

        confirmation_bar = FlowSignalBar(
            ts_event_ns=7,
            open=93.8,
            high=97.2,
            low=93.7,
            close=97.0,
            volume=100.0,
            taker_buy_volume=65.0,
        )
        confirmed = router.observe(confirmation_bar, 6)
        self.assertIsNotNone(confirmed.plan)
        assert confirmed.plan is not None
        self.assertEqual(confirmed.plan.direction, Direction.LONG)

    def test_directional_auction_is_not_faded(self) -> None:
        config = make_config(reversal_efficiency_max=0.20)
        router = CausalAggressorFlowRouter(config)
        for index, close in enumerate([96.0, 97.0, 98.0, 99.0, 100.0]):
            router.observe(
                FlowSignalBar(
                    ts_event_ns=index + 1,
                    open=close - 0.2,
                    high=close + 1.0,
                    low=close - 1.0,
                    close=close,
                    volume=100.0,
                    taker_buy_volume=[50.0, 55.0, 45.0, 60.0, 40.0][index],
                ),
                index,
            )
        result = router.observe(
            FlowSignalBar(
                ts_event_ns=6,
                open=100.5,
                high=101.8,
                low=99.8,
                close=100.4,
                volume=100.0,
                taker_buy_volume=75.0,
            ),
            5,
        )
        self.assertEqual(result.transitions, tuple())
        self.assertIsNone(result.plan)

    def test_formed_pool_is_consumed_only_once(self) -> None:
        router = warmed_router()
        bar = FlowSignalBar(
            ts_event_ns=6,
            open=100.5,
            high=101.8,
            low=99.8,
            close=100.4,
            volume=100.0,
            taker_buy_volume=75.0,
        )
        first = router.observe(bar, 5)
        self.assertEqual(router.consumed_pool_count, 1)
        formed_ns = int(first.transitions[0].details["liquidity_formed_ns"])
        duplicate, transition = router._new_episode(
            direction=Direction.SHORT,
            index=6,
            bar=FlowSignalBar(
                ts_event_ns=7,
                open=100.5,
                high=102.0,
                low=99.8,
                close=100.4,
                volume=100.0,
                taker_buy_volume=75.0,
            ),
            level=101.0,
            formed_ns=formed_ns,
            extreme=102.0,
            opposing_internal=94.0,
            opposing_external=93.0,
            reason="DUPLICATE_TEST",
            details={},
        )
        self.assertIsNone(duplicate)
        self.assertIsNone(transition)
        self.assertEqual(router.consumed_pool_count, 1)

    def test_unknown_config_parameter_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FlowLogicConfig.from_mapping({"not_a_parameter": 1})


if __name__ == "__main__":
    unittest.main()
