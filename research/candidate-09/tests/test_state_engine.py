from __future__ import annotations

import json
import unittest
from collections import deque
from decimal import Decimal
from pathlib import Path

from state_engine_v8_direct import (
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

CONFIG = json.loads((Path(__file__).resolve().parents[1] / 'config_v8.json').read_text())


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


class ConfigContractTest(unittest.TestCase):
    def test_single_variable_ablations(self):
        base = EngineConfig.from_mapping(CONFIG, ablation='baseline')
        no_acceptance = EngineConfig.from_mapping(CONFIG, ablation='no-acceptance')
        retest = EngineConfig.from_mapping(CONFIG, ablation='failure-retest')
        midpoint = EngineConfig.from_mapping(CONFIG, ablation='midpoint-target')
        self.assertTrue(base.require_acceptance_confirmation)
        self.assertFalse(base.require_failure_retest)
        self.assertFalse(base.use_midpoint_target)
        self.assertFalse(no_acceptance.require_acceptance_confirmation)
        self.assertTrue(retest.require_failure_retest)
        self.assertTrue(midpoint.use_midpoint_target)


class SessionContractTest(unittest.TestCase):
    def test_bar_close_boundary_is_causal(self):
        engine = LiquidityStateEngine(EngineConfig(sessions=sessions()))
        _, _, a, _, _ = engine._session_identity(480 * MINUTE_NS)
        _, _, b, _, _ = engine._session_identity(481 * MINUTE_NS)
        self.assertEqual(a.name, 'ASIA')
        self.assertEqual(b.name, 'EUROPE')


class AcceptedFailureTest(unittest.TestCase):
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

    def _pending(self) -> PendingAcceptanceFailure:
        return PendingAcceptanceFailure(
            's', LiquidityLevel('l', 'HIGH', 100.0, source()), 'UP', 'ACCEPTED',
            0, 105.0, 0.3, 0.2, 1, 'EUROPE', outside_closes=2,
            acceptance_index=1, acceptance_displacement_seen=True,
            acceptance_flow_seen=True, max_volume_ratio=1.5,
        )

    def test_acceptance_contract_requires_closes_displacement_volume_and_flow(self):
        engine = self._engine()
        pending = self._pending()
        self.assertTrue(engine._acceptance_ready(pending))
        pending.acceptance_flow_seen = False
        self.assertFalse(engine._acceptance_ready(pending))

    def test_accepted_high_failure_builds_sell_to_opposite_edge(self):
        engine = self._engine()
        pending = self._pending()
        engine._bars = deque([
            bar(500, 101, 102, 100.0, 101.0),
            bar(501, 101, 101.5, 99.8, 100.5),
            bar(502, 100.5, 101.0, 99.5, 100.0),
            bar(503, 100.0, 100.2, 98.0, 98.5, volume=160, buy_fraction=0.25),
        ], maxlen=512)
        failure = engine._bars[-1]
        self.assertTrue(engine._failure_confirmed(pending, failure))
        signal, reason, _ = engine._build_signal(pending, failure, entry_model='FAILURE_CLOSE')
        self.assertIsNotNone(signal, reason)
        assert signal is not None
        self.assertEqual(signal.side, 'SELL')
        self.assertEqual(signal.target_price, 80.0)
        self.assertGreater(signal.stop_price, signal.entry_reference)
        self.assertGreaterEqual(signal.net_reward_to_risk, 1.2)

    def test_failure_retest_requires_touch_and_rejection_inside(self):
        engine = self._engine()
        pending = self._pending()
        pending.state = 'FAILED'
        pending.failure_index = 3
        rejection = bar(504, 99.8, 100.1, 98.9, 99.0, volume=130, buy_fraction=0.25)
        self.assertTrue(engine._failure_retest_rejected(pending, rejection))


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
