from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

from state_engine_v12_direct import (
    AuctionLevel,
    EngineConfig,
    FlowBar,
    LiquidityStateEngine,
    PendingResolution,
    risk_based_quantity,
)

CONFIG = json.loads((Path(__file__).resolve().parents[1] / 'config_v12.json').read_text())


def bar(ts: int, o: float, h: float, l: float, c: float, *, volume: float = 100.0, buy_fraction: float = 0.5) -> FlowBar:
    return FlowBar(ts, o, h, l, c, volume, volume * buy_fraction, 100)


def level(kind: str, *, midpoint: float = 90.0) -> AuctionLevel:
    return AuctionLevel('x', kind, 100.0, 60, 0, 60, 120.0, 80.0, midpoint, 40.0, 0)


def pending_up(*, extreme: float = 101.0) -> PendingResolution:
    return PendingResolution(
        'up', level('HIGH', midpoint=90.0), 'UP', 'ACCEPTED', 1, 0.3, 0.2, 1, extreme,
        outside_closes=2, acceptance_index=2,
    )


def pending_down(*, extreme: float = 90.0) -> PendingResolution:
    return PendingResolution(
        'down', level('LOW', midpoint=90.0), 'DOWN', 'ACCEPTED', 1, 0.3, -0.2, 1, extreme,
        outside_closes=2, acceptance_index=2,
    )


class ConfigContractTest(unittest.TestCase):
    def test_single_variable_ablations(self):
        base = EngineConfig.from_mapping(CONFIG, ablation='baseline')
        disabled = EngineConfig.from_mapping(CONFIG, ablation='no-limit-salvage')
        all_limit = EngineConfig.from_mapping(CONFIG, ablation='limit-all')
        no_flow = EngineConfig.from_mapping(CONFIG, ablation='no-flow')
        self.assertTrue(base.enable_limit_salvage)
        self.assertFalse(base.limit_all_reversals)
        self.assertEqual(base.limit_entry_timeout_bars, 12)
        self.assertFalse(disabled.enable_limit_salvage)
        self.assertTrue(all_limit.limit_all_reversals)
        self.assertFalse(no_flow.use_flow_confirmation)
        self.assertEqual(base.auction_horizons_minutes, (15, 60, 1440))
        self.assertFalse(base.enable_continuation_entries)

    def test_timeout_must_be_positive(self):
        payload = json.loads(json.dumps(CONFIG))
        payload['trade']['limit_entry_timeout_bars'] = 0
        with self.assertRaises(ValueError):
            EngineConfig.from_mapping(payload)


class PassiveLimitSalvageTest(unittest.TestCase):
    def _engine(self, config: EngineConfig) -> LiquidityStateEngine:
        engine = LiquidityStateEngine(config)
        engine._atr = 2.0
        engine._index = 10
        return engine

    def test_strong_v10_reversal_remains_market_in_baseline(self):
        cfg = EngineConfig(
            minimum_net_reward_to_risk=0.5,
            composite_cost_per_fill=0.0,
            enable_limit_salvage=True,
            limit_all_reversals=False,
        )
        engine = self._engine(cfg)
        signal = engine._build_signal(
            pending_up(extreme=101.0),
            bar(10, 99.0, 99.5, 97.5, 98.0, buy_fraction=0.25),
            branch='REVERSAL',
        )
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertNotEqual(signal.details.get('entry_order_type'), 'LIMIT')
        self.assertFalse(engine._limit_attempted)

    def test_untradeable_failure_close_becomes_passive_boundary_limit(self):
        cfg = EngineConfig(
            minimum_net_reward_to_risk=1.2,
            composite_cost_per_fill=0.00075,
            enable_limit_salvage=True,
            limit_all_reversals=False,
            limit_entry_timeout_bars=12,
        )
        engine = self._engine(cfg)
        signal = engine._build_signal(
            pending_down(extreme=90.0),
            bar(10, 103.0, 106.0, 102.5, 105.0, buy_fraction=0.75),
            branch='REVERSAL',
        )
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.side, 'BUY')
        self.assertEqual(signal.entry_reference, 100.0)
        self.assertEqual(signal.details['entry_order_type'], 'LIMIT')
        self.assertEqual(signal.details['entry_timeout_bars'], 12)
        self.assertLess(signal.entry_reference, 105.0)
        self.assertLess(signal.stop_price, signal.entry_reference)
        self.assertEqual(signal.target_price, 120.0)
        self.assertGreaterEqual(signal.net_reward_to_risk, 1.2)

    def test_no_limit_salvage_is_exact_v10_rejection(self):
        cfg = EngineConfig(
            minimum_net_reward_to_risk=1.2,
            composite_cost_per_fill=0.00075,
            enable_limit_salvage=False,
            limit_all_reversals=False,
        )
        engine = self._engine(cfg)
        signal = engine._build_signal(
            pending_down(extreme=90.0),
            bar(10, 103.0, 106.0, 102.5, 105.0, buy_fraction=0.75),
            branch='REVERSAL',
        )
        self.assertIsNone(signal)
        self.assertFalse(engine._limit_attempted)

    def test_limit_all_converts_even_tradeable_v10_signal(self):
        cfg = EngineConfig(
            minimum_net_reward_to_risk=0.5,
            composite_cost_per_fill=0.0,
            enable_limit_salvage=True,
            limit_all_reversals=True,
        )
        engine = self._engine(cfg)
        signal = engine._build_signal(
            pending_up(extreme=101.0),
            bar(10, 99.0, 99.5, 97.5, 98.0, buy_fraction=0.25),
            branch='REVERSAL',
        )
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.side, 'SELL')
        self.assertEqual(signal.entry_reference, 100.0)
        self.assertEqual(signal.details['entry_order_type'], 'LIMIT')
        self.assertGreater(signal.entry_reference, 98.0)
        self.assertTrue(engine._limit_diagnostic['limit_all_controlled_ablation'])

    def test_limit_is_rejected_if_marketable_at_decision_time(self):
        cfg = EngineConfig(minimum_net_reward_to_risk=0.1, composite_cost_per_fill=0.0)
        engine = self._engine(cfg)
        signal, diagnostic = engine._build_failed_boundary_limit(
            pending_up(extreme=105.0),
            bar(10, 101.0, 102.0, 100.5, 101.5, buy_fraction=0.25),
        )
        self.assertIsNone(signal)
        self.assertEqual(
            diagnostic['rejection_reason'],
            'FAILED_BOUNDARY_LIMIT_WOULD_BE_MARKETABLE_AT_DECISION_TIME',
        )


class StrategySourceContractTest(unittest.TestCase):
    def test_nautilus_adapter_uses_native_limit_bracket_and_timeout_cancel(self):
        source = (Path(__file__).resolve().parents[1] / 'nautilus_strategy_v12.py').read_text()
        self.assertIn('entry_order_type=entry_order_type', source)
        self.assertIn('entry_price=entry_price', source)
        self.assertIn('entry_post_only=False', source)
        self.assertIn('ENTRY_LIMIT_TIMEOUT', source)
        self.assertIn('self.cancel_all_orders(self.config.instrument_id)', source)
        self.assertIn('"entry_order_type": self._entry_order_type', source)


class RiskSizingTest(unittest.TestCase):
    def test_limit_expected_fill_full_cost_floor_respects_three_percent(self):
        result = risk_based_quantity(
            nav=Decimal('100000'), risk_fraction=Decimal('0.03'),
            entry_price=Decimal('50000'), stop_price=Decimal('49500'),
            cost_rate_per_fill=Decimal('0.00075'), quantity_increment=Decimal('0.001'),
        )
        self.assertLessEqual(result.planned_loss, Decimal('3000'))
        self.assertGreater(result.per_unit_expected_loss, Decimal('500'))

    def test_above_three_percent_rejected(self):
        with self.assertRaises(ValueError):
            risk_based_quantity(
                nav=Decimal('100000'), risk_fraction=Decimal('0.030001'),
                entry_price=Decimal('50000'), stop_price=Decimal('49500'),
                cost_rate_per_fill=Decimal('0.00075'), quantity_increment=Decimal('0.001'),
            )


if __name__ == '__main__':
    unittest.main()
