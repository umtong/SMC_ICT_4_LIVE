from __future__ import annotations

import math
import unittest

from cross_market import (
    MINUTE_NS,
    CausalLeaderFollowerEngine,
    CrossObservation,
    _Shock,
)


class CrossMarketTests(unittest.TestCase):
    SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")

    @staticmethod
    def bar(index: int, price: float, flow_sign: float = 0.0) -> CrossObservation:
        volume = 100.0
        taker = volume * (0.5 + 0.4 * flow_sign)
        return CrossObservation(
            ts_ns=(index + 1) * MINUTE_NS,
            open=price,
            high=price * 1.0005,
            low=price * 0.9995,
            close=price,
            volume=volume,
            quote_volume=price * volume,
            taker_buy_volume=taker,
        )

    def test_positive_beta_and_residual_rms_are_causal_statistics(self) -> None:
        x = [0.0001 * ((index % 7) - 3) for index in range(120)]
        y = [1.5 * value + 0.00001 * ((index % 5) - 2) for index, value in enumerate(x)]
        model = CausalLeaderFollowerEngine._beta_and_rms(y, x)
        self.assertIsNotNone(model)
        assert model is not None
        beta, rms = model
        self.assertGreater(beta, 1.0)
        self.assertLess(beta, 2.0)
        self.assertGreater(rms, 0.0)

    def test_negative_beta_is_not_treated_as_follower_relation(self) -> None:
        x = [0.0001 * ((index % 7) - 3) for index in range(120)]
        y = [-2.0 * value + 0.00001 * ((index % 5) - 2) for index, value in enumerate(x)]
        model = CausalLeaderFollowerEngine._beta_and_rms(y, x)
        self.assertIsNotNone(model)
        assert model is not None
        beta, _ = model
        self.assertEqual(0.0, beta)

    def test_incomplete_synchronized_batch_fails_closed(self) -> None:
        engine = CausalLeaderFollowerEngine(self.SYMBOLS)
        observations = {
            symbol: self.bar(0, 100.0 + index)
            for index, symbol in enumerate(self.SYMBOLS[:-1])
        }
        with self.assertRaises(ValueError):
            engine.on_batch(MINUTE_NS, observations)

    def test_target_already_reached_is_rejected(self) -> None:
        engine = CausalLeaderFollowerEngine(self.SYMBOLS)
        shock = _Shock(
            shock_id="TEST",
            leader="BTCUSDT",
            direction="LONG",
            detected_ts_ns=10 * MINUTE_NS,
            base_ts_ns=7 * MINUTE_NS,
            base_prices={symbol: 100.0 for symbol in self.SYMBOLS},
            betas={symbol: 1.0 for symbol in self.SYMBOLS},
            residual_rms={symbol: 0.001 for symbol in self.SYMBOLS},
            flow_rms={symbol: 1000.0 for symbol in self.SYMBOLS},
            leader_initial_move=0.01,
            leader_peak_move=0.01,
            leader_score=2.0,
        )
        bar = CrossObservation(
            ts_ns=11 * MINUTE_NS,
            open=100.0,
            high=102.0,
            low=99.5,
            close=101.0,
            volume=100.0,
            quote_volume=10_000.0,
            taker_buy_volume=80.0,
        )
        plan = engine._costed_plan(
            symbol="ETHUSDT",
            bar=bar,
            shock=shock,
            entry=100.5,
            stop=99.0,
            target=101.5,
            atr=1.0,
            signal_score=3.0,
            details={},
        )
        self.assertIsNone(plan)
        self.assertEqual(1, engine.skips["CROSS_MARKET_TARGET_REACHED_BEFORE_CONFIRMATION"])

    def test_costed_fair_value_plan_includes_friction(self) -> None:
        engine = CausalLeaderFollowerEngine(self.SYMBOLS)
        shock = _Shock(
            shock_id="TEST",
            leader="BTCUSDT",
            direction="LONG",
            detected_ts_ns=10 * MINUTE_NS,
            base_ts_ns=7 * MINUTE_NS,
            base_prices={symbol: 100.0 for symbol in self.SYMBOLS},
            betas={symbol: 1.0 for symbol in self.SYMBOLS},
            residual_rms={symbol: 0.001 for symbol in self.SYMBOLS},
            flow_rms={symbol: 1000.0 for symbol in self.SYMBOLS},
            leader_initial_move=0.01,
            leader_peak_move=0.01,
            leader_score=2.0,
        )
        bar = CrossObservation(
            ts_ns=11 * MINUTE_NS,
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.4,
            volume=100.0,
            quote_volume=10_000.0,
            taker_buy_volume=80.0,
        )
        plan = engine._costed_plan(
            symbol="ETHUSDT",
            bar=bar,
            shock=shock,
            entry=100.2,
            stop=99.0,
            target=104.0,
            atr=1.0,
            signal_score=3.0,
            details={"source": "TEST"},
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertGreater(plan.loss_per_unit, abs(plan.expected_entry - plan.stop_price))
        self.assertGreaterEqual(plan.net_r, 1.25)
        self.assertEqual(19 * MINUTE_NS, plan.expire_ts_ns)

    def test_signed_flow_matches_aggressor_side(self) -> None:
        buy = self.bar(0, 100.0, 1.0)
        sell = self.bar(0, 100.0, -1.0)
        self.assertGreater(buy.signed_quote_flow, 0.0)
        self.assertLess(sell.signed_quote_flow, 0.0)


if __name__ == "__main__":
    unittest.main()
