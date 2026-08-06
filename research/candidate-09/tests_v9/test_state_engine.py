from __future__ import annotations

import json
import unittest
from collections import deque
from decimal import Decimal
from pathlib import Path

from state_engine_v9_direct import (
    MINUTE_NS,
    EngineConfig,
    FlowBar,
    LiquidityLevel,
    LiquidityStateEngine,
    PendingAcceptanceFailure,
    SessionRange,
    SessionSpec,
    risk_based_quantity,
)

CONFIG = json.loads((Path(__file__).resolve().parents[1] / 'config_v9.json').read_text())


def bar(minute: int, o: float, h: float, l: float, c: float, *, volume: float = 100.0, buy_fraction: float = 0.5) -> FlowBar:
    return FlowBar((minute + 1) * MINUTE_NS, o, h, l, c, volume, volume * buy_fraction, 100)


def sessions() -> tuple[SessionSpec, ...]:
    return (
        SessionSpec('ASIA', 0, 480, False),
        SessionSpec('EUROPE', 480, 780, True),
        SessionSpec('US', 780, 1260, True),
        SessionSpec('LATE', 1260, 1440, False),
    )


def source() -> SessionRange:
    return SessionRange('r', 'ASIA', 0, 480 * MINUTE_NS, 100.0, 80.0, 90.0, 480)


def pending_up() -> PendingAcceptanceFailure:
    return PendingAcceptanceFailure(
        's', LiquidityLevel('l', 'HIGH', 100.0, source()), 'UP', 'FAILED_ATTEMPT',
        0, 105.0, 0.3, 0.2, 1, 'EUROPE', outside_closes=2,
        acceptance_index=1, acceptance_displacement_seen=True,
        acceptance_flow_seen=True, max_volume_ratio=1.5,
        failure_index=4, failure_high=100.5, failure_low=95.0,
    )


class ConfigContractTest(unittest.TestCase):
    def test_single_variable_ablations(self):
        base = EngineConfig.from_mapping(CONFIG, ablation='baseline')
        plain = EngineConfig.from_mapping(CONFIG, ablation='plain-acceptance')
        retest = EngineConfig.from_mapping(CONFIG, ablation='reacceptance-retest')
        half = EngineConfig.from_mapping(CONFIG, ablation='half-range-target')
        self.assertTrue(base.require_failure_trap)
        self.assertFalse(base.require_reacceptance_retest)
        self.assertFalse(base.use_half_range_extension)
        self.assertFalse(plain.require_failure_trap)
        self.assertTrue(retest.require_reacceptance_retest)
        self.assertTrue(half.use_half_range_extension)
        self.assertTrue(base.require_acceptance_confirmation)


class FailedFailureTest(unittest.TestCase):
    def _engine(self, config: EngineConfig | None = None) -> LiquidityStateEngine:
        engine = LiquidityStateEngine(config or EngineConfig(
            sessions=sessions(), atr_period=3, volume_period=3, approach_period=2,
            mss_lookback_bars=3, minimum_net_reward_to_risk=1.2,
            composite_cost_per_fill=0.00075,
        ))
        engine._atr = 2.0
        engine._volume_median = 100.0
        engine._current_session = sessions()[1]
        engine._current_session_key = 1
        return engine

    def test_original_breakout_reacceptance_requires_new_displacement_mss_volume_and_flow(self):
        engine = self._engine()
        pending = pending_up()
        engine._bars = deque([
            bar(500, 97.0, 98.0, 96.0, 97.5),
            bar(501, 97.5, 99.0, 97.0, 98.5),
            bar(502, 98.5, 100.5, 98.0, 99.5),
            bar(503, 100.0, 103.5, 99.8, 103.0, volume=160, buy_fraction=0.75),
        ], maxlen=512)
        self.assertTrue(engine._reacceptance_confirmed(pending, engine._bars[-1]))

    def test_reaccepted_high_breakout_builds_buy_to_full_range_extension(self):
        engine = self._engine()
        pending = pending_up()
        entry = bar(503, 100.0, 103.5, 99.8, 103.0, volume=160, buy_fraction=0.75)
        signal, reason, diagnostic = engine._build_signal(pending, entry, entry_model='REACCEPTANCE_CLOSE')
        self.assertIsNotNone(signal, reason)
        assert signal is not None
        self.assertEqual(signal.side, 'BUY')
        self.assertEqual(signal.target_price, 120.0)
        self.assertLess(signal.stop_price, signal.entry_reference)
        self.assertGreaterEqual(signal.net_reward_to_risk, 1.2)
        self.assertEqual(diagnostic['target_model'], 'FULL_RANGE_EXTENSION')

    def test_half_range_ablation_changes_only_objective_distance(self):
        config = EngineConfig(
            sessions=sessions(), atr_period=3, volume_period=3, approach_period=2,
            mss_lookback_bars=3, minimum_net_reward_to_risk=0.2,
            composite_cost_per_fill=0.00075, use_half_range_extension=True,
        )
        engine = self._engine(config)
        signal, reason, diagnostic = engine._build_signal(
            pending_up(), bar(503, 100.0, 103.5, 99.8, 103.0, volume=160, buy_fraction=0.75),
            entry_model='REACCEPTANCE_CLOSE',
        )
        self.assertIsNotNone(signal, reason)
        assert signal is not None
        self.assertEqual(signal.target_price, 110.0)
        self.assertEqual(diagnostic['target_model'], 'HALF_RANGE_EXTENSION')

    def test_reacceptance_retest_requires_touch_and_defense_outside(self):
        engine = self._engine()
        pending = pending_up()
        pending.state = 'REACCEPTED'
        rejection = bar(504, 100.4, 102.2, 100.1, 101.8, volume=120, buy_fraction=0.70)
        self.assertTrue(engine._reacceptance_retest_rejected(pending, rejection))

    def test_plain_acceptance_ablation_removes_only_failure_trap_requirement(self):
        base = EngineConfig.from_mapping(CONFIG, ablation='baseline')
        plain = EngineConfig.from_mapping(CONFIG, ablation='plain-acceptance')
        self.assertEqual(base.sessions, plain.sessions)
        self.assertEqual(base.acceptance_closes, plain.acceptance_closes)
        self.assertEqual(base.minimum_net_reward_to_risk, plain.minimum_net_reward_to_risk)
        self.assertNotEqual(base.require_failure_trap, plain.require_failure_trap)


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
