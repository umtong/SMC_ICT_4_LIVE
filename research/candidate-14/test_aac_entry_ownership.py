from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch, sentinel

import aac_entry_ownership as owned
from logic import BarObs, CausalAuctionEngine, Direction, Scenario, Side
from semantic_execution import MARKET_ENTRY_SENTINEL_NS


class DummyEngine:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            stop_buffer_atr=0.08,
            min_stop_atr=0.08,
            min_net_r=1.25,
            effective_taker_rate=0.0008,
            effective_maker_rate=0.0004,
        )
        self.events: list[tuple[object, ...]] = []
        self.terminals: list[str] = []

    def _event(self, *args: object, **kwargs: object) -> None:
        self.events.append((*args, kwargs))

    def _terminal(self, _auction: object, _bar: object, reason: str) -> None:
        self.terminals.append(reason)


def auction(*, target: float = 106.0) -> SimpleNamespace:
    pool = SimpleNamespace(
        scenario_id="TEST-HIGH",
        level=98.5,
        source="COMPLETED_RANGE",
        range_id="TEST-RANGE",
    )
    return SimpleNamespace(
        direction=Direction.LONG,
        scenario=Scenario.AAC,
        target_price=target,
        pullback_extreme=99.0,
        pool=pool,
        atr=10.0,
        initial_sweep_ts_ns=1,
        sweep=SimpleNamespace(ts_ns=1),
        sweep_extreme=101.0,
        draw_side=Side.HIGH,
        draw_score=1.0,
        framed_draw_method="EXTERNAL_HAZARD_DOMINANCE",
        zone_low=99.7,
        zone_high=100.0,
        state="AAC_CONFIRMED",
        stop_price=None,
    )


def confirmation() -> BarObs:
    return BarObs(
        ts_ns=2,
        open=99.7,
        high=100.2,
        low=99.6,
        close=100.0,
        volume=1000.0,
        taker_buy_volume=700.0,
    )


class AACEntryOwnershipTests(unittest.TestCase):
    def test_confirmed_reacceleration_owns_market_entry(self) -> None:
        engine = DummyEngine()
        state = auction()
        plan = owned.aac_reacceleration_market_plan(engine, state, confirmation())
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.entry_order_type, "MARKET")
        self.assertFalse(plan.entry_post_only)
        self.assertEqual(plan.expected_entry, 100.0)
        self.assertEqual(plan.expire_ts_ns, MARKET_ENTRY_SENTINEL_NS)
        self.assertEqual(
            plan.details["entry_model"],
            "CONFIRMED_PULLBACK_REACCELERATION_MARKET",
        )
        self.assertFalse(plan.details["second_pullback_fallback_allowed"])
        self.assertEqual(state.state, "PENDING_ENTRY")
        self.assertFalse(engine.terminals)
        self.assertEqual(len(engine.events), 1)

    def test_non_executable_reacceleration_terminates_without_limit_fallback(self) -> None:
        engine = DummyEngine()
        state = auction(target=100.5)
        plan = owned.aac_reacceleration_market_plan(engine, state, confirmation())
        self.assertIsNone(plan)
        self.assertEqual(
            engine.terminals,
            ["AAC_OWNED_REACCELERATION_NOT_COST_EXECUTABLE"],
        )
        self.assertFalse(engine.events)
        self.assertEqual(state.state, "AAC_CONFIRMED")

    def test_install_delegates_non_aac_to_preserved_adapter(self) -> None:
        original = CausalAuctionEngine._costed_limit_plan

        def previous(*_args: object, **_kwargs: object) -> object:
            return sentinel.preserved

        try:
            CausalAuctionEngine._costed_limit_plan = previous  # type: ignore[method-assign]
            owned.install()
            result = CausalAuctionEngine._costed_limit_plan(
                object(),
                SimpleNamespace(scenario=Scenario.FAR),
                object(),
                "FAR_REASON",
            )
            self.assertIs(result, sentinel.preserved)
        finally:
            CausalAuctionEngine._costed_limit_plan = original


if __name__ == "__main__":
    unittest.main()
