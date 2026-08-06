from __future__ import annotations

import json
import unittest
from collections import deque
from decimal import Decimal
from pathlib import Path

from state_engine_v7_direct import (
    MINUTE_NS,
    EngineConfig,
    FlowBar,
    LiquidityLevel,
    LiquidityStateEngine,
    PendingSweep,
    SessionRange,
    SessionSpec,
    risk_based_quantity,
)

CONFIG = json.loads((Path(__file__).resolve().parents[1] / 'config_v7.json').read_text())


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
    return SessionRange('r', 'ASIA', 0, 480 * MINUTE_NS, 120.0, 100.0, 110.0, 480)


class ConfigContractTest(unittest.TestCase):
    def test_single_variable_ablations(self):
        base = EngineConfig.from_mapping(CONFIG, ablation='baseline')
        no_flow = EngineConfig.from_mapping(CONFIG, ablation='no-flow')
        no_retest = EngineConfig.from_mapping(CONFIG, ablation='no-fvg-retest')
        midpoint = EngineConfig.from_mapping(CONFIG, ablation='midpoint-target')
        self.assertTrue(base.use_flow_confirmation)
        self.assertTrue(base.require_fvg_retest)
        self.assertFalse(base.use_midpoint_target)
        self.assertFalse(no_flow.use_flow_confirmation)
        self.assertFalse(no_retest.require_fvg_retest)
        self.assertTrue(midpoint.use_midpoint_target)


class SessionContractTest(unittest.TestCase):
    def test_close_timestamp_is_assigned_to_bar_interval_session(self):
        engine = LiquidityStateEngine(EngineConfig(sessions=sessions()))
        _, _, session_a, _, _ = engine._session_identity(480 * MINUTE_NS)
        _, _, session_b, _, _ = engine._session_identity(481 * MINUTE_NS)
        self.assertEqual(session_a.name, 'ASIA')
        self.assertEqual(session_b.name, 'EUROPE')

    def test_completed_source_range_arms_only_previous_session_edges(self):
        engine = LiquidityStateEngine(EngineConfig(sessions=sessions(), volume_period=3, approach_period=2))
        for minute in range(0, 480):
            engine.on_bar(bar(minute, 100, 101 + minute % 2, 99, 100.5))
        result = engine.on_bar(bar(480, 100.5, 102, 100, 101))
        self.assertIsNotNone(engine.source_range)
        assert engine.source_range is not None
        self.assertEqual(engine.source_range.session_name, 'ASIA')
        self.assertEqual({item.kind for item in engine.active_levels}, {'HIGH', 'LOW'})
        self.assertTrue(any(event.event_type == 'SESSION_RANGE_CONFIRMED' for event in result.events))


class FvgEntryContractTest(unittest.TestCase):
    def _engine(self, *, flow: bool = True) -> LiquidityStateEngine:
        config = EngineConfig(
            sessions=sessions(),
            atr_period=3,
            volume_period=3,
            approach_period=2,
            mss_lookback_bars=3,
            minimum_displacement_atr=0.30,
            minimum_volume_ratio=0.5,
            minimum_fvg_atr=0.04,
            minimum_net_reward_to_risk=1.2,
            composite_cost_per_fill=0.00075,
            use_flow_confirmation=flow,
        )
        engine = LiquidityStateEngine(config)
        engine._atr = 10.0
        engine._volume_median = 100.0
        engine._current_session = sessions()[1]
        engine._current_session_key = 1
        return engine

    def test_downside_sweep_displacement_and_retest_build_buy(self):
        engine = self._engine()
        src = source()
        pending = PendingSweep('s', LiquidityLevel('l', 'LOW', 100.0, src), 'DOWN', 'SWEPT', 0, 95.0, 0.3, -0.2, 1, 'EUROPE')
        engine._bars = deque([
            bar(500, 99, 100, 98, 99.5),
            bar(501, 100, 101, 99, 100.5),
            bar(502, 101, 102, 100, 101.5),
            bar(503, 103, 108, 103, 107, volume=160, buy_fraction=0.75),
        ], maxlen=512)
        gap = engine._opposite_displacement_fvg(pending, engine._bars[-1])
        self.assertEqual(gap, (101, 103))
        pending.state = 'WAIT_FVG_RETEST'
        pending.displacement_index = 3
        pending.fvg_lower, pending.fvg_upper = gap
        rejection = bar(504, 102.0, 104.0, 101.5, 103.5, volume=120, buy_fraction=0.70)
        self.assertTrue(engine._fvg_retest_rejected(pending, rejection))
        signal, reason, _ = engine._build_signal(pending, rejection, entry_model='FVG_RETEST')
        self.assertIsNotNone(signal, reason)
        assert signal is not None
        self.assertEqual(signal.side, 'BUY')
        self.assertEqual(signal.target_price, 120.0)
        self.assertLess(signal.stop_price, signal.entry_reference)
        self.assertGreaterEqual(signal.net_reward_to_risk, 1.2)

    def test_no_flow_ablation_keeps_structure_but_removes_flow_gate(self):
        strict = self._engine(flow=True)
        relaxed = self._engine(flow=False)
        src = source()
        pending = PendingSweep('s', LiquidityLevel('l', 'LOW', 100.0, src), 'DOWN', 'SWEPT', 0, 95.0, 0.3, -0.2, 1, 'EUROPE')
        bars = deque([
            bar(500, 99, 100, 98, 99.5),
            bar(501, 100, 101, 99, 100.5),
            bar(502, 101, 102, 100, 101.5),
            bar(503, 103, 108, 103, 107, volume=160, buy_fraction=0.30),
        ], maxlen=512)
        strict._bars = bars
        relaxed._bars = deque(bars, maxlen=512)
        self.assertIsNone(strict._opposite_displacement_fvg(pending, strict._bars[-1]))
        self.assertEqual(relaxed._opposite_displacement_fvg(pending, relaxed._bars[-1]), (101, 103))


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
