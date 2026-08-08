from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import sentinel

import confirmed_acceptance_failure_v9 as v9
from logic import BarObs, Direction, Scenario, Side


class DummyEngine:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            acceptance_retest_atr=0.18,
            acceptance_hold_atr=0.02,
            acceptance_min_closes=2,
            acceptance_pullback_wing=2,
            reacceleration_flow_min=0.04,
            reacceleration_body_atr=0.18,
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
                scenario_id="CONT-LOW",
                side=Side.LOW,
                level=95.0,
                consumed=False,
                expiry_index=100,
            ),
            SimpleNamespace(
                scenario_id="REV-HIGH",
                side=Side.HIGH,
                level=112.0,
                consumed=False,
                expiry_index=100,
            ),
        ]
        self.bars: list[BarObs] = []
        self.events: list[tuple[object, ...]] = []
        self.terminals: list[str] = []
        self.track_calls = 0

    def _track_aac_pullback(self, auction: object, bar: BarObs) -> None:
        self.track_calls += 1
        # A LOW-side accepted auction fails only after a later deep re-entry.
        if bar.close > 101.8:
            auction.acceptance_invalidated = True

    def _far_target_pool(self, _pool: object, _price: float) -> object:
        return self.pools[1]

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
        return 102.0, 104.0


def auction(*, exclusive_rejection: bool = False) -> SimpleNamespace:
    source = SimpleNamespace(
        scenario_id="SOURCE-LOW",
        side=Side.LOW,
        level=100.0,
        source="COMPLETED_RANGE",
        range_id="RANGE",
        strength=2,
        trigger_start_ts_ns=0,
        trigger_end_ts_ns=10_000,
    )
    return SimpleNamespace(
        pool=source,
        sweep=SimpleNamespace(ts_ns=1),
        initial_sweep_ts_ns=1,
        sweep_index=1,
        atr=10.0,
        rejection_seed=True,
        acceptance_seed=not exclusive_rejection,
        acceptance_invalidated=False,
        cascade_count=2,
        framed_draw_side=Side.LOW,
        framed_draw_method="EXTERNAL_HAZARD_DOMINANCE",
        framed_target_pool_id="CONT-LOW",
        framed_target_level=95.0,
        continuation_target_pool_id="CONT-LOW",
        continuation_target_level=95.0,
        reversal_target_pool_id="REV-HIGH",
        reversal_target_level=112.0,
        last_crossed_level=100.0,
        sweep_extreme=97.0,
        outside_streak=2,
        pullback_known_index=8,
        pullback_extreme=99.0,
        acceptance_impulse_extreme=98.0,
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


def completion_bar() -> BarObs:
    return BarObs(
        ts_ns=100,
        open=98.5,
        high=98.6,
        low=97.0,
        close=97.4,
        volume=1000.0,
        taker_buy_volume=200.0,
    )


def failure_bar() -> BarObs:
    return BarObs(
        ts_ns=101,
        open=101.7,
        high=103.0,
        low=101.5,
        close=102.5,
        volume=1000.0,
        taker_buy_volume=700.0,
    )


def initiative_bar() -> BarObs:
    return BarObs(
        ts_ns=102,
        open=102.0,
        high=104.5,
        low=101.9,
        close=104.0,
        volume=1000.0,
        taker_buy_volume=800.0,
    )


class ConfirmedAcceptanceFailureTests(unittest.TestCase):
    def test_uncompleted_acceptance_cannot_register_failure(self) -> None:
        engine = DummyEngine()
        state = auction()
        state.acceptance_invalidated = True
        result = v9.resolve_confirmed_acceptance_failure(
            engine,
            state,
            failure_bar(),
            lambda *_args: sentinel.unexpected,
        )
        self.assertIsNone(result)
        self.assertFalse(v9._failures(engine))
        self.assertEqual(engine.track_calls, 0)

    def test_frozen_acceptance_completion_is_observed_without_order(self) -> None:
        engine = DummyEngine()
        state = auction()
        completion = v9.observe_completed_acceptance(
            engine,
            state,
            completion_bar(),
        )
        self.assertIsNotNone(completion)
        assert completion is not None
        self.assertEqual(completion.direction, Direction.SHORT)
        self.assertEqual(state.state, "OBSERVE")
        self.assertEqual(len(engine.events), 1)
        event = engine.events[0]
        self.assertEqual(event[1], "ACCEPTANCE_COMPLETION_OBSERVED")
        self.assertEqual(event[4], "OBSERVE")
        self.assertEqual(event[5], "OBSERVE")
        self.assertFalse(event[-1]["continuation_order_allowed"])

    def test_failure_requires_completion_then_later_initiative(self) -> None:
        engine = DummyEngine()
        state = auction()
        self.assertIsNotNone(
            v9.observe_completed_acceptance(engine, state, completion_bar()),
        )

        engine._index = 11
        self.assertIsNone(
            v9.resolve_confirmed_acceptance_failure(
                engine,
                state,
                failure_bar(),
                lambda *_args: sentinel.unexpected,
            ),
        )
        failure = v9._failures(engine)[state.pool.scenario_id]
        self.assertEqual(failure.failure_index, 11)
        observed = next(
            event for event in engine.events
            if event[1] == "CONFIRMED_ACCEPTANCE_FAILURE_OBSERVED"
        )
        self.assertEqual(observed[4], "OBSERVE")
        self.assertEqual(observed[5], "OBSERVE")

        engine._index = 12
        engine.bars = [completion_bar(), failure_bar(), initiative_bar()]
        plan = v9.resolve_confirmed_acceptance_failure(
            engine,
            state,
            initiative_bar(),
            lambda *_args: sentinel.unexpected,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, Scenario.FAR)
        self.assertEqual(plan.direction, Direction.LONG)
        self.assertEqual(plan.entry_order_type, "MARKET")
        self.assertFalse(plan.entry_post_only)
        self.assertFalse(plan.details["same_bar_reversal_allowed"])
        self.assertEqual(state.state, "PENDING_ENTRY")
        reversal = next(
            event for event in engine.events
            if event[1] == "CONFIRMED_ACCEPTANCE_FAILURE_REVERSAL"
        )
        self.assertEqual(reversal[4], "OBSERVE")
        self.assertEqual(reversal[5], "FAR_CONFIRMED")

    def test_acceptance_completion_is_not_counted_twice(self) -> None:
        engine = DummyEngine()
        state = auction()
        first = v9.observe_completed_acceptance(engine, state, completion_bar())
        calls = engine.track_calls
        second = v9.observe_completed_acceptance(engine, state, completion_bar())
        self.assertIs(first, second)
        self.assertEqual(engine.track_calls, calls)
        self.assertEqual(
            sum(event[1] == "ACCEPTANCE_COMPLETION_OBSERVED" for event in engine.events),
            1,
        )

    def test_exclusive_rejection_delegates_to_v6_far(self) -> None:
        engine = DummyEngine()
        state = auction(exclusive_rejection=True)

        def preserved(*_args: object) -> object:
            return sentinel.preserved

        result = v9.resolve_confirmed_acceptance_failure(
            engine,
            state,
            failure_bar(),
            preserved,
        )
        self.assertIs(result, sentinel.preserved)
        self.assertFalse(v9._completions(engine))
        self.assertFalse(v9._failures(engine))


if __name__ == "__main__":
    unittest.main()
