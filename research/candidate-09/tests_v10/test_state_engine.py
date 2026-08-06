from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

from state_engine_v10_direct import (
    AuctionLevel,
    EngineConfig,
    FlowBar,
    LiquidityStateEngine,
    PendingResolution,
    risk_based_quantity,
)

CONFIG = json.loads((Path(__file__).resolve().parents[1] / 'config_v10.json').read_text())


def bar(ts: int, o: float, h: float, l: float, c: float, *, volume: float = 100.0, buy_fraction: float = 0.5) -> FlowBar:
    return FlowBar(ts, o, h, l, c, volume, volume * buy_fraction, 100)


def level(kind: str = 'HIGH', price: float = 100.0) -> AuctionLevel:
    return AuctionLevel('x', kind, price, 60, 0, 60, 100.0, 80.0, 90.0, 20.0, 0)


class ConfigContractTest(unittest.TestCase):
    def test_single_variable_controlled_ablations(self):
        base = EngineConfig.from_mapping(CONFIG, ablation='baseline')
        continuation = EngineConfig.from_mapping(CONFIG, ablation='with-continuation')
        with_240 = EngineConfig.from_mapping(CONFIG, ablation='with-240m')
        no_flow = EngineConfig.from_mapping(CONFIG, ablation='no-flow')
        self.assertEqual(base.auction_horizons_minutes, (15, 60, 1440))
        self.assertFalse(base.enable_continuation_entries)
        self.assertTrue(continuation.enable_continuation_entries)
        self.assertEqual(continuation.auction_horizons_minutes, base.auction_horizons_minutes)
        self.assertEqual(with_240.auction_horizons_minutes, (15, 60, 240, 1440))
        self.assertFalse(with_240.enable_continuation_entries)
        self.assertFalse(no_flow.use_flow_confirmation)
        self.assertEqual(no_flow.auction_horizons_minutes, base.auction_horizons_minutes)


class V4MechanismContractTest(unittest.TestCase):
    def _config(self, *, continuation: bool) -> EngineConfig:
        return EngineConfig(
            auction_horizons_minutes=(15, 60, 1440),
            minimum_net_reward_to_risk=0.5,
            composite_cost_per_fill=0.0,
            enable_continuation_entries=continuation,
        )

    def test_original_reversal_keeps_equilibrium_target(self):
        engine = LiquidityStateEngine(self._config(continuation=False))
        engine._atr = 2.0
        pending = PendingResolution('s', level(), 'UP', 'ACCEPTED', 1, 0.3, 0.2, 1, 105.0,
                                    outside_closes=2, acceptance_index=2)
        signal = engine._build_signal(pending, bar(10, 99.0, 99.5, 97.5, 98.0, buy_fraction=0.25), branch='REVERSAL')
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.side, 'SELL')
        self.assertEqual(signal.target_price, 90.0)
        self.assertGreater(signal.stop_price, signal.entry_reference)

    def test_baseline_suppresses_only_continuation_entry(self):
        pending = PendingResolution('s', level(), 'UP', 'RETESTED', 1, 0.3, 0.2, 1, 105.0,
                                    outside_closes=2, acceptance_index=2, retest_index=3,
                                    retest_high=101.0, retest_low=99.0)
        current = bar(10, 101.0, 103.0, 100.5, 102.5, buy_fraction=0.75)
        base = LiquidityStateEngine(self._config(continuation=False))
        base._atr = 2.0
        base._levels['HIGH'].append(level('HIGH', 110.0))
        self.assertIsNone(base._build_signal(pending, current, branch='CONTINUATION'))

        restored = LiquidityStateEngine(self._config(continuation=True))
        restored._atr = 2.0
        restored._levels['HIGH'].append(level('HIGH', 110.0))
        self.assertIsNotNone(restored._build_signal(pending, current, branch='CONTINUATION'))


class RiskSizingTest(unittest.TestCase):
    def test_full_cost_floor_respects_three_percent(self):
        result = risk_based_quantity(
            nav=Decimal('100000'), risk_fraction=Decimal('0.03'),
            entry_price=Decimal('50000'), stop_price=Decimal('49500'),
            cost_rate_per_fill=Decimal('0.00075'), quantity_increment=Decimal('0.001'),
        )
        self.assertLessEqual(result.planned_loss, Decimal('3000'))

    def test_above_three_percent_rejected(self):
        with self.assertRaises(ValueError):
            risk_based_quantity(
                nav=Decimal('100000'), risk_fraction=Decimal('0.030001'),
                entry_price=Decimal('50000'), stop_price=Decimal('49500'),
                cost_rate_per_fill=Decimal('0.00075'), quantity_increment=Decimal('0.001'),
            )


if __name__ == '__main__':
    unittest.main()
