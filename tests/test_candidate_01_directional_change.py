from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "research" / "candidate-01"
SRC = ROOT / "src"
for item in (CANDIDATE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from aggtrade_clock import VolumeBar
from directional_change_failed_sweep_week import (
    DIRECTIONAL_CHANGE_FRACTION,
    STOP_BUFFER_FRACTION,
    DirectionalChangeDetector,
    DirectionalChangeEvent,
    FailedSweepRetestStateMachine,
)
from impact_regime_probe import EventFeature


NS = 1_000_000_000


def feature(
    index: int,
    *,
    close: float,
    high: float | None = None,
    low: float | None = None,
    imbalance: float = 0.0,
    z: float | None = 0.0,
) -> EventFeature:
    high = close if high is None else high
    low = close if low is None else low
    quote = 100.0
    signed = quote * imbalance
    bar = VolumeBar(
        index=index,
        start_time_ns=(2 * index + 1) * NS,
        end_time_ns=(2 * index + 2) * NS,
        open=close,
        high=high,
        low=low,
        close=close,
        base_quantity=1.0,
        quote_notional=quote,
        signed_quote_notional=signed,
        aggressive_buy_quote=max(signed, 0.0),
        aggressive_sell_quote=max(-signed, 0.0),
        aggregate_trades=1,
        first_agg_trade_id=index,
        last_agg_trade_id=index,
        target_quote_notional=100.0,
    )
    return EventFeature(
        bar=bar,
        true_range=high - low,
        atr=1.0,
        imbalance_z=z,
    )


def dc_event(
    *,
    event_type: str,
    confirmation_index: int,
    pivot_price: float,
    confirmation_price: float,
    trend_flow: float,
    reversal_flow: float,
    path_high: float,
    path_low: float,
) -> DirectionalChangeEvent:
    return DirectionalChangeEvent(
        event_type=event_type,  # type: ignore[arg-type]
        confirmation_index=confirmation_index,
        confirmation_time_ns=(2 * confirmation_index + 2) * NS,
        confirmation_price=confirmation_price,
        pivot_index=confirmation_index - 1,
        pivot_time_ns=(2 * confirmation_index) * NS,
        pivot_price=pivot_price,
        trend_start_index=max(confirmation_index - 3, 0),
        trend_flow_imbalance=trend_flow,
        reversal_flow_imbalance=reversal_flow,
        path_high=path_high,
        path_low=path_low,
    )


class DirectionalChangeDetectorTest(unittest.TestCase):
    def test_pivot_is_not_observable_until_full_cost_resolved_change(self) -> None:
        detector = DirectionalChangeDetector(
            threshold_fraction=DIRECTIONAL_CHANGE_FRACTION,
        )
        rows = [
            feature(0, close=100.0),
            feature(1, close=99.9),
            feature(2, close=100.20),
            feature(3, close=100.31),
        ]
        self.assertIsNone(detector.on_feature(index=0, features=rows[:1]))
        self.assertIsNone(detector.on_feature(index=1, features=rows[:2]))
        self.assertIsNone(detector.on_feature(index=2, features=rows[:3]))
        event = detector.on_feature(index=3, features=rows)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event_type, "UP")
        self.assertEqual(event.pivot_index, 1)
        self.assertEqual(event.pivot_price, 99.9)
        self.assertEqual(event.confirmation_index, 3)


class FailedSweepRetestTest(unittest.TestCase):
    def _machine_with_liquidity(self) -> FailedSweepRetestStateMachine:
        machine = FailedSweepRetestStateMachine()
        machine.high_events.append(
            dc_event(
                event_type="DOWN",
                confirmation_index=5,
                pivot_price=100.0,
                confirmation_price=99.0,
                trend_flow=0.20,
                reversal_flow=-0.10,
                path_high=100.1,
                path_low=98.0,
            ),
        )
        machine.low_events.extend(
            [
                dc_event(
                    event_type="UP",
                    confirmation_index=2,
                    pivot_price=90.0,
                    confirmation_price=91.0,
                    trend_flow=-0.20,
                    reversal_flow=0.10,
                    path_high=92.0,
                    path_low=89.9,
                ),
                dc_event(
                    event_type="UP",
                    confirmation_index=4,
                    pivot_price=92.0,
                    confirmation_price=93.0,
                    trend_flow=-0.10,
                    reversal_flow=0.10,
                    path_high=94.0,
                    path_low=91.9,
                ),
            ],
        )
        return machine

    def test_sweep_failure_arms_external_liquidity_target(self) -> None:
        machine = self._machine_with_liquidity()
        current = dc_event(
            event_type="DOWN",
            confirmation_index=10,
            pivot_price=101.0,
            confirmation_price=99.0,
            trend_flow=0.25,
            reversal_flow=-0.20,
            path_high=101.2,
            path_low=98.5,
        )
        observed = feature(10, close=99.0, high=99.2, low=98.5, z=-0.5)
        machine._arm_from_event(event=current, feature=observed)
        self.assertEqual(len(machine.active), 1)
        setup = machine.active[0]
        self.assertEqual(setup.side.value, "SHORT")
        self.assertEqual(setup.boundary, 100.0)
        self.assertEqual(setup.target_price, 90.0)
        self.assertEqual(machine.counts["armed"], 1)

    def test_boundary_retest_with_aligned_flow_emits_strict_plan(self) -> None:
        machine = self._machine_with_liquidity()
        current = dc_event(
            event_type="DOWN",
            confirmation_index=10,
            pivot_price=101.0,
            confirmation_price=99.0,
            trend_flow=0.25,
            reversal_flow=-0.20,
            path_high=101.2,
            path_low=98.5,
        )
        machine._arm_from_event(
            event=current,
            feature=feature(10, close=99.0, high=99.2, low=98.5, z=-0.5),
        )
        rows = [feature(index, close=95.0) for index in range(12)]
        rows[11] = feature(
            11,
            close=99.5,
            high=100.1,
            low=98.0,
            z=-1.0,
        )
        emitted = machine.on_feature(index=11, features=rows)
        self.assertEqual(len(emitted), 1)
        plan = emitted[0]
        self.assertEqual(plan.side.value, "SHORT")
        self.assertEqual(plan.target_price, 90.0)
        self.assertEqual(plan.confirmation_hold_price, 100.0)
        self.assertAlmostEqual(
            plan.stop_price,
            101.2 * (1.0 + STOP_BUFFER_FRACTION),
        )

    def test_future_bars_cannot_modify_emitted_plan(self) -> None:
        machine = self._machine_with_liquidity()
        current = dc_event(
            event_type="DOWN",
            confirmation_index=10,
            pivot_price=101.0,
            confirmation_price=99.0,
            trend_flow=0.25,
            reversal_flow=-0.20,
            path_high=101.2,
            path_low=98.5,
        )
        machine._arm_from_event(
            event=current,
            feature=feature(10, close=99.0, high=99.2, low=98.5, z=-0.5),
        )
        rows = [feature(index, close=95.0) for index in range(13)]
        rows[11] = feature(11, close=99.5, high=100.1, low=98.0, z=-1.0)
        plan = machine.on_feature(index=11, features=rows[:12])[0]
        signature = (plan.signal_time_ns, plan.stop_price, plan.target_price)
        rows[12] = feature(12, close=120.0, high=130.0, low=80.0, z=3.0)
        machine.on_feature(index=12, features=rows)
        self.assertEqual(
            signature,
            (
                machine.plans[0].signal_time_ns,
                machine.plans[0].stop_price,
                machine.plans[0].target_price,
            ),
        )


if __name__ == "__main__":
    unittest.main()
