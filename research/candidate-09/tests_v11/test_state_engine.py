from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

from state_engine_v11_direct import (
    AuctionLevel,
    EngineConfig,
    FlowBar,
    LiquidityStateEngine,
    PendingResolution,
    risk_based_quantity,
)

CONFIG = json.loads((Path(__file__).resolve().parents[1] / 'config_v11.json').read_text())


def bar(ts: int, o: float, h: float, l: float, c: float, *, volume: float = 100.0, buy_fraction: float = 0.5) -> FlowBar:
    return FlowBar(ts, o, h, l, c, volume, volume * buy_fraction, 100)


def level(kind: str = 'HIGH', midpoint: float = 90.0) -> AuctionLevel:
    return AuctionLevel('x', kind, 100.0, 60, 0, 60, 100.0, 80.0, midpoint, 20.0, 0)


def pending_up(*, extreme: float = 105.0) -> PendingResolution:
    return PendingResolution('s', level(), 'UP', 'ACCEPTED', 1, 0.3, 0.2, 1, extreme,
                             outside_closes=2, acceptance_index=2)


class ConfigContractTest(unittest.TestCase):
    def test_single_variable_ablations(self):
        base = EngineConfig.from_mapping(CONFIG, ablation='baseline')
        disabled = EngineConfig.from_mapping(CONFIG, ablation='no-retest-salvage')
        all_retest = EngineConfig.from_mapping(CONFIG, ablation='retest-all')
        no_flow = EngineConfig.from_mapping(CONFIG, ablation='no-flow')
        self.assertTrue(base.enable_retest_salvage)
        self.assertFalse(base.retest_all_reversals)
        self.assertFalse(disabled.enable_retest_salvage)
        self.assertTrue(all_retest.retest_all_reversals)
        self.assertFalse(no_flow.use_flow_confirmation)
        self.assertEqual(base.auction_horizons_minutes, (15, 60, 1440))
        self.assertFalse(base.enable_continuation_entries)


class RetestSalvageTest(unittest.TestCase):
    def _engine(self, config: EngineConfig) -> LiquidityStateEngine:
        engine = LiquidityStateEngine(config)
        engine._atr = 2.0
        engine._index = 10
        return engine

    def test_strong_v10_signal_remains_immediate_in_baseline(self):
        cfg = EngineConfig(minimum_net_reward_to_risk=0.5, composite_cost_per_fill=0.0,
                           enable_retest_salvage=True, retest_all_reversals=False)
        engine = self._engine(cfg)
        signal = engine._build_signal(pending_up(extreme=101.0), bar(10, 99.0, 99.5, 97.5, 98.0, buy_fraction=0.25), branch='REVERSAL')
        self.assertIsNotNone(signal)
        self.assertFalse(engine._retest_staged)

    def test_untradeable_failure_is_staged_not_discarded(self):
        cfg = EngineConfig(minimum_net_reward_to_risk=1.2, composite_cost_per_fill=0.00075,
                           enable_retest_salvage=True, retest_all_reversals=False)
        engine = self._engine(cfg)
        pending = pending_up(extreme=120.0)
        signal = engine._build_signal(pending, bar(10, 99.0, 99.5, 97.5, 98.0, buy_fraction=0.25), branch='REVERSAL')
        self.assertIsNone(signal)
        self.assertEqual(pending.state, 'FAILED_UNTRADEABLE_WAIT_RETEST')
        self.assertTrue(engine._retest_staged)

    def test_disabled_ablation_is_exact_v10_rejection(self):
        cfg = EngineConfig(minimum_net_reward_to_risk=1.2, composite_cost_per_fill=0.00075,
                           enable_retest_salvage=False, retest_all_reversals=False)
        engine = self._engine(cfg)
        pending = pending_up(extreme=120.0)
        self.assertIsNone(engine._build_signal(pending, bar(10, 99.0, 99.5, 97.5, 98.0, buy_fraction=0.25), branch='REVERSAL'))
        self.assertEqual(pending.state, 'ACCEPTED')
        self.assertFalse(engine._retest_staged)

    def test_failure_retest_rejection_is_directional(self):
        cfg = EngineConfig(minimum_net_reward_to_risk=0.1, composite_cost_per_fill=0.0)
        engine = self._engine(cfg)
        pending = pending_up(extreme=105.0)
        pending.state = 'FAILED_UNTRADEABLE_WAIT_RETEST'
        pending.retest_index = 9
        rejection = bar(11, 99.8, 100.1, 98.6, 99.0, buy_fraction=0.25)
        self.assertTrue(engine._failure_retest_rejected(pending, rejection))
        self.assertFalse(engine._failure_reaccepted(pending, rejection))

    def test_retest_all_stages_even_tradeable_signal(self):
        cfg = EngineConfig(minimum_net_reward_to_risk=0.5, composite_cost_per_fill=0.0,
                           enable_retest_salvage=True, retest_all_reversals=True)
        engine = self._engine(cfg)
        pending = pending_up(extreme=101.0)
        self.assertIsNone(engine._build_signal(pending, bar(10, 99.0, 99.5, 97.5, 98.0, buy_fraction=0.25), branch='REVERSAL'))
        self.assertEqual(pending.state, 'FAILED_WAIT_RETEST_ALL')


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
