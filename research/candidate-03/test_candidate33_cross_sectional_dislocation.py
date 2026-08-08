from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
import unittest

import candidate33_cross_sectional_dislocation as module
from candidate33_cross_sectional_dislocation import (
    CrossSectionalResidualDetector,
    CrossSectionalState,
    SCENARIO_KIND,
    _market_economics,
)
from logic import BarObs, CausalAuctionEngine, Direction, MINUTE_NS, Scenario, Side


class Candidate33CrossSectionalDislocationTests(unittest.TestCase):
    def engine(self, symbol: str, atr: float = 1.0):
        events: list[tuple[object, ...]] = []
        engine = SimpleNamespace(
            instrument_id=f"{symbol}-PERP.BINANCE",
            atr=atr,
            median_volume=100.0,
            config=SimpleNamespace(
                event_expiry_bars=60,
                retrace_expiry_bars=12,
                displacement_body_atr=0.20,
                min_relative_volume=0.85,
                absorption_flow_min=0.08,
                acceptance_close_atr=0.08,
                rejection_reclaim_atr=0.05,
                acceptance_min_closes=2,
                displacement_flow_min=0.03,
                acceptance_close_location=0.60,
                stop_buffer_atr=0.08,
                effective_taker_rate=0.0008,
                effective_maker_rate=0.0004,
                min_stop_atr=0.08,
                min_net_r=1.25,
            ),
            internal_highs=[(1 * MINUTE_NS, 2 * MINUTE_NS, 100.0)],
            internal_lows=[(1 * MINUTE_NS, 2 * MINUTE_NS, 90.0)],
            _index=100,
            active_trade_id=None,
            skips=Counter(),
            _candidate16_failed_far_state=None,
            _candidate33_cross_sectional_state=None,
            _candidate33_consumed_internal_keys=set(),
            _event=lambda *args: events.append(args),
        )
        return engine, events

    def test_unique_absolute_mover_with_peer_dissent_arms_state(self) -> None:
        symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
        detector = CrossSectionalResidualDetector(symbols)
        detector.previous_close = {symbol: 99.5 for symbol in symbols}
        engines = {symbol: self.engine(symbol)[0] for symbol in symbols}
        bars = {
            "BTCUSDT": BarObs(10 * MINUTE_NS, 99.5, 100.7, 99.4, 100.5, 200.0, 190.0),
            "ETHUSDT": BarObs(10 * MINUTE_NS, 99.5, 99.7, 99.2, 99.3, 100.0, 40.0),
            "SOLUSDT": BarObs(10 * MINUTE_NS, 99.5, 99.6, 99.1, 99.2, 100.0, 35.0),
            "XRPUSDT": BarObs(10 * MINUTE_NS, 99.5, 99.7, 99.4, 99.6, 100.0, 55.0),
        }
        plans = detector.on_batch(10 * MINUTE_NS, bars, engines)
        self.assertEqual(plans, [])
        state = engines["BTCUSDT"]._candidate33_cross_sectional_state
        self.assertIsNotNone(state)
        self.assertEqual(state.swept_side, Side.HIGH)
        self.assertEqual(state.direction, Direction.SHORT)
        self.assertEqual(state.pre_event_close, 99.5)

    def test_marketwide_same_sign_move_is_not_idiosyncratic(self) -> None:
        symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
        detector = CrossSectionalResidualDetector(symbols)
        detector.previous_close = {symbol: 99.5 for symbol in symbols}
        engines = {symbol: self.engine(symbol)[0] for symbol in symbols}
        closes = {"BTCUSDT": 100.5, "ETHUSDT": 100.2, "SOLUSDT": 100.1, "XRPUSDT": 99.9}
        bars = {
            symbol: BarObs(10 * MINUTE_NS, 99.5, close + 0.2, 99.4, close, 200.0, 190.0)
            for symbol, close in closes.items()
        }
        detector.on_batch(10 * MINUTE_NS, bars, engines)
        self.assertIsNone(engines["BTCUSDT"]._candidate33_cross_sectional_state)

    def test_later_reclaim_with_peer_reversal_builds_short_plan(self) -> None:
        symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
        detector = CrossSectionalResidualDetector(symbols)
        engine, events = self.engine("BTCUSDT")
        state = CrossSectionalState(
            scenario_id="BTC-CSD-1-2-SHORT",
            symbol="BTCUSDT",
            swept_side=Side.HIGH,
            direction=Direction.SHORT,
            pivot_candidate_ts_ns=1 * MINUTE_NS,
            pivot_known_ts_ns=2 * MINUTE_NS,
            pivot_level=100.0,
            event_ts_ns=10 * MINUTE_NS,
            event_index=100,
            expiry_index=112,
            pre_event_close=98.0,
            sweep_extreme=101.0,
            candidate_standardized_move=1.0,
            peer_standardized_moves={"ETHUSDT": -0.2, "SOLUSDT": -0.3, "XRPUSDT": 0.1},
            event_relative_volume=2.0,
            event_signed_flow=0.9,
        )
        engine._candidate33_cross_sectional_state = state
        engine._index = 101
        bar = BarObs(11 * MINUTE_NS, 100.4, 100.5, 98.8, 99.0, 120.0, 12.0)
        plan = detector._step_state(
            symbol="BTCUSDT",
            engine=engine,
            bar=bar,
            peer_moves={"ETHUSDT": -0.2, "SOLUSDT": -0.1, "XRPUSDT": 0.1},
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan.scenario, Scenario.FAR)
        self.assertEqual(plan.direction, Direction.SHORT)
        self.assertEqual(plan.entry_order_type, "MARKET")
        self.assertEqual(plan.details["scenario_kind"], SCENARIO_KIND)
        self.assertEqual(plan.target_price, 98.0)
        self.assertGreater(plan.stop_price, 101.0)
        self.assertEqual(events[-1][1], "TRADE_PLAN_CONFIRMED")

    def test_cost_model_reserves_taker_entry_stop_and_maker_target(self) -> None:
        risk, loss, gain, net_r = _market_economics(
            direction=Direction.SHORT,
            entry=99.0,
            stop=101.0,
            target=98.0,
            taker_rate=0.0008,
            maker_rate=0.0004,
        )
        self.assertAlmostEqual(risk, 2.0)
        self.assertAlmostEqual(loss, 2.1600)
        self.assertAlmostEqual(gain, 0.8816)
        self.assertAlmostEqual(net_r, 0.8816 / 2.1600)

    def test_install_captures_existing_lifecycle_hooks(self) -> None:
        old_methods = (
            CausalAuctionEngine.mark_submitted,
            CausalAuctionEngine.mark_rejected,
            CausalAuctionEngine.mark_trade_terminal,
        )
        old_bases = (
            module.BASE_MARK_SUBMITTED,
            module.BASE_MARK_REJECTED,
            module.BASE_MARK_TRADE_TERMINAL,
        )

        def prior_submitted(*args, **kwargs):
            return None

        def prior_rejected(*args, **kwargs):
            return None

        def prior_terminal(*args, **kwargs):
            return None

        try:
            CausalAuctionEngine.mark_submitted = prior_submitted
            CausalAuctionEngine.mark_rejected = prior_rejected
            CausalAuctionEngine.mark_trade_terminal = prior_terminal
            module.BASE_MARK_SUBMITTED = None
            module.BASE_MARK_REJECTED = None
            module.BASE_MARK_TRADE_TERMINAL = None
            module.install()
            self.assertIs(module.BASE_MARK_SUBMITTED, prior_submitted)
            self.assertIs(module.BASE_MARK_REJECTED, prior_rejected)
            self.assertIs(module.BASE_MARK_TRADE_TERMINAL, prior_terminal)
            self.assertIs(CausalAuctionEngine.mark_submitted, module.candidate33_mark_submitted)
        finally:
            (
                CausalAuctionEngine.mark_submitted,
                CausalAuctionEngine.mark_rejected,
                CausalAuctionEngine.mark_trade_terminal,
            ) = old_methods
            (
                module.BASE_MARK_SUBMITTED,
                module.BASE_MARK_REJECTED,
                module.BASE_MARK_TRADE_TERMINAL,
            ) = old_bases


if __name__ == "__main__":
    unittest.main(verbosity=2)
