"""Regression tests for candidate-10 v23 OI semantic routing."""
from __future__ import annotations

import unittest

from c10_liquidation_state import AuctionProbe
from c10_liquidation_state import FiveMinuteAuctionBar
from c10_liquidation_state import LiquidityPool
from c10_liquidation_state import LiquidationParams
from c10_v23_state import OISemanticExternalTargetStateMachine


def _bar(
    *,
    ts_ns: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    flow: float,
) -> FiveMinuteAuctionBar:
    quote_volume = 1_000_000.0
    taker_buy_quote = 0.5 * (flow + 1.0) * quote_volume
    ratio = (1.0 + flow) / (1.0 - flow)
    return FiveMinuteAuctionBar(
        ts_ns=ts_ns,
        open=open_,
        high=high,
        low=low,
        close=close,
        quote_volume=quote_volume,
        taker_buy_quote=taker_buy_quote,
        open_interest=10_000.0,
        open_interest_value=300_000_000.0,
        metric_taker_ratio=ratio,
    )


def _features() -> dict[str, float]:
    return {
        "atr": 10.0,
        "range_q90": 10.0,
        "volume_median": 20_000_000.0,
    }


def _probe(*, oi_state: str, initiated_sequence: int = 10) -> AuctionProbe:
    return AuctionProbe(
        scenario_id="BTCUSDT-PERP.BINANCE:LIQ:TEST",
        pool_id="SOURCE",
        source_side="LOW",
        source_price=100.0,
        mode="ACCEPTANCE",
        outward_direction=-1,
        raid_extreme=94.0,
        initiated_sequence=initiated_sequence,
        initiated_ns=1,
        initial_close=96.0,
        initial_imbalance=-0.35,
        initial_oi_change=(-0.02 if oi_state == "CLEARING" else 0.02),
        oi_state=oi_state,
        atr=10.0,
    )


def _machine(*, target_side: str, target_price: float) -> OISemanticExternalTargetStateMachine:
    machine = OISemanticExternalTargetStateMachine(
        LiquidationParams(probe_max_bars=2),
        tick_size=0.1,
        instrument_id="BTCUSDT-PERP.BINANCE",
    )
    machine.pools = [
        LiquidityPool(
            pool_id="SOURCE",
            side="LOW",
            price=100.0,
            created_ns=1,
            source="PIVOT_5M",
            reserved=True,
        ),
        LiquidityPool(
            pool_id="TARGET",
            side=target_side,
            price=target_price,
            created_ns=1,
            source="FUNDING_SESSION",
        ),
    ]
    return machine


class V23OISemanticTests(unittest.TestCase):
    def test_clearing_acceptance_reclaim_creates_reversal_plan(self) -> None:
        machine = _machine(target_side="HIGH", target_price=140.0)
        machine.active_probe = _probe(oi_state="CLEARING")
        machine.sequence = 11
        events, plan = machine._process_probe(
            _bar(
                ts_ns=2,
                open_=96.0,
                high=103.0,
                low=93.0,
                close=102.0,
                flow=0.25,
            ),
            _features(),
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.direction, 1)
        self.assertEqual(
            plan.scenario,
            "LIQUIDATION_CLEARING_EXHAUSTION_REVERSAL",
        )
        self.assertEqual(plan.target_pool_id, "TARGET")
        self.assertLess(plan.stop_price, plan.entry_estimate)
        self.assertEqual(
            plan.details["oi_semantic_mapping"],
            "CLEARING_EXHAUSTION_REVERSAL",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].reason_code,
            "CLEARING_BREAK_RECLAIMED_WITH_OPPOSITE_EXECUTED_FLOW",
        )
        self.assertIsNone(machine.active_probe)
        self.assertTrue(machine.pools[0].consumed)

    def test_clearing_acceptance_never_continues_while_break_holds(self) -> None:
        machine = _machine(target_side="LOW", target_price=70.0)
        machine.active_probe = _probe(oi_state="CLEARING")
        machine.sequence = 11
        events, plan = machine._process_probe(
            _bar(
                ts_ns=2,
                open_=96.0,
                high=97.0,
                low=92.0,
                close=94.0,
                flow=-0.30,
            ),
            _features(),
        )
        self.assertEqual(events, [])
        self.assertIsNone(plan)
        self.assertIsNotNone(machine.active_probe)

        machine.sequence = 12
        events, plan = machine._process_probe(
            _bar(
                ts_ns=3,
                open_=94.0,
                high=95.0,
                low=91.0,
                close=93.0,
                flow=-0.20,
            ),
            _features(),
        )
        self.assertIsNone(plan)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "SCENARIO_EXPIRED")
        self.assertEqual(
            events[0].reason_code,
            "NO_RECLAIM_WITH_OPPOSITE_FLOW_AFTER_CLEARING_BREAK",
        )
        self.assertIsNone(machine.active_probe)

    def test_building_acceptance_retains_v22_continuation(self) -> None:
        machine = _machine(target_side="LOW", target_price=70.0)
        machine.active_probe = _probe(oi_state="BUILDING")
        machine.sequence = 11
        events, plan = machine._process_probe(
            _bar(
                ts_ns=2,
                open_=96.0,
                high=97.0,
                low=92.0,
                close=94.0,
                flow=-0.30,
            ),
            _features(),
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.direction, -1)
        self.assertEqual(plan.scenario, "LEVERAGE_ACCEPTANCE_CONTINUATION")
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].reason_code,
            "BOUNDARY_HOLD_AND_SAME_SIDE_FLOW_AFTER_LEVERAGE_SHOCK",
        )

    def test_start_probe_routes_clearing_acceptance_to_reclaim_wait(self) -> None:
        machine = _machine(target_side="HIGH", target_price=140.0)
        machine.pools[0].reserved = False
        events = machine._start_probe(
            _bar(
                ts_ns=10,
                open_=101.0,
                high=102.0,
                low=94.0,
                close=96.0,
                flow=-0.40,
            ),
            {
                "atr": 10.0,
                "flow": 0.10,
                "range": 1.0,
                "volume": 100_000.0,
                "oi_low": -0.01,
                "oi_high": 0.01,
            },
            -0.02,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].next_state, "CLEARING_RECLAIM_WAIT")
        self.assertEqual(
            events[0].reason_code,
            "OI_CLEARING_ACCEPTED_BREAK_REQUIRES_RANGE_RECLAIM",
        )
        self.assertFalse(events[0].details["continuation_entry_allowed"])
        self.assertIsNotNone(machine.active_probe)


if __name__ == "__main__":
    unittest.main()
