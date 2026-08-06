from __future__ import annotations

from pathlib import Path
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from auction_continuation import CausalAuctionContinuationOverlay  # noqa: E402
from model import Direction, ScenarioKind, TradePlan  # noqa: E402
from model_flow import FlowLogicConfig, FlowSignalBar  # noqa: E402


def _bar(
    index: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    imbalance: float = 0.0,
    volume: float = 100.0,
) -> FlowSignalBar:
    taker_buy = volume * (1.0 + imbalance) / 2.0
    return FlowSignalBar(
        ts_event_ns=index + 1,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=taker_buy,
    )


def _warm(
    overlay: CausalAuctionContinuationOverlay,
    *,
    rising: bool,
) -> float:
    price = 100.0
    for index in range(52):
        open_price = price
        close = price + (0.2 if rising else 0.0)
        overlay.observe(
            _bar(
                index,
                open_price,
                max(open_price, close) + 0.05,
                min(open_price, close) - 0.05,
                close,
                0.01 if index % 2 == 0 else 0.03,
            ),
            index,
            eligible=True,
        )
        price = close
    return price


class AuctionContinuationTests(unittest.TestCase):
    def test_directional_acceptance_requires_counterflow_mitigation(self) -> None:
        config = FlowLogicConfig()
        overlay = CausalAuctionContinuationOverlay(
            config,
            failed_timeout_bars=24,
        )
        price = _warm(overlay, rising=True)
        contact = overlay.observe(
            _bar(
                52,
                price,
                price + 1.2,
                price - 0.05,
                price + 1.0,
                0.80,
                1_000.0,
            ),
            52,
            eligible=True,
        )
        transition = next(
            item
            for item in contact.transitions
            if item.reason_code == "UPPER_POOL_DIRECTIONAL_FLOW_ACCEPTED"
        )
        level = float(transition.reference_price)
        hold = overlay.observe(
            _bar(
                53,
                price + 1.0,
                price + 1.05,
                level + 0.05,
                price + 0.70,
                -0.10,
            ),
            53,
            eligible=True,
        )
        self.assertIsNotNone(hold.plan)
        assert hold.plan is not None
        self.assertEqual(hold.plan.kind, ScenarioKind.ACCEPTANCE_CONTINUATION)
        self.assertEqual(hold.plan.direction, Direction.LONG)
        self.assertEqual(hold.plan.expected_rr, 1.0)

    def test_unmitigated_breakout_is_not_chased(self) -> None:
        config = FlowLogicConfig()
        overlay = CausalAuctionContinuationOverlay(
            config,
            failed_timeout_bars=24,
        )
        price = _warm(overlay, rising=True)
        contact = overlay.observe(
            _bar(
                52,
                price,
                price + 1.2,
                price - 0.05,
                price + 1.0,
                0.80,
                1_000.0,
            ),
            52,
            eligible=True,
        )
        level = float(
            next(
                item.reference_price
                for item in contact.transitions
                if item.reason_code == "UPPER_POOL_DIRECTIONAL_FLOW_ACCEPTED"
            )
        )
        hold = overlay.observe(
            _bar(
                53,
                price + 1.0,
                price + 1.5,
                level + 0.05,
                price + 1.4,
                0.20,
            ),
            53,
            eligible=True,
        )
        self.assertIsNone(hold.plan)
        self.assertTrue(
            any(
                item.reason_code == "EXTERNAL_ACCEPTANCE_UNMITIGATED_CHASE"
                for item in hold.transitions
            )
        )

    def test_failed_absorption_requires_boundary_flow_and_outside_hold(self) -> None:
        config = FlowLogicConfig()
        overlay = CausalAuctionContinuationOverlay(
            config,
            failed_timeout_bars=24,
        )
        _warm(overlay, rising=False)
        source = TradePlan(
            scenario_id="source-short",
            kind=ScenarioKind.ABSORPTION_RECLAIM,
            direction=Direction.SHORT,
            observed_time_ns=50,
            entry_reference=100.0,
            stop_price=102.0,
            target_price=97.0,
            liquidity_level=100.0,
            expected_rr=1.5,
            details={
                "atr": 1.0,
                "opposing_internal": 98.0,
                "opposing_external": 95.0,
            },
        )
        overlay.arm_failed_from_plan(
            source,
            index=51,
            boundary_touched=False,
        )
        trigger = overlay.observe(
            _bar(52, 101.0, 102.5, 100.8, 102.3, 0.80, 1_000.0),
            52,
            eligible=True,
        )
        self.assertTrue(
            any(
                item.reason_code == "ORIGINAL_ATTACK_FLOW_ACCEPTED_BEYOND_STOP"
                for item in trigger.transitions
            )
        )
        hold = overlay.observe(
            _bar(53, 102.3, 103.0, 100.5, 102.4, -0.10),
            53,
            eligible=True,
        )
        self.assertIsNotNone(hold.plan)
        assert hold.plan is not None
        self.assertEqual(hold.plan.direction, Direction.LONG)

    def test_reclaimed_pool_terminates_failed_absorption_episode(self) -> None:
        config = FlowLogicConfig()
        overlay = CausalAuctionContinuationOverlay(
            config,
            failed_timeout_bars=24,
        )
        _warm(overlay, rising=False)
        source = TradePlan(
            scenario_id="source-short",
            kind=ScenarioKind.ABSORPTION_RECLAIM,
            direction=Direction.SHORT,
            observed_time_ns=50,
            entry_reference=100.0,
            stop_price=102.0,
            target_price=97.0,
            liquidity_level=100.0,
            expected_rr=1.5,
            details={
                "atr": 1.0,
                "opposing_internal": 98.0,
                "opposing_external": 95.0,
            },
        )
        overlay.arm_failed_from_plan(
            source,
            index=51,
            boundary_touched=True,
        )
        reclaimed = overlay.observe(
            _bar(52, 101.0, 102.1, 99.0, 99.5, 0.80, 1_000.0),
            52,
            eligible=True,
        )
        self.assertIsNone(reclaimed.plan)
        self.assertTrue(
            any(
                item.reason_code
                == "FAILED_ACCEPTANCE_RECLAIMED_ORIGINAL_POOL"
                for item in reclaimed.transitions
            )
        )


if __name__ == "__main__":
    unittest.main()
