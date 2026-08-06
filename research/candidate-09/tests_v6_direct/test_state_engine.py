from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

from state_engine_v6_direct import (
    AuctionLevel,
    CompletedRegimeRange,
    EngineConfig,
    FlowBar,
    LiquidityStateEngine,
    PendingResolution,
    risk_based_quantity,
)

CONFIG_V6 = json.loads((Path(__file__).resolve().parents[1] / 'config_v6.json').read_text())


def bar(ts: int, o: float, h: float, l: float, c: float, *, volume: float = 100.0, buy_fraction: float = 0.5) -> FlowBar:
    return FlowBar(ts, o, h, l, c, volume, volume * buy_fraction, 100)


def level(kind: str = 'LOW') -> AuctionLevel:
    if kind == 'LOW':
        return AuctionLevel('x', kind, 100.0, 60, 0, 60, 120.0, 100.0, 110.0, 20.0, 0)
    return AuctionLevel('x', kind, 100.0, 60, 0, 60, 100.0, 80.0, 90.0, 20.0, 0)


class ConfigContractTest(unittest.TestCase):
    def test_single_variable_ablations(self):
        base = EngineConfig.from_mapping(CONFIG_V6, ablation='baseline')
        no_regime = EngineConfig.from_mapping(CONFIG_V6, ablation='no-regime')
        no_retest = EngineConfig.from_mapping(CONFIG_V6, ablation='no-failure-retest')
        far_target = EngineConfig.from_mapping(CONFIG_V6, ablation='opposite-edge-target')
        self.assertTrue(base.require_regime_alignment)
        self.assertTrue(base.require_failure_retest)
        self.assertFalse(base.use_opposite_edge_target)
        self.assertFalse(no_regime.require_regime_alignment)
        self.assertTrue(no_regime.require_failure_retest)
        self.assertFalse(no_retest.require_failure_retest)
        self.assertTrue(far_target.use_opposite_edge_target)
        self.assertEqual(base.auction_horizons_minutes, (5, 15, 60, 1440))
        self.assertEqual(base.regime_horizon_minutes, 240)


class RegimeContractTest(unittest.TestCase):
    def test_completed_ranges_only_drive_regime(self):
        engine = LiquidityStateEngine(EngineConfig())
        self.assertEqual(engine.regime, 'NEUTRAL')
        engine._regime_ranges.append(CompletedRegimeRange(0, 1, 105, 95, 101, 100))
        self.assertEqual(engine.regime, 'NEUTRAL')
        engine._regime_ranges.append(CompletedRegimeRange(1, 2, 108, 96, 107, 102))
        self.assertEqual(engine.regime, 'BULLISH')
        engine._regime_ranges.append(CompletedRegimeRange(2, 3, 103, 91, 92, 97))
        self.assertEqual(engine.regime, 'BEARISH')


class FailureRetestTest(unittest.TestCase):
    def _engine(self, config: EngineConfig) -> LiquidityStateEngine:
        engine = LiquidityStateEngine(config)
        engine._atr = 2.0
        engine._volume_median = 100.0
        engine._regime_ranges.append(CompletedRegimeRange(0, 1, 105, 95, 101, 100))
        engine._regime_ranges.append(CompletedRegimeRange(1, 2, 108, 96, 107, 102))
        return engine

    def test_downside_failure_requires_retest_then_builds_midpoint_buy(self):
        cfg = EngineConfig(minimum_net_reward_to_risk=0.2, composite_cost_per_fill=0.0)
        engine = self._engine(cfg)
        pending = PendingResolution('s', level('LOW'), 'DOWN', 'FAILED', 1, 0.3, -0.2, 1, 88.0,
                                    acceptance_index=2, failure_index=3, failure_high=101.0, failure_low=98.0)
        rejection = bar(10, 99.8, 101.0, 99.6, 100.8, buy_fraction=0.70)
        self.assertTrue(engine._failure_retest_rejected(pending, rejection))
        signal = engine._build_signal(pending, rejection, entry_model='FAILURE_RETEST')
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.side, 'BUY')
        self.assertEqual(signal.target_price, 110.0)
        self.assertLess(signal.stop_price, signal.entry_reference)

    def test_regime_mismatch_blocks_sell_but_ablation_allows_it(self):
        pending = PendingResolution('s', level('HIGH'), 'UP', 'FAILED', 1, 0.3, 0.2, 1, 112.0,
                                    acceptance_index=2, failure_index=3, failure_high=102.0, failure_low=99.0)
        rejection = bar(10, 100.8, 101.0, 99.0, 99.2, buy_fraction=0.30)
        strict = self._engine(EngineConfig(minimum_net_reward_to_risk=0.2, composite_cost_per_fill=0.0))
        self.assertIsNone(strict._build_signal(pending, rejection, entry_model='FAILURE_RETEST'))
        relaxed = self._engine(EngineConfig(require_regime_alignment=False, minimum_net_reward_to_risk=0.2, composite_cost_per_fill=0.0))
        self.assertIsNotNone(relaxed._build_signal(pending, rejection, entry_model='FAILURE_RETEST'))


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
