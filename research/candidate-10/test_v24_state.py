"""Regression tests for candidate-10 v24 cross-market state grammar."""
from __future__ import annotations

import math
import unittest

from c10_v24_model import CrossMarketBar, CrossMarketParams
from c10_v24_state import CrossMarketReconciliationStateMachine


def _bar(
    index: int,
    *,
    spot: float,
    perp: float,
    spot_flow: float = 0.05,
    perp_flow: float = 0.05,
    width: float = 0.05,
) -> CrossMarketBar:
    volume = 1_000_000.0
    return CrossMarketBar(
        ts_ns=(index + 1) * 5_000_000_000,
        spot_open=spot,
        spot_high=spot + width,
        spot_low=spot - width,
        spot_close=spot,
        spot_quote_volume=volume,
        spot_taker_buy_quote=0.5 * (1.0 + spot_flow) * volume,
        spot_trade_count=100,
        perp_open=perp,
        perp_high=perp + width,
        perp_low=perp - width,
        perp_close=perp,
        perp_quote_volume=volume,
        perp_taker_buy_quote=0.5 * (1.0 + perp_flow) * volume,
        perp_trade_count=100,
    )


def _params(*, use_spot_flow: bool = True) -> CrossMarketParams:
    return CrossMarketParams(
        feature_lookback=40,
        minimum_feature_history=12,
        return_horizon_bars=3,
        dislocation_z=2.0,
        basis_z=1.5,
        lag_ratio=0.65,
        basis_contraction_fraction=0.15,
        probe_max_bars=4,
        cooldown_normal_bars=2,
        stop_range_multiple=0.25,
        impact_range_fraction=0.02,
        current_range_impact_fraction=0.01,
        execution_reserve_ticks=1,
        use_spot_flow=use_spot_flow,
    )


def _machine(*, use_spot_flow: bool = True) -> CrossMarketReconciliationStateMachine:
    machine = CrossMarketReconciliationStateMachine(
        _params(use_spot_flow=use_spot_flow),
        tick_size=0.01,
        instrument_id="BTCUSDT-PERP.BINANCE",
    )
    for index in range(24):
        common = 100.0 + 0.02 * math.sin(index / 2.0)
        basis = 0.01 * math.sin(index / 3.0)
        machine.on_bar(
            _bar(
                index,
                spot=common,
                perp=common + basis,
                spot_flow=0.04 if index % 2 == 0 else -0.04,
                perp_flow=0.03 if index % 3 else -0.03,
            ),
        )
    return machine


class CrossMarketStateTests(unittest.TestCase):
    def test_spot_lead_requires_spot_flow_only_in_full(self) -> None:
        full = _machine(use_spot_flow=True)
        ablation = _machine(use_spot_flow=False)
        event = _bar(
            24,
            spot=102.0,
            perp=100.2,
            spot_flow=-0.50,
            perp_flow=0.05,
        )
        full_events, full_plan = full.on_bar(event)
        ablation_events, ablation_plan = ablation.on_bar(event)
        self.assertEqual(full_events, [])
        self.assertIsNone(full_plan)
        self.assertEqual(len(ablation_events), 1)
        self.assertIsNone(ablation_plan)
        self.assertEqual(
            ablation_events[0].reason_code,
            "SPOT_DISPLACEMENT_WITH_PERP_LAG",
        )
        self.assertEqual(ablation.active_probe.mode, "SPOT_LEAD_CATCHUP")

    def test_spot_lead_catchup_confirms_on_later_completed_bar(self) -> None:
        machine = _machine()
        event_events, event_plan = machine.on_bar(
            _bar(
                24,
                spot=102.0,
                perp=100.2,
                spot_flow=0.60,
                perp_flow=0.02,
            ),
        )
        self.assertEqual(len(event_events), 1)
        self.assertIsNone(event_plan)
        probe = machine.active_probe
        self.assertIsNotNone(probe)
        assert probe is not None
        fixed_target = probe.fair_target

        confirm_events, plan = machine.on_bar(
            _bar(
                25,
                spot=102.05,
                perp=100.75,
                spot_flow=0.20,
                perp_flow=0.55,
            ),
        )
        self.assertEqual(len(confirm_events), 1)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, "SPOT_LEAD_CATCHUP")
        self.assertEqual(plan.direction, 1)
        self.assertEqual(plan.observed_ns, confirm_events[0].observed_time_ns)
        self.assertAlmostEqual(plan.target_price, fixed_target)
        self.assertLess(plan.stop_price, plan.entry_estimate)
        self.assertGreater(plan.target_price, plan.entry_estimate)
        self.assertGreaterEqual(plan.cost_adjusted_net_rr, machine.params.min_net_rr)
        self.assertTrue(machine.cooldown_active)
        self.assertIsNone(machine.active_probe)

    def test_perp_overshoot_reversion_requires_flow_reversal(self) -> None:
        machine = _machine()
        events, plan = machine.on_bar(
            _bar(
                24,
                spot=100.0,
                perp=102.0,
                spot_flow=0.00,
                perp_flow=0.65,
            ),
        )
        self.assertEqual(len(events), 1)
        self.assertIsNone(plan)
        self.assertEqual(
            machine.active_probe.mode,
            "PERP_OVERSHOOT_REVERSION",
        )

        no_confirm, no_plan = machine.on_bar(
            _bar(
                25,
                spot=100.0,
                perp=101.8,
                spot_flow=0.00,
                perp_flow=0.40,
            ),
        )
        self.assertEqual(no_confirm, [])
        self.assertIsNone(no_plan)
        self.assertIsNotNone(machine.active_probe)

        confirm, plan = machine.on_bar(
            _bar(
                26,
                spot=100.0,
                perp=101.5,
                spot_flow=0.00,
                perp_flow=-0.60,
            ),
        )
        self.assertEqual(len(confirm), 1)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.direction, -1)
        self.assertEqual(plan.scenario, "PERP_OVERSHOOT_REVERSION")
        self.assertLess(plan.target_price, plan.entry_estimate)
        self.assertGreater(plan.stop_price, plan.entry_estimate)

    def test_fair_target_is_fixed_at_detection_not_future_spot(self) -> None:
        machine = _machine()
        machine.on_bar(
            _bar(24, spot=102.0, perp=100.2, spot_flow=0.60, perp_flow=0.02),
        )
        probe = machine.active_probe
        assert probe is not None
        fixed = probe.fair_target
        _, plan = machine.on_bar(
            _bar(25, spot=102.2, perp=100.7, spot_flow=0.30, perp_flow=0.60),
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertAlmostEqual(plan.target_price, fixed)
        self.assertNotAlmostEqual(
            plan.target_price,
            102.2 * math.exp(probe.fair_basis),
        )

    def test_one_event_cannot_retrigger_until_normalized(self) -> None:
        machine = _machine()
        machine.on_bar(
            _bar(24, spot=102.0, perp=100.2, spot_flow=0.60, perp_flow=0.02),
        )
        _, plan = machine.on_bar(
            _bar(25, spot=102.05, perp=100.75, spot_flow=0.20, perp_flow=0.55),
        )
        self.assertIsNotNone(plan)
        event_count = machine.event_sequence
        events, plan = machine.on_bar(
            _bar(26, spot=104.0, perp=101.0, spot_flow=0.70, perp_flow=0.05),
        )
        self.assertEqual(events, [])
        self.assertIsNone(plan)
        self.assertEqual(machine.event_sequence, event_count)
        self.assertIsNone(machine.active_probe)

    def test_probe_expires_without_reconciliation(self) -> None:
        machine = _machine()
        machine.on_bar(
            _bar(24, spot=100.0, perp=102.0, spot_flow=0.0, perp_flow=0.65),
        )
        final_events = []
        for offset in range(1, 5):
            final_events, plan = machine.on_bar(
                _bar(
                    24 + offset,
                    spot=100.0,
                    perp=102.1 + 0.1 * offset,
                    spot_flow=0.0,
                    perp_flow=0.50,
                ),
            )
            self.assertIsNone(plan)
        self.assertEqual(len(final_events), 1)
        self.assertEqual(final_events[0].event_type, "SCENARIO_EXPIRED")
        self.assertEqual(
            final_events[0].reason_code,
            "NO_CROSS_MARKET_RECONCILIATION_WITHIN_ONE_MINUTE",
        )
        self.assertIsNone(machine.active_probe)


if __name__ == "__main__":
    unittest.main()
