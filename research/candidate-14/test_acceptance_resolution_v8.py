from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import sentinel

import acceptance_resolution_v8 as transition
from logic import BarObs, Direction, Scenario, Side


class DummyEngine:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            acceptance_retest_atr=0.18,
            displacement_flow_min=0.03,
            displacement_body_atr=0.20,
            stop_buffer_atr=0.08,
            min_stop_atr=0.08,
            min_net_r=1.25,
            effective_taker_rate=0.0008,
            effective_maker_rate=0.0004,
        )
        self._index = 10
        self.pools = [
            SimpleNamespace(
                scenario_id="TARGET-HIGH",
                side=Side.HIGH,
                level=108.0,
                consumed=False,
                expiry_index=100,
            ),
        ]
        self.bars: list[BarObs] = []
        self.events: list[tuple[object, ...]] = []
        self.terminals: list[str] = []

    def _track_aac_pullback(self, auction: object, _bar: object) -> None:
        auction.acceptance_invalidated = True

    def _far_target_pool(self, _pool: object, _price: float) -> object:
        return self.pools[0]

    def _event(self, *args: object, **kwargs: object) -> None:
        self.events.append((*args, kwargs))

    def _terminal(self, _auction: object, _bar: object, reason: str) -> None:
        self.terminals.append(reason)

    def _zone_from_displacement(
        self,
        _bars: object,
        _index: int,
        _direction: Direction,
    ) -> tuple[float, float]:
        return 99.0, 101.0


def auction(*, exclusive_rejection: bool = False) -> SimpleNamespace:
    pool = SimpleNamespace(
        scenario_id="SOURCE-LOW",
        side=Side.LOW,
        level=98.0,
        source="COMPLETED_RANGE",
        range_id="RANGE",
        strength=2,
    )
    return SimpleNamespace(
        pool=pool,
        sweep=SimpleNamespace(ts_ns=1),
        initial_sweep_ts_ns=1,
        atr=10.0,
        rejection_seed=True,
        acceptance_seed=not exclusive_rejection,
        acceptance_invalidated=False,
        reversal_target_pool_id="TARGET-HIGH",
        last_crossed_level=98.0,
        sweep_extreme=96.0,
        state="OBSERVE",
        scenario=None,
        direction=None,
        stop_price=None,
        target_price=None,
        draw_side=None,
        draw_score=0.0,
        displacement_index=None,
        zone_low=None,
        zone_high=None,
        elapsed=1,
    )


def failure_bar() -> BarObs:
    return BarObs(
        ts_ns=100,
        open=98.2,
        high=100.0,
        low=98.0,
        close=99.5,
        volume=1000.0,
        taker_buy_volume=700.0,
    )


def initiative_bar() -> BarObs:
    return BarObs(
        ts_ns=101,
        open=98.5,
        high=101.2,
        low=98.4,
        close=101.0,
        volume=1000.0,
        taker_buy_volume=800.0,
    )


class ExplicitAcceptanceFailureTests(unittest.TestCase):
    def test_failure_bar_cannot_be_same_bar_reversal(self) -> None:
        engine = DummyEngine()
        state = auction()
        preserved = lambda *_args: sentinel.unexpected
        plan = transition.explicit_acceptance_failure_far(
            engine,
            state,
            failure_bar(),
            preserved,
        )
        self.assertIsNone(plan)
        stored = transition._states(engine)[state.pool.scenario_id]
        self.assertEqual(stored.failure_index, 10)
        self.assertEqual(len(engine.events), 1)
        event = engine.events[0]
        self.assertEqual(event[1], "AAC_FAILURE_OBSERVED")
        self.assertEqual(event[4], "OBSERVE")
        self.assertEqual(event[5], "OBSERVE")
        self.assertEqual(state.state, "OBSERVE")

    def test_later_initiative_owns_market_reversal(self) -> None:
        engine = DummyEngine()
        state = auction()
        preserved = lambda *_args: sentinel.unexpected
        self.assertIsNone(
            transition.explicit_acceptance_failure_far(
                engine,
                state,
                failure_bar(),
                preserved,
            ),
        )
        engine._index = 11
        engine.bars = [failure_bar(), initiative_bar()]
        plan = transition.explicit_acceptance_failure_far(
            engine,
            state,
            initiative_bar(),
            preserved,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, Scenario.FAR)
        self.assertEqual(plan.direction, Direction.LONG)
        self.assertEqual(plan.entry_order_type, "MARKET")
        self.assertFalse(plan.entry_post_only)
        self.assertFalse(plan.details["same_bar_reversal_allowed"])
        self.assertEqual(state.state, "PENDING_ENTRY")
        self.assertNotIn(state.pool.scenario_id, transition._states(engine))
        reversal = next(
            event for event in engine.events
            if event[1] == "AAC_FAILURE_REVERSAL_CONFIRMED"
        )
        self.assertEqual(reversal[4], "OBSERVE")
        self.assertEqual(reversal[5], "FAR_CONFIRMED")
        plan_event = next(
            event for event in engine.events
            if event[1] == "TRADE_PLAN_CONFIRMED"
        )
        self.assertEqual(plan_event[4], "FAR_CONFIRMED")
        self.assertEqual(plan_event[5], "PENDING_ENTRY")

    def test_exclusive_rejection_delegates_to_v6_far(self) -> None:
        engine = DummyEngine()
        state = auction(exclusive_rejection=True)

        def preserved(*_args: object) -> object:
            return sentinel.preserved

        result = transition.explicit_acceptance_failure_far(
            engine,
            state,
            failure_bar(),
            preserved,
        )
        self.assertIs(result, sentinel.preserved)
        self.assertFalse(transition._states(engine))

    def test_current_acceptance_continuation_is_deliberately_flat(self) -> None:
        engine = DummyEngine()
        state = auction()

        def preserved(*_args: object) -> object:
            return sentinel.legacy_aac

        result = transition.suppress_incomplete_acceptance_continuation(
            engine,
            state,
            failure_bar(),
            preserved,
        )
        self.assertIsNone(result)

    def test_non_acceptance_aac_dispatch_preserves_base_behavior(self) -> None:
        engine = DummyEngine()
        state = auction(exclusive_rejection=True)

        def preserved(*_args: object) -> object:
            return sentinel.preserved

        result = transition.suppress_incomplete_acceptance_continuation(
            engine,
            state,
            failure_bar(),
            preserved,
        )
        self.assertIs(result, sentinel.preserved)


if __name__ == "__main__":
    unittest.main()
